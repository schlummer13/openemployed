"""Budgetplanung: Bereiche, Zuordnung gebuchter Ausgaben, Auslastung, Stornos, Vorschläge."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
TODAY = today_berlin()


async def booked(api: Any, supplier: str, net: str, category: str, n: int) -> str:
    up = (await api.post("/api/receipt-files", files={"file": (f"{n}.png", b"\x89PNG\r\n\x1a\n" + bytes([n]) * 40, "image/png")})).json()
    r = (await api.post("/api/receipts", json={"supplier_id": supplier, "file_id": up["id"], "receipt_date": TODAY.isoformat(),
                                               "invoice_number": f"B-{n}", "description": f"Ausgabe {n}", "category": category,
                                               "tax_case": "IN_DE_19", "net_amount": net,
                                               "tax_amount": str((Decimal(net) * Decimal("0.19")).quantize(Decimal("0.01")))})).json()
    assert (await api.post(f"/api/receipts/{r['id']}/book")).status_code == 200
    return str(r["id"])


async def test_budget_planning(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    marketing = (await api.post("/api/budgets", json={"name": "Marketing", "monthly_budget": "2500.00", "color": "#ef4444",
                                                       "categories": ["advertising"]})).json()["id"]
    office = (await api.post("/api/budgets", json={"name": "Büro", "monthly_budget": "1000.00", "categories": ["office"]})).json()["id"]
    assert (await api.post("/api/budgets", json={"name": "marketing", "monthly_budget": "1.00"})).status_code == 409, "Name eindeutig"
    assert (await api.post("/api/budgets", json={"name": "X", "monthly_budget": 10.5})).status_code == 422, "kein Float"
    assert (await api.post("/api/budgets", json={"name": "Y", "monthly_budget": "1.00", "categories": ["gibtsnicht"]})).status_code == 409

    supplier = (await api.post("/api/suppliers", json={"name": "Werbeagentur GmbH"})).json()["id"]
    ad1 = await booked(api, supplier, "1800.00", "advertising", 1)
    ad2 = await booked(api, supplier, "900.00", "advertising", 2)
    paper = await booked(api, supplier, "120.00", "office", 3)

    unassigned = (await api.get("/api/budgets/expenses?unassigned=true")).json()
    assert {e["id"] for e in unassigned} == {ad1, ad2, paper}
    assert {e["id"]: e["suggested_area_name"] for e in unassigned}[ad1] == "Marketing"

    assert (await api.post("/api/budgets/assign", json={"area_id": marketing, "receipt_ids": [ad1]})).json() == {"assigned": 1}
    applied = (await api.post("/api/budgets/apply-suggestions")).json()
    assert applied == {"assigned": 2}, "Kategorie-Regeln ordnen den Rest zu"

    ov = (await api.get("/api/budgets")).json()
    areas = {a["name"]: a for a in ov["areas"]}
    m = areas["Marketing"]
    assert Decimal(m["month_spent"]) == Decimal("2700.00") and m["over_budget"] is True and Decimal(m["month_remaining"]) == Decimal("-200.00")
    assert Decimal(areas["Büro"]["month_spent"]) == Decimal("120.00") and Decimal(areas["Büro"]["month_usage"]) == Decimal("12.0")
    assert Decimal(ov["totals"]["monthly_budget"]) == Decimal("3500.00") and Decimal(ov["unassigned_spent"]) == 0
    dash = (await api.get("/api/dashboard")).json()
    assert any("Budget „Marketing“ überschritten" in t["title"] for t in dash["tasks"])

    # Storno mindert den Bereich des Originalbelegs; Umbuchen und Lösen der Zuordnung
    await api.post(f"/api/receipts/{ad2}/reverse", json={"reason": "Doppelt erfasst"})
    m = {a["name"]: a for a in (await api.get("/api/budgets")).json()["areas"]}["Marketing"]
    assert Decimal(m["month_spent"]) == Decimal("1800.00") and m["over_budget"] is False
    await api.post("/api/budgets/assign", json={"area_id": office, "receipt_ids": [ad1]})
    areas = {a["name"]: a for a in (await api.get("/api/budgets")).json()["areas"]}
    assert Decimal(areas["Büro"]["month_spent"]) == Decimal("1920.00")
    await api.post("/api/budgets/assign", json={"area_id": None, "receipt_ids": [ad1]})
    assert Decimal((await api.get("/api/budgets")).json()["unassigned_spent"]) == Decimal("1800.00")

    # Archivieren löst Zuordnungen
    assert (await api.delete(f"/api/budgets/{office}")).status_code == 204
    assert [a["name"] for a in (await api.get("/api/budgets")).json()["areas"]] == ["Marketing"]
    assert paper in {e["id"] for e in (await api.get("/api/budgets/expenses?unassigned=true")).json()}
