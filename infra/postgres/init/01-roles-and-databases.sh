#!/bin/sh
# Legt beim ERSTEN Start der Datenbank Rollen und Datenbanken an.
# Läuft nur, wenn das Datenverzeichnis leer ist (offizielles postgres-Image).
#
# Rollen-Prinzip (Least Privilege):
#   openemployed_migrator  Eigentümer der Schemata, führt Migrationen aus
#   openemployed_app       Backend: CRUD auf public, nur LESEN auf ledger
#   openemployed_ledger    Rust-Festschreibung: nur SELECT/INSERT auf ledger (Rechte vergibt die Ledger-Migration)
#   documenso            eigene Datenbank für Documenso
set -eu

: "${OPENEMPLOYED_MIGRATOR_PASSWORD:?}"
: "${OPENEMPLOYED_APP_PASSWORD:?}"
: "${OPENEMPLOYED_LEDGER_PASSWORD:?}"
: "${DOCUMENSO_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v migrator_pw="$OPENEMPLOYED_MIGRATOR_PASSWORD" \
  -v app_pw="$OPENEMPLOYED_APP_PASSWORD" \
  -v ledger_pw="$OPENEMPLOYED_LEDGER_PASSWORD" \
  -v documenso_pw="$DOCUMENSO_DB_PASSWORD" <<'SQL'
CREATE ROLE openemployed_migrator LOGIN PASSWORD :'migrator_pw';
CREATE ROLE openemployed_app      LOGIN PASSWORD :'app_pw';
CREATE ROLE openemployed_ledger   LOGIN PASSWORD :'ledger_pw';
CREATE ROLE documenso           LOGIN PASSWORD :'documenso_pw';

CREATE DATABASE openemployed OWNER openemployed_migrator ENCODING 'UTF8' TEMPLATE template0;
CREATE DATABASE documenso  OWNER documenso           ENCODING 'UTF8' TEMPLATE template0;

REVOKE ALL ON DATABASE openemployed FROM PUBLIC;
GRANT CONNECT ON DATABASE openemployed TO openemployed_app, openemployed_ledger, openemployed_migrator;
REVOKE ALL ON DATABASE documenso FROM PUBLIC;
GRANT CONNECT ON DATABASE documenso TO documenso;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname openemployed <<'SQL'
-- Schema public: gehört dem Migrator, niemand sonst darf Objekte anlegen
ALTER SCHEMA public OWNER TO openemployed_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO openemployed_app;

-- Schema ledger: festgeschriebene Daten
CREATE SCHEMA ledger AUTHORIZATION openemployed_migrator;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC;
GRANT USAGE ON SCHEMA ledger TO openemployed_ledger, openemployed_app;

-- Neue Tabellen in public automatisch für das Backend freigeben
ALTER DEFAULT PRIVILEGES FOR ROLE openemployed_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO openemployed_app;
ALTER DEFAULT PRIVILEGES FOR ROLE openemployed_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO openemployed_app;

-- Sichere Standardwerte
ALTER ROLE openemployed_app    SET statement_timeout = '30s';
ALTER ROLE openemployed_ledger SET statement_timeout = '30s';
ALTER DATABASE openemployed SET timezone TO 'UTC';
SQL

echo "openemployed: Rollen und Datenbanken angelegt."
