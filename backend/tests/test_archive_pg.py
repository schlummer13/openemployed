"""Revisionssicheres Archiv: automatische Übernahme, Upload, Volltextsuche, Unveränderlichkeit, Fristen, Kettenprüfung."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.archive import ArchivingMailer, retain_until
from app.services.mail import OutgoingMail
from tests.conftest import FakeMailer
from tests.pgutil import ADMIN_URL
from tests.test_accounting_pg import TODAY
from tests.test_invoices_pg import Env, draft, env, setup_basics  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="BACKEND_TEST_ADMIN_URL nicht gesetzt")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


async def test_archive_sync_upload_search_and_guards(env: Env) -> None:  # noqa: F811
    api = env.api
    b2b, _ = await setup_basics(env)
    inv = (await api.post("/api/invoices", json=draft(b2b))).json()
    await api.post(f"/api/invoices/{inv['id']}/issue")
    await api.get(f"/api/invoices/{inv['id']}/document/pdf")  # erzeugt die Dokumente
    s1 = (await api.post("/api/archive/sync")).json()
    assert s1.get("invoice_out") == 1
    assert (await api.post("/api/archive/sync")).json() == {}, "idempotent"

    up = await api.post("/api/archive", files={"file": ("Mietvertrag.txt", "Mietvertrag Büro Kaiserstraße, Vermieter Rheinblick".encode(),
                                                        "text/plain")},
                        data={"category": "contract", "title": "Mietvertrag Büro", "document_date": TODAY.isoformat(), "party": "Rheinblick"})
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]
    bad = await api.post("/api/archive", files={"file": ("x.exe", b"MZ", "application/x-msdownload")},
                         data={"category": "other", "title": "x", "document_date": TODAY.isoformat()})
    assert bad.status_code == 409

    found = (await api.get("/api/archive", params={"q": "Kaiserstraße"})).json()
    assert [d["id"] for d in found["documents"]] == [doc_id] and "«" in (found["documents"][0]["snippet"] or "")
    allc = (await api.get("/api/archive", params={"category": "invoice_out"})).json()
    assert allc["total"] == 1 and allc["documents"][0]["retention_years"] == 8
    doc = (await api.get(f"/api/archive/{doc_id}")).json()
    assert doc["retain_until"] == retain_until(TODAY, 10).isoformat() and doc["number"].startswith(f"DOK-{TODAY.year}-")
    f = await api.get(f"/api/archive/{doc_id}/file")
    assert f.status_code == 200 and "Rheinblick" in f.text and f.headers["x-content-type-options"] == "nosniff"

    assert (await api.put(f"/api/archive/{doc_id}", json={"title": "Mietvertrag Büro 2026", "tags": ["miete"]})).status_code == 204
    assert (await api.post(f"/api/archive/{doc_id}/purge")).status_code == 409, "Frist läuft noch"
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError, match="unveränderlich"):
            await conn.execute(text("UPDATE archive_documents SET document_date = document_date - 1 WHERE id = :id"), {"id": doc_id})
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError, match="gelöscht"):
            await conn.execute(text("DELETE FROM archive_documents WHERE id = :id"), {"id": doc_id})
    async with env.engine.begin() as conn:
        with pytest.raises(DBAPIError, match="Aufbewahrungsfrist"):
            await conn.execute(text("UPDATE archive_documents SET content = NULL WHERE id = :id"), {"id": doc_id})
    v = (await api.get("/api/archive/verify")).json()
    assert v["ok"] and v["checked"] == 2


async def test_sent_mail_is_archived(env: Env) -> None:  # noqa: F811
    from app.core.config import get_settings

    await setup_basics(env)
    inner = FakeMailer()
    inner.from_name = "UG"  # type: ignore[attr-defined]
    mailer = ArchivingMailer(inner, env.engine, get_settings().tenant_id)
    mid = await mailer.send(OutgoingMail(to=["kunde@firma.de"], subject="Ihre Rechnung RE-1", text="Anbei die Rechnung."))
    assert mid == "<test@beispiel.de>" and len(inner.sent) == 1
    hits = (await env.api.get("/api/archive", params={"q": "Rechnung", "category": "mail"})).json()
    assert hits["total"] == 1 and hits["documents"][0]["mime_type"] == "message/rfc822"
    eml = (await env.api.get(f"/api/archive/{hits['documents'][0]['id']}/file")).content
    assert b"Subject: Ihre Rechnung RE-1" in eml and b"Message-ID: <test@beispiel.de>" in eml
