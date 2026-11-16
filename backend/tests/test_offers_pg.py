"""Angebote gegen echtes PostgreSQL: Unveränderlichkeit, Versionen, Annahme, Übernahme."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, B2C, COMPANY, Env, env  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

TODAY = today_berlin()
VALID = (TODAY + dt.timedelta(days=30)).isoformat()


def body(customer: str, **extra: object) -> dict:
    return {"customer_id": customer, "title": "HR-Software inkl. Einrichtung", "valid_until": VALID,
            "intro_text": "Vielen Dank für Ihre Anfrage.",
            "lines": [
                {"description": "Einrichtung", "quantity": "8", "unit_code": "HUR", "unit_price": "95.00", "tax_case": "OUT_DE_19"},
                {"description": "HR-Software Basic", "quantity": "1", "unit_code": "MON", "unit_price": "149.00",
                 "tax_case": "OUT_DE_19", "recurring": True},
            ], **extra}


async def test_offer_lifecycle_and_conversion(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    b2b = (await api.post("/api/customers", json=B2B)).json()["id"]

    r = await api.post("/api/offers", json=body(b2b, valid_until=(TODAY - dt.timedelta(days=1)).isoformat()))
    assert r.status_code == 422
    r = await api.post("/api/offers", json=body(b2b, lines=[{"description": "x", "quantity": 1.5, "unit_price": "1"}]))
    assert r.status_code == 422, "Gleitkommazahlen werden abgelehnt"

    o = (await api.post("/api/offers", json=body(b2b))).json()
    assert o["status"] == "draft" and o["number"].startswith(f"AN-{TODAY.year}-") and o["price_mode"] == "net"
    assert o["total_net"] == "909.00" and o["total_gross"] == "1081.71"
    assert [ln["recurring"] for ln in o["lines"]] == [False, True]

    o = (await api.put(f"/api/offers/{o['id']}", json=body(b2b, title="Neu"))).json()
    assert o["title"] == "Neu"
    pdf = await api.get(f"/api/offers/{o['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    # Annahme vor Versand nicht möglich
    assert (await api.post(f"/api/offers/{o['id']}/decision", json={"accept": True, "name": "Erika"})).status_code == 409
    o = (await api.post(f"/api/offers/{o['id']}/send")).json()
    assert o["status"] == "sent" and o["sent_at"]
    assert (await api.post(f"/api/offers/{o['id']}/send")).status_code == 409
    assert (await api.put(f"/api/offers/{o['id']}", json=body(b2b))).status_code == 409
    assert (await api.delete(f"/api/offers/{o['id']}")).status_code == 409

    # Datenbank verhindert auch direkte Änderungen
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE offers SET title = 'manipuliert' WHERE id = :id"), {"id": o["id"]})
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE offer_lines SET unit_price = 1 WHERE offer_id = :id"), {"id": o["id"]})

    assert (await api.post(f"/api/offers/{o['id']}/convert", json={})).status_code == 409, "erst nach Annahme"
    o = (await api.post(f"/api/offers/{o['id']}/decision", json={"accept": True, "name": "Erika Muster", "note": "per Mail"})).json()
    assert o["status"] == "accepted" and o["decided_by_name"] == "Erika Muster" and o["decision_channel"] == "manual"
    assert (await api.post(f"/api/offers/{o['id']}/decision", json={"accept": False, "name": "x y"})).status_code == 409

    start = (TODAY.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    o = (await api.post(f"/api/offers/{o['id']}/convert", json={"contract_start": start.isoformat()})).json()
    assert o["invoice_id"] and o["contract_id"]
    assert (await api.post(f"/api/offers/{o['id']}/convert", json={})).status_code == 409, "nur einmal übernehmbar"

    invoice = (await api.get(f"/api/invoices/{o['invoice_id']}")).json()
    assert invoice["status"] == "draft" and len(invoice["lines"]) == 1 and invoice["lines"][0]["unit_code"] == "HUR" and o["number"] in invoice["intro_text"]
    contract = (await api.get(f"/api/contracts/{o['contract_id']}")).json()
    assert contract["status"] == "draft" and contract["start_date"] == start.isoformat() and contract["amount_net"] == "149.00"


async def test_revision_and_decline_b2c(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    b2c = (await api.post("/api/customers", json=B2C)).json()["id"]
    o = (await api.post("/api/offers", json=body(b2c, lines=[
        {"description": "Website", "quantity": "1", "unit_code": "LS", "unit_price": "1190.00", "tax_case": "OUT_DE_19"}]))).json()
    assert o["price_mode"] == "gross" and o["total_gross"] == "1190.00" and o["total_net"] == "1000.00"
    await api.post(f"/api/offers/{o['id']}/send")

    later = (TODAY + dt.timedelta(days=60)).isoformat()
    v2 = (await api.post(f"/api/offers/{o['id']}/revision", json={"valid_until": later})).json()
    assert v2["status"] == "draft" and v2["revision_of"] == o["id"] and v2["number"] != o["number"]
    assert (await api.get(f"/api/offers/{o['id']}")).json()["status"] == "superseded"
    assert (await api.post(f"/api/offers/{o['id']}/decision", json={"accept": True, "name": "Max"})).status_code == 409

    await api.post(f"/api/offers/{v2['id']}/send")
    d = (await api.post(f"/api/offers/{v2['id']}/decision", json={"accept": False, "name": "Max Muster", "note": "zu teuer"})).json()
    assert d["status"] == "declined"
    v3 = (await api.post(f"/api/offers/{v2['id']}/revision", json={"valid_until": later})).json()
    assert v3["revision_of"] == v2["id"]
    assert (await api.get(f"/api/offers/{v2['id']}")).json()["status"] == "declined", "abgelehnt bleibt abgelehnt"

    # Entwurf löschen
    assert (await api.delete(f"/api/offers/{v3['id']}")).status_code == 204
    listing = (await api.get("/api/offers")).json()
    assert {x["id"] for x in listing} == {o["id"], v2["id"]}


async def test_send_offer_by_email(env: Env) -> None:  # noqa: F811
    from app.main import app

    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    b2b = (await api.post("/api/customers?welcome=false", json=B2B)).json()["id"]
    o = (await api.post("/api/offers", json=body(b2b))).json()
    before = len(app.state.mailer.sent)
    r = await api.post(f"/api/offers/{o['id']}/send", json={"email": True})
    assert r.status_code == 200 and r.json()["emailed"] is True and r.json()["status"] == "sent"
    mail = app.state.mailer.sent[before]
    assert mail.subject.startswith(f"Angebot {o['number']}") and mail.attachments[0].filename.endswith(".pdf")
    assert "Antwort auf diese E-Mail" in mail.text, "ohne Portalzugang kein Portal-Link"
