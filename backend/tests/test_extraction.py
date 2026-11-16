"""Belegerkennung: Steuerfall-Ableitung (Unit) und Ablauf mit E-Rechnung bzw. KI-Attrappe (PostgreSQL)."""

from __future__ import annotations

from typing import Any

import pytest

from app.main import app
from app.services.extraction import AI_SCHEMA, parse_einvoice, suggest
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

BASE = {"is_invoice": True, "supplier_name": "Muster GmbH", "supplier_vat_id": "DE123456789", "supplier_country": "DE",
        "invoice_number": "R-1", "receipt_date": "2026-11-02", "service_date": None, "currency": "EUR", "net_amount": "100.00",
        "tax_amount": "19.00", "gross_amount": "119.00", "tax_rates_percent": ["19"], "reverse_charge_note": False,
        "small_business_note": False, "already_paid": False, "category": "software", "description": "Lizenz", "confidence": 0.97,
        "notes": ""}


def s(**over: Any) -> dict[str, Any]:
    return suggest(BASE | over, source="ai")


def test_schema_is_strict() -> None:
    assert AI_SCHEMA["additionalProperties"] is False and set(AI_SCHEMA["required"]) == set(AI_SCHEMA["properties"])


@pytest.mark.parametrize(("over", "case", "tax"), [
    ({}, "IN_DE_19", "19.00"),
    ({"tax_rates_percent": ["7"], "tax_amount": "7.00", "gross_amount": "107.00"}, "IN_DE_7", "7.00"),
    ({"supplier_country": "IE", "supplier_vat_id": "IE6388047V", "reverse_charge_note": True, "tax_amount": "0.00",
      "gross_amount": "100.00", "tax_rates_percent": []}, "IN_13B_EU_SERVICE", "0.00"),
    ({"supplier_country": "US", "supplier_vat_id": None, "tax_amount": "0.00", "gross_amount": "100.00", "tax_rates_percent": [],
      "reverse_charge_note": True}, "IN_13B_DRITTLAND_SERVICE", "0.00"),
    ({"small_business_note": True, "tax_amount": "0.00", "gross_amount": "100.00", "tax_rates_percent": []}, "IN_OHNE_VST", "0.00"),
    ({"category": "hospitality"}, "IN_BEWIRTUNG", "19.00"),
])
def test_tax_case_rules(over: dict[str, Any], case: str, tax: str) -> None:
    r = s(**over)
    assert r["tax_case"] == case and r["tax_amount"] == tax


def test_warnings() -> None:
    assert any("Rechenprüfung" in w for w in s(gross_amount="120.00")["warnings"])
    assert any("Fremdwährung" in w for w in s(currency="USD")["warnings"])
    foreign = s(supplier_country="NL", supplier_vat_id="NL123", tax_amount="21.00", gross_amount="121.00", tax_rates_percent=["21"])
    assert foreign["tax_case"] == "IN_OHNE_VST" and any("nicht als Vorsteuer" in w for w in foreign["warnings"])
    assert any("Geringe" in w for w in s(confidence=0.4)["warnings"])
    assert any("mehrere" in w.lower() for w in s(tax_rates_percent=["19", "7"])["warnings"])
    rc_with_tax = s(supplier_country="IE", reverse_charge_note=True)
    assert rc_with_tax["tax_amount"] == "0.00" and any("Steuer ausgewiesen" in w for w in rc_with_tax["warnings"])


def test_rejects_xxe() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(Exception):  # noqa: B017 – jede Ablehnung ist recht, Hauptsache kein Dateizugriff
        parse_einvoice(evil)


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, content: bytes, mime_type: str, context: str) -> tuple[dict[str, Any], dict[str, int], str]:
        self.calls += 1
        assert "Beispiel Software UG" in context
        return BASE | {"supplier_name": "Anthropic, PBC", "supplier_vat_id": None, "supplier_country": "US",
                       "reverse_charge_note": True, "tax_amount": "0.00", "gross_amount": "100.00", "tax_rates_percent": []}, \
            {"input": 1000, "output": 200}, "claude-opus-5-5"


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_extract_flow(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    xml = (await api.get(f"/api/invoices/{inv['id']}/xml")).content

    # E-Rechnung: deterministisch, ohne KI
    app.state.extractor = None
    up = (await api.post("/api/receipt-files", files={"file": ("rechnung.xml", xml, "application/xml")})).json()
    r = await api.post(f"/api/receipt-files/{up['id']}/extract")
    assert r.status_code == 200, r.text
    sug = r.json()
    assert sug["source"] == "einvoice" and sug["invoice_number"].startswith("RE-") and sug["tax_case"] == "IN_DE_19"
    assert sug["net_amount"] == "1758.00" and sug["tax_amount"] == "334.02" and sug["gross_amount"] == "2092.02"

    # Bild ohne KI-Schlüssel → 503, manuelle Erfassung
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    img = (await api.post("/api/receipt-files", files={"file": ("quittung.png", png, "image/png")})).json()
    assert (await api.post(f"/api/receipt-files/{img['id']}/extract")).status_code == 503

    fake = FakeExtractor()
    app.state.extractor = fake
    first = (await api.post(f"/api/receipt-files/{img['id']}/extract")).json()
    assert first["tax_case"] == "IN_13B_DRITTLAND_SERVICE" and first["cached"] is False and first["supplier_id"] is None
    second = (await api.post(f"/api/receipt-files/{img['id']}/extract")).json()
    assert second["cached"] is True and fake.calls == 1, "jede Datei höchstens einmal an die KI"

    # Vorschlag lässt sich unverändert als Entwurf speichern
    supplier = (await api.post("/api/suppliers", json={"name": "Anthropic, PBC", "country_code": "US"})).json()
    third = (await api.post(f"/api/receipt-files/{img['id']}/extract")).json()
    assert third["supplier_id"] == supplier["id"], "Lieferant über den Namen zugeordnet"
    draft_r = await api.post("/api/receipts", json={
        "supplier_id": supplier["id"], "file_id": img["id"], "receipt_date": third["receipt_date"], "description": third["description"],
        "category": third["category"], "tax_case": third["tax_case"], "net_amount": third["net_amount"], "tax_amount": third["tax_amount"],
        "invoice_number": third["invoice_number"]})
    assert draft_r.status_code == 201, draft_r.text
    assert draft_r.json()["status"] == "draft" and draft_r.json()["rc_tax_amount"] == "19.00"
