"""Bankimport und Zahlungsabgleich."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.bank import parse_amount, parse_csv
from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

TODAY = today_berlin()


@pytest.mark.parametrize(("raw", "expected"), [("1.234,56", "1234.56"), ("1234.56", "1234.56"), ("-12,30 €", "-12.30"),
                                              ("1,234.56", "1234.56"), ("0,5", "0.50")])
def test_parse_amount(raw: str, expected: str) -> None:
    assert parse_amount(raw) == Decimal(expected)


def test_parse_csv_german() -> None:
    csv_data = "Buchungstag;Betrag;Gegenpartei;Verwendungszweck\n01.11.2026;2.092,02;HR 1 GmbH;RE 2026 00001 Danke\n\n".encode("cp1252")
    rows = parse_csv(csv_data)
    assert len(rows) == 1 and rows[0]["amount"] == Decimal("2092.02") and rows[0]["booked_on"].isoformat() == "2026-11-01"


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_import_and_match(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, b2c = await setup_basics(env)
    a = (await api.post("/api/invoices", json=draft(b2b))).json()
    a = (await api.post(f"/api/invoices/{a['id']}/issue")).json()
    b = (await api.post("/api/invoices", json=draft(b2c, "119.00"))).json()
    b = (await api.post(f"/api/invoices/{b['id']}/issue")).json()
    day = TODAY.strftime("%d.%m.%Y")
    csv_data = (f"Datum;Betrag;Name;Verwendungszweck\n"
                f"{day};2092,02;HR 1 GmbH;Rechnung {a['number']}\n"
                f"{day};{b['total_gross'].replace('.', ',')};Max Muster;Danke\n"
                f"{day};-49,00;Hetzner;Server\n").encode()
    r = await api.post("/api/bank/import", files={"file": ("umsaetze.csv", csv_data, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json() == {"rows": 3, "new": 3, "duplicates": 0, "auto_matched": 1}
    again = (await api.post("/api/bank/import", files={"file": ("umsaetze.csv", csv_data, "text/csv")})).json()
    assert again["new"] == 0 and again["duplicates"] == 3, "Doppelimport wird erkannt"

    assert (await api.get(f"/api/invoices/{a['id']}/receivable")).json()["status"] == "paid"
    txs = (await api.get("/api/bank/transactions")).json()["transactions"]
    open_credit = next(t for t in txs if t["status"] == "open" and Decimal(t["amount"]) > 0)
    assert [c["number"] for c in open_credit["candidates"]] == [b["number"]], "Vorschlag über den Betrag"
    m = await api.post(f"/api/bank/transactions/{open_credit['id']}/match", json={"invoice_id": b["id"]})
    assert m.status_code == 200
    assert (await api.post(f"/api/bank/transactions/{open_credit['id']}/match", json={"invoice_id": b["id"]})).status_code == 422
    debit = next(t for t in txs if Decimal(t["amount"]) < 0)
    assert (await api.post(f"/api/bank/transactions/{debit['id']}/match", json={"invoice_id": b["id"]})).status_code == 422
    assert (await api.post(f"/api/bank/transactions/{debit['id']}/ignore", json={"note": "Beleg folgt"})).status_code == 204
    payments = (await api.get(f"/api/invoices/{b['id']}/receivable")).json()["payments"]
    assert payments[0]["source"] == "import"
    assert (await api.post("/api/bank/qonto/sync")).status_code in (404, 405), "Qonto-Anbindung entfernt"
    assert (await api.get("/api/bank/transactions")).json()["fints_ready"] is False


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_expense_matching(env: Env) -> None:  # noqa: F811
    from tests.test_accounting_pg import PNG, receipt

    api = env.api
    await setup_basics(env)
    hetzner = (await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH", "default_category": "hosting"})).json()
    telekom = (await api.post("/api/suppliers", json={"name": "Telekom Deutschland GmbH", "default_category": "telecom"})).json()
    google = (await api.post("/api/suppliers", json={"name": "Google Ireland Ltd.", "country_code": "IE"})).json()
    h = await receipt(api, hetzner["id"], "89.00", "16.91", category="hosting", png=PNG + b"h")
    t = await receipt(api, telekom["id"], "89.00", "16.91", category="telecom", png=PNG + b"t")
    g = await receipt(api, google["id"], "55.20", "0.00", case="IN_13B_EU_SERVICE", category="software", png=PNG + b"g")
    for rid in (h, t, g):
        assert (await api.post(f"/api/receipts/{rid}/book")).status_code == 200
    day = TODAY.strftime("%d.%m.%Y")
    csv_data = (f"Datum;Betrag;Name;Verwendungszweck\n"
                f"{day};-105,91;HETZNER ONLINE;Rechnung R-89.00\n"      # Name + Rechnungsnummer → automatisch
                f"{day};-105,91;Lastschrift;Mobilfunk\n"                # gleicher Betrag, unklar → nur Vorschlag
                f"{day};-55,20;GOOGLE IRELAND;Workspace\n"              # Reverse Charge: Nettobetrag
                f"{day};-12,00;Unbekannt;Gebühr\n").encode()
    r = (await api.post("/api/bank/import", files={"file": ("u.csv", csv_data, "text/csv")})).json()
    assert r["new"] == 4
    receipts = {x["id"]: x for x in (await api.get("/api/receipts")).json()}
    assert receipts[h]["paid_on"] == TODAY.isoformat(), "eindeutig über Name und Rechnungsnummer"
    assert receipts[g]["paid_on"] == TODAY.isoformat(), "Reverse Charge über Nettobetrag + Name"
    assert receipts[t]["paid_on"] is None
    txs = (await api.get("/api/bank/transactions")).json()["transactions"]
    lastschrift = next(x for x in txs if x["counterparty"] == "Lastschrift")
    assert [c["receipt_id"] for c in lastschrift["receipt_candidates"]] == [t], "bereits bezahlter Hetzner-Beleg nicht mehr vorgeschlagen"
    fee = next(x for x in txs if x["counterparty"] == "Unbekannt")
    assert fee["receipt_candidates"] == []
    assert (await api.post(f"/api/bank/transactions/{fee['id']}/match-receipt", json={"receipt_id": t})).status_code == 422, "Betrag passt nicht"
    assert (await api.post(f"/api/bank/transactions/{lastschrift['id']}/match-receipt", json={"receipt_id": t})).status_code == 204
    assert (await api.get(f"/api/receipts/{t}")).json()["paid_on"] == TODAY.isoformat()
    entries = (await api.get("/api/accounting/journal")).json()["entries"]
    assert sum(1 for e in entries if e["source_type"] == "receipt_payment") == 3, "Zahlung: Verbindlichkeit an Bank"
