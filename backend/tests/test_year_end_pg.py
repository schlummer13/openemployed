"""Jahresabschluss: Bilanz/GuV-Entwurf, Abschlussbuchungen, Auflösung der Abgrenzung, Festschreibung, Übergabepaket."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY, receipt
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
YEAR = TODAY.year


async def test_year_end_flow(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    sup = (await api.post("/api/suppliers", json={"name": "Adobe", "default_category": "software"})).json()
    rid = await receipt(api, sup["id"], "600.00", "114.00", category="software")
    await api.post(f"/api/receipts/{rid}/book")

    o = (await api.get(f"/api/year-end/{YEAR}")).json()
    st = o["statements"]
    assert st["balanced"] is True and Decimal(st["total_aktiva"]) == Decimal(st["total_passiva"])
    guv = {x["label"]: Decimal(x["amount"]) for x in st["guv"]}
    assert guv["1. Umsatzerlöse"] == Decimal("1758.00") and guv["8. Sonstige betriebliche Aufwendungen"] == Decimal("-600.00")
    assert Decimal(st["result"]) == Decimal("1158.00")
    items = {i["key"]: i for i in o["checklist"]["items"]}
    assert items["tax_provision"]["done"] is False and o["checklist"]["closed"] is False
    assert Decimal(o["tax_proposal"]["kst"]) > 0

    # Steuerrückstellung (Vorschlag), Abschlusskosten, aktive Rechnungsabgrenzung mit Auflösung am 1.1.
    env.ledger.locked.add(f"{YEAR}-12")  # Dezember per UStVA gesperrt – Abschlussbuchungen trotzdem zum 31.12.
    r = await api.post(f"/api/year-end/{YEAR}/entries", json={"kind": "tax_provision", "description": "Ertragsteuern"})
    assert r.status_code == 201, r.text
    assert (await api.post(f"/api/year-end/{YEAR}/entries", json={"kind": "closing_costs", "description": "Steuerberater",
                                                                 "amount": "1500.00"})).status_code == 201
    bad = await api.post(f"/api/year-end/{YEAR}/entries", json={"kind": "prepaid", "description": "x", "amount": "100.00", "account": "1800"})
    assert bad.status_code == 409
    assert (await api.post(f"/api/year-end/{YEAR}/entries", json={"kind": "prepaid", "description": "Lizenz 2027 anteilig",
                                                                 "amount": "200.00", "account": "6837"})).status_code == 201
    o = (await api.get(f"/api/year-end/{YEAR}")).json()
    assert o["statements"]["balanced"] is True
    assert len(o["entries"]) == 3 and o["entries"][2]["reversal_number"], "Abgrenzung wird im Folgejahr aufgelöst"
    passiva = {x["label"]: Decimal(x["amount"]) for x in o["statements"]["passiva"]}
    assert passiva["B. Rückstellungen"] >= Decimal("1500.00")
    aktiva = {x["label"]: Decimal(x["amount"]) for x in o["statements"]["aktiva"]}
    assert aktiva["C. Rechnungsabgrenzungsposten"] == Decimal("200.00")
    j = (await api.get("/api/accounting/journal", params={"start": f"{YEAR}-12-31", "end": f"{YEAR + 1}-01-01"})).json()["entries"]
    closing = [e for e in j if e["source_type"] == "closing"]
    assert sum(1 for e in closing if e["closing"]) == 3 and any(e["entry_date"] == f"{YEAR + 1}-01-01" for e in closing)

    # Übergabepaket
    z = zipfile.ZipFile(io.BytesIO((await api.get(f"/api/year-end/{YEAR}/export")).content))
    names = {n.split("/")[-1] for n in z.namelist()}
    assert {"bilanz.csv", "guv.csv", "summen_und_saldenliste.csv", "anlagenspiegel.csv", "journal.csv", "LIESMICH.txt"} <= names

    # Festschreiben: offene Punkte (UStVA andere Monate, Bank) erfordern Bestätigung; danach keine Abschlussbuchung mehr
    assert (await api.post(f"/api/year-end/{YEAR}/lock", json={})).status_code == 409
    assert (await api.post(f"/api/year-end/{YEAR}/lock", json={"confirm_open": True})).status_code == 200
    assert (await api.get(f"/api/year-end/{YEAR}")).json()["checklist"]["closed"] is True
    late = await api.post(f"/api/year-end/{YEAR}/entries", json={"kind": "closing_costs", "description": "nachträglich", "amount": "10.00"})
    assert late.status_code == 409
