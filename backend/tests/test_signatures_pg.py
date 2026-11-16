"""Documenso-Signatur gegen echtes PostgreSQL mit Documenso-Attrappe."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.main import app
from app.services.invoices import today_berlin
from tests.pgutil import ADMIN_URL
from tests.test_invoices_pg import COMPANY, Env, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
TODAY = today_berlin()


class FakeDocumenso:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.distributed: list[int] = []
        self.status: dict[int, str] = {}

    async def create_document(self, *, title: str, external_id: str, pdf: bytes, recipients: list[dict[str, Any]]) -> int:
        doc_id = 1000 + len(self.created)
        self.created.append({"title": title, "external_id": external_id, "pdf": pdf, "recipients": recipients})
        self.status[doc_id] = "PENDING"
        return doc_id

    async def distribute(self, document_id: int, *, subject: str, message: str) -> None:
        self.distributed.append(document_id)

    async def get_status(self, document_id: int) -> str:
        return self.status[document_id]

    async def download(self, document_id: int) -> bytes:
        return b"%PDF-1.7 signed " + str(document_id).encode()


def webhook_headers() -> dict[str, str]:
    return {"X-Documenso-Secret": get_settings().documenso_webhook_secret.get_secret_value()}


async def test_offer_signature_flow(env: Env) -> None:  # noqa: F811
    api = env.api
    fake = FakeDocumenso()
    app.state.documenso = fake
    b2b, _ = await setup_basics(env)
    offer = (await api.post("/api/offers", json={"customer_id": b2b, "title": "Rahmenvertrag", "valid_until": (TODAY + dt.timedelta(days=14)).isoformat(),
                                                 "lines": [{"description": "Projekt", "quantity": "1", "unit_code": "LS", "unit_price": "5000.00"}]})).json()
    req = {"subject_type": "offer", "subject_id": offer["id"], "signer_name": "Erika Muster", "signer_email": "Erika@HR1.de"}
    assert (await api.post("/api/signatures", json=req)).status_code == 409, "Entwurf kann nicht signiert werden"
    await api.post(f"/api/offers/{offer['id']}/send")

    r = await api.post("/api/signatures", json=req | {"countersign": True})
    assert r.status_code == 201, r.text
    sig = r.json()
    created = fake.created[0]
    assert created["external_id"] == f"openemployed:{sig['id']}" and fake.distributed == [sig["documenso_id"]]
    signer, counter = created["recipients"]
    assert signer["email"] == "erika@hr1.de" and signer["role"] == "SIGNER" and signer["signingOrder"] == 1
    assert counter["email"] == "anna@beispiel.de" and counter["signingOrder"] == 2
    field = signer["fields"][0]
    assert field["type"] == "SIGNATURE" and field["pageNumber"] == 2 and set(field) == {"type", "pageNumber", "pageX", "pageY", "width", "height"}
    assert (await api.post("/api/signatures", json=req)).status_code == 409, "nur eine offene Anfrage"

    # Webhook: falsches Geheimnis, unbekanntes Dokument, Zwischenereignis
    body = {"event": "DOCUMENT_COMPLETED", "payload": {"id": sig["documenso_id"]}}
    assert (await api.post("/api/webhooks/documenso", json=body, headers={"X-Documenso-Secret": "falsch"})).status_code == 401
    unknown = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_COMPLETED", "payload": {"id": 1}}, headers=webhook_headers())
    assert unknown.json()["ignored"] is True
    opened = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_OPENED", "payload": {"id": sig["documenso_id"]}},
                            headers=webhook_headers())
    assert opened.json()["result"] == "noted"

    done = await api.post("/api/webhooks/documenso", json=body, headers=webhook_headers())
    assert done.status_code == 200 and done.json()["result"] == "completed"
    again = await api.post("/api/webhooks/documenso", json=body, headers=webhook_headers())
    assert again.json()["ignored"] is True, "Webhook ist idempotent"

    o = (await api.get(f"/api/offers/{offer['id']}")).json()
    assert o["status"] == "accepted" and o["decision_channel"] == "signature" and o["decided_by_name"] == "Erika Muster"
    items = (await api.get(f"/api/signatures?subject_type=offer&subject_id={offer['id']}")).json()["items"]
    assert items[0]["status"] == "completed" and items[0]["signed_sha256"]
    signed = await api.get(f"/api/signatures/{sig['id']}/document?signed=true")
    assert signed.status_code == 200 and signed.content.startswith(b"%PDF-1.7 signed")
    assert "signature.completed" in [r.record_type for r in env.ledger.records]
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE signature_requests SET signed_sha256 = 'x'"))


async def test_contract_signature_activates_and_refresh(env: Env) -> None:  # noqa: F811
    api = env.api
    fake = FakeDocumenso()
    app.state.documenso = fake
    _, b2c = await setup_basics(env)
    c = (await api.post("/api/contracts", json={"customer_id": b2c, "title": "Privat-Abo", "start_date": TODAY.replace(day=1).isoformat(),
                                                "min_term_months": 12, "notice_months": 1, "renewal": "monthly",
                                                "lines": [{"description": "Abo", "quantity": "1", "unit_code": "MON", "unit_price": "9.99"}]})).json()
    sig = (await api.post("/api/signatures", json={"subject_type": "contract", "subject_id": c["id"], "signer_name": "Max Muster",
                                                   "signer_email": "max@example.de"})).json()
    assert (await api.post(f"/api/signatures/{sig['id']}/refresh")).json() == {"status": "pending"}
    fake.status[sig["documenso_id"]] = "COMPLETED"
    r = await api.post(f"/api/signatures/{sig['id']}/refresh")
    assert r.json()["result"] == "completed"
    assert (await api.get(f"/api/contracts/{c['id']}")).json()["status"] == "active"
    original = await api.get(f"/api/signatures/{sig['id']}/document")
    assert original.status_code == 200 and original.content.startswith(b"%PDF")


async def test_rejected_and_not_configured(env: Env) -> None:  # noqa: F811
    api = env.api
    app.state.documenso = None
    b2b, _ = await setup_basics(env)
    offer = (await api.post("/api/offers", json={"customer_id": b2b, "title": "X", "valid_until": (TODAY + dt.timedelta(days=5)).isoformat(),
                                                 "lines": [{"description": "x", "quantity": "1", "unit_price": "10.00"}]})).json()
    await api.post(f"/api/offers/{offer['id']}/send")
    req = {"subject_type": "offer", "subject_id": offer["id"], "signer_name": "Erika", "signer_email": "e@hr1.de"}
    assert (await api.post("/api/signatures", json=req)).status_code == 503
    fake = FakeDocumenso()
    app.state.documenso = fake
    sig = (await api.post("/api/signatures", json=req)).json()
    r = await api.post("/api/webhooks/documenso", json={"event": "DOCUMENT_REJECTED", "payload": {"id": sig["documenso_id"]}},
                       headers=webhook_headers())
    assert r.json()["result"] == "rejected"
    assert (await api.get(f"/api/offers/{offer['id']}")).json()["status"] == "sent", "Ablehnung der Signatur ≠ Ablehnung des Angebots"
    assert (await api.post("/api/signatures", json=req)).status_code == 201, "nach Ablehnung neue Anfrage möglich"
    assert COMPANY
