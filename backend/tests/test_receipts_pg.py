"""Belegablauf gegen echtes PostgreSQL: Lieferant → Upload → Entwurf → Buchung (Ledger) → Storno."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env  # noqa: F401 – Fixture wiederverwenden

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")

PDF = b"%PDF-1.4\n% Testbeleg Hetzner\n"


async def test_receipt_lifecycle(env: Env) -> None:  # noqa: F811
    api = env.api
    meta = (await api.get("/api/expense-meta")).json()
    assert any(c["code"] == "software" for c in meta["categories"])
    assert any(t["code"] == "IN_13B_EU_SERVICE" for t in meta["tax_cases"])

    hetzner = (await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH", "default_category": "hosting"})).json()
    anthropic = (await api.post("/api/suppliers", json={
        "name": "Anthropic Ireland, Ltd.", "country_code": "IE", "vat_id": "ie 1234567x",
        "default_tax_case": "IN_13B_EU_SERVICE", "default_category": "software"})).json()
    assert anthropic["vat_id"] == "IE1234567X"
    assert (await api.post("/api/suppliers", json={"name": "Hetzner Online GmbH"})).status_code == 409

    # Upload: Typ nach Inhalt, Dubletten erkannt
    up = (await api.post("/api/receipt-files", files={"file": ("rechnung.pdf", PDF, "application/pdf")})).json()
    assert up["mime_type"] == "application/pdf" and up["duplicate_of_receipt"] is None
    bad = await api.post("/api/receipt-files", files={"file": ("x.pdf", b"MZ\x90\x00", "application/pdf")})
    assert bad.status_code == 422
    xxe = await api.post("/api/receipt-files", files={"file": ("x.xml", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>', "application/xml")})
    assert xxe.status_code == 422

    body = {"supplier_id": hetzner["id"], "file_id": up["id"], "receipt_date": "2026-11-03", "invoice_number": "R0001",
            "description": "Server HR 1 (11/2026)", "category": "hosting", "tax_case": "IN_DE_19",
            "net_amount": "45.00", "tax_amount": "8.55"}
    draft = (await api.post("/api/receipts", json=body)).json()
    assert draft["status"] == "draft" and draft["gross_amount"] == "53.55" and draft["input_tax_amount"] == "8.55"
    # Dublette Rechnungsnummer
    assert (await api.post("/api/receipts", json=body)).status_code == 409

    booked = (await api.post(f"/api/receipts/{draft['id']}/book")).json()
    assert booked["status"] == "booked" and booked["ledger_hash"]
    sealed = next(r for r in env.ledger.records if r.record_type == "receipt.booked")
    assert sealed.payload["gross_amount"] == "53.55" and sealed.payload["skr04_account"] == "6495"
    assert len(sealed.payload["file_sha256"]) == 64

    # Unveränderlich
    assert (await api.put(f"/api/receipts/{draft['id']}", json=body)).status_code == 409
    assert (await api.delete(f"/api/receipts/{draft['id']}")).status_code == 409
    async with env.engine.connect() as conn:
        with pytest.raises(Exception, match=r"unveränderlich|gelöscht|permission denied"):
            await conn.execute(text("UPDATE receipts SET net_amount = 1, gross_amount = 9.55 WHERE status = 'booked'"))
        await conn.rollback()
        with pytest.raises(Exception, match=r"permission denied|verboten"):
            await conn.execute(text("UPDATE receipt_files SET file_name = 'x'"))
        await conn.rollback()

    # Datei abrufbar, gleicher Upload erkennt die Verknüpfung
    f = await api.get(f"/api/receipt-files/{up['id']}")
    assert f.content == PDF and f.headers["content-type"] == "application/pdf"
    again = (await api.post("/api/receipt-files", files={"file": ("kopie.pdf", PDF, "application/pdf")})).json()
    assert again["duplicate_of_receipt"] == draft["id"]

    # Storno
    rev = (await api.post(f"/api/receipts/{draft['id']}/reverse", json={"reason": "falscher Lieferant"})).json()
    assert rev["kind"] == "reversal" and rev["gross_amount"] == "-53.55" and rev["input_tax_amount"] == "-8.55"
    assert (await api.get(f"/api/receipts/{draft['id']}")).json()["reversed_by"] == rev["id"]
    assert (await api.post(f"/api/receipts/{draft['id']}/reverse", json={"reason": "nochmal"})).status_code == 409

    # Reverse Charge: Beleg ohne USt, Buchung ohne Datei verboten
    up2 = (await api.post("/api/receipt-files", files={"file": ("claude.pdf", PDF + b"x", "application/pdf")})).json()
    rc_draft = (await api.post("/api/receipts", json={
        "supplier_id": anthropic["id"], "receipt_date": "2026-11-01", "description": "Claude Team 11/2026",
        "category": "software", "tax_case": "IN_13B_EU_SERVICE", "net_amount": "300.00"})).json()
    assert rc_draft["rc_tax_amount"] == "57.00" and rc_draft["input_tax_amount"] == "57.00"
    r = await api.post(f"/api/receipts/{rc_draft['id']}/book")
    assert r.status_code == 422 and "Belegdatei" in r.json()["detail"]
    rc_body = {"supplier_id": anthropic["id"], "file_id": up2["id"], "receipt_date": "2026-11-01",
               "description": "Claude Team 11/2026", "category": "software", "tax_case": "IN_13B_EU_SERVICE",
               "net_amount": "300.00", "tax_amount": "57.00"}
    r = await api.put(f"/api/receipts/{rc_draft['id']}", json=rc_body)
    assert r.status_code == 422 and "Reverse Charge" in r.json()["detail"]
    rc_body["tax_amount"] = "0"
    await api.put(f"/api/receipts/{rc_draft['id']}", json=rc_body)
    assert (await api.post(f"/api/receipts/{rc_draft['id']}/book")).json()["status"] == "booked"

    listing = (await api.get("/api/receipts")).json()
    assert len(listing) == 3
