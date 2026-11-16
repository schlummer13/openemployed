"""Documenso-Anbindung (API v2, selbst gehostet) und Signaturvorgänge für Angebote und Verträge.

Ablauf: PDF erzeugen (Dokument + Unterschriftenseite) → ``POST /api/v2/document/create`` (multipart, Empfänger mit
Signaturfeld auf der letzten Seite) → ``POST /api/v2/document/distribute`` (E-Mail durch Documenso) → Webhook
``DOCUMENT_COMPLETED`` (Header ``X-Documenso-Secret``) → signiertes PDF laden, Hash im Ledger festschreiben, Vorgang
automatisch zuordnen (Angebot angenommen / Vertrag aktiviert).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import logging
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import NotFoundError

log = logging.getLogger("selbstlite.documenso")


class SignatureError(RuntimeError):
    pass


class SignatureStateError(RuntimeError):
    pass


class DocumensoClient(Protocol):
    async def create_document(self, *, title: str, external_id: str, pdf: bytes, recipients: list[dict[str, Any]]) -> int: ...
    async def distribute(self, document_id: int, *, subject: str, message: str) -> None: ...
    async def get_status(self, document_id: int) -> str: ...
    async def download(self, document_id: int) -> bytes: ...


class HttpDocumensoClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(base_url=f"{base_url.rstrip('/')}/api/v2", timeout=timeout,
                                         headers={"Authorization": token, "Accept": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("message", resp.text)
            except ValueError:
                msg = resp.text
            raise SignatureError(f"Documenso {resp.status_code}: {str(msg)[:300]}")

    async def create_document(self, *, title: str, external_id: str, pdf: bytes, recipients: list[dict[str, Any]]) -> int:
        payload = {"title": title[:255], "externalId": external_id, "recipients": recipients,
                   "meta": {"language": "de", "timezone": "Europe/Berlin", "dateFormat": "dd.MM.yyyy HH:mm"}}
        try:
            resp = await self._client.post("/document/create", data={"payload": json.dumps(payload)},
                                           files={"file": (f"{external_id}.pdf", pdf, "application/pdf")})
        except httpx.HTTPError as exc:
            raise SignatureError(f"Documenso nicht erreichbar: {exc}") from exc
        self._check(resp)
        return int(resp.json()["id"])

    async def distribute(self, document_id: int, *, subject: str, message: str) -> None:
        resp = await self._client.post("/document/distribute", json={
            "documentId": document_id, "meta": {"subject": subject[:254], "message": message[:5000], "distributionMethod": "EMAIL",
                                                "language": "de", "timezone": "Europe/Berlin"}})
        self._check(resp)

    async def get_document(self, document_id: int) -> dict[str, Any]:
        try:
            resp = await self._client.get(f"/document/{document_id}")
        except httpx.HTTPError as exc:
            raise SignatureError(f"Documenso nicht erreichbar: {exc}") from exc
        self._check(resp)
        data: dict[str, Any] = resp.json()
        return data

    async def list_documents(self, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        resp = await self._client.get("/document", params={"page": page, "perPage": per_page, "orderByDirection": "desc"})
        self._check(resp)
        data: dict[str, Any] = resp.json()
        return data

    async def list_templates(self) -> dict[str, Any]:
        resp = await self._client.get("/template", params={"page": 1, "perPage": 100})
        self._check(resp)
        data: dict[str, Any] = resp.json()
        return data

    async def use_template(self, *, template_id: int, recipients: list[dict[str, Any]], external_id: str, title: str) -> dict[str, Any]:
        resp = await self._client.post("/template/use", json={
            "templateId": template_id, "externalId": external_id, "distributeDocument": True,
            "recipients": [{"id": int(r["id"]), "email": r["email"], "name": r.get("name") or ""} for r in recipients],
            "override": {"title": title[:255], "language": "de", "timezone": "Europe/Berlin"}})
        self._check(resp)
        data: dict[str, Any] = resp.json()
        return data

    async def get_status(self, document_id: int) -> str:
        resp = await self._client.get(f"/document/{document_id}")
        self._check(resp)
        return str(resp.json()["status"])

    async def download(self, document_id: int) -> bytes:
        resp = await self._client.get(f"/document/{document_id}/download", params={"version": "signed"})
        self._check(resp)
        if resp.headers.get("content-type", "").startswith("application/pdf"):
            return resp.content
        data = resp.json()
        if isinstance(data, dict):
            if data.get("downloadUrl"):
                file = await self._client.get(data["downloadUrl"])
                self._check(file)
                return file.content
            if data.get("data"):
                return base64.b64decode(data["data"])
        raise SignatureError("Unbekanntes Antwortformat beim Download des signierten Dokuments")


# ───────────────────────── PDF ─────────────────────────

SIGNATURE_BOX = {"x": 12.0, "y": 34.0, "w": 34.0, "h": 8.0}  # Prozent der Seite (Unterschriftenseite)
COUNTER_BOX = {"x": 54.0, "y": 34.0, "w": 34.0, "h": 8.0}


def signature_page_html(*, title: str, reference: str, signer: str, company: str, countersign: bool, b2c: bool) -> str:
    from html import escape

    withdrawal = ""
    if b2c:
        withdrawal = (
            "<h2>Widerrufsbelehrung</h2><p>Sie haben das Recht, binnen vierzehn Tagen ohne Angabe von Gründen diesen Vertrag zu "
            "widerrufen. Die Widerrufsfrist beträgt vierzehn Tage ab dem Tag des Vertragsabschlusses. Um Ihr Widerrufsrecht "
            f"auszuüben, müssen Sie uns ({escape(company)}) mittels einer eindeutigen Erklärung (z. B. E-Mail oder über unsere "
            "Kündigungs-/Widerrufsseite) über Ihren Entschluss informieren. Zur Wahrung der Frist reicht es aus, dass Sie die "
            "Mitteilung vor Ablauf der Frist absenden. Wenn Sie diesen Vertrag widerrufen, erstatten wir Ihnen alle Zahlungen "
            "unverzüglich, spätestens binnen vierzehn Tagen. Haben Sie verlangt, dass die Dienstleistung während der Widerrufsfrist "
            "beginnen soll, so haben Sie uns einen angemessenen Betrag zu zahlen, der dem Anteil der bis zum Widerruf erbrachten "
            "Leistungen entspricht.</p>")
    counter = (f'<div class="box" style="left:{COUNTER_BOX["x"]}%;"><div class="line"></div>{escape(company)}</div>'
               if countersign else "")
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 0; }}
body {{ font-family: "DejaVu Sans", sans-serif; font-size: 9.5pt; color: #0f172a; margin: 0; }}
.page {{ position: relative; width: 210mm; height: 297mm; padding: 20mm 25mm; box-sizing: border-box; }}
h1 {{ font-size: 14pt; margin: 0 0 4mm; }} h2 {{ font-size: 10.5pt; margin: 8mm 0 2mm; }}
.box {{ position: absolute; top: {SIGNATURE_BOX["y"] + SIGNATURE_BOX["h"]}%; width: {SIGNATURE_BOX["w"]}%;
        font-size: 8pt; color: #475569; }}
.line {{ border-top: 0.6pt solid #0f172a; margin-bottom: 1mm; }}
.withdrawal {{ position: absolute; top: 52%; left: 25mm; right: 25mm; font-size: 8pt; }}
</style></head><body><div class="page">
<h1>Unterschriften</h1>
<p>Mit der Unterschrift wird <strong>{escape(title)}</strong> ({escape(reference)}) verbindlich angenommen bzw. abgeschlossen.
Die vorstehenden Seiten sind Bestandteil dieser Vereinbarung. Die elektronische Signatur erfolgt über Documenso;
das Signaturzertifikat mit Prüfprotokoll wird dem Dokument beigefügt.</p>
<div class="box" style="left:{SIGNATURE_BOX["x"]}%;"><div class="line"></div>{escape(signer)}</div>
{counter}
<div class="withdrawal">{withdrawal}</div>
</div></body></html>"""


