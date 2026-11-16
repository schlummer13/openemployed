"""Benutzerverwaltung: Einladen, Rolle ändern, sperren, Zwei-Faktor zurücksetzen. Benutzer werden nie gelöscht (Audit-Log)."""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.mail import OutgoingMail
from app.services.masterdata import ConflictError, NotFoundError

INVITE_DAYS = 7


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def list_users(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, email, name, role, active, auth_provider, totp_enabled, password_hash IS NOT NULL AS has_password, last_login_at, "
        "created_at, invited_by, invite_token_hash IS NOT NULL AND invite_expires_at > now() AS invite_pending, locked_until > now() AS "
        "locked "
        "FROM users WHERE tenant_id = :t ORDER BY active DESC, role, email"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def _admins(conn: AsyncConnection, tenant_id: UUID, exclude: UUID | None = None) -> int:
    return int(await conn.scalar(text("SELECT count(*) FROM users WHERE tenant_id = :t AND role = 'admin' AND active AND id IS DISTINCT "
                                      "FROM :x"),
                                 {"t": tenant_id, "x": exclude}) or 0)


async def invite(conn: AsyncConnection, mailer: Any, tenant_id: UUID, email: str, name: str | None, role: str, actor: str,
                 app_origin: str) -> dict[str, Any]:
    from app.services import roles

    if not await roles.exists(conn, role):
        raise ConflictError("Unbekannte Rolle")
    role_label = await roles.label_of(conn, role)
    email = email.strip().lower()
    if await conn.scalar(text("SELECT 1 FROM users WHERE tenant_id = :t AND email = :e"), {"t": tenant_id, "e": email}):
        raise ConflictError("Für diese Adresse gibt es bereits einen Benutzer")
    token = secrets.token_urlsafe(32)
    uid = await conn.scalar(text(
        "INSERT INTO users (tenant_id, google_sub, email, name, role, auth_provider, invited_by, invite_token_hash, invite_expires_at) "
        "VALUES (:t, :sub, :e, :n, :r, 'password', :a, :h, now() + make_interval(days => :d)) RETURNING id"),
        {"t": tenant_id, "sub": f"invite:{email}", "e": email, "n": name, "r": role, "a": actor, "h": _hash(token), "d": INVITE_DAYS})
    link = f"{app_origin}/einladung?token={token}"
    sent = False
    if mailer is not None:
        await mailer.send(OutgoingMail(to=[email], subject="Einladung zu OpenEmployed", text="\n".join([
            f"Hallo{' ' + name if name else ''},", "", f"{actor} hat Sie zu OpenEmployed eingeladen (Rolle: {role_label}).", "",
            f"Zugang einrichten (Link {INVITE_DAYS} Tage gültig):", link, "",
            "Sie können ein Passwort festlegen oder sich – falls eingerichtet – mit Google bzw. Microsoft anmelden."])))
        sent = True
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="user.invited", entity_type="user", entity_id=uid,
                       after={"email": email, "role": role})
    return {"id": uid, "link": None if sent else link, "mailed": sent}


