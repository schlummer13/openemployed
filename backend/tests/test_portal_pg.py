"""Kundenportal gegen echtes PostgreSQL."""

from __future__ import annotations

import datetime as dt
import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.main import app
from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
TODAY = today_berlin()


def last_code() -> str:
    m = re.search(r"Anmeldecode: (\d{6})", app.state.mailer.sent[-1].subject)
    assert m
    return m.group(1)


async def login(e: Env, email: str) -> None:
    r = await e.api.post("/api/portal/login/request", json={"email": email})
    assert r.status_code == 200 and "dev_code" not in r.json()
    r = await e.api.post("/api/portal/login/verify", json={"email": email, "code": last_code()})
    assert r.status_code == 200, r.text


async def test_login_isolation_and_offer_acceptance(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, b2c = await setup_basics(env)
    other = (await api.post("/api/customers", json={**B2B, "company_name": "Andere GmbH", "email": "x@andere.de"})).json()["id"]
    own_inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{own_inv['id']}/issue")
    foreign = (await api.post("/api/invoices", json=draft(other))).json()
    await api.post(f"/api/invoices/{foreign['id']}/issue")
    offer = (await api.post("/api/offers", json={"customer_id": b2b, "title": "Wartung", "valid_until": (TODAY + dt.timedelta(days=10)).isoformat(),
                                                 "lines": [{"description": "Wartung", "quantity": "1", "unit_code": "LS",
                                                            "unit_price": "500.00", "tax_case": "OUT_DE_19"}]})).json()
    draft_offer = (await api.post("/api/offers", json={"customer_id": b2b, "title": "Geheim", "valid_until": (TODAY + dt.timedelta(days=10)).isoformat(),
                                                       "lines": [{"description": "x", "quantity": "1", "unit_price": "1.00"}]})).json()
    await api.post(f"/api/offers/{offer['id']}/send")

    # Unbekannte Adresse: gleiche Antwort, keine Mail
    sent_before = len(app.state.mailer.sent)
    r = await api.post("/api/portal/login/request", json={"email": "buchhaltung@hr1.de"})
    assert r.status_code == 200 and len(app.state.mailer.sent) == sent_before
    assert (await api.get("/api/portal/overview")).status_code == 401

    assert (await api.post(f"/api/customers/{b2b}/portal-access", json={"email": "Buchhaltung@HR1.de"})).status_code == 204
    await api.post("/api/portal/login/request", json={"email": "buchhaltung@hr1.de"})
    assert (await api.post("/api/portal/login/verify", json={"email": "buchhaltung@hr1.de", "code": "000000"})).status_code == 401
    await login(env, "buchhaltung@hr1.de")

    ov = (await api.get("/api/portal/overview")).json()
    assert [i["id"] for i in ov["invoices"]] == [own_inv["id"]]
    assert [o["id"] for o in ov["offers"]] == [offer["id"]], "Entwürfe sind im Portal unsichtbar"
    assert ov["customer"]["id"] == b2b and ov["invoices"][0]["status"] == "open"

    pdf = await api.get(f"/api/portal/invoices/{own_inv['id']}/document/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (await api.get(f"/api/portal/invoices/{foreign['id']}/document/pdf")).status_code == 404, "fremde Rechnung"
    assert (await api.get(f"/api/portal/offers/{draft_offer['id']}/pdf")).status_code == 404
    pay = (await api.get(f"/api/portal/invoices/{own_inv['id']}/payment")).json()
    assert pay["iban"] and pay["girocode"].startswith("data:image/svg+xml") and pay["reference"]

    assert (await api.post(f"/api/portal/offers/{offer['id']}/decision", json={"accept": True, "name": "Erika"})).status_code == 422
    d = await api.post(f"/api/portal/offers/{offer['id']}/decision", json={"accept": True, "name": "Erika Muster", "confirm": True})
    assert d.status_code == 200 and d.json()["status"] == "accepted"
    staff_view = (await api.get(f"/api/offers/{offer['id']}")).json()
    assert staff_view["decision_channel"] == "portal" and "Portal: buchhaltung@hr1.de" in staff_view["decision_note"]

    # Zugang entziehen → Sitzung sofort ungültig
    access = (await api.get(f"/api/customers/{b2b}/portal-access")).json()
    assert (await api.delete(f"/api/portal-access/{access[0]['id']}")).status_code == 204
    assert (await api.get("/api/portal/overview")).status_code == 401
    assert b2c


async def test_rate_limits_and_attempts(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    await api.post(f"/api/customers/{b2b}/portal-access", json={"email": "a@hr1.de"})
    await api.post("/api/portal/login/request", json={"email": "a@hr1.de"})
    code = last_code()
    for _ in range(5):
        await api.post("/api/portal/login/verify", json={"email": "a@hr1.de", "code": "999999" if code != "999999" else "111111"})
    r = await api.post("/api/portal/login/verify", json={"email": "a@hr1.de", "code": code})
    assert r.status_code == 401, "nach 5 Fehlversuchen ist der Code verbraucht"
    await api.post("/api/portal/login/request", json={"email": "a@hr1.de"})
    await api.post("/api/portal/login/request", json={"email": "a@hr1.de"})
    assert (await api.post("/api/portal/login/request", json={"email": "a@hr1.de"})).status_code == 429
    async with env.engine.connect() as conn:
        hashes = (await conn.execute(text("SELECT code_hash FROM portal_login_codes"))).scalars().all()
    assert all(h is None or (len(h) == 64 and not h.isdigit()) for h in hashes), "nur HMAC gespeichert"


async def test_public_termination_and_processing(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, b2c = await setup_basics(env)
    start = TODAY.replace(day=1).isoformat()
    c = (await api.post("/api/contracts", json={"customer_id": b2c, "title": "Privat-Abo", "start_date": start, "min_term_months": 0,
                                                "notice_months": 1, "renewal": "monthly", "lines": [
                                                    {"description": "Abo", "quantity": "1", "unit_code": "MON", "unit_price": "9.99"}]})).json()
    await api.post(f"/api/contracts/{c['id']}/activate")
    customer = (await api.get(f"/api/customers/{b2c}")).json()
    sent_before = len(app.state.mailer.sent)
    r = await api.post("/api/public/terminations", json={"name": "Max Muster", "email": "MAX@example.de",
                                                          "customer_number": customer["number"], "contract_reference": c["number"]})
    assert r.status_code == 201 and r.json()["confirmation_sent"] is True and r.json()["number"].startswith("KE-")
    mail = app.state.mailer.sent[sent_before]
    assert "Eingangsbestätigung" in mail.subject and "312k" in mail.text

    inbox = (await api.get("/api/terminations")).json()
    assert inbox[0]["contract_id"] == c["id"], "Vertrag automatisch zugeordnet (Kundennr. + E-Mail passen)"
    done = await api.post(f"/api/terminations/{inbox[0]['id']}/process", json={"accept": True})
    assert done.status_code == 200 and done.json()["effective_date"]
    assert (await api.post(f"/api/terminations/{inbox[0]['id']}/process", json={"accept": True})).status_code == 409
    contract = (await api.get(f"/api/contracts/{c['id']}")).json()
    assert contract["end_date"] == done.json()["effective_date"]

    # Falsche Zuordnungsdaten → Eingang trotzdem protokolliert, ohne Zuordnung
    r = await api.post("/api/public/terminations", json={"name": "Jemand", "email": "fremd@example.de", "customer_number": customer["number"]})
    assert r.status_code == 201
    assert (await api.get("/api/terminations")).json()[0]["customer_id"] is None
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("DELETE FROM contract_terminations"))
    assert b2b