def with_signature_page(document_pdf: bytes, page_html: str) -> tuple[bytes, int]:
    from pypdf import PdfReader, PdfWriter
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False, fail_on_errors=False)
    page = HTML(string=page_html, url_fetcher=fetcher, base_url=None).write_pdf()
    writer = PdfWriter()
    for src in (document_pdf, page):
        for p in PdfReader(io.BytesIO(src)).pages:
            writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), len(writer.pages)


def contract_html(contract: dict[str, Any], seller: dict[str, Any], buyer: dict[str, Any], logo: str | None) -> str:
    from app.einvoice.pdf import _env

    return _env.get_template("contract.html").render(c=contract, seller=seller, buyer=buyer, logo=logo)


# ───────────────────────── Vorgänge ─────────────────────────

async def _subject(conn: AsyncConnection, tenant_id: UUID, subject_type: str, subject_id: UUID, today: dt.date) -> dict[str, Any]:
    """Liefert Titel, Referenz, Kundentyp und das Dokument-PDF des Vorgangs."""
    from app.einvoice import pdf as pdf_module
    from app.services import contracts as ct
    from app.services import offers as of
    from app.services.documents import active_logo
    from app.services.invoices import _buyer, _seller
    from app.services.masterdata import current_company_profile, get_customer

    if subject_type == "personnel_document":
        from app.services.hr import signature_subject

        subj = await signature_subject(conn, tenant_id, subject_id)
        subj.pop("form", None)
        return subj
    if subject_type == "document":
        from app.services.sign_documents import subject as doc_subject

        return await doc_subject(conn, tenant_id, subject_id)
    if subject_type == "offer":
        offer = await of.get_offer(conn, tenant_id, subject_id, today)
        if offer["status"] != "sent":
            raise SignatureStateError("Nur versendete, offene Angebote können zur Signatur gesendet werden")
        if offer["valid_until"] < today:
            raise SignatureStateError("Angebot ist abgelaufen")
        number, pdf = await of.render_pdf(conn, tenant_id, subject_id, today)
        return {"title": f"Angebot {number} – {offer['title']}", "reference": number, "b2c": offer["customer_type"] == "b2c",
                "customer_id": offer["customer_id"], "pdf": pdf}
    contract = await ct.get_contract(conn, tenant_id, subject_id)
    if contract["status"] != "draft":
        raise SignatureStateError("Nur Vertragsentwürfe können zur Signatur gesendet werden")
    profile = await current_company_profile(conn, tenant_id, today)
    if profile is None:
        raise SignatureError("Firmendaten fehlen")
    customer = await get_customer(conn, tenant_id, contract["customer_id"])
    logo = await active_logo(conn, tenant_id)
    html = contract_html(contract, _seller(profile), _buyer(customer), pdf_module.logo_data_uri(*logo) if logo else None)
    pdf = pdf_module.render_plain_pdf(html)
    return {"title": f"Vertrag {contract['number']} – {contract['title']}", "reference": contract["number"],
            "b2c": customer["customer_type"] == "b2c", "customer_id": contract["customer_id"], "pdf": pdf}


