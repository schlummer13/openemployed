"""SEPA-Zahlungsverkehr gegen PostgreSQL: Lieferantenüberweisung, Lohnüberweisung, Mandate und Lastschrift."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from app.domain.sepa import NS_001, NS_008, next_business_day
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")


async def test_supplier_transfer_mandate_and_debit(env: Env) -> None:  # noqa: F811
    from tests.test_accounting_pg import PNG, receipt

    api = env.api
    b2b, _ = await setup_basics(env)
    sup = (await api.post("/api/suppliers", json={"name": "Druckerei Schmidt", "default_category": "office"})).json()
    rid = await receipt(api, sup["id"], "100.00", "19.00", category="office", png=PNG + b"s")
    assert (await api.post(f"/api/receipts/{rid}/book")).status_code == 200
    ov = (await api.get("/api/sepa")).json()
    assert [t["id"] for t in ov["transfers"]] == [rid] and ov["transfers"][0]["iban"] is None
    exe = next_business_day(TODAY, 1).isoformat()
    miss = await api.post("/api/sepa/transfers", json={"receipt_ids": [rid], "execution_date": exe})
    assert miss.status_code == 409 and "IBAN fehlt" in miss.text
    assert (await api.put(f"/api/suppliers/{sup['id']}", json={"name": "Druckerei Schmidt", "default_category": "office",
                                                               "iban": "DE02 1203 0000 0000 2020 51"})).status_code == 200
    fid = (await api.post("/api/sepa/transfers", json={"receipt_ids": [rid], "execution_date": exe})).json()["id"]
    xml = (await api.get(f"/api/sepa/files/{fid}.xml")).text
    root = ET.fromstring(xml)
    assert root.find(".//{%s}InstdAmt" % NS_001).text == "119.00"  # type: ignore[union-attr]
    assert (await api.get("/api/sepa")).json()["transfers"][0]["in_file_at"] is not None
    past = await api.post("/api/sepa/transfers", json={"receipt_ids": [rid], "execution_date": (TODAY - dt.timedelta(days=1)).isoformat()})
    assert past.status_code == 409

    # Lastschrift: Gläubiger-ID und Mandat erforderlich
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    assert (await api.put("/api/sepa/settings", json={"creditor_id": "DE00ZZZ09999999999"})).status_code == 409
    assert (await api.put("/api/sepa/settings", json={"creditor_id": "DE98ZZZ09999999999"})).status_code == 204
    assert (await api.get("/api/sepa")).json()["debits"] == [], "ohne Mandat kein Einzug"
    m = await api.post("/api/sepa/mandates", json={"customer_id": b2b, "signed_on": TODAY.isoformat(), "account_holder": "HR 1 GmbH",
                                                   "iban": "DE02500105170137075030"})
    assert m.status_code == 201
    dup = await api.post("/api/sepa/mandates", json={"customer_id": b2b, "signed_on": TODAY.isoformat(), "account_holder": "HR 1 GmbH",
                                                     "iban": "DE02500105170137075030"})
    assert dup.status_code == 409
    debits = (await api.get("/api/sepa")).json()["debits"]
    assert len(debits) == 1 and debits[0]["sequence"] == "FRST"
    early = await api.post("/api/sepa/debits", json={"invoice_ids": [inv["id"]], "collection_date": TODAY.isoformat()})
    assert early.status_code == 409 and "frühestens" in early.text
    coll = next_business_day(TODAY, 2).isoformat()
    did = (await api.post("/api/sepa/debits", json={"invoice_ids": [inv["id"]], "collection_date": coll})).json()["id"]
    dxml = (await api.get(f"/api/sepa/files/{did}.xml")).text
    assert ET.fromstring(dxml).find(".//{%s}SeqTp" % NS_008).text == "FRST"  # type: ignore[union-attr]
    again = (await api.get("/api/sepa")).json()
    assert again["debits"][0]["sequence"] == "RCUR" and again["debits"][0]["in_file_at"] is not None
    mandate = again["mandates"][0]
    assert mandate["first_used_on"] == coll
    assert (await api.post(f"/api/sepa/mandates/{mandate['id']}/revoke", json={"revoked_on": TODAY.isoformat()})).status_code == 204
    assert (await api.get("/api/sepa")).json()["debits"] == []
    assert len((await api.get("/api/sepa")).json()["files"]) == 2
