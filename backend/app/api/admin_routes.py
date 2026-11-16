"""Verwaltung (nur Geschäftsführung): Benutzer und Rollen, Einstellungen, API-Schlüssel, Webhooks, System und Datensicherung."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.services import api_keys, events, roles, settings_store, system, users, webhooks

router = APIRouter(prefix="/api/admin", tags=["verwaltung"])
RoleName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")]


# ───────── Benutzer ─────────

@router.get("/users")
async def list_users(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        rs = await roles.list_roles(conn)
        return {"users": await users.list_users(conn, tenant()), "roles": {r["key"]: r["label"] for r in rs}}
    return money_json(await run(request, fn))


@router.get("/roles")
async def list_roles(request: Request, _u: CurrentUser) -> Any:
    from app.auth.roles import AREAS

    return {"roles": await run(request, roles.list_roles),
            "areas": [{"key": k, "label": v[0], "description": v[1]} for k, v in AREAS.items()]}


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    permissions: dict[str, Literal["none", "read", "write"]]


@router.put("/roles/{key}", status_code=204)
async def save_role(request: Request, key: str, data: RoleIn, user: CurrentUser) -> None:
    await run(request, lambda conn: roles.save(conn, key, data.label, data.description, data.permissions, user.email, tenant()))


@router.delete("/roles/{key}", status_code=204)
async def delete_role(request: Request, key: str, user: CurrentUser) -> None:
    await run(request, lambda conn: roles.delete(conn, key, user.email, tenant()))


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=100)
    role: RoleName


@router.post("/users", status_code=201)
async def invite(request: Request, data: InviteIn, user: CurrentUser) -> Any:
    mailer = getattr(request.app.state, "mailer", None)
    out = await run(request, lambda conn: users.invite(conn, mailer, tenant(), data.email, data.name, data.role, user.email,
                                                       get_settings().app_origin))
    return money_json(out)


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: RoleName
    active: bool


@router.put("/users/{user_id}", status_code=204)
async def update_user(request: Request, user_id: UUID, data: UserIn, user: CurrentUser) -> None:
    await run(request, lambda conn: users.update(conn, tenant(), user_id, role=data.role, active=data.active, actor=user.email,
                                                 actor_id=user.id))


@router.post("/users/{user_id}/reset-2fa", status_code=204)
async def reset_2fa(request: Request, user_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: users.reset_2fa(conn, tenant(), user_id, user.email))


# ───────── Einstellungen ─────────

@router.get("/settings")
async def get_settings_(request: Request, _u: CurrentUser) -> Any:
    out = await run(request, lambda conn: settings_store.overview(conn, tenant()))
    s = get_settings()
    out["redirect_uris"] = {p: s.oidc_redirect_uri(p) for p in ("google", "microsoft", "oidc")}
    return out


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] = Field(max_length=60)


@router.put("/settings")
async def save_settings(request: Request, data: SettingsIn, user: CurrentUser) -> Any:
    out = await run(request, lambda conn: settings_store.save(conn, tenant(), data.changes, user.email))
    if any(k.startswith(("mail_", "ms_", "smtp_", "imap_")) for k in data.changes):
        # Mailer mit den neuen Zugangsdaten neu aufbauen
        from app.main import build_mailer
        from app.services.archive import ArchivingMailer

        mailer = build_mailer(get_settings())
        request.app.state.mailer = ArchivingMailer(mailer, request.app.state.engine, get_settings().tenant_id) if mailer else None
    return out


# ───────── API-Schlüssel ─────────

@router.get("/api-keys")
async def keys(request: Request, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: api_keys.list_keys(conn, tenant())))


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    role: Literal["accounting", "advisor"] = "advisor"
    days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/api-keys", status_code=201)
async def create_key(request: Request, data: KeyIn, user: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: api_keys.create(conn, tenant(), data.name, data.role, data.days, user.email)))


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_key(request: Request, key_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: api_keys.revoke(conn, tenant(), key_id, user.email))


# ───────── Webhooks ─────────

@router.get("/webhooks")
async def list_webhooks(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return {"endpoints": await webhooks.endpoints(conn, tenant()), "deliveries": await webhooks.deliveries(conn, tenant()),
                "events": events.PUBLIC}
    return money_json(await run(request, fn))


class WebhookIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=10, max_length=500)
    events: list[str] = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)


@router.post("/webhooks", status_code=201)
async def create_webhook(request: Request, data: WebhookIn, user: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: webhooks.create(conn, tenant(), data.url, data.events, data.description, user.email)))


class ActiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


@router.put("/webhooks/{wid}", status_code=204)
async def toggle_webhook(request: Request, wid: UUID, data: ActiveIn, user: CurrentUser) -> None:
    await run(request, lambda conn: webhooks.set_active(conn, tenant(), wid, data.active, user.email))


@router.delete("/webhooks/{wid}", status_code=204)
async def delete_webhook(request: Request, wid: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: webhooks.delete(conn, tenant(), wid, user.email))


@router.post("/webhooks/{wid}/test", status_code=202)
async def test_webhook(request: Request, wid: UUID, _u: CurrentUser) -> Any:
    await run(request, lambda conn: webhooks.send_test(conn, tenant(), wid))
    return await webhooks.deliver_due(request.app.state.engine)


# ───────── System und Datensicherung ─────────

@router.get("/system")
async def system_info(_u: CurrentUser) -> Any:
    return money_json({**system.info(), "backups": system.backups()})


@router.post("/system/backup", status_code=202)
async def backup_now(_u: CurrentUser) -> Any:
    try:
        system.request_backup()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Sicherungsverzeichnis ist nicht eingebunden (docker-compose: /backups)") from exc
    return {"requested": True}


@router.get("/system/backups/{name}")
async def download_backup(name: str, user: CurrentUser) -> FileResponse:
    try:
        path = system.backup_file(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Sicherung nicht gefunden") from exc
    import logging

    logging.getLogger("openemployed.system").warning("Sicherung %s heruntergeladen von %s", name, user.email)
    return FileResponse(path, media_type="application/octet-stream", filename=name)
