#!/bin/sh
# Tägliche Datensicherung (GoBD: Aufbewahrung 8/10 Jahre; Datensicherung ist Teil der Verfahrensdokumentation).
# - pg_dump im Custom-Format für openemployed (inkl. Ledger-Schema) und Documenso
# - Prüfung jeder Sicherung mit pg_restore --list, SHA-256-Prüfsumme
# - Aufbewahrung: 30 tägliche, 12 monatliche (1. des Monats), 10 jährliche (1. Januar)
# - Eine Kopie außer Haus (z. B. Netcup Storage Box per rsync) richtet ihr zusätzlich ein (docs/betrieb.md).
set -eu
BACKUP_DIR=/backups
HOUR="${BACKUP_HOUR:-2}"
export PGPASSWORD="$POSTGRES_PASSWORD"

run_backup() {
  ts=$(date +%Y-%m-%d_%H%M)
  day=$(date +%d); month=$(date +%m)
  mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly" "$BACKUP_DIR/yearly"
  for db in openemployed documenso; do
    file="$BACKUP_DIR/daily/${db}_${ts}.dump"
    pg_dump -h postgres -U "$POSTGRES_USER" -d "$db" -Fc -Z 6 -f "$file.part"
    pg_restore --list "$file.part" > /dev/null   # Sicherung lesbar?
    mv "$file.part" "$file"
    (cd "$(dirname "$file")" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
    if [ "$day" = "01" ]; then cp "$file" "$file.sha256" "$BACKUP_DIR/monthly/"; fi
    if [ "$day" = "01" ] && [ "$month" = "01" ]; then cp "$file" "$file.sha256" "$BACKUP_DIR/yearly/"; fi
  done
  # Rotation (nur selbst erzeugte Dateien)
  ls -1t "$BACKUP_DIR"/daily/*.dump 2>/dev/null | awk 'NR>60' | while read -r f; do rm -f "$f" "$f.sha256"; done
  ls -1t "$BACKUP_DIR"/monthly/*.dump 2>/dev/null | awk 'NR>24' | while read -r f; do rm -f "$f" "$f.sha256"; done
  ls -1t "$BACKUP_DIR"/yearly/*.dump 2>/dev/null | awk 'NR>20' | while read -r f; do rm -f "$f" "$f.sha256"; done
  date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/last_success"
  echo "Sicherung $ts erfolgreich"
}

if [ "${1:-}" = "now" ]; then run_backup; exit 0; fi
echo "Datensicherung aktiv – täglich um ${HOUR}:30 Uhr"
last=""
while true; do
  today=$(date +%Y-%m-%d)
  if [ "$(date +%H)" -ge "$HOUR" ] && [ "$(date +%M)" -ge 30 ] && [ "$last" != "$today" ]; then
    if run_backup; then last="$today"; else echo "FEHLER: Sicherung fehlgeschlagen" >&2; fi
  fi
  # Sicherung auf Anforderung aus der App (Einstellungen → System → „Jetzt sichern“)
  if [ -f "$BACKUP_DIR/.request" ]; then
    rm -f "$BACKUP_DIR/.request"
    if run_backup; then :; else echo "FEHLER: Sicherung fehlgeschlagen" >&2; date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_DIR/last_failure"; fi
  fi
  sleep 30
done
