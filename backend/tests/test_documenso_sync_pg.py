"""In Documenso angelegte Dokumente erkennen (Webhook + Abgleich), abgeschlossene archivieren, Documenso-Vorlagen für Mitarbeiter."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.einvoice.pdf import render_plain_pdf
from app.main import app
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import Env, env, setup_basics  # noqa: F401
from tests.test_signatures_pg import FakeDocumenso, webhook_headers

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
SIGNED = render_plain_pdf("<h1>signiert</h1>")


class SyncingDocumenso(FakeDocumenso):
    """Attrappe mit Dokumentliste und Vorlagen wie die Documenso-API v2."""

    def __init__(self) -> None:
        super().__init__()
        self.docs: dict[int, dict[str, Any]] = {}
        self.used: list[dict[str, Any]] = []

    async def get_document(self, document_id: int) -> dict[str, Any]:
        return self.docs[document_id]

    async def list_documents(self, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        return {"data": list(self.docs.values()), "totalPages": 1}

    async def list_templates(self) -> dict[str, Any]:
        return {"data": [{"id": 7, "title": "Arbeitsvertrag (Documenso)", "recipients": [
            {"id": 71, "name": "", "email": "", "role": "SIGNER", "signingOrder": 1},
            {"id": 72, "name": "Geschäftsführung", "email": "gf@firma.de", "role": "SIGNER", "signingOrder": 2}]}]}

    async def use_template(self, *, template_id: int, recipients: list[dict[str, Any]], external_id: str, title: str) -> dict[str, Any]:
        doc_id = 500 + len(self.used)
        self.used.append({"template": template_id, "recipients": recipients, "external_id": external_id})
        self.docs[doc_id] = {"id": doc_id, "externalId": external_id, "title": title, "status": "PENDING", "templateId": template_id,
                             "recipients": [{"name": r.get("name"), "email": r["email"], "role": "SIGNER", "signingOrder": i + 1}
                                            for i, r in enumerate(recipients)]}
        return {"id": doc_id}

    async def download(self, document_id: int) -> bytes:
        return SIGNED


async def test_documents_created_in_documenso_are_recognized(env: Env) -> None:  # noqa: F811
    api = env.api
    fake = SyncingDocumenso()
    app.state.documenso = fake
    await setup_basics(env)
    fake.docs[42] = {"id": 42, "externalId": None, "title": "Mietvertrag Lager (in Documenso)", "status": "DRAFT",
                     "recipients": [{"name": "Vera Vermieter", "email": "vera@vermietung.de", "role": "SIGNER", "signingOrder": 1}]}
    # Webhook DOCUMENT_CREATED → übernommen (inkl. Formhinweis Mietvertrag)
    r = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_CREATED", "payload": {"id": 42}}, headers=webhook_headers())
    assert r.json()["result"] == "imported"
    doc = next(d for d in (await api.get("/api/signatures/documents")).json()["documents"] if d["documenso_id"] == 42)
    assert doc["source"] == "documenso" and doc["status"] == "draft" and doc["signer_email"] == "vera@vermietung.de"
    assert doc["form_level"] == "warn" and "550" in doc["form_message"]
    # In Documenso bearbeitet und versendet, dann abgeschlossen – der Abgleich holt es nach (verpasster Webhook)
    fake.docs[42] |= {"status": "PENDING", "title": "Mietvertrag Lager 2027"}
    assert (await api.post("/api/signatures/sync")).json()["updated"] == 1
    fake.docs[42]["status"] = "COMPLETED"
    res = (await api.post("/api/signatures/sync")).json()
    assert res["archived"] == 1
    doc = next(d for d in (await api.get("/api/signatures/documents")).json()["documents"] if d["documenso_id"] == 42)
    assert doc["status"] == "signed" and doc["title"] == "Mietvertrag Lager 2027" and doc["archive_document_id"]
    assert (await api.get(f"/api/signatures/documents/{doc['id']}/file")).content.startswith(b"%PDF"), "nur signiertes Exemplar vorhanden"
    assert (await api.post("/api/signatures/sync")).json()["archived"] == 0, "nicht doppelt archivieren"


async def test_documenso_template_for_employee(env: Env) -> None:  # noqa: F811
    api = env.api
    fake = SyncingDocumenso()
    app.state.documenso = fake
    await setup_basics(env)
    today = dt.date.today()  # noqa: DTZ011
    emp = (await api.post("/api/payroll/employees", json={
        "first_name": "Tina", "last_name": "Vorlage", "email": "tina@firma.de", "entry_date": today.isoformat(), "salary_type": "monthly",
        "gross_month": "3200", "weekly_hours": "40", "iban": "DE02120300000000202051"})).json()
    tpls = (await api.get("/api/personnel/documenso-templates")).json()
    assert tpls[0]["title"] == "Arbeitsvertrag (Documenso)" and len(tpls[0]["recipients"]) == 2
    body = {"template_id": 7, "recipients": [{"id": 71, "email": "tina@firma.de", "name": "Tina Vorlage"},
                                             {"id": 72, "email": "anna@beispiel.de", "name": "Anna"}]}
    r = await api.post(f"/api/personnel/{emp['id']}/documenso-template", json=body)
    assert r.status_code == 201, r.text
    did = r.json()["documenso_id"]
    assert fake.used[0]["external_id"].startswith("oe-doc:")
    # Abschluss → signiertes Exemplar in Archiv und Personalakte
    done = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_COMPLETED", "payload": {"id": did}}, headers=webhook_headers())
    fake.docs[did]["status"] = "COMPLETED"
    done = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_COMPLETED", "payload": {"id": did}}, headers=webhook_headers())
    assert done.json()["result"] == "archived"
    files = (await api.get(f"/api/personnel/{emp['id']}/documents")).json()["documents"]
    signed = next(f for f in files if "signiert" in f["title"])
    assert signed["signature_status"] == "signed" and signed["visible_to_employee"]
    # befristeter Mitarbeiter → Bestätigung nötig
    emp2 = (await api.post("/api/payroll/employees", json={
        "first_name": "Ben", "last_name": "Befristet", "email": "ben@firma.de", "entry_date": today.isoformat(),
        "exit_date": (today + dt.timedelta(days=200)).isoformat(), "salary_type": "monthly", "gross_month": "3200", "weekly_hours": "40",
        "iban": "DE02120300000000202051"})).json()
    r = await api.post(f"/api/personnel/{emp2['id']}/documenso-template", json=body | {"recipients": [{"id": 71, "email": "ben@firma.de"}]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "form_ack_required"