def build_recipients(*, signer_name: str, signer_email: str, countersigner: str | None, company: str, pages: int) -> list[dict[str, Any]]:
    def field(box: dict[str, float]) -> dict[str, Any]:
        return {"type": "SIGNATURE", "pageNumber": pages, "pageX": box["x"], "pageY": box["y"], "width": box["w"], "height": box["h"]}

    recipients = [{"email": signer_email, "name": signer_name, "role": "SIGNER", "signingOrder": 1, "fields": [field(SIGNATURE_BOX)]}]
    if countersigner:
        recipients.append({"email": countersigner, "name": company, "role": "SIGNER", "signingOrder": 2, "fields": [field(COUNTER_BOX)]})
    return recipients


async def create_request(conn: AsyncConnection, client: DocumensoClient, tenant_id: UUID, *, subject_type: str, subject_id: UUID,
                         signer_name: str, signer_email: str, countersigner_email: str | None, actor: str,
                         today: dt.date) -> dict[str, Any]:
    from app.services.masterdata import current_company_profile

    pending = await conn.scalar(text("SELECT id FROM signature_requests WHERE subject_type = :s AND subject_id = :id "
                                     "AND status = 'pending'"), {"s": subject_type, "id": subject_id})
    if pending:
        raise SignatureStateError("Für diesen Vorgang läuft bereits eine Signaturanfrage")
    subject = await _subject(conn, tenant_id, subject_type, subject_id, today)
    profile = await current_company_profile(conn, tenant_id, today)
    company = profile["legal_name"] if profile else ""
    page_html = signature_page_html(title=subject["title"], reference=subject["reference"], signer=signer_name, company=company,
                                    countersign=bool(countersigner_email), b2c=subject["b2c"])
    pdf, pages = with_signature_page(subject["pdf"], page_html)
    sha = hashlib.sha256(pdf).hexdigest()
    request_id = await conn.scalar(text(
        "INSERT INTO signature_requests (tenant_id, subject_type, subject_id, title, signer_name, signer_email, countersigner_email, "
        "original_pdf, original_sha256, created_by) VALUES (:t, :st, :sid, :title, :n, :e, :ce, :pdf, :sha, :a) RETURNING id"),
        {"t": tenant_id, "st": subject_type, "sid": subject_id, "title": subject["title"], "n": signer_name, "e": signer_email.lower(),
         "ce": countersigner_email, "pdf": pdf, "sha": sha, "a": actor})
    doc_id = await client.create_document(
        title=subject["title"], external_id=f"selbstlite:{request_id}", pdf=pdf,
        recipients=build_recipients(signer_name=signer_name, signer_email=signer_email.lower(), countersigner=countersigner_email,
                                    company=company, pages=pages))
    await conn.execute(text("UPDATE signature_requests SET documenso_id = :d, last_event = 'CREATED' WHERE id = :id"),
                       {"d": doc_id, "id": request_id})
    if subject_type == "personnel_document":
        from app.services.hr import mark_signature

        await mark_signature(conn, subject_id, "pending")
    elif subject_type == "document":
        from app.services.sign_documents import mark

        await mark(conn, tenant_id, subject_id, "pending")
    await client.distribute(doc_id, subject=f"Bitte unterschreiben: {subject['title']}",
                            message=f"Guten Tag {signer_name},\n\nbitte prüfen und unterschreiben Sie {subject['title']}.\n\n"
                                    f"Mit freundlichen Grüßen\n{company}")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="signature.requested", entity_type=subject_type,
                       entity_id=subject_id, after={"documenso_id": doc_id, "signer": signer_email, "sha256": sha})
    return {"id": request_id, "documenso_id": doc_id, "status": "pending"}


