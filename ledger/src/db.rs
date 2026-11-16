//! Datenbankzugriff. Jede Festschreibung läuft in genau einer Transaktion:
//! Sperre → Kopf der Kette lesen → ggf. Nummer vergeben → Datensatz einfügen → Commit.
//! Schlägt irgendetwas fehl, wird alles zurückgerollt (auch der Nummernzähler) → keine Lücken.

use serde_json::Value;
use sqlx::{PgPool, Postgres, Row, Transaction};
use time::OffsetDateTime;
use uuid::Uuid;

use crate::chain::{build_record, ChainVerifier, StoredRecord, VerifyReport, GENESIS_HASH};
use crate::error::AppError;
use crate::numbering::{validate_range_key, NumberPattern};

/// Fester Schlüssel für die transaktionale Advisory-Sperre der Kette ("SLLEDGER").
const CHAIN_LOCK_KEY: i64 = 0x534C_4C45_4447_4552;

#[derive(Debug, Clone)]
pub struct NumberRange {
    pub key: String,
    pub pattern: String,
}

#[derive(Debug, Clone)]
pub struct SealInput {
    pub tenant_id: Uuid,
    pub record_type: String,
    pub subject_id: Option<Uuid>,
    pub sealed_by: String,
    pub payload: Value,
    pub number_range: Option<NumberRange>,
    /// Jahr für Nummernkreise mit `{YYYY}` (aus dem Ausstellungsdatum)
    pub number_year: Option<i32>,
    /// Buchungsperiode JJJJ-MM; gesperrte (festgeschriebene) Perioden werden abgelehnt
    pub posting_period: Option<String>,
}

const SELECT_COLUMNS: &str =
    "seq, id, tenant_id, record_type, subject_id, number_range_key, document_number, \
     payload, canonical_payload, prev_hash, hash, sealed_at, sealed_by";

fn row_to_record(row: &sqlx::postgres::PgRow) -> Result<StoredRecord, sqlx::Error> {
    Ok(StoredRecord {
        seq: row.try_get("seq")?,
        id: row.try_get("id")?,
        tenant_id: row.try_get("tenant_id")?,
        record_type: row.try_get("record_type")?,
        subject_id: row.try_get("subject_id")?,
        number_range_key: row.try_get("number_range_key")?,
        document_number: row.try_get("document_number")?,
        payload: row.try_get::<sqlx::types::Json<Value>, _>("payload")?.0,
        canonical_payload: row.try_get("canonical_payload")?,
        prev_hash: row.try_get("prev_hash")?,
        hash: row.try_get("hash")?,
        sealed_at: row.try_get("sealed_at")?,
        sealed_by: row.try_get("sealed_by")?,
    })
}

async fn assign_number(
    tx: &mut Transaction<'_, Postgres>,
    tenant_id: Uuid,
    range: &NumberRange,
    number_year: Option<i32>,
) -> Result<String, AppError> {
    validate_range_key(&range.key)?;
    let pattern = NumberPattern::parse(&range.pattern)?;
    let year = match (pattern.uses_year(), number_year) {
        (true, Some(y)) => y,
        (true, None) => {
            return Err(AppError::BadRequest(
                "issue_date erforderlich für Nummernkreise mit {YYYY}".into(),
            ))
        }
        (false, _) => number_year.unwrap_or(2000),
    };

    sqlx::query("INSERT INTO ledger.number_ranges (tenant_id, range_key, pattern) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING")
        .bind(tenant_id)
        .bind(&range.key)
        .bind(&range.pattern)
        .execute(&mut **tx)
        .await?;
    let stored: String = sqlx::query_scalar(
        "SELECT pattern FROM ledger.number_ranges WHERE tenant_id = $1 AND range_key = $2",
    )
    .bind(tenant_id)
    .bind(&range.key)
    .fetch_one(&mut **tx)
    .await?;
    if stored != range.pattern {
        return Err(AppError::Conflict(format!(
            "Nummernkreis '{}' verwendet bereits das Muster '{stored}' – Muster können nicht geändert werden",
            range.key
        )));
    }

    let counter: i64 = sqlx::query_scalar(
        "INSERT INTO ledger.number_counters (tenant_id, range_key, year, last_value) VALUES ($1, $2, $3, 1) \
         ON CONFLICT (tenant_id, range_key, year) DO UPDATE SET last_value = ledger.number_counters.last_value + 1 \
         RETURNING last_value",
    )
    .bind(tenant_id)
    .bind(&range.key)
    .bind(pattern.counter_year(year))
    .fetch_one(&mut **tx)
    .await?;

    Ok(pattern.format(year, counter)?)
}

