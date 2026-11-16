"""Zeiterfassung mit Abrechnung und Reisekosten mit Buchung."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


async def test_time_tracking_and_billing(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    anna = (await api.post("/api/shareholders", json={"name": "Anna", "share_percent": "100"})).json()["id"]
    pid = (await api.post("/api/projects", json={"name": "Schnittstelle", "customer_id": b2b})).json()["id"]
    day = TODAY.isoformat()
    ids = [(await api.post("/api/time", json={"project_id": pid, "shareholder_id": anna, "work_date": day, "minutes": m,
                                              "description": d})).json()["id"] for m, d in ((90, "Konzept"), (150, "Umsetzung"))]
    await api.post("/api/time", json={"project_id": pid, "work_date": day, "minutes": 30, "description": "intern", "billable": False})
    t = (await api.get("/api/time", params={"project_id": pid})).json()
    assert t["total_minutes"] == 270 and t["open_minutes"] == 240

    no_rate = await api.post(f"/api/projects/{pid}/bill", json={"entry_ids": ids})
    assert no_rate.status_code == 409 and "Stundensatz" in no_rate.text
    assert (await api.put(f"/api/projects/{pid}/billing", json={"hourly_rate": "95.00", "customer_id": b2b})).status_code == 204
    assert Decimal((await api.get("/api/time", params={"project_id": pid})).json()["open_value"]) == Decimal("380.00")
    r = (await api.post(f"/api/projects/{pid}/bill", json={"entry_ids": ids})).json()
    assert r["entries"] == 2 and Decimal(r["hours"]) == Decimal("4.00")
    inv = (await api.get(f"/api/invoices/{r['invoice_id']}")).json()
    assert inv["status"] == "draft" and len(inv["lines"]) == 2 and inv["lines"][0]["unit_code"] == "HUR"
    assert Decimal(inv["lines"][0]["quantity"]) == Decimal("1.5") and "Konzept" in inv["lines"][0]["description"]
    assert (await api.get("/api/time", params={"project_id": pid, "open_only": True})).json()["entries"] == []
    assert (await api.post(f"/api/projects/{pid}/bill", json={"entry_ids": ids})).status_code == 409, "nicht doppelt"
    assert (await api.delete(f"/api/time/{ids[0]}")).status_code == 409, "abgerechnete Zeit bleibt"
    # Entwurf gelöscht → Zeiten wieder offen
    assert (await api.delete(f"/api/invoices/{r['invoice_id']}")).status_code == 204
    assert (await api.get("/api/time", params={"project_id": pid})).json()["open_minutes"] == 240


async def test_travel_expense_booking(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    ben = (await api.post("/api/shareholders", json={"name": "Ben", "share_percent": "100"})).json()["id"]
    trip = {"shareholder_id": ben, "purpose": "Kundentermin", "destination": "Berlin", "start_date": "2026-11-10", "start_time": "15:00",
            "end_date": "2026-11-12", "end_time": "11:00", "km": "0", "breakfasts": 2, "dinners": 1, "other_costs": "12.50"}
    p = (await api.post("/api/travel/preview", json=trip)).json()
    assert Decimal(p["meals_amount"]) == Decimal("33.60") and Decimal(p["total"]) == Decimal("46.10")
    tid = (await api.post("/api/travel", json=trip)).json()["id"]
    assert (await api.put(f"/api/travel/{tid}", json={**trip, "km": "600"})).status_code == 204
    row = (await api.get("/api/travel", params={"year": 2026})).json()["trips"][0]
    assert Decimal(row["km_amount"]) == Decimal("180.00") and Decimal(row["total"]) == Decimal("226.10") and row["number"] == 1
    assert (await api.post(f"/api/travel/{tid}/paid", json={"paid_on": "2026-11-15"})).status_code == 409, "erst buchen"
    assert (await api.post(f"/api/travel/{tid}/book")).status_code == 204
    assert (await api.put(f"/api/travel/{tid}", json=trip)).status_code == 409
    assert (await api.post(f"/api/travel/{tid}/paid", json={"paid_on": "2026-11-15"})).status_code == 204
    entries = (await api.get("/api/accounting/journal", params={"start": "2026-11-01", "end": "2026-11-30"})).json()["entries"]
    booking = next(e for e in entries if e["source_type"] == "travel")
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in booking["lines"]} == {
        ("6664", "33.60", "0.00"), ("6668", "180.00", "0.00"), ("6650", "12.50", "0.00"), ("3500", "0.00", "226.10")}
    payment = next(e for e in entries if e["source_type"] == "travel_payment")
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in payment["lines"]} == {("3500", "226.10", "0.00"), ("1800", "0.00", "226.10")}
    bad = await api.post("/api/travel", json={**trip, "end_date": "2026-11-09"})
    assert bad.status_code in (409, 422)
