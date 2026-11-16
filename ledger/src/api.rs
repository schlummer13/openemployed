//! HTTP-Schnittstelle (nur im internen Netz, geschützt mit `X-Internal-Token`).

use axum::extract::{DefaultBodyLimit, Path, Request, State};
use axum::http::StatusCode;
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgPool;
use std::sync::Arc;
use subtle::ConstantTimeEq;
use uuid::Uuid;

use crate::chain::{format_timestamp, StoredRecord, VerifyReport};
use crate::db::{self, NumberRange, SealInput};
use crate::error::AppError;
use crate::validate;

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub token: Arc<str>,
}

pub fn router(state: AppState) -> Router {
    let protected = Router::new()
        .route("/v1/seal", post(seal))
        .route("/v1/records/{id}", get(get_record))
        .route(
            "/v1/tenants/{tenant_id}/subjects/{subject_id}/records",
            get(records_by_subject),
        )
        .route("/v1/verify", get(verify))
        .route("/v1/periods/lock", post(lock_period))
        .route(
            "/v1/tenants/{tenant_id}/locked-periods",
            get(locked_periods),
        )
        .route("/v1/periods/{tenant_id}/{period}", get(period_status))
        .route_layer(middleware::from_fn_with_state(state.clone(), require_token));

    Router::new()
        .route("/health", get(health))
        .merge(protected)
        .layer(DefaultBodyLimit::max(1024 * 1024))
        .with_state(state)
}

