#!/bin/sh
# Update: Sicherung → neuen Stand holen → neu bauen → Migrationen. Bricht bei jedem Fehler ab.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "→ Sicherung vor dem Update"
docker compose exec -T backup /bin/sh /backup.sh now
if [ -d .git ]; then
  echo "→ Neuen Stand holen"
  git pull --ff-only
fi
echo "→ Neu bauen und starten"
docker compose up -d --build --remove-orphans
echo "→ Migrationen"
docker compose exec -T backend alembic upgrade head
echo "Update abgeschlossen: Version $(cat VERSION 2>/dev/null || echo unbekannt)"
