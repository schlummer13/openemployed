#!/bin/sh
# Wiederherstellung einer Sicherung (zuerst in eine TEST-Datenbank, erst nach Prüfung produktiv!)
#   scripts/restore.sh backups/daily/selbstlite_2026-11-14_0230.dump selbstlite_restore_test
# Danach: Ledger-Prüfung (Hash-Kette) ausführen – nur bei „intakt“ produktiv übernehmen.
set -eu
FILE="${1:?Sicherungsdatei angeben}"
TARGET="${2:?Ziel-Datenbank angeben (z. B. selbstlite_restore_test)}"
(cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256")
docker compose exec -T postgres sh -c "createdb -U \"\$POSTGRES_USER\" \"$TARGET\""
docker compose exec -T postgres sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"$TARGET\" --no-owner --exit-on-error" < "$FILE"
echo "Wiederhergestellt in $TARGET. Bitte Inhalt prüfen; produktive Übernahme siehe docs/betrieb.md."
