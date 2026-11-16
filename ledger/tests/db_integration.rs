//! Integrationstests gegen ein echtes PostgreSQL.
//!
//! Aktivieren mit einer Superuser-Verbindung, z. B.:
//!   LEDGER_TEST_ADMIN_URL=postgres://postgres:pw@localhost:55432/postgres cargo test --test db_integration
//! Ohne die Variable werden die Tests übersprungen (mit Hinweis).
//!
//! Jeder Testlauf legt eine frische Datenbank an, wendet die Migration an und löscht sie danach.

use std::str::FromStr;

use ledger::db::{self, NumberRange, SealInput};
use ledger::error::AppError;
use serde_json::json;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{Connection, Executor, PgConnection, PgPool};
use uuid::Uuid;

const LEDGER_PW: &str = "ledger_test_pw";
const APP_PW: &str = "app_test_pw";
const MIGRATOR_PW: &str = "migrator_test_pw";

struct TestDb {
    admin_url: String,
    name: String,
    ledger: PgPool,
    app: PgPool,
    admin: PgPool,
}

async fn setup() -> Option<TestDb> {
    let Ok(admin_url) = std::env::var("LEDGER_TEST_ADMIN_URL") else {
        eprintln!("LEDGER_TEST_ADMIN_URL nicht gesetzt – Datenbank-Integrationstests übersprungen");
        return None;
    };
    let name = format!("ledger_test_{}", Uuid::new_v4().simple());
    let mut admin = PgConnection::connect(&admin_url)
        .await
        .expect("Admin-Verbindung");
    for (role, pw) in [
        ("selbstlite_ledger", LEDGER_PW),
        ("selbstlite_app", APP_PW),
        ("selbstlite_migrator", MIGRATOR_PW),
    ] {
        admin
            .execute(
                format!(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN \
                     CREATE ROLE {role} LOGIN PASSWORD '{pw}'; ELSE ALTER ROLE {role} LOGIN PASSWORD '{pw}'; END IF; END $$;"
                )
                .as_str(),
            )
            .await
            .unwrap();
    }
    admin
        .execute(format!("CREATE DATABASE {name} OWNER selbstlite_migrator").as_str())
        .await
        .unwrap();
    admin.close().await.unwrap();

    let base = PgConnectOptions::from_str(&admin_url)
        .unwrap()
        .database(&name);
    let admin_pool = PgPoolOptions::new()
        .max_connections(2)
        .connect_with(base.clone())
        .await
        .unwrap();
    admin_pool
        .execute(
            "CREATE SCHEMA ledger AUTHORIZATION selbstlite_migrator; \
             GRANT USAGE ON SCHEMA ledger TO selbstlite_ledger, selbstlite_app;",
        )
        .await
        .unwrap();

    let migrator_opts = base
        .clone()
        .username("selbstlite_migrator")
        .password(MIGRATOR_PW)
        .options([("search_path", "ledger")]);
    let migrator = PgPoolOptions::new()
        .max_connections(1)
        .connect_with(migrator_opts)
        .await
        .unwrap();
    ledger::MIGRATOR.run(&migrator).await.expect("Migration");
    migrator.close().await;

    let ledger = PgPoolOptions::new()
        .max_connections(20)
        .connect_with(
            base.clone()
                .username("selbstlite_ledger")
                .password(LEDGER_PW),
        )
        .await
        .unwrap();
    let app = PgPoolOptions::new()
        .max_connections(2)
        .connect_with(base.username("selbstlite_app").password(APP_PW))
        .await
        .unwrap();
    Some(TestDb {
        admin_url,
        name,
        ledger,
        app,
        admin: admin_pool,
    })
}

impl TestDb {
    async fn teardown(self) {
        self.ledger.close().await;
        self.app.close().await;
        self.admin.close().await;
        let mut admin = PgConnection::connect(&self.admin_url).await.unwrap();
        admin
            .execute(format!("DROP DATABASE {} WITH (FORCE)", self.name).as_str())
            .await
            .unwrap();
    }
}

fn tenant() -> Uuid {
    Uuid::from_u128(42)
}

