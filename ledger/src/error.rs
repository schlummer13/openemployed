use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::canonical::CanonicalError;
use crate::numbering::NumberingError;
use crate::validate::ValidationError;

#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error(transparent)]
    Validation(#[from] ValidationError),
    #[error(transparent)]
    Canonical(#[from] CanonicalError),
    #[error(transparent)]
    Numbering(#[from] NumberingError),
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    Conflict(String),
    #[error("nicht gefunden")]
    NotFound,
    #[error("nicht autorisiert")]
    Unauthorized,
    #[error("Datenbankfehler")]
    Database(#[from] sqlx::Error),
}

impl AppError {
    fn status_and_code(&self) -> (StatusCode, &'static str) {
        match self {
            Self::Validation(_) | Self::Canonical(_) | Self::Numbering(_) | Self::BadRequest(_) => {
                (StatusCode::UNPROCESSABLE_ENTITY, "invalid_input")
            }
            Self::Conflict(_) => (StatusCode::CONFLICT, "conflict"),
            Self::NotFound => (StatusCode::NOT_FOUND, "not_found"),
            Self::Unauthorized => (StatusCode::UNAUTHORIZED, "unauthorized"),
            Self::Database(e) if is_unique_violation(e) => (StatusCode::CONFLICT, "conflict"),
            Self::Database(_) => (StatusCode::INTERNAL_SERVER_ERROR, "internal_error"),
        }
    }
}

pub fn is_unique_violation(e: &sqlx::Error) -> bool {
    matches!(e, sqlx::Error::Database(db) if db.code().as_deref() == Some("23505"))
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, code) = self.status_and_code();
        let message = match &self {
            // Interne Details nie nach außen geben – nur ins Log
            Self::Database(e) if status == StatusCode::INTERNAL_SERVER_ERROR => {
                tracing::error!(error = %e, "Datenbankfehler");
                "Interner Fehler".to_owned()
            }
            Self::Database(_) => {
                "Eindeutigkeitsverletzung (Datensatz existiert bereits)".to_owned()
            }
            other => other.to_string(),
        };
        (
            status,
            Json(json!({"error": {"code": code, "message": message}})),
        )
            .into_response()
    }
}
