//! Nummernkreise: Muster wie `RE-{YYYY}-{SEQ:5}`.
//!
//! - genau ein `{SEQ:n}` (n = 1..=10 Stellen, Auffüllen mit Nullen; größere Zahlen werden nie abgeschnitten)
//! - höchstens ein `{YYYY}`; ist es enthalten, beginnt der Zähler jedes Jahr bei 1
//! - erlaubte Zeichen außerhalb der Platzhalter: A–Z a–z 0–9 - _ / .
//! - Ergebnis höchstens 36 Zeichen (DATEV „Belegfeld 1“)

pub const MAX_NUMBER_LEN: usize = 36;
pub const MAX_PATTERN_LEN: usize = 64;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum NumberingError {
    #[error("Nummernmuster ungültig: {0}")]
    InvalidPattern(String),
    #[error("Nummernkreis-Schlüssel ungültig (erlaubt: a–z, 0–9, _; 1–32 Zeichen, beginnt mit Buchstabe)")]
    InvalidKey,
    #[error("Belegnummer '{0}' ist länger als {MAX_NUMBER_LEN} Zeichen")]
    TooLong(String),
    #[error("Jahr {0} außerhalb des zulässigen Bereichs")]
    InvalidYear(i32),
    #[error("Zählerwert muss positiv sein")]
    InvalidCounter,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Part {
    Literal(String),
    Year,
    Seq(usize),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NumberPattern {
    parts: Vec<Part>,
}

pub fn validate_range_key(key: &str) -> Result<(), NumberingError> {
    let mut chars = key.chars();
    let valid = matches!(chars.next(), Some('a'..='z'))
        && key.len() <= 32
        && chars.all(|c| matches!(c, 'a'..='z' | '0'..='9' | '_'));
    if valid {
        Ok(())
    } else {
        Err(NumberingError::InvalidKey)
    }
}

fn is_literal_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '/' | '.')
}

impl NumberPattern {
    pub fn parse(pattern: &str) -> Result<Self, NumberingError> {
        let invalid = |m: &str| NumberingError::InvalidPattern(m.to_owned());
        if pattern.is_empty() || pattern.len() > MAX_PATTERN_LEN {
            return Err(invalid("Länge 1–64 Zeichen"));
        }
        let mut parts = Vec::new();
        let mut literal = String::new();
        let mut rest = pattern;
        let (mut seq_count, mut year_count) = (0, 0);
        while let Some(c) = rest.chars().next() {
            if c == '{' {
                let end = rest
                    .find('}')
                    .ok_or_else(|| invalid("'{' ohne schließendes '}'"))?;
                let token = &rest[1..end];
                if !literal.is_empty() {
                    parts.push(Part::Literal(std::mem::take(&mut literal)));
                }
                if token == "YYYY" {
                    year_count += 1;
                    parts.push(Part::Year);
                } else if let Some(width) = token.strip_prefix("SEQ:") {
                    let w: usize = width
                        .parse()
                        .ok()
                        .filter(|w| {
                            (1..=10).contains(w)
                                && !width.starts_with('+')
                                && !width.starts_with('0')
                        })
                        .ok_or_else(|| invalid("{SEQ:n} braucht n zwischen 1 und 10"))?;
                    seq_count += 1;
                    parts.push(Part::Seq(w));
                } else {
                    return Err(invalid(
                        "unbekannter Platzhalter (erlaubt: {YYYY}, {SEQ:n})",
                    ));
                }
                rest = &rest[end + 1..];
            } else if is_literal_char(c) {
                literal.push(c);
                rest = &rest[c.len_utf8()..];
            } else {
                return Err(invalid(
                    "unerlaubtes Zeichen (erlaubt: A–Z a–z 0–9 - _ / .)",
                ));
            }
        }
        if !literal.is_empty() {
            parts.push(Part::Literal(literal));
        }
        if seq_count != 1 {
            return Err(invalid("genau ein {SEQ:n} erforderlich"));
        }
        if year_count > 1 {
            return Err(invalid("höchstens ein {YYYY}"));
        }
        // Zwei Zahlen direkt hintereinander wären mehrdeutig (z. B. "{YYYY}{SEQ:3}")
        if parts.windows(2).any(|w| {
            matches!(
                (&w[0], &w[1]),
                (Part::Year, Part::Seq(_)) | (Part::Seq(_), Part::Year)
            )
        }) {
            return Err(invalid(
                "{YYYY} und {SEQ:n} müssen durch ein Trennzeichen getrennt sein",
            ));
        }
        Ok(Self { parts })
    }

