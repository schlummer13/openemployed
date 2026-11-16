#![forbid(unsafe_code)]

use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use tracing_subscriber::EnvFilter;

use ledger::api::{router, AppState};

const MIN_TOKEN_LEN: usize = 32;

fn env(name: &str) -> Result<String, String> {
    std::env::var(name).map_err(|_| format!("Umgebungsvariable {name} fehlt"))
}

#[tokio::main]
async fn main() {
    // `ledger healthcheck` für den Docker-Healthcheck (das Laufzeit-Image hat kein curl)
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        std::process::exit(healthcheck().await);
    }

    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .json()
        .init();

    if let Err(e) = run().await {
        tracing::error!(error = %e, "Ledger konnte nicht starten");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), String> {
    let database_url = env("LEDGER_DATABASE_URL")?;
    let token = env("LEDGER_INTERNAL_TOKEN")?;
    if token.len() < MIN_TOKEN_LEN {
        return Err(format!(
            "LEDGER_INTERNAL_TOKEN muss mindestens {MIN_TOKEN_LEN} Zeichen haben"
        ));
    }
    let bind = std::env::var("LEDGER_BIND").unwrap_or_else(|_| "0.0.0.0:8080".into());

    if let Ok(migrator_url) = std::env::var("LEDGER_MIGRATOR_DATABASE_URL") {
        let opts = PgConnectOptions::from_str(&migrator_url)
            .map_err(|e| format!("LEDGER_MIGRATOR_DATABASE_URL ungültig: {e}"))?
            .options([("search_path", "ledger")]);
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .acquire_timeout(Duration::from_secs(30))
            .connect_with(opts)
            .await
            .map_err(|e| format!("Migrator-Verbindung fehlgeschlagen: {e}"))?;
        ledger::MIGRATOR
            .run(&pool)
            .await
            .map_err(|e| format!("Migration fehlgeschlagen: {e}"))?;
        pool.close().await;
        tracing::info!("Ledger-Migrationen angewendet");
    }

    let pool = PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(10))
        .connect(&database_url)
        .await
        .map_err(|e| format!("Datenbankverbindung fehlgeschlagen: {e}"))?;

    let app = router(AppState {
        pool,
        token: Arc::from(token),
    });
    let listener = tokio::net::TcpListener::bind(&bind)
        .await
        .map_err(|e| format!("Bind {bind}: {e}"))?;
    tracing::info!(%bind, "Ledger läuft");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .map_err(|e| e.to_string())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            s.recv().await;
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! { () = ctrl_c => {}, () = terminate => {} }
}

/// Minimaler HTTP-Check gegen /health ohne zusätzliche Abhängigkeiten.
async fn healthcheck() -> i32 {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let bind = std::env::var("LEDGER_BIND").unwrap_or_else(|_| "0.0.0.0:8080".into());
    let port = bind.rsplit(':').next().unwrap_or("8080");
    let Ok(Ok(mut stream)) = tokio::time::timeout(
        Duration::from_secs(2),
        tokio::net::TcpStream::connect(format!("127.0.0.1:{port}")),
    )
    .await
    else {
        return 1;
    };
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await
        .is_err()
    {
        return 1;
    }
    let mut buf = Vec::new();
    let _ = tokio::time::timeout(Duration::from_secs(2), stream.read_to_end(&mut buf)).await;
    i32::from(!buf.starts_with(b"HTTP/1.1 200"))
}