/// Schreibt innerhalb einer bestehenden Transaktion einen Datensatz fest (Sperre muss bereits gehalten werden).
async fn seal_in_tx(
    tx: &mut Transaction<'_, Postgres>,
    input: SealInput,
) -> Result<StoredRecord, AppError> {
    let head: Option<(i64, String)> =
        sqlx::query_as("SELECT seq, hash FROM ledger.chain_records ORDER BY seq DESC LIMIT 1")
            .fetch_optional(&mut **tx)
            .await?;
    let (seq, prev_hash) = match head {
        Some((s, h)) => (s + 1, h),
        None => (1, GENESIS_HASH.to_owned()),
    };

    let document_number = match &input.number_range {
        Some(range) => Some(assign_number(tx, input.tenant_id, range, input.number_year).await?),
        None => None,
    };

    let record = build_record(
        seq,
        &prev_hash,
        Uuid::new_v4(),
        input.tenant_id,
        &input.record_type,
        input.subject_id,
        input.number_range.as_ref().map(|r| r.key.as_str()),
        document_number.as_deref(),
        input.payload,
        OffsetDateTime::now_utc(),
        &input.sealed_by,
    )?;

    sqlx::query(
        "INSERT INTO ledger.chain_records (seq, id, tenant_id, record_type, subject_id, number_range_key, document_number, \
         payload, canonical_payload, prev_hash, hash, sealed_at, sealed_by) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)",
    )
    .bind(record.seq)
    .bind(record.id)
    .bind(record.tenant_id)
    .bind(&record.record_type)
    .bind(record.subject_id)
    .bind(&record.number_range_key)
    .bind(&record.document_number)
    .bind(sqlx::types::Json(&record.payload))
    .bind(&record.canonical_payload)
    .bind(&record.prev_hash)
    .bind(&record.hash)
    .bind(record.sealed_at)
    .bind(&record.sealed_by)
    .execute(&mut **tx)
    .await?;

    Ok(record)
}

async fn begin_locked(pool: &PgPool) -> Result<Transaction<'_, Postgres>, AppError> {
    let mut tx = pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock($1)")
        .bind(CHAIN_LOCK_KEY)
        .execute(&mut *tx)
        .await?;
    Ok(tx)
}

pub async fn seal(pool: &PgPool, input: SealInput) -> Result<StoredRecord, AppError> {
    let mut tx = begin_locked(pool).await?;
    // Prüfung unter derselben Sperre wie das Festschreiben → kein Wettlauf mit einer gleichzeitigen Periodensperre
    if let Some(period) = &input.posting_period {
        let locked: bool = sqlx::query_scalar(
            "SELECT EXISTS (SELECT 1 FROM ledger.locked_periods WHERE tenant_id = $1 AND period = $2)",
        )
        .bind(input.tenant_id)
        .bind(period)
        .fetch_one(&mut *tx)
        .await?;
        if locked {
            return Err(AppError::Conflict(format!(
                "Periode {period} ist festgeschrieben – Buchung in eine offene Periode erforderlich"
            )));
        }
    }
    let record = seal_in_tx(&mut tx, input).await?;
    tx.commit().await?;
    Ok(record)
}

