"""Integrationstests gegen echtes PostgreSQL (Migrationen, Rechte, Sitzungsspeicher).

Aktivieren mit einer Superuser-Verbindung, z. B.:
  docker run -d --rm --name be-test -e POSTGRES_PASSWORD=testpw -p 127.0.0.1:55433:5432 postgres:17-alpine
  BACKEND_TEST_ADMIN_URL=postgresql://postgres:testpw@127.0.0.1:55433/postgres .venv/bin/python -m pytest -k postgres
Ohne die Variable werden die Tests übersprungen.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.auth.google import GoogleIdentity
from app.auth.store import PostgresAuthStore
from tests.pgutil import ADMIN_URL, app_url, create_database, drop_database

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
async def app_engine() -> AsyncIterator[AsyncEngine]:
    db = await create_database()
    engine = create_async_engine(app_url(db))
    try:
        yield engine
    finally:
        await engine.dispose()
        await drop_database(db)


async def test_session_store_against_postgres(app_engine: AsyncEngine) -> None:
    store = PostgresAuthStore(app_engine, tenant_id=TENANT, max_age=dt.timedelta(hours=1), idle=dt.timedelta(hours=1))
    user = await store.upsert_google_user(GoogleIdentity("sub-1", "anna@beispiel.de", "Anna", None))
    assert user is not None
    again = await store.upsert_google_user(GoogleIdentity("sub-1", "anna@beispiel.de", "Anna B.", None))
    assert again is not None and again.id == user.id and again.name == "Anna B."

    token = await store.create_session(user, ip="203.0.113.7", user_agent="pytest")
    assert (await store.resolve_session(token)) == again
    assert await store.resolve_session("falsch") is None

    await store.revoke_session(token, actor=user.email)
    assert await store.resolve_session(token) is None

    # Leerlauf überschritten
    token2 = await store.create_session(user, ip=None, user_agent=None)
    async with app_engine.begin() as conn:
        await conn.execute(text("UPDATE user_sessions SET last_seen_at = now() - interval '2 hours'"))
    assert await store.resolve_session(token2) is None

    # gesperrter Benutzer
    token3 = await store.create_session(user, ip=None, user_agent=None)
    async with app_engine.begin() as conn:
        await conn.execute(text("UPDATE users SET active = false"))
    assert await store.resolve_session(token3) is None
    assert await store.upsert_google_user(GoogleIdentity("sub-1", "anna@beispiel.de", "Anna", None)) is None

    async with app_engine.connect() as conn:
        actions = [r[0] for r in await conn.execute(text("SELECT action FROM audit_log ORDER BY id"))]
        assert actions == ["auth.login", "auth.logout", "auth.login", "auth.login"]
        assert (await conn.execute(text("SELECT DISTINCT length(token_hash) FROM user_sessions"))).scalar() == 32


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "DELETE FROM audit_log",
        "UPDATE audit_log SET actor = 'x'",
        "DELETE FROM company_profiles",
        "SELECT * FROM alembic_version",
    ],
)
async def test_app_role_restrictions(app_engine: AsyncEngine, sql: str) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(
            text("INSERT INTO audit_log (tenant_id, actor, actor_type, action, entity_type) "
                 "VALUES (:t, 'a', 'system', 'x', 'y')"),
            {"t": TENANT},
        )
        with pytest.raises(Exception, match=r"permission denied|verboten"):
            await conn.execute(text(sql))
