"""API-Schlüssel für Integrationen (z. B. Zapier, n8n, eigene Skripte): ``Authorization: Bearer sl_…``.

Nur der SHA-256-Hash wird gespeichert; der Schlüssel wird einmalig beim Anlegen angezeigt. Rechte über die Rolle
(Buchhaltung = lesen und schreiben, Steuerberater = nur lesen); Verwaltung von Benutzern und Einstellungen ist nie möglich.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.auth.store import User
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError


def _hash(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


async def resolve(engine: AsyncEngine, key: str) -> User | None:
    if len(key) > 120:
        return None
    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "UPDATE api_keys SET last_used_at = now() WHERE key_hash = :h AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > "
            "now()) "
            "RETURNING id, name, role"), {"h": _hash(key)})).one_or_none()
    if row is None:
        return None
    return User(id=row.id, email=f"api:{row.name}", name=row.name, picture=None, role=row.role, via_api_key=True)


async def list_keys(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, name, prefix, role, created_by, created_at, expires_at, last_used_at, revoked_at FROM api_keys WHERE tenant_id = :t "
        "ORDER BY revoked_at NULLS FIRST, created_at DESC"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def create(conn: AsyncConnection, tenant_id: UUID, name: str, role: str, days: int | None, actor: str) -> dict[str, Any]:
    if role not in ("accounting", "advisor"):
        raise ConflictError("Rolle für API-Schlüssel: Buchhaltung oder Steuerberater")
    prefix = secrets.token_hex(4)
    key = f"sl_{prefix}_{secrets.token_urlsafe(32)}"
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(days=days) if days else None
    kid = await conn.scalar(text(
        "INSERT INTO api_keys (tenant_id, name, prefix, key_hash, role, created_by, expires_at) VALUES (:t, :n, :p, :h, :r, :a, :x) "
        "RETURNING id"),
        {"t": tenant_id, "n": name, "p": prefix, "h": _hash(key), "r": role, "a": actor, "x": expires})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="api_key.created", entity_type="api_key", entity_id=kid,
                       after={"name": name, "role": role, "prefix": prefix, "expires_at": str(expires) if expires else None})
    return {"id": kid, "key": key, "prefix": prefix}


async def revoke(conn: AsyncConnection, tenant_id: UUID, key_id: UUID, actor: str) -> None:
    n = (await conn.execute(text("UPDATE api_keys SET revoked_at = now() WHERE tenant_id = :t AND id = :id AND revoked_at IS NULL"),
                            {"t": tenant_id, "id": key_id})).rowcount
    if not n:
        raise NotFoundError("API-Schlüssel nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="api_key.revoked", entity_type="api_key", entity_id=key_id)
