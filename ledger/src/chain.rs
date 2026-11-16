//! Hash-Kette über alle festgeschriebenen Datensätze.
//!
//! hash(n) = SHA-256( prev_hash(n) ‖ "\n" ‖ kanonischer Umschlag(n) )
//! Der Umschlag enthält alle Spalten des Datensatzes inkl. der kanonischen Nutzdaten.
//! Jede nachträgliche Änderung an irgendeiner Spalte eines Datensatzes macht dessen Hash
//! und alle folgenden ungültig.

use crate::canonical::{canonicalize, CanonicalError};
use serde_json::Value;
use sha2::{Digest, Sha256};
use time::OffsetDateTime;
use uuid::Uuid;

/// Vorgänger-Hash des ersten Datensatzes.
pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

/// Alle Felder, die in den Hash eingehen.
#[derive(Debug, Clone)]
pub struct Envelope<'a> {
    pub seq: i64,
    pub id: Uuid,
    pub tenant_id: Uuid,
    pub record_type: &'a str,
    pub subject_id: Option<Uuid>,
    pub number_range_key: Option<&'a str>,
    pub document_number: Option<&'a str>,
    pub sealed_at: OffsetDateTime,
    pub sealed_by: &'a str,
    /// bereits kanonisierte Nutzdaten
    pub canonical_payload: &'a str,
}

/// PostgreSQL speichert Mikrosekunden; vor dem Hashen wird genau darauf gekürzt.
pub fn truncate_to_micros(t: OffsetDateTime) -> OffsetDateTime {
    let t = t.to_offset(time::UtcOffset::UTC);
    let micros = t.nanosecond() / 1_000;
    t.replace_nanosecond(micros * 1_000)
        .expect("gültige Nanosekunden")
}

/// Fester Zeitstempel-Text: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC, immer 6 Nachkommastellen).
pub fn format_timestamp(t: OffsetDateTime) -> String {
    let t = truncate_to_micros(t);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}Z",
        t.year(),
        u8::from(t.month()),
        t.day(),
        t.hour(),
        t.minute(),
        t.second(),
        t.nanosecond() / 1_000
    )
}

fn json_str(s: &str) -> String {
    serde_json::to_string(s).expect("String-Serialisierung kann nicht fehlschlagen")
}

fn opt_str(s: Option<&str>) -> String {
    s.map_or_else(|| "null".to_owned(), json_str)
}

/// Kanonischer Umschlag – Schlüssel bereits alphabetisch sortiert.
pub fn envelope_canonical(e: &Envelope<'_>) -> String {
    format!(
        concat!(
            "{{\"document_number\":{},\"id\":{},\"number_range_key\":{},\"payload\":{},",
            "\"record_type\":{},\"sealed_at\":{},\"sealed_by\":{},\"seq\":{},",
            "\"subject_id\":{},\"tenant_id\":{}}}"
        ),
        opt_str(e.document_number),
        json_str(&e.id.to_string()),
        opt_str(e.number_range_key),
        e.canonical_payload,
        json_str(e.record_type),
        json_str(&format_timestamp(e.sealed_at)),
        json_str(e.sealed_by),
        e.seq,
        opt_str(e.subject_id.map(|u| u.to_string()).as_deref()),
        json_str(&e.tenant_id.to_string()),
    )
}

pub fn compute_hash(prev_hash: &str, envelope: &Envelope<'_>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(prev_hash.as_bytes());
    hasher.update(b"\n");
    hasher.update(envelope_canonical(envelope).as_bytes());
    hex::encode(hasher.finalize())
}

/// Gespeicherter Datensatz, wie er aus der Datenbank gelesen wird.
#[derive(Debug, Clone)]
pub struct StoredRecord {
    pub seq: i64,
    pub id: Uuid,
    pub tenant_id: Uuid,
    pub record_type: String,
    pub subject_id: Option<Uuid>,
    pub number_range_key: Option<String>,
    pub document_number: Option<String>,
    pub payload: Value,
    pub canonical_payload: String,
    pub prev_hash: String,
    pub hash: String,
    pub sealed_at: OffsetDateTime,
    pub sealed_by: String,
}

