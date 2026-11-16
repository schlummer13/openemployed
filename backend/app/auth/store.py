"""Benutzer und Sitzungen mit Rollen (app/auth/roles.py) und mehreren Anmeldeverfahren.

Sitzungs-Token werden nur als SHA-256-Hash gespeichert; der Klartext existiert nur im Cookie.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.google import GoogleIdentity


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    email: str
    name: str | None
    picture: str | None
    role: str = "admin"
    via_api_key: bool = False
    permissions: tuple[tuple[str, str], ...] = ()

    def can(self, area: str, level: str = "read") -> bool:
        perms = dict(self.permissions)
        have = perms.get(area, "none")
        return have == "write" or (level == "read" and have == "read")


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class AuthStore(Protocol):
    async def upsert_google_user(self, identity: GoogleIdentity) -> User | None:
        """Legt den Benutzer an bzw. aktualisiert ihn. None, wenn im System gesperrt."""
        ...

    async def upsert_identity(self, provider: str, sub: str, email: str, name: str | None, picture: str | None) -> User | None: ...

    async def get_user(self, user_id: UUID) -> User | None: ...

    async def password_login(self, email: str, password: str, totp_code: str | None) -> tuple[str, User | None]: ...

    async def create_session(self, user: User, *, ip: str | None, user_agent: str | None) -> str: ...

    async def resolve_session(self, token: str) -> User | None: ...

    async def revoke_session(self, token: str, *, actor: str | None) -> None: ...


class PostgresAuthStore:
    def __init__(self, engine: AsyncEngine, *, tenant_id: UUID, max_age: dt.timedelta, idle: dt.timedelta) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        self._max_age = max_age
        self._idle = idle

    async def upsert_google_user(self, identity: GoogleIdentity) -> User | None:
        provider = "dev" if identity.sub.startswith("dev:") else "google"
        return await self.upsert_identity(provider, identity.sub, identity.email, identity.name, identity.picture)

    async def upsert_identity(self, provider: str, sub: str, email: str, name: str | None, picture: str | None) -> User | None:
        """Anmeldung per SSO: vorhandenes Konto (gleiche Kennung oder eingeladene E-Mail) oder – je nach Einstellung – neu anlegen.

        Der erste Benutzer überhaupt wird immer Geschäftsführung (Einrichtung). ``None`` = gesperrt bzw. nicht eingeladen.
        """
        from app.core.config import get_settings

        default_role = get_settings().auth_default_role
        async with self._engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT id, active, google_sub FROM users WHERE tenant_id = :t AND (google_sub = :sub OR email = :email) "
                "ORDER BY (google_sub = :sub) DESC LIMIT 1"), {"t": self._tenant_id, "sub": sub, "email": email})).one_or_none()
            if row is None:
                first = not await conn.scalar(text("SELECT 1 FROM users WHERE tenant_id = :t LIMIT 1"), {"t": self._tenant_id})
                role = "admin" if first else default_role
                if role == "none":
                    return None
                row = (await conn.execute(text(
                    "INSERT INTO users (tenant_id, google_sub, email, name, picture_url, role, auth_provider, last_login_at) "
                    "VALUES (:t, :sub, :email, :name, :pic, :role, :prov, now()) RETURNING id, active, google_sub"),
                    {"t": self._tenant_id, "sub": sub, "email": email, "name": name, "pic": picture, "role": role, "prov": provider})).one()
            else:
                # eingeladenes Konto (Kennung invite:…) übernimmt beim ersten SSO-Login die Kennung des Anbieters
                new_sub = sub if row.google_sub.startswith("invite:") or row.google_sub == sub else row.google_sub
                if new_sub != sub and not row.google_sub.startswith("invite:"):
                    return None  # gleiche E-Mail, aber anderes Konto/anderer Anbieter – keine stille Übernahme
                await conn.execute(text(
                    "UPDATE users SET google_sub = :sub, email = :email, name = COALESCE(:name, name), picture_url = :pic, "
                    "auth_provider = CASE WHEN auth_provider = 'password' THEN auth_provider ELSE :prov END, last_login_at = now(), "
                    "invite_token_hash = NULL WHERE id = :id"),
                    {"sub": new_sub, "email": email, "name": name, "pic": picture, "prov": provider, "id": row.id})
            if not row.active:
                return None
        return await self.get_user(row.id)

    async def get_user(self, user_id: UUID) -> User | None:
        async with self._engine.connect() as conn:
            r = (await conn.execute(text("SELECT id, email, name, picture_url, role, active FROM users WHERE id = :id"),
                                    {"id": user_id})).one_or_none()
        if r is None or not r.active:
            return None
        return User(id=r.id, email=r.email, name=r.name, picture=r.picture_url, role=r.role)

    async def password_login(self, email: str, password: str, totp_code: str | None) -> tuple[str, User | None]:
        """Rückgabe (Status, Benutzer): ok | invalid | locked | totp_required | totp_invalid | totp_setup."""
        from app.auth.passwords import verify_password, verify_totp
        from app.core.config import get_settings

        async with self._engine.begin() as conn:
            r = (await conn.execute(text(
                "SELECT id, password_hash, totp_secret, totp_enabled, active, failed_logins, locked_until > now() AS locked FROM users "
                "WHERE tenant_id = :t AND email = :e FOR UPDATE"), {"t": self._tenant_id, "e": email})).one_or_none()
            if r is not None and r.locked:
                return "locked", None
            ok = verify_password(password, r.password_hash if r else None)
            if r is None or not ok or not r.active:
                if r is not None:
                    await conn.execute(text(
                        "UPDATE users SET failed_logins = failed_logins + 1, locked_until = CASE WHEN failed_logins + 1 >= 5 "
                        "THEN now() + interval '15 minutes' END WHERE id = :id"), {"id": r.id})
                return "invalid", None
            if r.totp_enabled:
                if not totp_code:
                    return "totp_required", None
                if not verify_totp(r.totp_secret, totp_code):
                    await conn.execute(text("UPDATE users SET failed_logins = failed_logins + 1 WHERE id = :id"), {"id": r.id})
                    return "totp_invalid", None
            await conn.execute(text("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = :id"),
                               {"id": r.id})
            needs_setup = get_settings().auth_require_2fa and not r.totp_enabled
        user = await self.get_user(r.id)
        return ("totp_setup" if needs_setup else "ok"), user

    async def create_session(self, user: User, *, ip: str | None, user_agent: str | None) -> str:
        token = new_token()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO user_sessions (id, token_hash, user_id, expires_at, ip, user_agent)
                    VALUES (:id, :hash, :user_id, now() + CAST(:max_age AS interval), CAST(:ip AS inet), :ua)
                    """
                ),
                {"id": uuid4(), "hash": hash_token(token), "user_id": user.id, "max_age": self._max_age,
                 "ip": ip, "ua": (user_agent or "")[:500]},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO audit_log (tenant_id, actor, actor_type, action, entity_type, entity_id, ip, user_agent)
                    VALUES (:tenant_id, :actor, 'user', 'auth.login', 'user', :uid, CAST(:ip AS inet), :ua)
                    """
                ),
                {"tenant_id": self._tenant_id, "actor": user.email, "uid": str(user.id), "ip": ip,
                 "ua": (user_agent or "")[:500]},
            )
        return token

    async def resolve_session(self, token: str) -> User | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT s.id AS session_id, s.last_seen_at, u.id, u.email, u.name, u.picture_url, u.role
                          FROM user_sessions s JOIN users u ON u.id = s.user_id
                         WHERE s.token_hash = :hash
                           AND s.revoked_at IS NULL
                           AND s.expires_at > now()
                           AND s.last_seen_at > now() - CAST(:idle AS interval)
                           AND u.active
                        """
                    ),
                    {"hash": hash_token(token), "idle": self._idle},
                )
            ).one_or_none()
            if row is None:
                return None
            # „zuletzt gesehen“ höchstens alle 5 Minuten schreiben
            await conn.execute(
                text(
                    "UPDATE user_sessions SET last_seen_at = now() "
                    "WHERE id = :id AND last_seen_at < now() - interval '5 minutes'"
                ),
                {"id": row.session_id},
            )
        return User(id=row.id, email=row.email, name=row.name, picture=row.picture_url, role=row.role)

    async def revoke_session(self, token: str, *, actor: str | None) -> None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("UPDATE user_sessions SET revoked_at = now() WHERE token_hash = :hash AND revoked_at IS NULL "
                     "RETURNING user_id"),
                {"hash": hash_token(token)},
            )
            revoked = result.one_or_none()
            if revoked is not None and actor:
                await conn.execute(
                    text(
                        "INSERT INTO audit_log (tenant_id, actor, actor_type, action, entity_type, entity_id) "
                        "VALUES (:tenant_id, :actor, 'user', 'auth.logout', 'user', :uid)"
                    ),
                    {"tenant_id": self._tenant_id, "actor": actor, "uid": str(revoked.user_id)},
                )


