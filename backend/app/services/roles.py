"""Rollenverwaltung: Standardrollen (anpassbar außer Geschäftsführung) und eigene Rollen mit Rechten je Bereich."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.auth.roles import AREAS, BUILTIN, ROLE_LABELS, permissions_of
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

_cache: dict[str, tuple[float, dict[str, str] | None, str | None]] = {}
TTL = 30.0


def invalidate() -> None:
    _cache.clear()


async def resolve(engine: AsyncEngine | None, role: str) -> dict[str, str]:
    """Rechte einer Rolle (30 s zwischengespeichert)."""
    if role == "admin" or engine is None:
        return permissions_of(role)
    hit = _cache.get(role)
    if hit and hit[0] > time.time():
        return permissions_of(role, hit[1])
    custom: dict[str, str] | None = None
    try:
        async with engine.connect() as conn:
            row = await conn.scalar(text("SELECT permissions FROM roles WHERE key = :k"), {"k": role})
            custom = dict(row) if row else None
    except Exception:  # Tabelle fehlt (Tests ohne Datenbank)
        custom = None
    _cache[role] = (time.time() + TTL, custom, None)
    return permissions_of(role, custom)


async def label_of(conn: AsyncConnection, role: str) -> str:
    if role in ROLE_LABELS and role == "admin":
        return ROLE_LABELS[role]
    label = await conn.scalar(text("SELECT label FROM roles WHERE key = :k"), {"k": role})
    return str(label or ROLE_LABELS.get(role, role))


async def list_roles(conn: AsyncConnection) -> list[dict[str, Any]]:
    stored = {r.key: r for r in await conn.execute(text("SELECT key, label, description, permissions FROM roles"))}
    counts = {r[0]: r[1] for r in await conn.execute(text("SELECT role, count(*) FROM users WHERE active GROUP BY role"))}
    out = []
    for key, label in ROLE_LABELS.items():
        s = stored.get(key)
        out.append({"key": key, "label": s.label if s else label, "description": s.description if s else None, "builtin": True,
                    "editable": key != "admin", "customized": s is not None,
                    "permissions": permissions_of(key, dict(s.permissions) if s else None), "users": counts.get(key, 0)})
    for key, s in stored.items():
        if key not in ROLE_LABELS:
            out.append({"key": key, "label": s.label, "description": s.description, "builtin": False, "editable": True, "customized": True,
                        "permissions": permissions_of(key, dict(s.permissions)), "users": counts.get(key, 0)})
    return out


def _clean(perms: dict[str, Any]) -> dict[str, str]:
    bad = [k for k in perms if k not in AREAS]
    if bad:
        raise ConflictError(f"Unbekannte Bereiche: {', '.join(bad)}")
    return {a: str(perms.get(a, "none")) if perms.get(a) in ("none", "read", "write") else "none" for a in AREAS}


async def save(conn: AsyncConnection, key: str, label: str, description: str | None, perms: dict[str, Any], actor: str,
               tenant_for_audit: Any) -> str:
    if key == "admin":
        raise ConflictError("Die Geschäftsführung hat immer alle Rechte")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", key):
        raise ConflictError("Kennung: Kleinbuchstaben, Ziffern, Unterstrich (2–40 Zeichen)")
    clean = _clean(perms)
    await conn.execute(text("INSERT INTO roles (key, label, description, permissions, updated_by) VALUES (:k, :l, :d, CAST(:p AS jsonb), "
                            ":a) "
                            "ON CONFLICT (key) DO UPDATE SET label = :l, description = :d, permissions = CAST(:p AS jsonb), updated_by = "
                            ":a, "
                            "updated_at = now()"), {"k": key, "l": label, "d": description, "p": json.dumps(clean), "a": actor})
    await audit.record(conn, tenant_id=tenant_for_audit, actor=actor, action="role.saved", entity_type="role", entity_id=key,
                       after={"label": label, "permissions": clean})
    invalidate()
    return key


async def delete(conn: AsyncConnection, key: str, actor: str, tenant_for_audit: Any) -> None:
    if key in BUILTIN:
        # Standardrolle: Anpassung zurücksetzen
        await conn.execute(text("DELETE FROM roles WHERE key = :k"), {"k": key})
    else:
        used = await conn.scalar(text("SELECT count(*) FROM users WHERE role = :k") , {"k": key})
        used = (used or 0) + (await conn.scalar(text("SELECT count(*) FROM company_members WHERE role = :k"), {"k": key}) or 0)
        if used:
            raise ConflictError("Die Rolle ist noch vergeben – Benutzer zuerst umstellen")
        n = (await conn.execute(text("DELETE FROM roles WHERE key = :k"), {"k": key})).rowcount
        if not n:
            raise NotFoundError("Rolle nicht gefunden")
    await audit.record(conn, tenant_id=tenant_for_audit, actor=actor, action="role.deleted", entity_type="role", entity_id=key)
    invalidate()


async def exists(conn: AsyncConnection, key: str) -> bool:
    return key in BUILTIN or bool(await conn.scalar(text("SELECT 1 FROM roles WHERE key = :k"), {"k": key}))