/// Sperrt eine Periode (JJJJ-MM): festgeschriebener Datensatz `period.lock` + Eintrag in `locked_periods`.
pub async fn lock_period(
    pool: &PgPool,
    tenant_id: Uuid,
    period: &str,
    sealed_by: &str,
) -> Result<StoredRecord, AppError> {
    let mut tx = begin_locked(pool).await?;
    let exists: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM ledger.locked_periods WHERE tenant_id = $1 AND period = $2)",
    )
    .bind(tenant_id)
    .bind(period)
    .fetch_one(&mut *tx)
    .await?;
    if exists {
        return Err(AppError::Conflict(format!(
            "Periode {period} ist bereits gesperrt"
        )));
    }
    let record = seal_in_tx(
        &mut tx,
        SealInput {
            tenant_id,
            record_type: "period.lock".into(),
            subject_id: None,
            sealed_by: sealed_by.into(),
            payload: serde_json::json!({ "period": period }),
            number_range: None,
            number_year: None,
            posting_period: None,
        },
    )
    .await?;
    sqlx::query(
        "INSERT INTO ledger.locked_periods (tenant_id, period, chain_seq) VALUES ($1, $2, $3)",
    )
    .bind(tenant_id)
    .bind(period)
    .bind(record.seq)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(record)
}

pub async fn period_lock(
    pool: &PgPool,
    tenant_id: Uuid,
    period: &str,
) -> Result<Option<i64>, AppError> {
    Ok(sqlx::query_scalar(
        "SELECT chain_seq FROM ledger.locked_periods WHERE tenant_id = $1 AND period = $2",
    )
    .bind(tenant_id)
    .bind(period)
    .fetch_optional(pool)
    .await?)
}

/// Alle gesperrten Perioden eines Mandanten (aufsteigend).
pub async fn locked_periods(pool: &PgPool, tenant_id: Uuid) -> Result<Vec<String>, AppError> {
    Ok(sqlx::query_scalar(
        "SELECT period FROM ledger.locked_periods WHERE tenant_id = $1 ORDER BY period",
    )
    .bind(tenant_id)
    .fetch_all(pool)
    .await?)
}

pub async fn get_record(pool: &PgPool, id: Uuid) -> Result<Option<StoredRecord>, AppError> {
    let row = sqlx::query(&format!(
        "SELECT {SELECT_COLUMNS} FROM ledger.chain_records WHERE id = $1"
    ))
    .bind(id)
    .fetch_optional(pool)
    .await?;
    Ok(row.as_ref().map(row_to_record).transpose()?)
}

/// Alle festgeschriebenen Datensätze zu einem Vorgang (z. B. einer Rechnung), aufsteigend.
pub async fn records_by_subject(
    pool: &PgPool,
    tenant_id: Uuid,
    subject_id: Uuid,
) -> Result<Vec<StoredRecord>, AppError> {
    let rows = sqlx::query(&format!(
        "SELECT {SELECT_COLUMNS} FROM ledger.chain_records \
         WHERE tenant_id = $1 AND subject_id = $2 ORDER BY seq LIMIT 100"
    ))
    .bind(tenant_id)
    .bind(subject_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.iter().map(row_to_record).collect::<Result<_, _>>()?)
}

/// Prüft die gesamte Kette in Blöcken (speicherschonend).
pub async fn verify(pool: &PgPool) -> Result<VerifyReport, AppError> {
    const BATCH: i64 = 1_000;
    let mut verifier = ChainVerifier::default();
    let mut after: i64 = 0;
    loop {
        let rows = sqlx::query(&format!(
            "SELECT {SELECT_COLUMNS} FROM ledger.chain_records WHERE seq > $1 ORDER BY seq LIMIT $2"
        ))
        .bind(after)
        .bind(BATCH)
        .fetch_all(pool)
        .await?;
        if rows.is_empty() {
            break;
        }
        for row in &rows {
            let record = row_to_record(row)?;
            after = record.seq;
            if !verifier.push(&record) {
                return Ok(verifier.finish());
            }
        }
    }
    Ok(verifier.finish())
}

pub async fn ping(pool: &PgPool) -> Result<(), AppError> {
    sqlx::query("SELECT 1").execute(pool).await?;
    Ok(())
}
