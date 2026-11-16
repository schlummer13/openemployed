"""Auslandskunden (EU Reverse Charge, Drittland), Fremdwährung mit Kursdifferenz, XRechnung mit Leitweg-ID, ZM."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from lxml import etree

from app.einvoice.xrechnung import GUIDELINE_XRECHNUNG, leitweg_problem
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal
NS = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}

FR = {"customer_type": "b2b", "company_name": "Société Exemple SARL", "email": "compta@exemple.fr", "street": "1 rue de Rivoli",
      "postal_code": "75001", "city": "Paris", "country_code": "FR", "vat_id": "FR40303265045"}
US = {"customer_type": "b2b", "company_name": "Example Inc.", "email": "ap@example.com", "street": "1 Main St", "postal_code": "10001",
      "city": "New York", "country_code": "US", "currency": "USD"}


def test_leitweg() -> None:
    assert leitweg_problem("991-33333TEST-33") is None and leitweg_problem("04011000-1234512345-06") is None
    assert leitweg_problem("991-12345-67") and leitweg_problem("abc")


async def _journal(api, ref: str) -> set[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    entries = (await api.get("/api/accounting/journal", params={"start": f"{TODAY.year}-01-01", "end": f"{TODAY.year}-12-31"})).json()
    e = next(x for x in entries["entries"] if x["document_ref"] == ref and x["source_type"] == "invoice")
    return {(ln["account"], ln["debit"], ln["credit"]) for ln in e["lines"]}


async def test_eu_reverse_charge_and_zm(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    assert (await api.post("/api/customers", json={**FR, "vat_id": "DE123456789"})).status_code == 422, "falsches Länderpräfix"
    created = await api.post("/api/customers", json=FR)
    assert created.status_code == 201, created.text
    fr = created.json()["id"]
    line = {"description": "Beratung", "quantity": "10", "unit_code": "HUR", "unit_price": "120.00", "tax_case": "OUT_DE_19"}
    bad = (await api.post("/api/invoices", json={**draft(fr), "lines": [line]})).json()
    r = await api.post(f"/api/invoices/{bad['id']}/issue")
    assert r.status_code in (409, 422) and "EU-Ausland" in r.text
    await api.delete(f"/api/invoices/{bad['id']}")
    inv = (await api.post("/api/invoices", json={**draft(fr), "lines": [{**line, "tax_case": "OUT_EU_B2B_SERVICE"}]})).json()
    issued = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    assert D(issued["total_tax"]) == 0 and D(issued["total_gross"]) == D("1200.00")
    xml = (await api.get(f"/api/invoices/{inv['id']}/xml")).content
    assert b"<ram:CategoryCode>AE</ram:CategoryCode>" in xml and b"FR40303265045" in xml
    assert await _journal(api, issued["number"]) == {("1200", "1200.00", "0.00"), ("4336", "0.00", "1200.00")}
    q = f"{TODAY.year}-Q{(TODAY.month - 1) // 3 + 1}"
    zm = (await api.get(f"/api/reports/zm/{q}")).json()
    assert zm["lines"][0]["country"] == "FR" and zm["lines"][0]["kind"] == "S" and D(zm["lines"][0]["amount"]) == D("1200.00")
    ustva = (await api.get(f"/api/tax/vat/{q}")).json()
    assert any(ln["kz"] == "21" and D(str(ln["base"])) == D("1200") for ln in ustva["lines"])


async def test_foreign_currency_with_fx_difference(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    us = (await api.post("/api/customers", json=US)).json()["id"]
    line = {"description": "Software license", "quantity": "1", "unit_code": "C62", "unit_price": "1085.00", "tax_case": "OUT_DRITTLAND_SERVICE"}
    inv = (await api.post("/api/invoices", json={**draft(us), "lines": [line]})).json()
    no_rate = await api.post(f"/api/invoices/{inv['id']}/issue")
    assert no_rate.status_code in (409, 422) and "EZB" in no_rate.text
    assert (await api.put("/api/currencies/USD", json={"rate_date": TODAY.isoformat(), "rate": "1.0850"})).status_code == 204
    ir = await api.post(f"/api/invoices/{inv['id']}/issue")
    assert ir.status_code == 200, ir.text
    issued = ir.json()
    snap = (await api.get(f"/api/invoices/{inv['id']}")).json()["snapshot"]
    assert snap["currency"] == "USD" and D(str(snap["exchange_rate"])) == D("1.0850")
    xml = (await api.get(f"/api/invoices/{inv['id']}/xml")).content
    assert b"<ram:InvoiceCurrencyCode>USD</ram:InvoiceCurrencyCode>" in xml and b"<ram:TaxCurrencyCode>EUR" in xml
    assert b"FR40303265045" not in xml and b"<ram:CategoryCode>O</ram:CategoryCode>" in xml
    assert await _journal(api, issued["number"]) == {("1200", "1000.00", "0.00"), ("4338", "0.00", "1000.00")}
    # Zahlung 1.085 USD, gutgeschrieben 1.010 EUR → 10 EUR Kursgewinn
    p = await api.post(f"/api/invoices/{inv['id']}/payments", json={"paid_on": TODAY.isoformat(), "amount": "1085.00", "amount_eur": "1010.00"})
    assert p.status_code == 201, p.text
    entries = (await api.get("/api/accounting/journal", params={"start": TODAY.isoformat(), "end": TODAY.isoformat()})).json()["entries"]
    pay = next(e for e in entries if e["source_type"] == "payment")
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in pay["lines"]} == {
        ("1800", "1010.00", "0.00"), ("1200", "0.00", "1000.00"), ("4840", "0.00", "10.00")}


async def test_xrechnung_with_leitweg(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    from tests.test_invoices_pg import COMPANY

    # Pflicht für XRechnung: Telefon und E-Mail des Verkäufers (neue Fassung des Firmenprofils)
    prof = await api.post("/api/company-profile", json={**COMPANY, "valid_from": TODAY.isoformat(), "phone": "0211 123456",
                                                        "email": "rechnung@beispiel.de"})
    assert prof.status_code == 201, prof.text
    base = {"customer_type": "b2b", "company_name": "Stadt Musterstadt", "email": "eingang@musterstadt.de", "street": "Rathausplatz 1",
            "postal_code": "40213", "city": "Düsseldorf", "einvoice_format": "xrechnung"}
    assert (await api.post("/api/customers", json=base)).status_code == 422, "Leitweg-ID Pflicht"
    assert (await api.post("/api/customers", json={**base, "buyer_reference": "991-12345-67"})).status_code == 422, "Prüfziffer"
    cid = (await api.post("/api/customers", json={**base, "buyer_reference": "991-33333TEST-33", "peppol_id": "0204:991-33333TEST-33"})).json()["id"]
    inv = (await api.post("/api/invoices", json=draft(cid))).json()
    r = await api.post(f"/api/invoices/{inv['id']}/issue")
    assert r.status_code == 200, r.text
    xr = await api.get(f"/api/invoices/{inv['id']}/xrechnung")
    assert xr.status_code == 200
    root = etree.fromstring(xr.content)
    assert root.find(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", NS).text == GUIDELINE_XRECHNUNG
    assert root.find(".//ram:BuyerReference", NS).text == "991-33333TEST-33"
    assert root.find(".//ram:SellerTradeParty/ram:DefinedTradeContact/ram:TelephoneUniversalCommunication/ram:CompleteNumber", NS) is not None
    buyer_uri = root.find(".//ram:BuyerTradeParty/ram:URIUniversalCommunication/ram:URIID", NS)
    assert buyer_uri.get("schemeID") == "0204" and buyer_uri.text == "991-33333TEST-33"
    sent = (await api.post(f"/api/invoices/{inv['id']}/send")).json()
    from app.main import app
    att = [a.filename for a in app.state.mailer.sent[-1].attachments]
    assert att[0].endswith("_XRechnung.xml") and sent["recipient"] == "eingang@musterstadt.de"
    peppol = await api.post(f"/api/invoices/{inv['id']}/peppol")
    assert peppol.status_code == 409 and "Access-Point" in peppol.text
    assert dt.date  # Import genutzt
