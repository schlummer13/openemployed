#!/bin/sh
# Legt beim ERSTEN Start der Datenbank Rollen und Datenbanken an.
# Läuft nur, wenn das Datenverzeichnis leer ist (offizielles postgres-Image).
#
# Rollen-Prinzip (Least Privilege):
#   selbstlite_migrator  Eigentümer der Schemata, führt Migrationen aus
#   selbstlite_app       Backend: CRUD auf public, nur LESEN auf ledger
#   selbstlite_ledger    Rust-Festschreibung: nur SELECT/INSERT auf ledger (Rechte vergibt die Ledger-Migration)
#   documenso            eigene Datenbank für Documenso
set -eu

: "${SELBSTLITE_MIGRATOR_PASSWORD:?}"
: "${SELBSTLITE_APP_PASSWORD:?}"
: "${SELBSTLITE_LEDGER_PASSWORD:?}"
: "${DOCUMENSO_DB_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v migrator_pw="$SELBSTLITE_MIGRATOR_PASSWORD" \
  -v app_pw="$SELBSTLITE_APP_PASSWORD" \
  -v ledger_pw="$SELBSTLITE_LEDGER_PASSWORD" \
  -v documenso_pw="$DOCUMENSO_DB_PASSWORD" <<'SQL'
CREATE ROLE selbstlite_migrator LOGIN PASSWORD :'migrator_pw';
CREATE ROLE selbstlite_app      LOGIN PASSWORD :'app_pw';
CREATE ROLE selbstlite_ledger   LOGIN PASSWORD :'ledger_pw';
CREATE ROLE documenso           LOGIN PASSWORD :'documenso_pw';

CREATE DATABASE selbstlite OWNER selbstlite_migrator ENCODING 'UTF8' TEMPLATE template0;
CREATE DATABASE documenso  OWNER documenso           ENCODING 'UTF8' TEMPLATE template0;

REVOKE ALL ON DATABASE selbstlite FROM PUBLIC;
GRANT CONNECT ON DATABASE selbstlite TO selbstlite_app, selbstlite_ledger, selbstlite_migrator;
REVOKE ALL ON DATABASE documenso FROM PUBLIC;
GRANT CONNECT ON DATABASE documenso TO documenso;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname selbstlite <<'SQL'
-- Schema public: gehört dem Migrator, niemand sonst darf Objekte anlegen
ALTER SCHEMA public OWNER TO selbstlite_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO selbstlite_app;

-- Schema ledger: festgeschriebene Daten
CREATE SCHEMA ledger AUTHORIZATION selbstlite_migrator;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC;
GRANT USAGE ON SCHEMA ledger TO selbstlite_ledger, selbstlite_app;

-- Neue Tabellen in public automatisch für das Backend freigeben
ALTER DEFAULT PRIVILEGES FOR ROLE selbstlite_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO selbstlite_app;
ALTER DEFAULT PRIVILEGES FOR ROLE selbstlite_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO selbstlite_app;

-- Sichere Standardwerte
ALTER ROLE selbstlite_app    SET statement_timeout = '30s';
ALTER ROLE selbstlite_ledger SET statement_timeout = '30s';
ALTER DATABASE selbstlite SET timezone TO 'UTC';
SQL

echo "selbstlite: Rollen und Datenbanken angelegt."
