"""Rechnungsablauf gegen echtes PostgreSQL: Stammdaten → Entwurf → Freigabe → Storno, inkl. Schutzmechanismen."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.auth.store import MemoryAuthStore, User
from app.main import app
from app.services.invoices import B2C_DEFAULT_NOTE, today_berlin
from tests.conftest import ORIGIN, FakeLedger, FakeMailer, reset_app_state
from tests.pgutil import ADMIN_URL, app_url, create_database, drop_database

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

YEAR = today_berlin().year


@dataclass
class Env:
    api: httpx.AsyncClient
    ledger: FakeLedger
    engine: AsyncEngine


def issued_records(e: Env, record_type: str = "invoice.issued") -> list[Any]:
    return [r for r in e.ledger.records if r.record_type == record_type]


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    db = await create_database()
    engine = create_async_engine(app_url(db))
    reset_app_state()
    store = MemoryAuthStore()
    ledger = FakeLedger()
    app.state.engine = engine
    app.state.ledger = ledger
    app.state.auth_store = store
    app.state.google = None
    app.state.mailer = FakeMailer()
    token = await store.create_session(User(id=uuid4(), email="anna@beispiel.de", name="Anna", picture=None),
                                       ip=None, user_agent=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        headers={"Origin": ORIGIN}, cookies={"selbstlite_session": token},
    ) as api:
        yield Env(api, ledger, engine)
    reset_app_state()
    await engine.dispose()
    await drop_database(db)


COMPANY = {
    "valid_from": "2026-01-01",
    "legal_name": "Beispiel Software UG (haftungsbeschränkt)",
    "legal_form": "UG (haftungsbeschränkt)",
    "street": "Königsallee 1",
    "postal_code": "40212",
    "city": "Düsseldorf",
    "register_court": "Amtsgericht Düsseldorf",
    "register_number": "HRB 12345",
    "managing_directors": ["Anna A", "Ben B", "Cem C", "Dana D"],
    "tax_number": "103/5734/0815",
    "iban": "DE89 3704 0044 0532 0130 00",
}
B2B = {"customer_type": "b2b", "company_name": "HR 1 GmbH", "email": "Buchhaltung@HR1.de", "street": "Weg 1",
       "postal_code": "50667", "city": "Köln"}
B2C = {"customer_type": "b2c", "first_name": "Max", "last_name": "Muster", "email": "max@example.de",
       "street": "Gasse 2", "postal_code": "10115", "city": "Berlin"}


def draft(customer_id: str, price: str = "1758.00", **extra: Any) -> dict[str, Any]:
    return {
        "customer_id": customer_id,
        "service_date_from": f"{YEAR}-11-01",
        "service_date_to": f"{YEAR}-11-30",
        "lines": [{"description": "Software-Miete HR (monatlich)", "quantity": "1", "unit_code": "MON",
                   "unit_price": price, "tax_case": "OUT_DE_19"}],
        **extra,
    }


async def setup_basics(e: Env) -> tuple[str, str]:
    assert (await e.api.post("/api/company-profile", json=COMPANY)).status_code == 201
    b2b = (await e.api.post("/api/customers?welcome=false", json=B2B)).json()
    b2c = (await e.api.post("/api/customers?welcome=false", json=B2C)).json()
    return b2b["id"], b2c["id"]


# ───────────────────────── Stammdaten ─────────────────────────

async def test_company_profile_rules(env: Env) -> None:
    bad = {**COMPANY, "legal_name": "Beispiel Software"}
    r = await env.api.post("/api/company-profile", json=bad)
    assert r.status_code == 422 and "Zusatz" in r.text
    r = await env.api.post("/api/company-profile", json={**COMPANY, "iban": "DE00 1234 5678 9012 3456 78"})
    assert r.status_code == 422 and "IBAN" in r.text
    r = await env.api.post("/api/company-profile", json={**COMPANY, "register_court": None, "register_number": ""})
    assert r.status_code == 201
    assert r.json()["missing_for_invoices"] == ["Registergericht und Handelsregisternummer"]
    assert r.json()["iban"] == "DE89370400440532013000"
    # gleiche Gültigkeit doppelt → Konflikt; neue Version mit späterem Datum → ok
    assert (await env.api.post("/api/company-profile", json=COMPANY)).status_code == 409
    r = await env.api.post("/api/company-profile", json={**COMPANY, "valid_from": "2026-02-01"})
    assert r.status_code == 201
    current = (await env.api.get("/api/company-profile")).json()
    assert current["missing_for_invoices"] == []


async def test_customers(env: Env) -> None:
    r = await env.api.post("/api/customers", json={**B2B, "company_name": None})
    assert r.status_code == 422
    r = await env.api.post("/api/customers", json={**B2B, "postal_code": "1234"})
    assert r.status_code == 422
    c1 = (await env.api.post("/api/customers", json=B2B)).json()
    c2 = (await env.api.post("/api/customers", json=B2C)).json()
    assert c1["number"] == "K-10001" and c2["number"] == "K-10002"
    assert c1["email"] == "buchhaltung@hr1.de"
    assert c2["display_name"] == "Max Muster"
    r = await env.api.put(f"/api/customers/{c1['id']}", json={**B2B, "city": "Düsseldorf", "postal_code": "40213"})
    assert r.json()["city"] == "Düsseldorf"
    await env.api.post(f"/api/customers/{c2['id']}/archive")
    assert [c["number"] for c in (await env.api.get("/api/customers")).json()] == ["K-10001"]
    assert len((await env.api.get("/api/customers", params={"include_archived": True})).json()) == 2
    assert (await env.api.get(f"/api/customers/{uuid4()}")).status_code == 404


async def test_products(env: Env) -> None:
    p = {"sku": "HR-BASIC", "name": "HR-Software Basic", "unit_code": "MON", "price": "1758.00"}
    r = await env.api.post("/api/products", json=p)
    assert r.status_code == 201 and r.json()["price"] == "1758.0000" and r.json()["warnings"] == []
    assert (await env.api.post("/api/products", json=p)).status_code == 409
    r = await env.api.post("/api/products", json={**p, "sku": "ABO-B2C", "price": "1.10", "price_basis": "gross"})
    assert r.json()["warnings"] and "nicht exakt darstellbar" in r.json()["warnings"][0]
    r = await env.api.post("/api/products", json={**p, "sku": "X", "price": 1.5})
    assert r.status_code == 422


# ───────────────────────── Rechnungen ─────────────────────────

async def test_full_invoice_lifecycle(env: Env) -> None:
    b2b, b2c = await setup_basics(env)
    d = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    assert d["status"] == "draft" and d["number"] is None and d["price_mode"] == "net" and d["due_days"] == 14

    # Entwurf ändern ist erlaubt
    r = await env.api.put(f"/api/invoices/{d['id']}", json=draft(b2b, intro_text="Vielen Dank!"))
    assert r.json()["intro_text"] == "Vielen Dank!"

    issued = (await env.api.post(f"/api/invoices/{d['id']}/issue")).json()
    assert issued["status"] == "issued"
    assert issued["number"] == f"RE-{YEAR}-00001"
    assert (issued["total_net"], issued["total_tax"], issued["total_gross"]) == ("1758.00", "334.02", "2092.02")
    assert issued["snapshot"]["seller"]["legal_name"].endswith("UG (haftungsbeschränkt)")
    assert issued["snapshot"]["buyer"]["name"] == "HR 1 GmbH"
    assert issued["snapshot"]["service_period"] == {"from": f"{YEAR}-11-01", "to": f"{YEAR}-11-30"}
    assert issued["ledger_hash"] == issued_records(env)[0].hash
    assert issued["snapshot"] == issued_records(env)[0].payload, "gespeicherte Fassung = festgeschriebene Fassung"

    # Dokumente: ZUGFeRD-PDF + XML erzeugt, abgelegt, Hashes festgeschrieben
    import hashlib

    pdf = await env.api.get(f"/api/invoices/{d['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    xml = await env.api.get(f"/api/invoices/{d['id']}/xml")
    assert xml.status_code == 200 and b"CrossIndustryInvoice" in xml.content
    assert f'filename="RE-{YEAR}-00001.xml"' in xml.headers["content-disposition"]
    doc_seal = issued_records(env, "invoice.documents")[0]
    assert doc_seal.payload["pdf_sha256"] == hashlib.sha256(pdf.content).hexdigest()
    assert doc_seal.payload["xml_sha256"] == hashlib.sha256(xml.content).hexdigest()
    # erneuter Abruf liefert exakt dieselben Bytes (keine Neuerzeugung)
    assert (await env.api.get(f"/api/invoices/{d['id']}/pdf")).content == pdf.content
    async with env.engine.connect() as conn:
        with pytest.raises(Exception, match=r"permission denied|unveränderlich|verboten"):
            await conn.execute(text("UPDATE invoice_documents SET pdf = 'x'"))  # Recht entzogen + Trigger
        await conn.rollback()

    # Versand per E-Mail (Attrappe) mit PDF + XML, Versandprotokoll
    sent = await env.api.post(f"/api/invoices/{d['id']}/send")
    assert sent.status_code == 200, sent.text
    mail = app.state.mailer.sent[-1]
    assert mail.to == ["buchhaltung@hr1.de"]
    assert [a.filename for a in mail.attachments] == [f"RE-{YEAR}-00001.pdf", f"RE-{YEAR}-00001.xml"]
    assert mail.attachments[0].data == pdf.content
    deliveries = (await env.api.get(f"/api/invoices/{d['id']}/deliveries")).json()
    assert len(deliveries) == 1 and deliveries[0]["recipient"] == "buchhaltung@hr1.de"
    # Entwürfe haben keine Dokumente
    draft_only = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    assert (await env.api.get(f"/api/invoices/{draft_only['id']}/pdf")).status_code == 409
    await env.api.delete(f"/api/invoices/{draft_only['id']}")

    # Ausgestellt: nicht mehr änderbar, löschbar oder erneut freigebbar
    assert (await env.api.put(f"/api/invoices/{d['id']}", json=draft(b2b))).status_code == 409
    assert (await env.api.delete(f"/api/invoices/{d['id']}")).status_code == 409
    assert (await env.api.post(f"/api/invoices/{d['id']}/issue")).status_code == 409

    # Auch direkt in der Datenbank geschützt (Trigger)
    async with env.engine.connect() as conn:
        for sql in (
            "UPDATE invoices SET total_gross = 1 WHERE status = 'issued'",
            "DELETE FROM invoices WHERE status = 'issued'",
            "UPDATE invoice_lines SET unit_price = 1",
        ):
            with pytest.raises(Exception, match=r"unveränderlich|gelöscht"):
                await conn.execute(text(sql))
            await conn.rollback()

    # B2C: Bruttopreise, Verzugshinweis
    d2 = (await env.api.post("/api/invoices", json=draft(b2c, price="29.00"))).json()
    assert d2["price_mode"] == "gross"
    i2 = (await env.api.post(f"/api/invoices/{d2['id']}/issue")).json()
    assert i2["number"] == f"RE-{YEAR}-00002"
    assert (i2["total_net"], i2["total_tax"], i2["total_gross"]) == ("24.37", "4.63", "29.00")
    assert B2C_DEFAULT_NOTE in i2["snapshot"]["notes"]

    # Storno
    r = await env.api.post(f"/api/invoices/{d['id']}/cancel", json={"reason": "Doppelt berechnet"})
    assert r.status_code == 200, r.text
    storno = r.json()
    assert storno["kind"] == "cancellation" and storno["number"] == f"RE-{YEAR}-00003"
    assert (storno["total_net"], storno["total_tax"], storno["total_gross"]) == ("-1758.00", "-334.02", "-2092.02")
    assert storno["cancels_invoice_id"] == d["id"]
    assert storno["snapshot"]["cancels"]["number"] == f"RE-{YEAR}-00001"
    assert storno["snapshot"]["buyer"] == issued["snapshot"]["buyer"]
    original = (await env.api.get(f"/api/invoices/{d['id']}")).json()
    assert original["cancelled_by"] == storno["id"]
    assert (await env.api.post(f"/api/invoices/{d['id']}/cancel", json={"reason": "nochmal"})).status_code == 409
    r = await env.api.post(f"/api/invoices/{storno['id']}/cancel", json={"reason": "Storno vom Storno"})
    assert r.status_code == 409

    # Liste
    listing = (await env.api.get("/api/invoices")).json()
    assert {x["number"] for x in listing} == {f"RE-{YEAR}-00001", f"RE-{YEAR}-00002", f"RE-{YEAR}-00003"}

    # Audit-Log enthält die Vorgänge
    async with env.engine.connect() as conn:
        actions = {r[0] for r in await conn.execute(text("SELECT action FROM audit_log"))}
    assert {"invoice.issued", "invoice.cancellation", "invoice.cancelled", "customer.created"} <= actions


async def test_issue_requires_complete_master_data(env: Env) -> None:
    b2b = (await env.api.post("/api/customers", json=B2B)).json()["id"]
    d = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    r = await env.api.post(f"/api/invoices/{d['id']}/issue")
    assert r.status_code == 422 and "Firmendaten fehlen" in r.json()["detail"]
    await env.api.post("/api/company-profile", json={**COMPANY, "iban": None})
    r = await env.api.post(f"/api/invoices/{d['id']}/issue")
    assert r.status_code == 422 and "IBAN" in r.json()["detail"]
    assert not env.ledger.records, "bei Fehlern wird nichts festgeschrieben und keine Nummer verbraucht"

    no_date = (await env.api.post("/api/invoices", json={**draft(b2b), "service_date_from": None,
                                                        "service_date_to": None})).json()
    await env.api.post("/api/company-profile", json={**COMPANY, "valid_from": "2026-01-02"})
    r = await env.api.post(f"/api/invoices/{no_date['id']}/issue")
    assert r.status_code == 422 and "Leistungsdatum" in r.json()["detail"]


async def test_lost_ledger_response_is_recovered_without_new_number(env: Env) -> None:
    b2b, _ = await setup_basics(env)
    d = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    env.ledger.fail_after_seal = True
    r = await env.api.post(f"/api/invoices/{d['id']}/issue")
    assert r.status_code == 502
    assert (await env.api.get(f"/api/invoices/{d['id']}")).json()["status"] == "draft"
    assert len(issued_records(env)) == 1  # festgeschrieben, aber Antwort verloren

    # Entwurf wird zwischenzeitlich geändert – die festgeschriebene Fassung gewinnt trotzdem
    await env.api.put(f"/api/invoices/{d['id']}", json=draft(b2b, price="1.00"))
    env.ledger.fail_after_seal = False
    issued = (await env.api.post(f"/api/invoices/{d['id']}/issue")).json()
    assert issued["number"] == f"RE-{YEAR}-00001"
    assert issued["total_gross"] == "2092.02"
    assert issued["lines"][0]["unit_price"] == "1758.0000"
    assert len(issued_records(env)) == 1, "keine zweite Nummer verbraucht"


async def test_parallel_issue_only_once(env: Env) -> None:
    b2b, _ = await setup_basics(env)
    d = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    results = await asyncio.gather(*(env.api.post(f"/api/invoices/{d['id']}/issue") for _ in range(5)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]
    assert len(issued_records(env)) == 1
    assert len(issued_records(env, "invoice.documents")) == 1


async def test_storno_of_gross_invoice_mirrors_exactly(env: Env) -> None:
    _, b2c = await setup_basics(env)
    body = draft(b2c, price="1.10")
    body["lines"].append({"description": "Buch", "quantity": "3", "unit_code": "C62", "unit_price": "12.99",
                          "tax_case": "OUT_DE_7"})
    d = (await env.api.post("/api/invoices", json=body)).json()
    issued = (await env.api.post(f"/api/invoices/{d['id']}/issue")).json()
    storno = (await env.api.post(f"/api/invoices/{d['id']}/cancel", json={"reason": "Widerruf"})).json()
    assert Decimal(storno["total_gross"]) == -Decimal(issued["total_gross"])
    assert Decimal(storno["total_tax"]) == -Decimal(issued["total_tax"])



async def test_send_without_mail_configuration(env: Env) -> None:
    b2b, _ = await setup_basics(env)
    d = (await env.api.post("/api/invoices", json=draft(b2b))).json()
    await env.api.post(f"/api/invoices/{d['id']}/issue")
    app.state.mailer = None
    r = await env.api.post(f"/api/invoices/{d['id']}/send")
    assert r.status_code == 503 and "nicht eingerichtet" in r.json()["detail"]


async def test_logo_upload(env: Env) -> None:
    assert (await env.api.get("/api/company-profile/logo")).status_code == 404
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="20"><rect width="80" height="20"/></svg>'
    r = await env.api.post("/api/company-profile/logo", files={"file": ("logo.svg", svg, "image/svg+xml")})
    assert r.status_code == 201 and r.json() == {"mime_type": "image/svg+xml"}
    got = await env.api.get("/api/company-profile/logo")
    assert got.content == svg and "default-src 'none'" in got.headers["content-security-policy"]
    bad = await env.api.post("/api/company-profile/logo",
                             files={"file": ("x.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")})
    assert bad.status_code == 422
    fake_png = await env.api.post("/api/company-profile/logo", files={"file": ("x.png", b"GIF89a", "image/png")})
    assert fake_png.status_code == 422
    assert (await env.api.delete("/api/company-profile/logo")).status_code == 204
    assert (await env.api.get("/api/company-profile/logo")).status_code == 404
