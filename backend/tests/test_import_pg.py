"""Umstieg: Eröffnungsbilanz, offene Posten, DATEV-Buchungsstapel und Stammdaten-CSV."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
D = Decimal
Y = TODAY.year


def _lines(entry: dict) -> set[tuple[str, str, str]]:
    return {(ln["account"], ln["debit"], ln["credit"]) for ln in entry["lines"]}


async def test_opening_balance_and_items(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    body = {"opening_date": f"{Y}-01-01", "balances": [{"account": "1800", "debit": "10000.00"}, {"account": "2900", "credit": "12000.00"}],
            "items": [{"kind": "receivable", "party": "Altkunde GmbH", "document_number": "RE-2025-099", "document_date": f"{Y - 1}-12-10",
                       "amount": "2500.00"}, {"kind": "payable", "party": "Hosting AG", "document_number": "H-77",
                                              "document_date": f"{Y - 1}-12-20", "amount": "500.00"}]}
    bad = await api.post("/api/import/opening", json={**body, "balances": [body["balances"][0], {"account": "2900", "credit": "11000.00"}]})
    assert bad.status_code == 409 and "nicht ausgeglichen" in bad.text and "1000.00" in bad.text
    # 10.000 Bank + 2.500 Forderung = 12.000 Kapital + 500 Verbindlichkeit
    r = await api.post("/api/import/opening", json=body)
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["entry"] and len(state["items"]) == 2
    assert {(ln["account"], ln["debit"], ln["credit"]) for ln in state["lines"]} >= {("1800", "10000.00", "0.00"), ("1200", "2500.00", "0.00"),
                                                                                     ("3300", "0.00", "500.00")}
    assert (await api.post("/api/import/opening", json=body)).status_code == 409, "nur einmal"
    item = next(i for i in state["items"] if i["kind"] == "receivable")
    assert (await api.post(f"/api/import/opening/items/{item['id']}/paid", json={"paid_on": TODAY.isoformat()})).status_code == 204
    assert (await api.post(f"/api/import/opening/items/{item['id']}/paid", json={"paid_on": TODAY.isoformat()})).status_code == 409
    csv_items = "Art;Name;Belegnummer;Belegdatum;Fällig;Betrag\nForderung;Kunde A;R-1;01.12.2025;15.12.2025;1.190,00\nVerbindlichkeit;X;;01.12.2025;;5\n"
    parsed = (await api.post("/api/import/opening/items-csv", files={"file": ("op.csv", csv_items.encode())})).json()
    assert len(parsed["items"]) == 1 and parsed["items"][0]["amount"] == "1190.00" and "Belegnummer" in parsed["errors"][0]


DATEV = (
    f'"EXTF";700;21;"Buchungsstapel";13;{Y}0101120000000;;"";"";"";1001;1;{Y}0101;4;{Y}0301;{Y}0331;"Import";"";1;0;0;"EUR"\n'
    'Umsatz (ohne Soll/Haben-Kz);Soll/Haben-Kennzeichen;WKZ Umsatz;Konto;Gegenkonto (ohne BU-Schlüssel);BU-Schlüssel;Belegdatum;'
    "Belegfeld 1;Buchungstext\n"
    "119,00;S;EUR;1800;4400;;0303;RE-1;Beratung März\n"
    "50,00;S;EUR;6815;1800;9;0503;B-7;Büromaterial\n"
    "200,00;S;EUR;70001;1800;;1003;Z-1;Zahlung Lieferant\n"
)


async def test_datev_import_with_preview_and_tax_split(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    files = {"file": ("EXTF_Buchungsstapel.csv", DATEV.encode("cp1252"))}
    prev = (await api.post("/api/import/datev", files=files, data={"dry_run": "true"})).json()
    assert prev["summary"]["rows"] == 3 and prev["errors"] == [] and prev["preview"][2]["debit"] == "3300"
    before = (await api.get("/api/accounting/journal", params={"start": f"{Y}-03-01", "end": f"{Y}-03-31"})).json()["entries"]
    assert not [e for e in before if e["source_type"] == "import"], "Vorschau bucht nichts"
    r = await api.post("/api/import/datev", files=files, data={"dry_run": "false"})
    assert r.status_code == 200, r.text
    entries = [e for e in (await api.get("/api/accounting/journal", params={"start": f"{Y}-03-01", "end": f"{Y}-03-31"})).json()["entries"]
               if e["source_type"] == "import"]
    by_ref = {e["document_ref"]: e for e in entries}
    assert _lines(by_ref["RE-1"]) == {("1800", "119.00", "0.00"), ("4400", "0.00", "100.00"), ("3806", "0.00", "19.00")}
    assert _lines(by_ref["B-7"]) == {("6815", "42.02", "0.00"), ("1406", "7.98", "0.00"), ("1800", "0.00", "50.00")}
    assert _lines(by_ref["Z-1"]) == {("3300", "200.00", "0.00"), ("1800", "0.00", "200.00")}
    again = await api.post("/api/import/datev", files=files, data={"dry_run": "false"})
    assert again.status_code == 409 and "bereits" in again.text
    broken = DATEV.replace("50,00;S", "abc;S")
    b = (await api.post("/api/import/datev", files={"file": ("x.csv", broken.encode())}, data={"dry_run": "true"})).json()
    assert b["errors"] and "Zeile 4" in b["errors"][0]
    assert (await api.post("/api/import/datev", files={"file": ("x.csv", b"Konto;Betrag\n1;2\n")})).status_code == 409


async def test_customer_and_supplier_csv(env: Env) -> None:  # noqa: F811
    api = env.api
    await setup_basics(env)
    lexoffice = ("Kundennummer;Firmenname;Vorname;Nachname;E-Mail;Straße;PLZ;Ort;USt-IdNr.\n"
                 "10001;Neukunde GmbH;;;info@neukunde.de;Hauptstr. 1;40210;Düsseldorf;DE123456789\n"
                 "10002;;Eva;Muster;eva@muster.de;Weg 2;50667;Köln;\n"
                 "10003;Ohne Adresse AG;;;x@y.de;;;;\n")
    files = {"file": ("kunden.csv", lexoffice.encode())}
    prev = (await api.post("/api/import/customers", files=files)).json()
    assert prev["summary"]["new"] == 2 and len(prev["errors"]) == 1 and "Ohne Adresse" in prev["errors"][0]
    assert (await api.post("/api/import/customers", files=files, data={"dry_run": "false"})).status_code == 200
    names = [c["display_name"] for c in (await api.get("/api/customers")).json()]
    assert "Neukunde GmbH" in names and "Eva Muster" in names
    again = (await api.post("/api/import/customers", files={"file": ("k2.csv", lexoffice.encode() + b"\n")})).json()
    assert again["summary"]["new"] == 0 and again["summary"]["skipped_existing"] == 2
    sev = "Name,Email,Land\nDruckerei Schmidt,a@b.de,DE\nCloud Ltd,,IE\n"
    r = (await api.post("/api/import/suppliers", files={"file": ("lief.csv", sev.encode())}, data={"dry_run": "false"})).json()
    assert r["summary"]["new"] == 2
    hist = (await api.get("/api/import")).json()["history"]
    assert {h["kind"] for h in hist} == {"customers", "suppliers"}
