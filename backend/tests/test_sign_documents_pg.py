"""Formprüfung vor der elektronischen Signatur und Signatur beliebiger Verträge mit Archivierung."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain.signature_form import check
from app.einvoice.pdf import render_plain_pdf
from app.main import app
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401
from tests.test_signatures_pg import FakeDocumenso, webhook_headers

pdf = render_plain_pdf("<!doctype html><html><body><h1>Vertrag</h1><p>Text</p></body></html>")


def test_form_rules() -> None:
    assert check("general").level == "ok" and check("nda").level == "ok" and check("employment").level == "ok"
    assert check("employment_fixed_term").level == "warn" and "TzBfG" in check("employment_fixed_term").message
    assert check("lease").level == "warn" and check("non_compete").level == "warn"
    assert check("termination").level == "block" and check("guarantee").level == "block" and check("real_estate").level == "block"
    # Titel überstimmt die gewählte Art
    assert check("general", "Aufhebungsvertrag Max Muster").level == "block"
    assert check("service", "Gewerbemietvertrag Büro").kind == "lease"
    assert check("general", "Rahmenvertrag Hosting").level == "ok"
    assert check("employment", "Arbeitsvertrag (unbefristet, Muster)").level == "ok"
    assert check("employment", "Befristeter Arbeitsvertrag").level == "warn"


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_generic_contract_signature_and_archive(env: Env) -> None:  # noqa: F811
    api = env.api
    fake = FakeDocumenso()
    app.state.documenso = fake
    await setup_basics(env)

    def form(**kw: str) -> dict[str, str]:
        return {"title": "NDA Partner AG", "kind": "nda", "counterparty": "Partner AG", "signer_name": "Petra Partner",
                "signer_email": "Petra@Partner.de"} | kw

    # Bürgschaft: elektronisch ausgeschlossen → anlegen ja, aber nicht über Documenso
    r = await api.post("/api/signatures/documents", data=form(title="Bürgschaft", kind="guarantee"), files={"file": ("b.pdf", pdf, "application/pdf")})
    assert r.status_code == 201 and r.json()["form_level"] == "block"
    blocked = await api.post(f"/api/signatures/documents/{r.json()['id']}/send", json={"acknowledge_form": True})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "form_blocked"
    assert (await api.post("/api/signatures/documents", data=form(), files={"file": ("x.txt", b"hallo", "text/plain")})).status_code == 409
    broken = await api.post("/api/signatures/documents", data=form(), files={"file": ("k.pdf", b"%PDF-1.4 kaputt", "application/pdf")})
    assert broken.status_code == 409 and "beschädigt" in broken.text

    doc = (await api.post("/api/signatures/documents", data=form(), files={"file": ("nda.pdf", pdf, "application/pdf")})).json()
    assert doc["form_level"] == "ok" and doc["status"] == "draft"
    sent = await api.post(f"/api/signatures/documents/{doc['id']}/send", json={"countersign": True})
    assert sent.status_code == 201, sent.text
    assert fake.created[-1]["recipients"][0]["email"] == "petra@partner.de"
    # Abschluss per Webhook → signiert und im Archiv (Kategorie Vertrag)
    done = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_COMPLETED", "payload": {"id": sent.json()["documenso_id"]}},
                          headers=webhook_headers())
    assert done.json()["result"] == "completed"
    listed = next(d for d in (await api.get("/api/signatures/documents")).json()["documents"] if d["id"] == doc["id"])
    assert listed["status"] == "signed" and listed["archive_document_id"]
    arch = (await api.get(f"/api/archive/{listed['archive_document_id']}")).json()
    assert arch["category"] == "contract" and arch["party"] == "Partner AG"
    assert (await api.get(f"/api/signatures/documents/{doc['id']}/file?signed=true")).content.startswith(b"%PDF")

    # Mietvertrag: Schriftform → nur mit ausdrücklicher Bestätigung
    lease = (await api.post("/api/signatures/documents", data=form(title="Mietvertrag Lager", kind="lease"),
                            files={"file": ("m.pdf", pdf, "application/pdf")})).json()
    assert lease["form_level"] == "warn"
    r = await api.post(f"/api/signatures/documents/{lease['id']}/send", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "form_ack_required"
    assert (await api.post(f"/api/signatures/documents/{lease['id']}/send", json={"acknowledge_form": True})).status_code == 201


@pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
async def test_personnel_signature_form_check(env: Env) -> None:  # noqa: F811
    api = env.api
    app.state.documenso = FakeDocumenso()
    await setup_basics(env)
    today = dt.date.today()  # noqa: DTZ011
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Bea", "last_name": "Befristet", "email": "bea@firma.de", "entry_date": today.isoformat(),
        "exit_date": (today + dt.timedelta(days=365)).isoformat(), "salary_type": "monthly", "gross_month": "3000", "weekly_hours": "40",
        "iban": "DE02120300000000202051"})).json()
    up = await api.post(f"/api/personnel/{emp['id']}/documents", data={"category": "contract", "title": "Arbeitsvertrag"},
                        files={"file": ("v.pdf", pdf, "application/pdf")})
    did = up.json()["id"]
    r = await api.post(f"/api/personnel/documents/{did}/sign", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "form_ack_required" and "TzBfG" in r.json()["detail"]["message"]
    assert (await api.post(f"/api/personnel/documents/{did}/sign", json={"acknowledge_form": True})).status_code == 201
    term = await api.post(f"/api/personnel/{emp['id']}/documents", data={"category": "termination", "title": "Kündigung"},
                          files={"file": ("k.pdf", pdf, "application/pdf")})
    r = await api.post(f"/api/personnel/documents/{term.json()['id']}/sign", json={"acknowledge_form": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "form_blocked" and "623" in r.json()["detail"]["message"]