async def list_requests(conn: AsyncConnection, tenant_id: UUID, subject_type: str | None = None,
                        subject_id: UUID | None = None) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, subject_type, subject_id, title, signer_name, signer_email, countersigner_email, documenso_id, status, "
        "original_sha256, signed_sha256, last_event, created_by, created_at, completed_at FROM signature_requests "
        "WHERE tenant_id = :t AND (CAST(:st AS text) IS NULL OR subject_type = :st) AND (CAST(:sid AS uuid) IS NULL OR subject_id = :sid) "
        "ORDER BY created_at DESC LIMIT 200"), {"t": tenant_id, "st": subject_type, "sid": subject_id})
    return [dict(r) for r in rows.mappings()]


async def document(conn: AsyncConnection, tenant_id: UUID, request_id: UUID, signed: bool) -> tuple[str, bytes]:
    row = (await conn.execute(text("SELECT title, original_pdf, signed_pdf FROM signature_requests WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": request_id})).one_or_none()
    if row is None or (signed and row.signed_pdf is None):
        raise NotFoundError("Dokument nicht gefunden")
    return row.title, bytes(row.signed_pdf if signed else row.original_pdf)


async def apply_event(conn: AsyncConnection, client: DocumensoClient, ledger: Any, tenant_id: UUID, *, documenso_id: int,
                      event: str) -> dict[str, Any]:
    """Verarbeitet ein Documenso-Ereignis idempotent (Webhook oder manueller Abgleich)."""
    from app.services import contracts as ct
    from app.services import offers as of
    from app.services.invoices import today_berlin

    row = (await conn.execute(text("SELECT * FROM signature_requests WHERE tenant_id = :t AND documenso_id = :d FOR UPDATE"),
                              {"t": tenant_id, "d": documenso_id})).mappings().one_or_none()
    if row is None:
        # In Documenso direkt angelegt bzw. aus einer Vorlage erzeugt → abgleichen
        if event.startswith("DOCUMENT_") and hasattr(client, "get_document"):
            from app.services.documenso_setup import upsert_external

            try:
                result = await upsert_external(conn, client, tenant_id, await client.get_document(documenso_id))
            except SignatureError:
                return {"ignored": True, "reason": "Dokument nicht abrufbar"}
            return {"ignored": False, "result": result}
        return {"ignored": True, "reason": "unbekanntes Dokument"}
    if row["status"] != "pending":
        await conn.execute(text("UPDATE signature_requests SET last_event = :e WHERE id = :id"), {"e": event, "id": row["id"]})
        return {"ignored": True, "reason": "bereits abgeschlossen"}
    actor = "system:documenso"
    if event == "DOCUMENT_COMPLETED":
        signed = await client.download(documenso_id)
        if not signed.startswith(b"%PDF"):
            raise SignatureError("Signiertes Dokument ist kein PDF")
        sha = hashlib.sha256(signed).hexdigest()
        sealed = await ledger.seal(tenant_id=tenant_id, record_type="signature.completed", sealed_by=actor, subject_id=row["subject_id"],
                                   payload={"signature_request_id": str(row["id"]), "subject_type": row["subject_type"],
                                            "documenso_id": documenso_id, "original_sha256": row["original_sha256"],
                                            "signed_sha256": sha, "signer_email": row["signer_email"]})
        await conn.execute(text(
            "UPDATE signature_requests SET status = 'completed', signed_pdf = :p, signed_sha256 = :s, ledger_record_id = :r, "
            "completed_at = now(), last_event = :e WHERE id = :id"), {"p": signed, "s": sha, "r": sealed.id, "e": event, "id": row["id"]})
        if row["subject_type"] == "personnel_document":
            from app.services.hr import mark_signature

            await mark_signature(conn, row["subject_id"], "signed", signed)
        elif row["subject_type"] == "document":
            from app.services.sign_documents import mark

            await mark(conn, tenant_id, row["subject_id"], "signed", signed)
        elif row["subject_type"] == "offer":
            offer = await of.get_offer(conn, tenant_id, row["subject_id"], today_berlin())
            if offer["status"] == "sent" and offer["valid_until"] >= today_berlin():
                await of.decide(conn, tenant_id, row["subject_id"], accept=True, name=row["signer_name"], channel="signature",
                                note=f"Digital signiert (Documenso #{documenso_id}, SHA-256 {sha[:16]}…)", actor=actor,
                                today=today_berlin())
            elif offer["status"] == "sent":
                # Verspätete Annahme gilt als neues Angebot (§ 150 Abs. 1 BGB) → bewusst keine automatische Annahme
                await audit.record(conn, tenant_id=tenant_id, actor=actor, action="signature.late_acceptance", entity_type="offer",
                                   entity_id=row["subject_id"], after={"note": "Signatur nach Ablauf der Bindefrist – manuell prüfen"})
        else:
            contract = await ct.get_contract(conn, tenant_id, row["subject_id"])
            if contract["status"] == "draft":
                await ct.activate(conn, tenant_id, row["subject_id"], actor)
        result = "completed"
    elif event in ("DOCUMENT_REJECTED", "DOCUMENT_CANCELLED"):
        status = "rejected" if event == "DOCUMENT_REJECTED" else "cancelled"
        await conn.execute(text("UPDATE signature_requests SET status = :s, completed_at = now(), last_event = :e WHERE id = :id"),
                           {"s": status, "e": event, "id": row["id"]})
        if row["subject_type"] == "personnel_document":
            from app.services.hr import mark_signature

            await mark_signature(conn, row["subject_id"], "rejected")
        elif row["subject_type"] == "document":
            from app.services.sign_documents import mark

            await mark(conn, tenant_id, row["subject_id"], "rejected")
        result = status
    else:
        await conn.execute(text("UPDATE signature_requests SET last_event = :e WHERE id = :id"), {"e": event, "id": row["id"]})
        result = "noted"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"signature.{result}", entity_type=row["subject_type"],
                       entity_id=row["subject_id"], after={"documenso_id": documenso_id, "event": event})
    return {"ignored": False, "result": result}


async def refresh(conn: AsyncConnection, client: DocumensoClient, ledger: Any, tenant_id: UUID, request_id: UUID) -> dict[str, Any]:
    """Abgleich ohne Webhook (z. B. wenn der Webhook nicht zugestellt wurde)."""
    row = (await conn.execute(text("SELECT documenso_id, status FROM signature_requests WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": request_id})).one_or_none()
    if row is None or row.documenso_id is None:
        raise NotFoundError("Signaturvorgang nicht gefunden")
    status = await client.get_status(row.documenso_id)
    event = {"COMPLETED": "DOCUMENT_COMPLETED", "REJECTED": "DOCUMENT_REJECTED", "CANCELLED": "DOCUMENT_CANCELLED"}.get(status)
    if event is None:
        return {"status": status.lower()}
    return await apply_event(conn, client, ledger, tenant_id, documenso_id=row.documenso_id, event=event)
