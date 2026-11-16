//! Eingabeprüfungen, die nicht in die Datenbank-Constraints gehören bzw. vorher verständliche Fehler liefern.

use time::{Date, Month};

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ValidationError {
    #[error("record_type ungültig (z. B. 'invoice.issued'; a–z, 0–9, _, Punkte; max. 64 Zeichen)")]
    RecordType,
    #[error("sealed_by muss 1–200 Zeichen lang sein")]
    SealedBy,
    #[error("Periode muss im Format JJJJ-MM vorliegen (z. B. 2026-11)")]
    Period,
    #[error("Datum muss im Format JJJJ-MM-TT vorliegen")]
    Date,
    #[error("payload muss ein JSON-Objekt sein")]
    PayloadNotObject,
}

pub fn record_type(s: &str) -> Result<(), ValidationError> {
    let ok = !s.is_empty()
        && s.len() <= 64
        && s.split('.').all(|seg| {
            let mut c = seg.chars();
            matches!(c.next(), Some('a'..='z'))
                && c.all(|ch| matches!(ch, 'a'..='z' | '0'..='9' | '_'))
        });
    if ok {
        Ok(())
    } else {
        Err(ValidationError::RecordType)
    }
}

pub fn sealed_by(s: &str) -> Result<(), ValidationError> {
    let n = s.chars().count();
    if (1..=200).contains(&n) && !s.contains('\0') && s.trim() == s {
        Ok(())
    } else {
        Err(ValidationError::SealedBy)
    }
}

fn digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// Periode `JJJJ-MM`, Jahr 2000–2100.
pub fn period(s: &str) -> Result<(i32, Month), ValidationError> {
    let (y, m) = s.split_once('-').ok_or(ValidationError::Period)?;
    if y.len() != 4 || m.len() != 2 || !digits(y) || !digits(m) {
        return Err(ValidationError::Period);
    }
    let year: i32 = y.parse().map_err(|_| ValidationError::Period)?;
    let month: u8 = m.parse().map_err(|_| ValidationError::Period)?;
    if !(2000..=2100).contains(&year) {
        return Err(ValidationError::Period);
    }
    let month = Month::try_from(month).map_err(|_| ValidationError::Period)?;
    Ok((year, month))
}

/// Buchungs-/Sperrperiode: `JJJJ-MM` oder die Abschlussperiode `JJJJ-13` (Abschlussbuchungen zum 31.12.).
pub fn booking_period(s: &str) -> Result<(), ValidationError> {
    if let Some(year) = s.strip_suffix("-13") {
        if year.len() == 4
            && digits(year)
            && (2000..=2100).contains(&year.parse::<i32>().unwrap_or(0))
        {
            return Ok(());
        }
        return Err(ValidationError::Period);
    }
    period(s).map(|_| ())
}

/// Datum `JJJJ-MM-TT` (streng, keine Zeitangaben).
pub fn date(s: &str) -> Result<Date, ValidationError> {
    let mut it = s.split('-');
    let (y, m, d) = (it.next(), it.next(), it.next());
    if it.next().is_some() {
        return Err(ValidationError::Date);
    }
    let (Some(y), Some(m), Some(d)) = (y, m, d) else {
        return Err(ValidationError::Date);
    };
    if y.len() != 4 || m.len() != 2 || d.len() != 2 || !digits(y) || !digits(m) || !digits(d) {
        return Err(ValidationError::Date);
    }
    let month = Month::try_from(m.parse::<u8>().map_err(|_| ValidationError::Date)?)
        .map_err(|_| ValidationError::Date)?;
    Date::from_calendar_date(
        y.parse().map_err(|_| ValidationError::Date)?,
        month,
        d.parse().map_err(|_| ValidationError::Date)?,
    )
    .map_err(|_| ValidationError::Date)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_types() {
        for ok in ["invoice.issued", "period.lock", "vat_return.submitted", "a"] {
            assert!(record_type(ok).is_ok(), "{ok}");
        }
        for bad in [
            "",
            "Invoice",
            "invoice.",
            ".x",
            "invoice..x",
            "in-voice",
            "1x",
            &"a".repeat(65),
        ] {
            assert!(record_type(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn periods() {
        assert_eq!(period("2026-11").unwrap(), (2026, Month::November));
        for bad in [
            "2026-13",
            "2026-00",
            "2026-1",
            "26-11",
            "2026/11",
            "1999-12",
            "2026-11-01",
            "２０２６-11",
        ] {
            assert!(period(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn booking_periods() {
        assert!(booking_period("2026-12").is_ok());
        assert!(booking_period("2026-13").is_ok(), "Abschlussperiode");
        for bad in [
            "2026-14",
            "2026-00",
            "1999-13",
            "26-13",
            "2026-13-01",
            "abcd-13",
        ] {
            assert!(booking_period(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn dates() {
        assert_eq!(date("2026-02-28").unwrap().to_string(), "2026-02-28");
        for bad in ["2026-02-30", "2026-2-1", "2026-02-01T00:00", "2026-02", "x"] {
            assert!(date(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn sealed_by_values() {
        assert!(sealed_by("user:3f2c").is_ok());
        assert!(sealed_by("").is_err());
        assert!(sealed_by(" user").is_err());
        assert!(sealed_by(&"x".repeat(201)).is_err());
    }
}
