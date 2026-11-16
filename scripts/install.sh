#!/bin/sh
# selbstlite mit einem Befehl installieren (Ubuntu/Debian-Server mit Docker; lokal genauso):
#   ./scripts/install.sh            Produktion hinter eigenem Reverse-Proxy (nginx), Ports nur auf 127.0.0.1
#   ./scripts/install.sh --lokal    Ausprobieren auf dem eigenen Rechner (Caddy, Mailpit, Entwickler-Anmeldung)
# Danach im Browser öffnen und den Einrichtungsassistenten durchlaufen (Firma, Steuern, E-Mail, Benutzer).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="${1:-}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Fehlt: $1 – bitte installieren ($2)"; exit 1; }; }
need docker "https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || { echo "Fehlt: docker compose (Plugin)"; exit 1; }
need openssl "apt install openssl"
need python3 "apt install python3"

if [ ! -f .env ]; then
  echo "→ Erzeuge .env mit zufälligen Geheimnissen"
  ./scripts/generate-env.sh
  if [ "$MODE" = "--lokal" ]; then
    sed -i.bak -e 's/^APP_ENV=.*/APP_ENV=development/' -e 's/^DEV_LOGIN_ENABLED=.*/DEV_LOGIN_ENABLED=true/' \
      -e 's/^MAIL_AUTH_MODE=.*/MAIL_AUTH_MODE=local/' -e 's/^MAIL_MAILBOX=.*/MAIL_MAILBOX=buchhaltung@selbstlite.test/' \
      -e 's/^SMTP_HOST=.*/SMTP_HOST=mailpit/' -e 's/^SMTP_PORT=.*/SMTP_PORT=1025/' .env && rm -f .env.bak
  else
    printf "Adresse der App (z. B. https://app.firma.de): "; read -r ORIGIN
    sed -i.bak -e "s|^APP_ORIGIN=.*|APP_ORIGIN=${ORIGIN}|" -e "s|^APP_ENV=.*|APP_ENV=production|" .env && rm -f .env.bak
  fi
fi
mkdir -p secrets backups
set -a; . ./.env; set +a
[ -f secrets/documenso-cert.p12 ] || ./infra/documenso/generate-cert.sh || true

echo "→ Baue und starte die Dienste (beim ersten Mal einige Minuten)"
if [ "$MODE" = "--lokal" ]; then docker compose --profile lokal up -d --build --remove-orphans
else docker compose up -d --build --remove-orphans; fi
echo "→ Datenbank-Migrationen"
docker compose exec -T backend alembic upgrade head

if [ "$MODE" = "--lokal" ]; then
  echo "Fertig: http://localhost  (Testpostfach: http://localhost:8025, Demodaten: make demo)"
else
  echo "Fertig. nginx-Vorlage: infra/nginx/selbstlite.conf – danach ${APP_ORIGIN:-https://…}/einrichtung öffnen."
fi
