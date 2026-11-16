"""Mehrere Firmen: Anlegen, Wechseln, strikte Datentrennung, Zugriff je Firma mit eigener Rolle."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.auth.store import PostgresAuthStore
from app.core.config import get_settings
from app.main import app
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, ORIGIN, Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


def client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": ORIGIN},
                             cookies={"openemployed_session": token})


async def test_company_switch_and_isolation(env: Env) -> None:  # noqa: F811
    await setup_basics(env)
    s = get_settings()
    store = PostgresAuthStore(env.engine, tenant_id=s.tenant_id, max_age=dt.timedelta(hours=8), idle=dt.timedelta(hours=2))
    app.state.auth_store = store
    admin = await store.upsert_identity("google", "google:1", "anna@beispiel.de", "Anna", None)
    assert admin is not None
    token = await store.create_session(admin, ip=None, user_agent=None)
    api = client(token)

    def use(company: str) -> None:
        api.cookies = httpx.Cookies({"openemployed_session": token, "openemployed_company": company})
    from app.services.companies import ensure_home

    await ensure_home(env.engine, s.tenant_id)
    assert (await api.post("/api/customers", json=B2B)).status_code == 201
    home_customers = (await api.get("/api/customers")).json()

    new = (await api.post("/api/companies", json={"name": "Tochter UG"})).json()["id"]
    mine = (await api.get("/api/companies")).json()
    assert {c["name"] for c in mine["companies"]} >= {"Tochter UG"} and mine["current"] == str(s.tenant_id)
    sel = await api.post("/api/companies/select", json={"company_id": new})
    assert sel.status_code == 200
    use(sel.cookies["openemployed_company"])
    assert (await api.get("/api/companies")).json()["current"] == new
    assert (await api.get("/api/customers")).json() == [], "andere Firma sieht keine Kunden der Stammfirma"
    assert (await api.post("/api/customers", json={**B2B, "company_name": "Kunde der Tochter"})).status_code == 201
    assert [c["display_name"] for c in (await api.get("/api/customers")).json()] == ["Kunde der Tochter"]
    use(str(s.tenant_id))
    assert len((await api.get("/api/customers")).json()) == len(home_customers)

    # Zweiter Benutzer: nur Stammfirma, kein Zugriff auf die Tochter (Cookie wird ignoriert)
    bob = await store.upsert_identity("google", "google:2", "bob@beispiel.de", "Bob", None)
    assert bob is not None
    btoken = await store.create_session(bob, ip=None, user_agent=None)
    b = client(btoken)
    b.cookies = httpx.Cookies({"openemployed_session": btoken, "openemployed_company": new})
    assert (await b.post("/api/companies/select", json={"company_id": new})).status_code == 403
    assert all(c["display_name"] != "Kunde der Tochter" for c in (await b.get("/api/customers")).json())
    # Zugriff als Steuerberater in der Tochter
    use(new)
    assert (await api.put("/api/companies/members", json={"email": "bob@beispiel.de", "role": "advisor"})).status_code == 204
    assert [c["display_name"] for c in (await b.get("/api/customers")).json()] == ["Kunde der Tochter"]
    assert (await b.post("/api/customers", json=B2B)).status_code == 403, "dort nur lesen"
