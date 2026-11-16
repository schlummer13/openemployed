"""Übersicht: Liquiditätsvorschau, Einrichtungs-Checkliste und zentrale Liste „Zu erledigen“."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


async def test_forecast_uses_receivables_and_balance(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    assert (await api.post("/api/bank/balance", json={"balance_on": inv["issue_date"], "amount": "10000.00"})).status_code in (200, 201, 204)
    await api.put("/api/finance/settings", json={"min_liquidity": "5000", "monthly_fixed_costs": "1000", "offer_followup_days": 7,
                                                 "offer_auto_reminder": False, "welcome_mail": True})
    f = (await api.get("/api/dashboard")).json()["forecast"]
    assert len(f["months"]) == 3 and Decimal(f["start"]) == Decimal("10000.00")
    total_receivables = sum(Decimal(m["receivables"]) for m in f["months"])
    assert total_receivables == Decimal(inv["total_gross"]), "offene Rechnung als erwarteter Zahlungseingang"
    assert all(Decimal(m["fixed_costs"]) == Decimal("1000.00") for m in f["months"])
    m0 = f["months"][0]
    assert Decimal(m0["closing"]) == Decimal(f["start"]) + Decimal(m0["inflow"]) - Decimal(m0["outflow"])
    assert Decimal(f["months"][2]["closing"]) == Decimal(f["lowest"]) or Decimal(f["lowest"]) <= Decimal(f["months"][2]["closing"])


async def test_setup_checklist_and_manual_flags(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    s = (await api.get("/api/setup")).json()
    items = {i["key"]: i for i in s["items"]}
    assert items["company"]["done"] is True, "Firmenprofil aus setup_basics ist vollständig"
    assert items["procedure_doc"]["done"] is False and items["procedure_doc"]["manual"] is True
    assert s["total"] == len(s["items"]) and 0 < s["done"] < s["total"]
    assert (await api.put("/api/setup/procedure_doc", json={"done": True})).status_code == 204
    items = {i["key"]: i for i in (await api.get("/api/setup")).json()["items"]}
    assert items["procedure_doc"]["done"] is True and items["procedure_doc"]["done_by"]
    assert (await api.put("/api/setup/procedure_doc", json={"done": False})).status_code == 204
    assert (await api.put("/api/setup/company", json={"done": True})).status_code == 404, "automatische Punkte nicht manuell setzbar"


async def test_inbox_lists_tasks(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    await api.post("/api/invoices", json=draft(b2b))
    pid = (await api.post("/api/projects", json={"name": "Onboarding"})).json()["id"]
    col = (await api.get(f"/api/projects/{pid}/board")).json()["columns"][1]["id"]
    await api.post(f"/api/projects/{pid}/tasks", json={"column_id": col, "title": "Zugänge", "priority": "dringend"})
    r = (await api.get("/api/inbox")).json()
    assert any(t["href"].startswith("/rechnungen") for t in r["tasks"]), "Rechnungsentwurf als offener Punkt"
    assert [t["title"] for t in r["project_tasks"]] == ["Zugänge"]
