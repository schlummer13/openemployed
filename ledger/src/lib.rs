//! openemployed Ledger – Festschreibung (GoBD), Nummernvergabe und Hash-Kette.
#![forbid(unsafe_code)]

pub mod api;
pub mod canonical;
pub mod chain;
pub mod db;
pub mod error;
pub mod numbering;
pub mod validate;

/// Eingebettete SQL-Migrationen (ledger/migrations).
pub static MIGRATOR: sqlx::migrate::Migrator = sqlx::migrate!("./migrations");
