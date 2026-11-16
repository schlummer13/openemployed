"""Hilfsfunktionen für Integrationstests gegen echtes PostgreSQL (frische Datenbank je Test, Migrationen)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg

ADMIN_URL = os.environ.get("BACKEND_TEST_ADMIN_URL")
ROLES = {"selbstlite_migrator": "mig_pw", "selbstlite_app": "app_pw", "selbstlite_ledger": "led_pw"}


def db_url(user: str, password: str, db: str) -> str:
    if not ADMIN_URL:
        raise RuntimeError("BACKEND_TEST_ADMIN_URL fehlt")
    rest = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    return f"postgresql+asyncpg://{user}:{password}@{rest}/{db}"


async def create_database() -> str:
    """Legt Rollen + Datenbank an (wie infra/postgres/init) und migriert. Gibt den DB-Namen zurück."""
    if not ADMIN_URL:
        raise RuntimeError("BACKEND_TEST_ADMIN_URL fehlt")
    db = f"backend_test_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        for role, pw in ROLES.items():
            await admin.execute(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
                f"CREATE ROLE {role} LOGIN PASSWORD '{pw}'; ELSE ALTER ROLE {role} LOGIN PASSWORD '{pw}'; END IF; END $$;"
            )
        await admin.execute(f"CREATE DATABASE {db} OWNER selbstlite_migrator")
    finally:
        await admin.close()

    conn = await asyncpg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{db}")
    try:
        await conn.execute("""
            ALTER SCHEMA public OWNER TO selbstlite_migrator;
            REVOKE ALL ON SCHEMA public FROM PUBLIC;
            GRANT USAGE ON SCHEMA public TO selbstlite_app;
            ALTER DEFAULT PRIVILEGES FOR ROLE selbstlite_migrator IN SCHEMA public
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO selbstlite_app;
            ALTER DEFAULT PRIVILEGES FOR ROLE selbstlite_migrator IN SCHEMA public
              GRANT USAGE, SELECT ON SEQUENCES TO selbstlite_app;
        """)
    finally:
        await conn.close()

    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env={**os.environ, "MIGRATOR_DATABASE_URL": db_url("selbstlite_migrator", ROLES["selbstlite_migrator"], db)},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Migration fehlgeschlagen:\n{result.stderr[-3000:]}")
    return db


async def drop_database(db: str) -> None:
    if not ADMIN_URL:
        return
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    finally:
        await admin.close()


def app_url(db: str) -> str:
    return db_url("selbstlite_app", ROLES["selbstlite_app"], db)
