.PHONY: help install install-lokal update backup secrets cert up down logs ps test test-backend test-ledger lint demo

COMPOSE = docker compose

help:
	@echo "make install   Installation auf dem Server (nginx davor, Ports auf 127.0.0.1)"
	@echo "make install-lokal  zum Ausprobieren auf dem eigenen Rechner"
	@echo "make update    Sicherung, neuer Stand, Neubau, Migrationen"
	@echo "make backup    Datensicherung sofort"
	@echo "make secrets   .env mit zufälligen Geheimnissen + Documenso-Zertifikat erzeugen"
	@echo "make up        kompletten Stack starten (http://localhost, http://sign.localhost)"
	@echo "make down      Stack stoppen (Daten bleiben erhalten)"
	@echo "make logs      Logs aller Dienste"
	@echo "make demo      Demodaten anlegen (lokal; Mails → http://localhost:8025)"
	@echo "make test      alle Tests (Backend + Ledger)"
	@echo "make lint      Typprüfung und Linter"

secrets:
	./scripts/generate-env.sh
	@set -a; . ./.env; set +a; ./infra/documenso/generate-cert.sh

up:
	$(COMPOSE) --profile lokal up -d --build --remove-orphans

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

test: test-ledger test-backend

test-ledger:
	cd ledger && cargo test

test-backend:
	cd backend && .venv/bin/python -m pytest

lint:
	cd ledger && cargo fmt --check && cargo clippy --all-targets -- -D warnings
	cd backend && .venv/bin/ruff check . && .venv/bin/mypy app

demo:
	$(COMPOSE) exec backend python -m app.demo

install:
	./scripts/install.sh

install-lokal:
	./scripts/install.sh --lokal

update:
	./scripts/update.sh

backup:
	$(COMPOSE) exec -T backup /bin/sh /backup.sh now