    pub fn uses_year(&self) -> bool {
        self.parts.contains(&Part::Year)
    }

    /// Jahr-Schlüssel für den Zähler: das Jahr, wenn das Muster `{YYYY}` enthält, sonst 0.
    pub fn counter_year(&self, year: i32) -> i32 {
        if self.uses_year() {
            year
        } else {
            0
        }
    }

    pub fn format(&self, year: i32, counter: i64) -> Result<String, NumberingError> {
        if !(2000..=2100).contains(&year) {
            return Err(NumberingError::InvalidYear(year));
        }
        if counter <= 0 {
            return Err(NumberingError::InvalidCounter);
        }
        let mut out = String::new();
        for p in &self.parts {
            match p {
                Part::Literal(s) => out.push_str(s),
                Part::Year => out.push_str(&format!("{year:04}")),
                Part::Seq(w) => out.push_str(&format!("{counter:0w$}")),
            }
        }
        if out.len() > MAX_NUMBER_LEN {
            return Err(NumberingError::TooLong(out));
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_invoice_numbers() {
        let p = NumberPattern::parse("RE-{YYYY}-{SEQ:5}").unwrap();
        assert!(p.uses_year());
        assert_eq!(p.format(2026, 1).unwrap(), "RE-2026-00001");
        assert_eq!(
            p.format(2026, 123_456).unwrap(),
            "RE-2026-123456",
            "niemals abschneiden"
        );
        assert_eq!(p.counter_year(2026), 2026);
    }

    #[test]
    fn pattern_without_year_uses_year_zero() {
        let p = NumberPattern::parse("K-{SEQ:5}").unwrap();
        assert!(!p.uses_year());
        assert_eq!(p.counter_year(2026), 0);
        assert_eq!(p.format(2026, 42).unwrap(), "K-00042");
    }

    #[test]
    fn rejects_invalid_patterns() {
        for bad in [
            "",
            "RE-{YYYY}",
            "RE-{SEQ:5}-{SEQ:3}",
            "RE-{YYYY}-{YYYY}-{SEQ:2}",
            "RE-{SEQ:0}",
            "RE-{SEQ:11}",
            "RE-{SEQ:05}",
            "RE-{SEQ:+5}",
            "RE {SEQ:5}",
            "RE-{FOO}-{SEQ:5}",
            "RE-{SEQ:5",
            "RE-ÄÖ-{SEQ:5}",
            "{YYYY}{SEQ:3}",
        ] {
            assert!(
                NumberPattern::parse(bad).is_err(),
                "sollte ungültig sein: {bad:?}"
            );
        }
    }

    #[test]
    fn rejects_too_long_numbers_and_bad_inputs() {
        let p = NumberPattern::parse("RECHNUNGSNUMMER-SEHR-LANG-{YYYY}-{SEQ:6}").unwrap();
        assert!(matches!(p.format(2026, 1), Err(NumberingError::TooLong(_))));
        let p = NumberPattern::parse("RE-{SEQ:3}").unwrap();
        assert_eq!(p.format(2026, 0), Err(NumberingError::InvalidCounter));
        assert_eq!(p.format(1999, 1), Err(NumberingError::InvalidYear(1999)));
    }

    #[test]
    fn validates_range_keys() {
        assert!(validate_range_key("invoice").is_ok());
        assert!(validate_range_key("invoice_2").is_ok());
        for bad in ["", "Invoice", "1abc", "in-voice", "a".repeat(33).as_str()] {
            assert!(validate_range_key(bad).is_err(), "{bad:?}");
        }
    }
}
