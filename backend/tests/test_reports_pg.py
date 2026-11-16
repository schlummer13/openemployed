"""BWA, Zusammenfassende Meldung, Steuervorauszahlungen und Monatsabschluss."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.services.reports import bwa_group
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal


def test_bwa_groups() -> None:
    assert bwa_group("SKR04", "4400", "revenue") == "revenue" and bwa_group("SKR04", "6020", "expense") == "personnel"
    assert bwa_group("SKR04", "6815", "expense") == "other_costs" and bwa_group("SKR04", "7610", "expense") == "income_taxes"
    assert bwa_group("SKR03", "8400", "revenue") == "revenue" and bwa_group("SKR03", "4320", "expense") == "income_taxes"
    assert bwa_group("SKR03", "4830", "expense") == "depreciation" and bwa_group("SKR03", "4210", "expense") == "rooms"


async def test_bwa_prepayments_and_month_close(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b, price="1000.00"))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    period = (await api.get(f"/api/invoices/{inv['id']}")).json()["issue_date"][:7]
    await api.get("/api/accounting/journal")
    b = (await api.get(f"/api/reports/bwa/{period}")).json()
    rows = {r["key"]: r for r in b["rows"]}
    assert D(rows["revenue"]["month"]) == D("1000.00") and D(rows["total_output"]["month"]) == D("1000.00")
    assert D(rows["result"]["ytd"]) == D("1000.00") and D(rows["revenue"]["prev_month"]) == 0 and len(b["trend"]) == 12

    # Vorauszahlungen laut Bescheid
    year = TODAY.year
    assert (await api.put("/api/reports/prepayments/schedule", json={"tax": "kst", "year": year, "quarterly": "1500.00",
                                                                       "soli": "82.50"})).status_code == 204
    assert (await api.put("/api/reports/prepayments/schedule", json={"tax": "gewst", "year": year, "quarterly": "900.00"})).status_code == 204
    pp = (await api.get(f"/api/reports/prepayments/{year}")).json()
    assert len(pp["items"]) == 8 and {i["due_date"][5:7] for i in pp["items"] if i["tax"] == "kst"} == {"03", "06", "09", "12"}
    first = next(i for i in pp["items"] if i["tax"] == "kst")
    assert (await api.post(f"/api/reports/prepayments/{first['id']}/paid", json={"paid_on": TODAY.isoformat()})).status_code == 204
    entries = (await api.get("/api/accounting/journal", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()})).json()["entries"]
    tp = next(e for e in entries if e["source_type"] == "tax_payment")
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in tp["lines"]} == {("7600", "1500.00", "0.00"), ("7608", "82.50", "0.00"),
                                                                                  ("1800", "0.00", "1582.50")}
    # Neuer Bescheid ersetzt nur offene Termine
    await api.put("/api/reports/prepayments/schedule", json={"tax": "kst", "year": year, "quarterly": "2000.00", "soli": "110"})
    kst = [i for i in (await api.get(f"/api/reports/prepayments/{year}")).json()["items"] if i["tax"] == "kst"]
    assert sorted(D(i["amount"]) for i in kst) == [D("1500.00"), D("2000.00"), D("2000.00"), D("2000.00")]
    cal = (await api.get(f"/api/tax/calendar/{year}")).json()["deadlines"]
    assert any(d["kind"] == "gewst" and d.get("amount") == "900.00" for d in cal)

    # Monatsabschluss: Vormonat (laufender Monat ist nicht abschließbar)
    prev = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    chk = (await api.get(f"/api/reports/month/{prev}")).json()
    assert chk["closable"] and not chk["locked"] and any(i["key"] == "bank" and not i["ok"] for i in chk["items"])
    blocked = await api.post(f"/api/reports/month/{prev}/close", json={})
    assert blocked.status_code == 409 and "Kontoauszüge" in blocked.text
    closed = (await api.post(f"/api/reports/month/{prev}/close", json={"confirm_open": True})).json()
    assert closed["locked"] and closed["closed"]["confirmed"]
    assert (await api.post(f"/api/reports/month/{prev}/close", json={"confirm_open": True})).status_code == 409
    assert (await api.post(f"/api/reports/month/{TODAY:%Y-%m}/close", json={"confirm_open": True})).status_code == 409


async def test_zm_from_invoices(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    z = (await api.get(f"/api/reports/zm/{TODAY.year}-Q{(TODAY.month - 1) // 3 + 1}")).json()
    assert z["lines"] == [] and z["missing_vat_id"] == []
    csv = await api.get(f"/api/reports/zm/{TODAY.year}-Q1.csv")
    assert csv.status_code == 200 and csv.text.startswith("Laenderkennzeichen,USt-IdNr.")