impl StoredRecord {
    pub fn envelope(&self) -> Envelope<'_> {
        Envelope {
            seq: self.seq,
            id: self.id,
            tenant_id: self.tenant_id,
            record_type: &self.record_type,
            subject_id: self.subject_id,
            number_range_key: self.number_range_key.as_deref(),
            document_number: self.document_number.as_deref(),
            sealed_at: self.sealed_at,
            sealed_by: &self.sealed_by,
            canonical_payload: &self.canonical_payload,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ChainBreak {
    pub seq: i64,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct VerifyReport {
    pub ok: bool,
    pub records_checked: u64,
    pub head_seq: Option<i64>,
    pub head_hash: Option<String>,
    pub first_break: Option<ChainBreak>,
}

/// Prüft eine nach `seq` aufsteigend sortierte Folge von Datensätzen.
/// Bricht beim ersten Fehler ab und meldet ihn.
pub struct ChainVerifier {
    expected_seq: i64,
    expected_prev: String,
    checked: u64,
    first_break: Option<ChainBreak>,
}

impl Default for ChainVerifier {
    fn default() -> Self {
        Self {
            expected_seq: 1,
            expected_prev: GENESIS_HASH.to_owned(),
            checked: 0,
            first_break: None,
        }
    }
}

impl ChainVerifier {
    /// Gibt `false` zurück, sobald ein Bruch gefunden wurde (weitere Datensätze werden ignoriert).
    pub fn push(&mut self, r: &StoredRecord) -> bool {
        if self.first_break.is_some() {
            return false;
        }
        if let Some(reason) = self.check(r) {
            self.first_break = Some(ChainBreak { seq: r.seq, reason });
            return false;
        }
        self.checked += 1;
        self.expected_seq = r.seq + 1;
        self.expected_prev.clone_from(&r.hash);
        true
    }

    fn check(&self, r: &StoredRecord) -> Option<String> {
        if r.seq != self.expected_seq {
            return Some(format!(
                "Lücke oder falsche Reihenfolge: erwartet seq {}, gefunden {}",
                self.expected_seq, r.seq
            ));
        }
        if r.prev_hash != self.expected_prev {
            return Some("prev_hash passt nicht zum Hash des Vorgängers".to_owned());
        }
        match canonicalize(&r.payload) {
            Ok(c) if c == r.canonical_payload => {}
            Ok(_) => {
                return Some(
                    "Nutzdaten (payload) weichen von der kanonischen Fassung ab".to_owned(),
                )
            }
            Err(e) => return Some(format!("Nutzdaten nicht kanonisierbar: {e}")),
        }
        if compute_hash(&r.prev_hash, &r.envelope()) != r.hash {
            return Some("Hash stimmt nicht – Datensatz wurde verändert".to_owned());
        }
        None
    }

    pub fn finish(self) -> VerifyReport {
        let head = if self.checked > 0 {
            Some((self.expected_seq - 1, self.expected_prev))
        } else {
            None
        };
        VerifyReport {
            ok: self.first_break.is_none(),
            records_checked: self.checked,
            head_seq: head.as_ref().map(|h| h.0),
            head_hash: head.map(|h| h.1),
            first_break: self.first_break,
        }
    }
}

/// Baut einen neuen Datensatz (rein, ohne Datenbank) – Grundlage für Tests und `db::seal`.
#[allow(clippy::too_many_arguments)]
pub fn build_record(
    seq: i64,
    prev_hash: &str,
    id: Uuid,
    tenant_id: Uuid,
    record_type: &str,
    subject_id: Option<Uuid>,
    number_range_key: Option<&str>,
    document_number: Option<&str>,
    payload: Value,
    sealed_at: OffsetDateTime,
    sealed_by: &str,
) -> Result<StoredRecord, CanonicalError> {
    let canonical_payload = canonicalize(&payload)?;
    let sealed_at = truncate_to_micros(sealed_at);
    let mut record = StoredRecord {
        seq,
        id,
        tenant_id,
        record_type: record_type.to_owned(),
        subject_id,
        number_range_key: number_range_key.map(str::to_owned),
        document_number: document_number.map(str::to_owned),
        payload,
        canonical_payload,
        prev_hash: prev_hash.to_owned(),
        hash: String::new(),
        sealed_at,
        sealed_by: sealed_by.to_owned(),
    };
    record.hash = compute_hash(prev_hash, &record.envelope());
    Ok(record)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use time::macros::datetime;

    fn chain(n: usize) -> Vec<StoredRecord> {
        let tenant = Uuid::from_u128(1);
        let mut prev = GENESIS_HASH.to_owned();
        (1..=n as i64)
            .map(|seq| {
                let r = build_record(
                    seq,
                    &prev,
                    Uuid::from_u128(1000 + seq as u128),
                    tenant,
                    "invoice.issued",
                    Some(Uuid::from_u128(seq as u128)),
                    Some("invoice"),
                    Some(&format!("RE-2026-{seq:05}")),
                    json!({"total_gross": "2092.02", "lines": [{"net": "1758.00"}]}),
                    datetime!(2026-11-14 12:00:00.123456789 UTC),
                    "user:test",
                )
                .unwrap();
                prev.clone_from(&r.hash);
                r
            })
            .collect()
    }

    fn verify(records: &[StoredRecord]) -> VerifyReport {
        let mut v = ChainVerifier::default();
        for r in records {
            if !v.push(r) {
                break;
            }
        }
        v.finish()
    }

    #[test]
    fn timestamp_format_is_fixed_and_truncated() {
        assert_eq!(
            format_timestamp(datetime!(2026-01-02 03:04:05.000000999 UTC)),
            "2026-01-02T03:04:05.000000Z"
        );
        assert_eq!(
            format_timestamp(datetime!(2026-12-31 23:59:59.987654321 +01:00)),
            "2026-12-31T22:59:59.987654Z"
        );
    }

    #[test]
    fn hash_is_deterministic_and_known() {
        let a = chain(1);
        let b = chain(1);
        assert_eq!(a[0].hash, b[0].hash);
        assert_eq!(a[0].hash.len(), 64);
    }

    #[test]
    fn valid_chain_verifies() {
        let report = verify(&chain(5));
        assert!(report.ok, "{report:?}");
        assert_eq!(report.records_checked, 5);
        assert_eq!(report.head_seq, Some(5));
    }

    #[test]
    fn empty_chain_is_ok() {
        let report = verify(&[]);
        assert!(report.ok);
        assert_eq!(report.head_seq, None);
    }

    #[test]
    fn detects_payload_tampering() {
        let mut c = chain(5);
        c[2].payload = json!({"total_gross": "1.00", "lines": [{"net": "1758.00"}]});
        let report = verify(&c);
        assert!(!report.ok);
        assert_eq!(report.first_break.unwrap().seq, 3);
    }

    #[test]
    fn detects_tampering_even_if_canonical_payload_is_updated_too() {
        let mut c = chain(5);
        c[2].payload = json!({"total_gross": "1.00"});
        c[2].canonical_payload = canonicalize(&c[2].payload).unwrap();
        let report = verify(&c);
        assert_eq!(report.first_break.unwrap().seq, 3);
    }

    #[test]
    fn detects_metadata_tampering() {
        for mutate in [
            (|r: &mut StoredRecord| r.document_number = Some("RE-2026-99999".into()))
                as fn(&mut StoredRecord),
            |r| r.sealed_by = "user:anderer".into(),
            |r| r.sealed_at = datetime!(2025-01-01 00:00:00 UTC),
            |r| r.record_type = "invoice.cancelled".into(),
            |r| r.subject_id = None,
        ] {
            let mut c = chain(4);
            mutate(&mut c[1]);
            assert_eq!(verify(&c).first_break.unwrap().seq, 2);
        }
    }

    #[test]
    fn detects_recomputed_hash_breaking_successor() {
        // Angreifer ändert Datensatz 2 und berechnet dessen Hash neu → Datensatz 3 passt nicht mehr.
        let mut c = chain(4);
        c[1].payload = json!({"total_gross": "0.01"});
        c[1].canonical_payload = canonicalize(&c[1].payload).unwrap();
        c[1].hash = compute_hash(&c[1].prev_hash, &c[1].envelope());
        let report = verify(&c);
        let brk = report.first_break.unwrap();
        assert_eq!(brk.seq, 3);
        assert!(brk.reason.contains("prev_hash"));
    }

    #[test]
    fn detects_gap_and_deletion() {
        let mut c = chain(5);
        c.remove(2);
        let report = verify(&c);
        assert_eq!(report.first_break.unwrap().seq, 4);
    }
}
