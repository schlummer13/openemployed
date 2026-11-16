#!/bin/sh
# Erzeugt eine .env mit zufälligen, starken Geheimnissen aus .env.example.
# Überschreibt niemals eine bestehende .env.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  echo ".env existiert bereits – nichts geändert."
  exit 0
fi

rand() { openssl rand -hex "${1:-32}"; }
uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }

sed \
  -e "s|__POSTGRES_SUPERUSER_PASSWORD__|$(rand)|" \
  -e "s|__OPENEMPLOYED_MIGRATOR_PASSWORD__|$(rand)|" \
  -e "s|__OPENEMPLOYED_APP_PASSWORD__|$(rand)|" \
  -e "s|__OPENEMPLOYED_LEDGER_PASSWORD__|$(rand)|" \
  -e "s|__DOCUMENSO_DB_PASSWORD__|$(rand)|" \
  -e "s|__LEDGER_INTERNAL_TOKEN__|$(rand)|" \
  -e "s|__TENANT_ID__|$(uuid)|" \
  -e "s|__DOCUMENSO_NEXTAUTH_SECRET__|$(rand)|" \
  -e "s|__DOCUMENSO_ENCRYPTION_KEY__|$(rand)|" \
  -e "s|__DOCUMENSO_ENCRYPTION_SECONDARY_KEY__|$(rand)|" \
  -e "s|__DOCUMENSO_SIGNING_PASSPHRASE__|$(rand 24)|" \
  -e "s|__DOCUMENSO_WEBHOOK_SECRET__|$(rand)|" \
  -e "s|__SESSION_SECRET__|$(rand)|" \
  .env.example > .env

chmod 600 .env
echo ".env mit zufälligen Geheimnissen erzeugt (Rechte 600)."
