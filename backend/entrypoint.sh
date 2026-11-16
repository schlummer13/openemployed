#!/bin/sh
set -eu
# 1. Migrationen mit der Migrator-Rolle
alembic upgrade head
# 2. Anwendung mit der eingeschränkten App-Rolle (DATABASE_URL); Migrator-Zugang wird nicht weitergereicht
unset MIGRATOR_DATABASE_URL
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*" --no-server-header \
  --workers "${WEB_CONCURRENCY:-2}" --timeout-keep-alive 30 --timeout-graceful-shutdown 20