async def update(conn: AsyncConnection, tenant_id: UUID, user_id: UUID, *, role: str, active: bool, actor: str, actor_id: UUID) -> None:
    from app.services import roles

    if not await roles.exists(conn, role):
        raise ConflictError("Unbekannte Rolle")
    cur = (await conn.execute(text("SELECT role, active FROM users WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                              {"t": tenant_id, "id": user_id})).one_or_none()
    if cur is None:
        raise NotFoundError("Benutzer nicht gefunden")
    if (role != "admin" or not active) and cur.role == "admin" and await _admins(conn, tenant_id, exclude=user_id) == 0:
        raise ConflictError("Mindestens ein aktiver Benutzer muss Geschäftsführung (Admin) bleiben")
    if user_id == actor_id and not active:
        raise ConflictError("Sie können sich nicht selbst sperren")
    await conn.execute(text("UPDATE users SET role = :r, active = :a WHERE id = :id"), {"r": role, "a": active, "id": user_id})
    if not active:
        await conn.execute(text("UPDATE user_sessions SET revoked_at = now() WHERE user_id = :id AND revoked_at IS NULL"), {"id": user_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="user.updated", entity_type="user", entity_id=user_id,
                       before={"role": cur.role, "active": cur.active}, after={"role": role, "active": active})


async def reset_2fa(conn: AsyncConnection, tenant_id: UUID, user_id: UUID, actor: str) -> None:
    n = (await conn.execute(text("UPDATE users SET totp_enabled = false, totp_secret = NULL WHERE tenant_id = :t AND id = :id"),
                            {"t": tenant_id, "id": user_id})).rowcount
    if not n:
        raise NotFoundError("Benutzer nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="user.2fa_reset", entity_type="user", entity_id=user_id)


async def invite_info(conn: AsyncConnection, tenant_id: UUID, token: str) -> dict[str, Any]:
    r = (await conn.execute(text("SELECT id, email, name, role FROM users WHERE tenant_id = :t AND invite_token_hash = :h "
                                 "AND invite_expires_at > now() AND active"), {"t": tenant_id, "h": _hash(token)})).mappings().one_or_none()
    if r is None:
        raise NotFoundError("Einladung ungültig oder abgelaufen")
    from app.services import roles

    return dict(r) | {"role_label": await roles.label_of(conn, r["role"])}


async def accept_invite(conn: AsyncConnection, tenant_id: UUID, token: str, password: str) -> UUID:
    from app.auth.passwords import hash_password, password_problem

    info = await invite_info(conn, tenant_id, token)
    problem = password_problem(password, info["email"])
    if problem:
        raise ConflictError(problem)
    await conn.execute(text("UPDATE users SET password_hash = :p, invite_token_hash = NULL, google_sub = :sub, auth_provider = 'password' "
                            "WHERE id = :id"), {"p": hash_password(password), "sub": f"password:{info['email']}", "id": info["id"]})
    await audit.record(conn, tenant_id=tenant_id, actor=info["email"], action="user.invite_accepted", entity_type="user",
                       entity_id=info["id"])
    uid: UUID = info["id"]
    return uid


async def change_password(conn: AsyncConnection, user_id: UUID, current: str | None, new: str, email: str, tenant_id: UUID) -> None:
    from app.auth.passwords import hash_password, password_problem, verify_password

    stored = await conn.scalar(text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id})
    if stored and not verify_password(current or "", stored):
        raise ConflictError("Aktuelles Passwort falsch")
    problem = password_problem(new, email)
    if problem:
        raise ConflictError(problem)
    await conn.execute(text("UPDATE users SET password_hash = :p WHERE id = :id"), {"p": hash_password(new), "id": user_id})
    await audit.record(conn, tenant_id=tenant_id, actor=email, action="user.password_changed", entity_type="user", entity_id=user_id)


async def start_totp(conn: AsyncConnection, user_id: UUID, email: str, issuer: str) -> dict[str, str]:
    from app.auth.passwords import new_totp_secret, otpauth_uri, qr_svg_data_uri

    secret = new_totp_secret()
    await conn.execute(text("UPDATE users SET totp_secret = :s, totp_enabled = false WHERE id = :id"), {"s": secret, "id": user_id})
    uri = otpauth_uri(secret, email, issuer)
    return {"secret": secret, "uri": uri, "qr": qr_svg_data_uri(uri)}


async def confirm_totp(conn: AsyncConnection, user_id: UUID, code: str, email: str, tenant_id: UUID) -> None:
    from app.auth.passwords import verify_totp

    secret = await conn.scalar(text("SELECT totp_secret FROM users WHERE id = :id"), {"id": user_id})
    if not secret or not verify_totp(secret, code):
        raise ConflictError("Code ungültig – Uhrzeit des Handys prüfen und erneut versuchen")
    await conn.execute(text("UPDATE users SET totp_enabled = true WHERE id = :id"), {"id": user_id})
    await audit.record(conn, tenant_id=tenant_id, actor=email, action="user.2fa_enabled", entity_type="user", entity_id=user_id)


def since(d: dt.datetime | None) -> str | None:
    return d.isoformat() if d else None