fn invoice(total: &str) -> SealInput {
    SealInput {
        tenant_id: tenant(),
        record_type: "invoice.issued".into(),
        subject_id: Some(Uuid::new_v4()),
        sealed_by: "user:test".into(),
        payload: json!({"total_gross": total}),
        number_range: Some(NumberRange {
            key: "invoice".into(),
            pattern: "RE-{YYYY}-{SEQ:5}".into(),
        }),
        number_year: Some(2026),
        posting_period: None,
    }
}

#[tokio::test]
async fn full_ledger_behaviour() {
    let Some(t) = setup().await else { return };

    // 1. Fortlaufende Nummern und gültige Kette
    let r1 = db::seal(&t.ledger, invoice("2092.02")).await.unwrap();
    let r2 = db::seal(&t.ledger, invoice("119.00")).await.unwrap();
    assert_eq!(r1.document_number.as_deref(), Some("RE-2026-00001"));
    assert_eq!(r2.document_number.as_deref(), Some("RE-2026-00002"));
    assert_eq!(r2.prev_hash, r1.hash);
    assert!(db::verify(&t.ledger).await.unwrap().ok);

    // 1b. Abfrage je Vorgang (für idempotente Freigabe im Backend)
    let by_subject = db::records_by_subject(&t.ledger, tenant(), r1.subject_id.unwrap())
        .await
        .unwrap();
    assert_eq!(by_subject.len(), 1);
    assert_eq!(by_subject[0].hash, r1.hash);
    assert!(
        db::records_by_subject(&t.ledger, Uuid::new_v4(), r1.subject_id.unwrap())
            .await
            .unwrap()
            .is_empty(),
        "fremder Mandant sieht nichts"
    );

    // 2. Neues Jahr → Zähler beginnt neu
    let mut next_year = invoice("1.19");
    next_year.number_year = Some(2027);
    assert_eq!(
        db::seal(&t.ledger, next_year)
            .await
            .unwrap()
            .document_number
            .as_deref(),
        Some("RE-2027-00001")
    );

    // 3. Muster eines Nummernkreises ist unveränderlich
    let mut other_pattern = invoice("1.00");
    other_pattern.number_range = Some(NumberRange {
        key: "invoice".into(),
        pattern: "R-{YYYY}-{SEQ:4}".into(),
    });
    assert!(matches!(
        db::seal(&t.ledger, other_pattern).await,
        Err(AppError::Conflict(_))
    ));

    // 4. Fehlgeschlagene Festschreibung verbraucht keine Nummer (Rollback)
    let r4 = db::seal(&t.ledger, invoice("5.00")).await.unwrap();
    assert_eq!(
        r4.document_number.as_deref(),
        Some("RE-2026-00003"),
        "keine Lücke nach Konflikt"
    );

    // 5. UPDATE/DELETE/TRUNCATE sind auch für den Ledger-Dienst verboten
    for sql in [
        "UPDATE ledger.chain_records SET sealed_by = 'x' WHERE seq = 1",
        "DELETE FROM ledger.chain_records WHERE seq = 1",
        "TRUNCATE ledger.chain_records CASCADE",
        "UPDATE ledger.number_counters SET last_value = 1",
        "DELETE FROM ledger.number_counters",
        "UPDATE ledger.number_ranges SET pattern = 'X-{SEQ:2}'",
    ] {
        let err = t.ledger.execute(sql).await.expect_err(sql);
        let code = err
            .as_database_error()
            .and_then(|d| d.code())
            .map(|c| c.into_owned());
        assert_eq!(code.as_deref(), Some("42501"), "{sql}: {err}");
    }

    // 6. Backend-Rolle darf lesen, aber nicht schreiben
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM ledger.chain_records")
        .fetch_one(&t.app)
        .await
        .unwrap();
    assert_eq!(count, 4);
    let err = t
        .app
        .execute("INSERT INTO ledger.locked_periods (tenant_id, period, chain_seq) VALUES (gen_random_uuid(), '2026-01', 1)")
        .await
        .unwrap_err();
    assert_eq!(
        err.as_database_error().and_then(|d| d.code()).as_deref(),
        Some("42501")
    );

    // 7. Periodensperre: einmalig, danach Konflikt
    db::lock_period(&t.ledger, tenant(), "2026-10", "user:test")
        .await
        .unwrap();
    assert!(db::period_lock(&t.ledger, tenant(), "2026-10")
        .await
        .unwrap()
        .is_some());
    assert!(matches!(
        db::lock_period(&t.ledger, tenant(), "2026-10", "user:test").await,
        Err(AppError::Conflict(_))
    ));
    assert_eq!(
        db::locked_periods(&t.ledger, tenant()).await.unwrap(),
        vec!["2026-10".to_string()]
    );
    // 7b. Buchungen in eine festgeschriebene Periode lehnt der Ledger selbst ab (GoBD)
    let mut late = invoice("1.00");
    late.number_range = None;
    late.number_year = None;
    late.record_type = "journal.posted".into();
    late.posting_period = Some("2026-10".into());
    assert!(matches!(
        db::seal(&t.ledger, late.clone()).await,
        Err(AppError::Conflict(_))
    ));
    late.posting_period = Some("2026-11".into());
    db::seal(&t.ledger, late).await.unwrap();
    assert!(db::period_lock(&t.ledger, tenant(), "2026-11")
        .await
        .unwrap()
        .is_none());

    // 7c. Abschlussperiode „2026-13“: Abschlussbuchungen zum 31.12. bis zur Festschreibung des Jahresabschlusses
    let mut closing = invoice("2.00");
    closing.number_range = None;
    closing.number_year = None;
    closing.record_type = "journal.posted".into();
    closing.posting_period = Some("2026-13".into());
    db::seal(&t.ledger, closing.clone()).await.unwrap();
    db::lock_period(&t.ledger, tenant(), "2026-13", "user:test")
        .await
        .unwrap();
    assert!(matches!(
        db::seal(&t.ledger, closing).await,
        Err(AppError::Conflict(_))
    ));

    // 8. Parallele Festschreibungen: lückenlos, eindeutig, Kette gültig
    let handles: Vec<_> = (0..25)
        .map(|i| {
            let pool = t.ledger.clone();
            tokio::spawn(async move { db::seal(&pool, invoice(&format!("{i}.00"))).await.unwrap() })
        })
        .collect();
    let mut numbers = Vec::new();
    for h in handles {
        numbers.push(h.await.unwrap().document_number.unwrap());
    }
    numbers.sort();
    let expected: Vec<String> = (4..=28).map(|n| format!("RE-2026-{n:05}")).collect();
    assert_eq!(numbers, expected);
    let report = db::verify(&t.ledger).await.unwrap();
    assert!(report.ok, "{report:?}");
    assert_eq!(report.records_checked, 33);

    // 9. Manipulation als Superuser (Trigger abgeschaltet) wird von verify erkannt
    t.admin
        .execute(
            "ALTER TABLE ledger.chain_records DISABLE TRIGGER chain_records_immutable; \
             UPDATE ledger.chain_records SET payload = '{\"total_gross\": \"0.01\"}', \
                    canonical_payload = '{\"total_gross\":\"0.01\"}' WHERE seq = 2; \
             ALTER TABLE ledger.chain_records ENABLE TRIGGER chain_records_immutable;",
        )
        .await
        .unwrap();
    let report = db::verify(&t.ledger).await.unwrap();
    assert!(!report.ok);
    assert_eq!(report.first_break.unwrap().seq, 2);

    // 10. Doppelte Belegnummer ist auf DB-Ebene ausgeschlossen
    let dup = t
        .admin
        .execute(
            "INSERT INTO ledger.chain_records (seq, id, tenant_id, record_type, number_range_key, document_number, payload, \
             canonical_payload, prev_hash, hash, sealed_at, sealed_by) \
             SELECT 999, gen_random_uuid(), tenant_id, record_type, number_range_key, document_number, payload, \
             canonical_payload, prev_hash, repeat('a', 64), now(), sealed_by FROM ledger.chain_records WHERE seq = 1",
        )
        .await
        .unwrap_err();
    assert_eq!(
        dup.as_database_error().and_then(|d| d.code()).as_deref(),
        Some("23505")
    );

    t.teardown().await;
}
