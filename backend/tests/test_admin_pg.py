"""Rollen, Einladung mit Passwort und TOTP, API-Schlüssel, Einstellungen in der App und Webhooks."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from uuid import uuid4

import httpx
import pytest

from app.auth.passwords import totp
from app.auth.roles import allowed
from app.auth.store import PostgresAuthStore, User
from app.core.config import get_settings
from app.main import app
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import ORIGIN, Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


def test_role_policy() -> None:
    assert allowed("admin", "POST", "/api/admin/users")
    assert not allowed("accounting", "GET", "/api/admin/users") and allowed("accounting", "POST", "/api/invoices")
    assert allowed("advisor", "GET", "/api/accounting/journal") and not allowed("advisor", "POST", "/api/invoices")
    assert allowed("advisor", "POST", "/api/datev/export") and not allowed("advisor", "GET", "/api/admin/settings")
    assert allowed("employee", "POST", "/api/time") and not allowed("employee", "GET", "/api/invoices")


def client(token: str | None = None, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": ORIGIN, **headers},
                             cookies={"openemployed_session": token} if token else None)


async def test_users_roles_password_totp_and_api_keys(env: Env) -> None:  # noqa: F811
    s = get_settings()
    store = PostgresAuthStore(env.engine, tenant_id=s.tenant_id, max_age=dt.timedelta(hours=8), idle=dt.timedelta(hours=2))
    app.state.auth_store = store
    admin = await store.upsert_identity("google", "google:1", "anna@beispiel.de", "Anna", None)
    assert admin is not None and admin.role == "admin", "erster Benutzer = Geschäftsführung"
    api = client(await store.create_session(admin, ip=None, user_agent=None))

    inv = (await api.post("/api/admin/users", json={"email": "Stb@Kanzlei.de", "name": "Kanzlei", "role": "advisor"})).json()
    assert inv["mailed"] is True
    token = re.search(r"token=([\w-]+)", app.state.mailer.sent[-1].text).group(1)  # type: ignore[union-attr]
    pub = client()
    assert (await pub.get(f"/api/auth/invite/{token}")).json()["role_label"].startswith("Steuerberater")
    assert (await pub.post(f"/api/auth/invite/{token}", json={"password": "kurz"})).status_code == 422
    acc = await pub.post(f"/api/auth/invite/{token}", json={"password": "Sehr-sicheres-Passwort-2026"})
    assert acc.status_code == 200
    stb = client(acc.cookies["openemployed_session"])
    me = (await stb.get("/api/auth/me")).json()
    assert me["role"] == "advisor" and me["has_password"]
    assert (await stb.get("/api/customers")).status_code == 200, "lesen erlaubt"
    assert (await stb.post("/api/customers", json={})).status_code == 403, "schreiben verboten"
    assert (await stb.get("/api/admin/users")).status_code == 403

    # Passwort-Anmeldung (nur wenn aktiviert) mit Sperre und 2FA
    assert (await pub.post("/api/auth/password/login", json={"email": "stb@kanzlei.de", "password": "x"})).status_code == 403
    saved = await api.put("/api/admin/settings", json={"changes": {"auth_password_enabled": True}})
    assert saved.status_code == 200 and next(f for f in saved.json()["fields"] if f["key"] == "auth_password_enabled")["value"] is True
    try:
        assert (await pub.post("/api/auth/password/login", json={"email": "stb@kanzlei.de", "password": "falsch"})).status_code == 401
        start = (await stb.post("/api/auth/totp/start")).json()
        assert start["qr"].startswith("data:image/svg+xml") and "otpauth://totp/" in start["uri"]
        assert (await stb.post("/api/auth/totp/confirm", json={"code": "000000"})).status_code == 409
        assert (await stb.post("/api/auth/totp/confirm", json={"code": totp(start["secret"])})).status_code == 204
        step1 = (await pub.post("/api/auth/password/login", json={"email": "stb@kanzlei.de", "password": "Sehr-sicheres-Passwort-2026"})).json()
        assert step1["status"] == "totp_required"
        ok = await pub.post("/api/auth/password/login", json={"email": "stb@kanzlei.de", "password": "Sehr-sicheres-Passwort-2026",
                                                              "totp": totp(start["secret"])})
        assert ok.status_code == 200 and ok.cookies.get("openemployed_session")
        # Einstellungen werden vor dem Speichern geprüft
        bad = await api.put("/api/admin/settings", json={"changes": {"mail_provider": "microsoft", "mail_auth_mode": "service_account"}})
        assert bad.status_code == 409 and "service_account" in bad.text
    finally:
        await api.put("/api/admin/settings", json={"changes": {"auth_password_enabled": None}})
    assert get_settings().auth_password_enabled is False

    # Letzter Admin bleibt Admin; Sperren beendet Sitzungen
    users = (await api.get("/api/admin/users")).json()["users"]
    me_id = next(u["id"] for u in users if u["email"] == "anna@beispiel.de")
    stb_id = next(u["id"] for u in users if u["email"] == "stb@kanzlei.de")
    assert (await api.put(f"/api/admin/users/{me_id}", json={"role": "accounting", "active": True})).status_code == 409
    assert (await api.put(f"/api/admin/users/{stb_id}", json={"role": "advisor", "active": False})).status_code == 204
    assert (await stb.get("/api/customers")).status_code == 401

    # API-Schlüssel: nur Hash gespeichert, ohne Origin nutzbar, widerrufbar
    key = (await api.post("/api/admin/api-keys", json={"name": "n8n", "role": "advisor"})).json()["key"]
    bot = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Authorization": f"Bearer {key}"})
    assert (await bot.get("/api/customers")).status_code == 200
    assert (await bot.post("/api/customers", json={})).status_code == 403
    kid = (await api.get("/api/admin/api-keys")).json()[0]["id"]
    assert (await api.delete(f"/api/admin/api-keys/{kid}")).status_code == 204
    assert (await bot.get("/api/customers")).status_code == 401


async def test_webhook_signed_delivery(env: Env) -> None:  # noqa: F811
    from app.services import webhooks

    api = env.api
    await setup_basics(env)
    bad = await api.post("/api/admin/webhooks", json={"url": "ftp://x.de/hook", "events": ["customer.created"]})
    assert bad.status_code == 409
    created = (await api.post("/api/admin/webhooks", json={"url": "https://hooks.example.org/sl", "events": ["customer.created"]})).json()
    secret = created["secret"]
    await api.post("/api/customers", json={"customer_type": "b2b", "company_name": "Hook GmbH", "email": "a@hook.de", "street": "Weg 1",
                                           "postal_code": "40210", "city": "Düsseldorf"})
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(204 if len(seen) > 1 else 500)

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await webhooks.deliver_due(env.engine, mock))["delivered"] == 0
    from sqlalchemy import text

    async with env.engine.begin() as conn:
        await conn.execute(text("UPDATE webhook_deliveries SET next_attempt = now()"))
    assert (await webhooks.deliver_due(env.engine, mock))["delivered"] == 1
    req = seen[-1]
    body = json.loads(req.content)
    assert body["event"] == "customer.created" and body["data"]["company_name"] == "Hook GmbH"
    ts, mac = re.match(r"t=(\d+),v1=([0-9a-f]+)", req.headers["x-openemployed-signature"]).groups()  # type: ignore[union-attr]
    assert hmac.compare_digest(mac, hmac.new(secret.encode(), f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest())
    log = (await api.get("/api/admin/webhooks")).json()["deliveries"]
    assert log[0]["status"] == "delivered" and log[0]["attempts"] == 2
    assert uuid4() and User  # Importe genutzt


async def test_custom_role_permissions(env: Env) -> None:  # noqa: F811
    s = get_settings()
    store = PostgresAuthStore(env.engine, tenant_id=s.tenant_id, max_age=dt.timedelta(hours=8), idle=dt.timedelta(hours=2))
    app.state.auth_store = store
    from app.services import roles

    roles.invalidate()
    admin = await store.upsert_identity("google", "google:1", "anna@beispiel.de", "Anna", None)
    bob = await store.upsert_identity("google", "google:2", "bob@beispiel.de", "Bob", None)
    assert admin and bob
    api = client(await store.create_session(admin, ip=None, user_agent=None))
    perms = {"sales": "write", "invoices": "read"}
    assert (await api.put("/api/admin/roles/vertrieb", json={"label": "Vertrieb", "permissions": perms})).status_code == 204
    assert (await api.put("/api/admin/roles/admin", json={"label": "x", "permissions": perms})).status_code == 409
    r = (await api.get("/api/admin/roles")).json()
    assert any(x["key"] == "vertrieb" and x["permissions"]["sales"] == "write" and x["permissions"]["payroll"] == "none" for x in r["roles"])
    assert (await api.put(f"/api/admin/users/{bob.id}", json={"role": "vertrieb", "active": True})).status_code == 204
    b = client(await store.create_session(bob, ip=None, user_agent=None))
    me = (await b.get("/api/auth/me")).json()
    assert me["role_label"] == "Vertrieb" and me["permissions"]["sales"] == "write"
    assert (await b.get("/api/customers")).status_code == 200
    assert (await b.get("/api/invoices")).status_code == 200, "lesen"
    assert (await b.post("/api/invoices", json={})).status_code == 403, "nicht bearbeiten"
    assert (await b.get("/api/payroll/employees")).status_code == 403
    assert (await b.get("/api/help")).status_code == 200, "Handbuch für alle"
    assert (await api.delete("/api/admin/roles/vertrieb")).status_code == 409, "noch vergeben"
