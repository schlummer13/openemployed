"""Mehrere Firmen in einer Installation (z. B. Holding + Tochter-UG). Alle Fachdaten sind je Firma getrennt (tenant_id);
gemeinsam sind nur die Benutzerkonten, Anmeldeverfahren und Systemeinstellungen (Postfach, KI, Integrationen)."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError


async def ensure_home(engine: AsyncEngine, home: UUID) -> None:
    """Stammfirma anlegen (Name aus dem Firmenprofil), falls die Tabelle noch leer ist."""
    try:
        async with engine.begin() as conn:
            name = await conn.scalar(text("SELECT legal_name FROM company_profiles WHERE tenant_id = :t ORDER BY valid_from DESC LIMIT 1"),
                                     {"t": home})
            await conn.execute(text("INSERT INTO companies (id, name, created_by) VALUES (:id, :n, 'system') ON CONFLICT (id) DO NOTHING"),
                               {"id": home, "n": name or "Meine Firma"})
    except Exception:  # Migration noch nicht gelaufen
        return


async def all_ids(conn: AsyncConnection, home: UUID) -> list[UUID]:
    try:
        ids = [r[0] for r in await conn.execute(text("SELECT id FROM companies WHERE archived_at IS NULL ORDER BY created_at"))]
    except Exception:
        ids = []
    return ids or [home]


async def role_in(conn: AsyncConnection, user_id: UUID, user_tenant: UUID, user_role: str, company: UUID) -> str | None:
    """Rolle des Benutzers in einer Firma: Stammfirma = Rolle des Kontos, sonst laut Mitgliedschaft; None = kein Zugriff."""
    if company == user_tenant:
        return user_role
    role = await conn.scalar(text("SELECT m.role FROM company_members m JOIN companies c ON c.id = m.company_id "
                                  "WHERE m.company_id = :c AND m.user_id = :u AND c.archived_at IS NULL"), {"c": company, "u": user_id})
    return str(role) if role else None


async def for_user(conn: AsyncConnection, user_id: UUID, home: UUID) -> list[dict[str, Any]]:
    home_tenant = await conn.scalar(text("SELECT tenant_id FROM users WHERE id = :u"), {"u": user_id}) or home
    rows = await conn.execute(text(
        "SELECT c.id, c.name, c.color, COALESCE(m.role, CASE WHEN c.id = :ht THEN u.role END) AS role, "
        "(SELECT legal_name FROM company_profiles p WHERE p.tenant_id = c.id ORDER BY valid_from DESC LIMIT 1) AS legal_name "
        "FROM companies c JOIN users u ON u.id = :u LEFT JOIN company_members m ON m.company_id = c.id AND m.user_id = :u "
        "WHERE c.archived_at IS NULL AND (c.id = :ht OR m.user_id IS NOT NULL) ORDER BY (c.id = :ht) DESC, c.name"),
        {"u": user_id, "ht": home_tenant})
    from app.services import roles

    return [dict(r._mapping) | {"role_label": await roles.label_of(conn, r.role) if r.role else ""} for r in rows]


async def create(conn: AsyncConnection, name: str, user_id: UUID, actor: str, tenant_for_audit: UUID) -> UUID:
    cid = uuid.uuid4()
    await conn.execute(text("INSERT INTO companies (id, name, created_by) VALUES (:id, :n, :a)"), {"id": cid, "n": name, "a": actor})
    await conn.execute(text("INSERT INTO company_members (company_id, user_id, role, created_by) VALUES (:c, :u, 'admin', :a)"),
                       {"c": cid, "u": user_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_for_audit, actor=actor, action="company.created", entity_type="company", entity_id=cid,
                       after={"name": name})
    await audit.record(conn, tenant_id=cid, actor=actor, action="company.created", entity_type="company", entity_id=cid,
                       after={"name": name})
    return cid


async def members(conn: AsyncConnection, company: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT u.id, u.email, u.name, COALESCE(m.role, u.role) AS role, (u.tenant_id = :c) AS home FROM users u "
        "LEFT JOIN company_members m ON m.user_id = u.id AND m.company_id = :c WHERE u.active AND (u.tenant_id = :c OR m.user_id IS NOT "
        "NULL) "
        "ORDER BY u.email"), {"c": company})
    return [dict(r) for r in rows.mappings()]


async def set_member(conn: AsyncConnection, company: UUID, email: str, role: str | None, actor: str) -> None:
    from app.services import roles

    if role is not None and not await roles.exists(conn, role):
        raise ConflictError("Unbekannte Rolle")
    user = (await conn.execute(text("SELECT id, tenant_id FROM users WHERE email = :e AND active"), {"e": email.lower()})).one_or_none()
    if user is None:
        raise NotFoundError("Kein aktiver Benutzer mit dieser E-Mail – bitte zuerst einladen")
    if user.tenant_id == company:
        raise ConflictError("Die Rolle in der Stammfirma wird unter Benutzer & Rollen geändert")
    if role is None:
        await conn.execute(text("DELETE FROM company_members WHERE company_id = :c AND user_id = :u"), {"c": company, "u": user.id})
    else:
        await conn.execute(text("INSERT INTO company_members (company_id, user_id, role, created_by) VALUES (:c, :u, :r, :a) "
                                "ON CONFLICT (company_id, user_id) DO UPDATE SET role = :r"),
                           {"c": company, "u": user.id, "r": role, "a": actor})
    await audit.record(conn, tenant_id=company, actor=actor, action="company.member", entity_type="user", entity_id=user.id,
                       after={"role": role})
