"""Anlagenbuchhaltung: Übernahme aus Belegen, Abschreibungslauf als Abschlussbuchung, Sperren."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import PNG, TODAY
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
YEAR = TODAY.year


async def _laptop_receipt(api: object) -> str:
    sup = (await api.post("/api/suppliers", json={"name": "Apple", "default_category": "fixed_asset", "default_account": "0690"})).json()  # type: ignore[attr-defined]
    up = (await api.post("/api/receipt-files", files={"file": ("mb.png", PNG + b"mb", "image/png")})).json()  # type: ignore[attr-defined]
    r = (await api.post("/api/receipts", json={"supplier_id": sup["id"], "file_id": up["id"], "receipt_date": f"{YEAR}-01-15",  # type: ignore[attr-defined]
                                               "invoice_number": "MB-1", "description": "Büroschrank", "category": "fixed_asset",
                                               "tax_case": "IN_DE_19", "net_amount": "1300.00", "tax_amount": "247.00"})).json()
    assert (await api.post(f"/api/receipts/{r['id']}/book")).status_code == 200  # type: ignore[attr-defined]
    return str(r["id"])


async def test_asset_from_receipt_and_depreciation_run(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    rid = await _laptop_receipt(api)
    data = (await api.get("/api/assets", params={"year": YEAR})).json()
    assert [c["id"] for c in data["candidates"]] == [rid], "gebuchter Beleg auf Anlagekonto wartet auf Übernahme"
    assert any(a["number"] == "0690" for a in data["accounts"]) and not any(a["number"] == "1800" for a in data["accounts"])
    assert data["useful_life"]["furniture"] == 156

    bad = await api.post("/api/assets", json={"name": "x", "account": "1800", "acquisition_date": f"{YEAR}-01-15", "cost": "100.00",
                                              "useful_life_months": 12})
    assert bad.status_code == 409 and "Anlagekonto" in bad.text
    pool_bad = await api.post("/api/assets", json={"name": "x", "account": "0675", "method": "pool", "acquisition_date": f"{YEAR}-01-15",
                                                   "cost": "1300.00", "useful_life_months": 60})
    assert pool_bad.status_code == 409 and "Sammelposten" in pool_bad.text

    body = {"name": "Büroschrank", "account": "0690", "asset_class": "furniture", "method": "linear", "acquisition_date": f"{YEAR}-01-15",
            "cost": "1300.00", "useful_life_months": 156, "receipt_id": rid}
    aid = (await api.post("/api/assets", json=body)).json()["id"]
    a = next(x for x in (await api.get("/api/assets", params={"year": YEAR})).json()["assets"] if x["id"] == aid)
    assert a["number"] == 1 and Decimal(a["depreciation"]) == Decimal("100.00") and Decimal(a["addition"]) == Decimal("1300.00")
    assert Decimal(a["book_value_end"]) == Decimal("1200.00") and a["posted"] is False
    assert (await api.get("/api/assets", params={"year": YEAR})).json()["candidates"] == []

    # Dezember per Umsatzsteuer-Voranmeldung gesperrt – Abschlussbuchung gehört trotzdem auf den 31.12.
    env.ledger.locked.add(f"{YEAR}-12")
    run = (await api.post("/api/assets/depreciation", json={"year": YEAR})).json()
    assert len(run["posted"]) == 1 and Decimal(run["total"]) == Decimal("100.00")
    entry = next(e for e in (await api.get("/api/accounting/journal", params={"start": f"{YEAR}-01-01", "end": f"{YEAR}-12-31"})).json()["entries"]
                 if e["source_type"] == "depreciation")
    assert entry["entry_date"] == f"{YEAR}-12-31", "nicht ins Folgejahr verschoben"
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]} == {("6220", "100.00", "0.00"), ("0690", "0.00", "100.00")}
    assert (await api.post("/api/assets/depreciation", json={"year": YEAR})).json()["posted"] == [], "nicht doppelt"

    # nach der ersten Abschreibung sind die Rechengrundlagen fest
    assert (await api.put(f"/api/assets/{aid}", json={**body, "cost": "1400.00"})).status_code == 409
    assert (await api.put(f"/api/assets/{aid}", json={**body, "name": "Aktenschrank"})).status_code == 204

    # Jahresabschluss festgeschrieben → keine weiteren Abschlussbuchungen
    env.ledger.locked.add(f"{YEAR + 1}-13")
    later = (await api.post("/api/assets", json={**body, "name": "Regal", "receipt_id": None, "acquisition_date": f"{YEAR}-03-01"})).json()
    assert later["id"]
    assert (await api.post("/api/assets/depreciation", json={"year": YEAR + 1})).status_code == 409