class MemoryAuthStore:
    """Für Tests: gleiche Semantik ohne Datenbank (alle Benutzer Geschäftsführung)."""

    def __init__(self) -> None:
        self.users: dict[str, tuple[User, bool]] = {}
        self.sessions: dict[bytes, tuple[User, bool]] = {}
        self.events: list[str] = []

    async def upsert_google_user(self, identity: GoogleIdentity) -> User | None:
        existing = self.users.get(identity.sub)
        user = User(id=existing[0].id if existing else uuid4(), email=identity.email, name=identity.name,
                    picture=identity.picture)
        active = existing[1] if existing else True
        self.users[identity.sub] = (user, active)
        return user if active else None

    async def upsert_identity(self, provider: str, sub: str, email: str, name: str | None, picture: str | None) -> User | None:
        return await self.upsert_google_user(GoogleIdentity(sub=sub, email=email, name=name, picture=picture))

    async def get_user(self, user_id: UUID) -> User | None:
        return next((u for u, active in self.users.values() if u.id == user_id and active), None)

    async def password_login(self, email: str, password: str, totp_code: str | None) -> tuple[str, User | None]:
        return "invalid", None

    async def create_session(self, user: User, *, ip: str | None, user_agent: str | None) -> str:
        token = new_token()
        self.sessions[hash_token(token)] = (user, True)
        self.events.append(f"login:{user.email}")
        return token

    async def resolve_session(self, token: str) -> User | None:
        entry = self.sessions.get(hash_token(token))
        return entry[0] if entry and entry[1] else None

    async def revoke_session(self, token: str, *, actor: str | None) -> None:
        key = hash_token(token)
        if key in self.sessions:
            self.sessions[key] = (self.sessions[key][0], False)
            self.events.append(f"logout:{actor}")
