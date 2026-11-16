//! Kanonisches JSON: identische Daten ergeben byte-identische Zeichenketten und damit identische Hashes.
//!
//! Regeln:
//! - Objekt-Schlüssel lexikografisch nach UTF-8-Bytes sortiert, keine Leerzeichen
//! - Strings nach JSON-Standard escaped (serde_json), NUL-Zeichen verboten (PostgreSQL JSONB lehnt sie ab)
//! - **Nur Ganzzahlen**. Zahlen mit Nachkommastellen oder Exponent werden abgelehnt:
//!   Geldbeträge müssen als Dezimal-Strings übergeben werden (z. B. "1758.00").
//! - Maximale Verschachtelungstiefe begrenzt (Schutz vor Stack-Überlauf)

use serde_json::Value;
use std::fmt::Write as _;

pub const MAX_DEPTH: usize = 32;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum CanonicalError {
    #[error(
        "Zahl mit Nachkommastellen bei '{0}' nicht erlaubt – Beträge als Dezimal-String übergeben"
    )]
    NonIntegerNumber(String),
    #[error("NUL-Zeichen in String bei '{0}' nicht erlaubt")]
    NulCharacter(String),
    #[error("Verschachtelung tiefer als {MAX_DEPTH} Ebenen")]
    TooDeep,
}

/// Wandelt einen JSON-Wert in seine kanonische Form.
pub fn canonicalize(value: &Value) -> Result<String, CanonicalError> {
    let mut out = String::new();
    write_value(value, &mut out, 0, "$")?;
    Ok(out)
}

fn write_value(
    value: &Value,
    out: &mut String,
    depth: usize,
    path: &str,
) -> Result<(), CanonicalError> {
    if depth > MAX_DEPTH {
        return Err(CanonicalError::TooDeep);
    }
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                let _ = write!(out, "{i}");
            } else if let Some(u) = n.as_u64() {
                let _ = write!(out, "{u}");
            } else {
                return Err(CanonicalError::NonIntegerNumber(path.to_owned()));
            }
        }
        Value::String(s) => write_string(s, out, path)?,
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_value(item, out, depth + 1, &format!("{path}[{i}]"))?;
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort_unstable_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
            out.push('{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                let child_path = format!("{path}.{key}");
                write_string(key, out, &child_path)?;
                out.push(':');
                write_value(&map[*key], out, depth + 1, &child_path)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

fn write_string(s: &str, out: &mut String, path: &str) -> Result<(), CanonicalError> {
    if s.contains('\0') {
        return Err(CanonicalError::NulCharacter(path.to_owned()));
    }
    // serde_json-Escaping ist deterministisch: ", \, Steuerzeichen; Nicht-ASCII bleibt unverändert.
    out.push_str(&serde_json::to_string(s).expect("String-Serialisierung kann nicht fehlschlagen"));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sorts_keys_and_removes_whitespace() {
        let v: Value =
            serde_json::from_str(r#"{ "b": 1, "a": {"z": true, "y": null}, "c": [3, 2] }"#)
                .unwrap();
        assert_eq!(
            canonicalize(&v).unwrap(),
            r#"{"a":{"y":null,"z":true},"b":1,"c":[3,2]}"#
        );
    }

    #[test]
    fn same_data_different_order_gives_same_result() {
        let a: Value = serde_json::from_str(r#"{"x":"1","y":{"b":2,"a":1}}"#).unwrap();
        let b: Value = serde_json::from_str(r#"{"y":{"a":1,"b":2},"x":"1"}"#).unwrap();
        assert_eq!(canonicalize(&a).unwrap(), canonicalize(&b).unwrap());
    }

    #[test]
    fn rejects_floats() {
        let v = json!({"betrag": 17.58});
        assert_eq!(
            canonicalize(&v),
            Err(CanonicalError::NonIntegerNumber("$.betrag".into()))
        );
        let v: Value = serde_json::from_str(r#"{"n": 1e3}"#).unwrap();
        assert!(canonicalize(&v).is_err());
    }

    #[test]
    fn accepts_large_integers_and_decimal_strings() {
        let v = json!({"cent": 9_007_199_254_740_993_i64, "betrag": "1758.00", "neg": -5});
        assert_eq!(
            canonicalize(&v).unwrap(),
            r#"{"betrag":"1758.00","cent":9007199254740993,"neg":-5}"#
        );
    }

    #[test]
    fn escapes_strings_deterministically() {
        let v = json!({"t": "Zeile\n\"Zitat\" \\ Ä €"});
        assert_eq!(
            canonicalize(&v).unwrap(),
            "{\"t\":\"Zeile\\n\\\"Zitat\\\" \\\\ Ä €\"}"
        );
    }

    #[test]
    fn sorts_by_bytes_not_locale() {
        let v = json!({"ä": 1, "z": 2, "Z": 3, "a": 4});
        // Bytes: "Z"(0x5A) < "a"(0x61) < "z"(0x7A) < "ä"(0xC3 0xA4)
        assert_eq!(canonicalize(&v).unwrap(), r#"{"Z":3,"a":4,"z":2,"ä":1}"#);
    }

    #[test]
    fn rejects_nul() {
        let v = json!({"t": "a\u{0000}b"});
        assert!(matches!(
            canonicalize(&v),
            Err(CanonicalError::NulCharacter(_))
        ));
    }

    #[test]
    fn rejects_excessive_depth() {
        let mut v = json!(1);
        for _ in 0..=MAX_DEPTH {
            v = json!([v]);
        }
        assert_eq!(canonicalize(&v), Err(CanonicalError::TooDeep));
    }
}
