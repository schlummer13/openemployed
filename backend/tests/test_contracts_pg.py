"""Verträge und Abrechnungslauf gegen echtes PostgreSQL."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, B2C, COMPANY, Env, env  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

TODAY = today_berlin()
PREV_FIRST = (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)


def body(customer: str, **extra: object) -> dict:
    return {"customer_id": customer, "title": "HR-Software Miete", "start_date": PREV_FIRST.isoformat(),
            "min_term_months": 12, "notice_months": 3, "renewal": "monthly",
            "lines": [{"description": "HR-Software Basic", "quantity": "1", "unit_code": "MON", "unit_price": "1758.00",
                       "tax_case": "OUT_DE_19"}], **extra}


async def test_contract_lifecycle_and_billing(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    b2b = (await api.post("/api/customers", json=B2B)).json()["id"]
    b2c = (await api.post("/api/customers", json=B2C)).json()["id"]

    empty = (await api.get("/api/contracts")).json()
    assert empty == {"contracts": [], "mrr": "0.00"}, "MRR immer als Dezimal-String"

    # Verbraucherregeln
    r = await api.post("/api/contracts", json=body(b2c, min_term_months=24, notice_months=3))
    assert r.status_code == 422 and "Verbraucher" in r.json()["detail"]

    c = (await api.post("/api/contracts", json=body(b2b))).json()
    assert c["status"] == "draft" and c["number"].startswith("V-") and c["amount_net"] == "1758.00" and c["mrr"] == "0.00"
    c = (await api.post(f"/api/contracts/{c['id']}/activate")).json()
    assert c["status"] == "active" and c["mrr"] == "1758.00"
    assert (await api.put(f"/api/contracts/{c['id']}", json=body(b2b))).status_code == 409

    run1 = (await api.post("/api/contracts/run-billing")).json()
    assert run1["errors"] == [] and len(run1["created"]) == 2          # Vormonat + laufender Monat (Vorauszahlung)
    first, second = run1["created"]
    assert first["status"] == "draft", "erste Rechnung eines Vertrags zur Prüfung als Entwurf"
    assert second["status"] == "issued" and second["number"]
    inv = (await api.get(f"/api/invoices/{second['invoice_id']}")).json()
    assert inv["service_date_from"] == TODAY.replace(day=1).isoformat() and inv["total_net"] == "1758.00"
    assert "Vertrag" in inv["intro_text"]

    run2 = (await api.post("/api/contracts/run-billing")).json()
    assert run2["created"] == [], "Abrechnungslauf ist idempotent"

    detail = (await api.get(f"/api/contracts/{c['id']}")).json()
    assert len(detail["billings"]) == 2 and detail["term_end"]
    assert any(not s["billed"] for s in detail["schedule"])

    # Kündigung innerhalb der Mindestlaufzeit → Ende der Mindestlaufzeit
    t = (await api.post(f"/api/contracts/{c['id']}/terminate", json={"notice_received": TODAY.isoformat()})).json()
    assert t["end_date"] == detail["term_end"] == t["earliest_end"]
    assert (await api.post(f"/api/contracts/{c['id']}/terminate", json={"notice_received": TODAY.isoformat()})).status_code == 409

    listing = (await api.get("/api/contracts")).json()
    assert listing["mrr"] == "1758.00"


async def test_prorated_start(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    b2b = (await api.post("/api/customers", json=B2B)).json()["id"]
    start = PREV_FIRST.replace(day=16)
    c = (await api.post("/api/contracts", json=body(b2b, start_date=start.isoformat(), min_term_months=0))).json()
    await api.post(f"/api/contracts/{c['id']}/activate")
    run = (await api.post("/api/contracts/run-billing")).json()
    first = (await api.get(f"/api/invoices/{run['created'][0]['invoice_id']}")).json()
    assert first["service_date_from"] == start.isoformat()
    assert first["lines"][0]["quantity"] != "1.0000" and "Anteilige" in first["intro_text"]