async fn require_token(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Result<Response, AppError> {
    let provided = req
        .headers()
        .get("x-internal-token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let expected = state.token.as_bytes();
    // Längenvergleich ist unkritisch (Token-Länge ist kein Geheimnis), Inhalt in konstanter Zeit
    if provided.len() == expected.len() && bool::from(provided.as_bytes().ct_eq(expected)) {
        Ok(next.run(req).await)
    } else {
        Err(AppError::Unauthorized)
    }
}

async fn health(State(state): State<AppState>) -> (StatusCode, Json<Value>) {
    match db::ping(&state.pool).await {
        Ok(()) => (StatusCode::OK, Json(serde_json::json!({"status": "ok"}))),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({"status": "database_unavailable"})),
        ),
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NumberRangeIn {
    pub key: String,
    pub pattern: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SealRequest {
    pub tenant_id: Uuid,
    pub record_type: String,
    #[serde(default)]
    pub subject_id: Option<Uuid>,
    pub sealed_by: String,
    pub payload: Value,
    #[serde(default)]
    pub number_range: Option<NumberRangeIn>,
    /// Ausstellungsdatum JJJJ-MM-TT (Jahr für Nummernkreise mit {YYYY})
    #[serde(default)]
    pub issue_date: Option<String>,
    /// Buchungsperiode JJJJ-MM: ist sie festgeschrieben (gesperrt), wird der Datensatz abgelehnt (GoBD)
    #[serde(default)]
    pub posting_period: Option<String>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct RecordOut {
    pub id: Uuid,
    pub seq: i64,
    pub tenant_id: Uuid,
    pub record_type: String,
    pub subject_id: Option<Uuid>,
    pub number_range_key: Option<String>,
    pub document_number: Option<String>,
    pub payload: Value,
    pub prev_hash: String,
    pub hash: String,
    pub sealed_at: String,
    pub sealed_by: String,
}

impl From<StoredRecord> for RecordOut {
    fn from(r: StoredRecord) -> Self {
        Self {
            id: r.id,
            seq: r.seq,
            tenant_id: r.tenant_id,
            record_type: r.record_type,
            subject_id: r.subject_id,
            number_range_key: r.number_range_key,
            document_number: r.document_number,
            payload: r.payload,
            prev_hash: r.prev_hash,
            hash: r.hash,
            sealed_at: format_timestamp(r.sealed_at),
            sealed_by: r.sealed_by,
        }
    }
}

/// Prüft alle Eingaben, bevor die Datenbank berührt wird.
pub fn validate_seal_request(req: SealRequest, today: time::Date) -> Result<SealInput, AppError> {
    validate::record_type(&req.record_type)?;
    validate::sealed_by(&req.sealed_by)?;
    if !req.payload.is_object() {
        return Err(validate::ValidationError::PayloadNotObject.into());
    }
    // Kanonisierbarkeit früh prüfen (z. B. Fließkommazahlen)
    crate::canonical::canonicalize(&req.payload)?;

    let number_year = match &req.issue_date {
        Some(s) => {
            let d = validate::date(s)?;
            // Ausstellungsdatum darf nur um ±1 Tag von heute (UTC) abweichen (Zeitzonen Berlin/UTC)
            let diff = (d - today).whole_days().abs();
            if diff > 1 {
                return Err(AppError::BadRequest(
                    "issue_date muss das heutige Datum sein".into(),
                ));
            }
            Some(d.year())
        }
        None => None,
    };
    if let Some(p) = &req.posting_period {
        validate::booking_period(p)?;
    }
    if req.issue_date.is_some() && req.number_range.is_none() {
        return Err(AppError::BadRequest(
            "issue_date nur zusammen mit number_range".into(),
        ));
    }

    Ok(SealInput {
        tenant_id: req.tenant_id,
        record_type: req.record_type,
        subject_id: req.subject_id,
        sealed_by: req.sealed_by,
        payload: req.payload,
        number_range: req.number_range.map(|r| NumberRange {
            key: r.key,
            pattern: r.pattern,
        }),
        number_year,
        posting_period: req.posting_period,
    })
}

async fn seal(
    State(state): State<AppState>,
    Json(req): Json<SealRequest>,
) -> Result<(StatusCode, Json<RecordOut>), AppError> {
    let input = validate_seal_request(req, time::OffsetDateTime::now_utc().date())?;
    let record = db::seal(&state.pool, input).await?;
    tracing::info!(seq = record.seq, record_type = %record.record_type, number = ?record.document_number, "festgeschrieben");
    Ok((StatusCode::CREATED, Json(record.into())))
}

async fn get_record(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Result<Json<RecordOut>, AppError> {
    db::get_record(&state.pool, id)
        .await?
        .map(|r| Json(r.into()))
        .ok_or(AppError::NotFound)
}

async fn records_by_subject(
    State(state): State<AppState>,
    Path((tenant_id, subject_id)): Path<(Uuid, Uuid)>,
) -> Result<Json<Vec<RecordOut>>, AppError> {
    let records = db::records_by_subject(&state.pool, tenant_id, subject_id).await?;
    Ok(Json(records.into_iter().map(Into::into).collect()))
}

async fn locked_periods(
    State(state): State<AppState>,
    Path(tenant_id): Path<Uuid>,
) -> Result<Json<Vec<String>>, AppError> {
    Ok(Json(db::locked_periods(&state.pool, tenant_id).await?))
}

async fn verify(State(state): State<AppState>) -> Result<Json<VerifyReport>, AppError> {
    let report = db::verify(&state.pool).await?;
    if !report.ok {
        tracing::error!(?report, "HASH-KETTE GEBROCHEN");
    }
    Ok(Json(report))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LockPeriodRequest {
    pub tenant_id: Uuid,
    pub period: String,
    pub sealed_by: String,
}

async fn lock_period(
    State(state): State<AppState>,
    Json(req): Json<LockPeriodRequest>,
) -> Result<(StatusCode, Json<RecordOut>), AppError> {
    validate::booking_period(&req.period)?;
    validate::sealed_by(&req.sealed_by)?;
    let record = db::lock_period(&state.pool, req.tenant_id, &req.period, &req.sealed_by).await?;
    tracing::info!(period = %req.period, seq = record.seq, "Periode gesperrt");
    Ok((StatusCode::CREATED, Json(record.into())))
}

#[derive(Debug, Serialize)]
struct PeriodStatus {
    period: String,
    locked: bool,
    chain_seq: Option<i64>,
}

async fn period_status(
    State(state): State<AppState>,
    Path((tenant_id, period)): Path<(Uuid, String)>,
) -> Result<Json<PeriodStatus>, AppError> {
    validate::period(&period)?;
    let seq = db::period_lock(&state.pool, tenant_id, &period).await?;
    Ok(Json(PeriodStatus {
        period,
        locked: seq.is_some(),
        chain_seq: seq,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use time::macros::date;

    fn req(v: Value) -> SealRequest {
        serde_json::from_value(v).unwrap()
    }

    fn base() -> Value {
        json!({
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "record_type": "invoice.issued",
            "sealed_by": "user:1",
            "payload": {"total": "10.00"},
            "number_range": {"key": "invoice", "pattern": "RE-{YYYY}-{SEQ:5}"},
            "issue_date": "2026-11-14"
        })
    }

    #[test]
    fn accepts_valid_request() {
        let input = validate_seal_request(req(base()), date!(2026 - 11 - 14)).unwrap();
        assert_eq!(input.number_year, Some(2026));
    }

    #[test]
    fn accepts_issue_date_one_day_off_for_timezones() {
        let mut v = base();
        v["issue_date"] = json!("2027-01-01");
        let input = validate_seal_request(req(v), date!(2026 - 12 - 31)).unwrap();
        assert_eq!(input.number_year, Some(2027));
    }

    #[test]
    fn rejects_backdated_issue_date() {
        let mut v = base();
        v["issue_date"] = json!("2026-10-01");
        assert!(validate_seal_request(req(v), date!(2026 - 11 - 14)).is_err());
    }

    #[test]
    fn rejects_floats_and_non_objects() {
        let mut v = base();
        v["payload"] = json!({"total": 10.5});
        assert!(matches!(
            validate_seal_request(req(v), date!(2026 - 11 - 14)),
            Err(AppError::Canonical(_))
        ));
        let mut v = base();
        v["payload"] = json!([1, 2]);
        assert!(validate_seal_request(req(v), date!(2026 - 11 - 14)).is_err());
    }

    #[test]
    fn rejects_unknown_fields() {
        let mut v = base();
        v["hash"] = json!("manipuliert");
        assert!(serde_json::from_value::<SealRequest>(v).is_err());
    }

    #[test]
    fn rejects_issue_date_without_number_range() {
        let mut v = base();
        v.as_object_mut().unwrap().remove("number_range");
        assert!(validate_seal_request(req(v), date!(2026 - 11 - 14)).is_err());
    }
}
