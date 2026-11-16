"""UStVA, Jahresübersicht und Periodensperre gegen echtes PostgreSQL."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import B2B, COMPANY, Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

TODAY = today_berlin()
FIRST_OF_MONTH = TODAY.replace(day=1)
PREV = FIRST_OF_MONTH - dt.timedelta(days=1)          # letzter Tag des Vormonats
PREV_SPEC = f"{PREV.year}-{PREV.month:02d}"
CUR_SPEC = f"{TODAY.year}-{TODAY.month:02d}"
PDF = b"%PDF-1.4\n% Beleg "


async def _receipt(e: Env, supplier: str, day: dt.date, case: str, net: str, tax: str, n: int) -> dict:
    f = (await e.api.post("/api/receipt-files", files={"file": (f"b{n}.pdf", PDF + str(n).encode(), "application/pdf")})).json()
    r = (await e.api.post("/api/receipts", json={
        "supplier_id": supplier, "file_id": f["id"], "receipt_date": day.isoformat(), "description": f"Beleg {n}",
        "category": "software", "tax_case": case, "net_amount": net, "tax_amount": tax})).json()
    booked = (await e.api.post(f"/api/receipts/{r['id']}/book")).json()
    return booked


async def test_vat_return_lock_and_overview(env: Env) -> None:  # noqa: F811
    api = env.api
    await api.post("/api/company-profile", json=COMPANY)
    cust = (await api.post("/api/customers", json=B2B)).json()["id"]
    inv = (await api.post("/api/invoices", json={
        "customer_id": cust, "service_date_from": PREV.replace(day=1).isoformat(), "service_date_to": PREV.isoformat(),
        "lines": [{"description": "Miete", "quantity": "1", "unit_code": "MON", "unit_price": "1758.99", "tax_case": "OUT_DE_19"},
                  {"description": "Buch", "quantity": "1", "unit_code": "C62", "unit_price": "10.00", "tax_case": "OUT_DE_7"}]})).json()
    issued = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    assert issued["vat_period"] == PREV_SPEC

    hetzner = (await api.post("/api/suppliers", json={"name": "Hetzner"})).json()["id"]
    anthropic = (await api.post("/api/suppliers", json={"name": "Anthropic", "country_code": "IE",
                                                        "default_tax_case": "IN_13B_EU_SERVICE"})).json()["id"]
    r1 = await _receipt(env, hetzner, PREV, "IN_DE_19", "45.00", "8.55", 1)
    await _receipt(env, anthropic, PREV, "IN_13B_EU_SERVICE", "300.00", "0", 2)
    assert r1["vat_period"] == PREV_SPEC

    vat = (await api.get(f"/api/tax/vat/{PREV_SPEC}")).json()
    kz = {line["kz"]: line for line in vat["lines"]}
    assert kz["81"]["base"] == "1758" and kz["81"]["tax"] == "334.02"      # BMG auf volle Euro gekürzt
    assert kz["81"]["tax_exact"] == "334.21" and kz["81"]["rounding_difference"] == "-0.19"
    assert kz["86"]["base"] == "10" and kz["86"]["tax"] == "0.70"
    assert kz["46"]["base"] == "300" and kz["47"]["tax"] == "57.00" and kz["67"]["tax"] == "57.00"
    assert kz["66"]["tax"] == "8.55"
    # 334,02 + 0,70 + 57,00 − 8,55 − 57,00
    assert kz["83"]["tax"] == "326.17" and vat["payable"] == "326.17"
    assert vat["locked"] is False and vat["closable"] is True
    assert len(kz["81"]["items"]) == 1 and kz["81"]["items"][0]["ref"] == issued["number"]

    # Laufender Monat kann nicht gesperrt werden
    assert (await api.post(f"/api/tax/vat/{CUR_SPEC}/lock")).status_code == 409
    locked = (await api.post(f"/api/tax/vat/{PREV_SPEC}/lock")).json()
    assert locked["locked"] is True and locked["payable"] == "326.17"
    assert (await api.post(f"/api/tax/vat/{PREV_SPEC}/lock")).status_code == 409
    sealed = next(r for r in env.ledger.records if r.record_type == "vat_return.locked")
    assert sealed.payload["kennzahlen"]["83"]["tax"] == "326.17"

    # Nachzügler mit Datum im gesperrten Monat landet im nächsten offenen Monat
    late = await _receipt(env, hetzner, PREV, "IN_DE_19", "10.00", "1.90", 3)
    assert late["vat_period"] == CUR_SPEC
    after = (await api.get(f"/api/tax/vat/{PREV_SPEC}")).json()
    assert after["payable"] == "326.17" and after["locked"] is True, "gemeldete Werte bleiben unverändert"
    cur = (await api.get(f"/api/tax/vat/{CUR_SPEC}")).json()
    assert {line["kz"]: line for line in cur["lines"]}["66"]["tax"] == "1.90"

    overview = (await api.get(f"/api/tax/overview/{PREV.year}")).json()
    assert overview["totals"]["revenue"] == "1768.99"
    assert overview["income_tax"]["kst_rate"] == "15"
    cal = (await api.get(f"/api/tax/calendar/{PREV.year}")).json()
    assert any(d["kind"] == "ustva" for d in cal["deadlines"])


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_elster_xml_and_guarded_send(env: Env) -> None:  # noqa: F811
    from xml.etree import ElementTree as ET

    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    inv = (await api.post(f"/api/invoices/{inv['id']}/issue")).json()
    spec = inv["issue_date"][:7]
    st = (await api.get("/api/elster/status")).json()
    assert st["eric_installed"] is False and st["state"] == "NW"
    r = await api.get(f"/api/elster/ustva/{spec}/xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.content)
    vals = {el.tag.split("}")[1]: el.text for el in root.iter() if el.tag.split("}")[1].startswith("Kz")}
    assert vals["Kz81"] == "1758" and "Testmerker" in r.text and "<Steuernummer>5103057340815</Steuernummer>" in r.text
    v = await api.post(f"/api/elster/ustva/{spec}/validate")
    assert v.status_code == 409 and "ERiC" in v.text, "ohne ERiC klare Meldung statt Fehler"
    s = await api.post(f"/api/elster/ustva/{spec}/send", json={"pin": "123456"})
    assert s.status_code == 409 and "HERSTELLER" in s.text.upper()
