"""E-Rechnungs-Dokumente: Erzeugen, Prüfen, unveränderlich Ablegen, Festschreiben, Versenden; Firmenlogo."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.einvoice import pdf as pdf_module
from app.einvoice.cii import build_cii
from app.einvoice.validate import validate_einvoice
from app.services import audit
from app.services.ledger_client import LedgerClient
from app.services.mail import Attachment, OutgoingMail, WorkspaceMailer

DOCUMENT_FORMAT = "factur-x-1.0 en16931 (ZUGFeRD 2.x EN 16931), PDF/A-3b"
MAX_LOGO_BYTES = 1024 * 1024
_SVG_FORBIDDEN = re.compile(rb"<script|<foreignObject|\son[a-z]+\s*=|javascript:|<!ENTITY", re.IGNORECASE)


class LogoError(ValueError):
    pass


class DeliveryError(RuntimeError):
    pass


# ───────────────────────── Logo ─────────────────────────

def detect_logo_type(content: bytes) -> str:
    """Bestimmt den Typ anhand des Inhalts (nicht des Dateinamens) und prüft SVGs auf aktive Inhalte."""
    if not content:
        raise LogoError("Datei ist leer")
    if len(content) > MAX_LOGO_BYTES:
        raise LogoError("Logo ist größer als 1 MB")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    head = content[:2048].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        if _SVG_FORBIDDEN.search(content):
            raise LogoError("SVG enthält Skripte oder unzulässige Elemente")
        return "image/svg+xml"
    raise LogoError("Nur PNG, JPEG oder SVG erlaubt")


async def active_logo(conn: AsyncConnection, tenant_id: UUID) -> tuple[bytes, str] | None:
    row = (await conn.execute(
        text("SELECT content, mime_type FROM company_logos WHERE tenant_id = :t AND active"), {"t": tenant_id}
    )).one_or_none()
    return (bytes(row.content), str(row.mime_type)) if row else None


async def set_logo(conn: AsyncConnection, tenant_id: UUID, content: bytes, actor: str) -> str:
    mime = detect_logo_type(content)
    digest = hashlib.sha256(content).hexdigest()
    await conn.execute(text("UPDATE company_logos SET active = false WHERE tenant_id = :t AND active"), {"t": tenant_id})
    await conn.execute(
        text("INSERT INTO company_logos (tenant_id, content, mime_type, sha256, created_by) "
             "VALUES (:t, :c, :m, :h, :a)"),
        {"t": tenant_id, "c": content, "m": mime, "h": digest, "a": actor},
    )
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="company_logo.uploaded", entity_type="company_logo",
                       entity_id=None, after={"sha256": digest, "mime_type": mime, "bytes": len(content)})
    return mime


async def remove_logo(conn: AsyncConnection, tenant_id: UUID, actor: str) -> None:
    await conn.execute(text("UPDATE company_logos SET active = false WHERE tenant_id = :t AND active"), {"t": tenant_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="company_logo.removed", entity_type="company_logo",
                       entity_id=None)


# ───────────────────────── Dokumente ─────────────────────────

@dataclass(frozen=True, slots=True)
class StoredDocuments:
    pdf: bytes
    xml: bytes
    pdf_sha256: str
    xml_sha256: str
    xrechnung: bytes | None = None


async def get_documents(conn: AsyncConnection, invoice_id: UUID) -> StoredDocuments | None:
    row = (await conn.execute(
        text("SELECT pdf, xml, pdf_sha256, xml_sha256, xrechnung FROM invoice_documents WHERE invoice_id = :id"), {"id": invoice_id}
    )).one_or_none()
    if row is None:
        return None
    return StoredDocuments(bytes(row.pdf), bytes(row.xml), row.pdf_sha256, row.xml_sha256,
                           bytes(row.xrechnung) if row.xrechnung is not None else None)


async def ensure_documents(
    conn: AsyncConnection,
    ledger: LedgerClient,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
    number: str,
    snapshot: dict[str, Any],
    actor: str,
) -> StoredDocuments:
    """Erzeugt PDF + XML genau einmal aus der festgeschriebenen Fassung, prüft sie und legt sie unveränderlich ab."""
    existing = await get_documents(conn, invoice_id)
    if existing is not None:
        return existing
    # Gleichzeitige Erzeugung verhindern (Sperre auf die Rechnung)
    await conn.execute(text("SELECT 1 FROM invoices WHERE id = :id FOR UPDATE"), {"id": invoice_id})
    existing = await get_documents(conn, invoice_id)
    if existing is not None:
        return existing

    xml = build_cii(number, snapshot)
    validate_einvoice(xml)  # wirft bei Verstoß gegen XSD oder EN-16931-Geschäftsregeln
    xrechnung = None
    if snapshot["buyer"].get("einvoice_format") == "xrechnung":
        from app.einvoice.validate import EInvoiceValidationError
        from app.einvoice.xrechnung import check_xrechnung, to_xrechnung

        try:
            xrechnung = to_xrechnung(xml, snapshot)
        except ValueError as exc:
            raise EInvoiceValidationError([str(exc)]) from exc
        problems = check_xrechnung(xrechnung)
        if problems:
            raise EInvoiceValidationError([f"XRechnung: {p}" for p in problems])
    logo = await active_logo(conn, tenant_id)
    pdf = pdf_module.render_facturx_pdf(
        number, snapshot, xml, logo=pdf_module.logo_data_uri(*logo) if logo else None
    )
    pdf_hash = hashlib.sha256(pdf).hexdigest()
    xml_hash = hashlib.sha256(xml).hexdigest()
    sealed = await ledger.seal(
        tenant_id=tenant_id,
        record_type="invoice.documents",
        payload={"invoice_number": number, "pdf_sha256": pdf_hash, "xml_sha256": xml_hash, "format": DOCUMENT_FORMAT,
                 **({"xrechnung_sha256": hashlib.sha256(xrechnung).hexdigest()} if xrechnung else {})},
        sealed_by=f"user:{actor}",
        subject_id=invoice_id,
    )
    await conn.execute(
        text("INSERT INTO invoice_documents (invoice_id, pdf, xml, pdf_sha256, xml_sha256, format, ledger_record_id, xrechnung, "
             "xrechnung_sha256) VALUES (:id, :pdf, :xml, :ph, :xh, :fmt, :rid, :xr, :xrh)"),
        {"id": invoice_id, "pdf": pdf, "xml": xml, "ph": pdf_hash, "xh": xml_hash, "fmt": DOCUMENT_FORMAT,
         "rid": sealed.id, "xr": xrechnung, "xrh": hashlib.sha256(xrechnung).hexdigest() if xrechnung else None},
    )
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.documents_created", entity_type="invoice",
                       entity_id=invoice_id, after={"pdf_sha256": pdf_hash, "xml_sha256": xml_hash})
    return StoredDocuments(pdf, xml, pdf_hash, xml_hash, xrechnung)


def file_name(number: str, extension: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", number) + f".{extension}"


# ───────────────────────── Versand ─────────────────────────

def compose_mail(number: str, snapshot: dict[str, Any], documents: StoredDocuments) -> OutgoingMail:
    seller = snapshot["seller"]
    buyer = snapshot["buyer"]
    credit = snapshot["kind"] == "cancellation"
    title = "Stornorechnung" if credit else "Rechnung"
    amount = pdf_module._euro(snapshot["totals"]["gross"])
    if snapshot.get("currency", "EUR") != "EUR":
        amount = amount.replace(" €", f" {snapshot['currency']}")
    salutation = "Guten Tag" if buyer["type"] == "b2b" else f"Guten Tag {buyer['name']}"
    lines = [
        f"{salutation},",
        "",
        f"anbei erhalten Sie unsere {title} {number} über {amount}.",
    ]
    if not credit:
        lines.append(f"Bitte überweisen Sie den Betrag bis zum {pdf_module._date(snapshot['due_date'])} "
                     f"unter Angabe der Rechnungsnummer {number}.")
    lines += [
        "",
        ("Die Rechnung liegt als XRechnung (XML, Standard 3.0) bei; das PDF dient nur zur Ansicht."
         if documents.xrechnung else
         "Die Rechnung liegt als ZUGFeRD-PDF (E-Rechnung nach EN 16931) bei; die strukturierten Daten sind im PDF "
         "eingebettet und zusätzlich als XML angehängt."),
        "",
        "Mit freundlichen Grüßen",
        seller["legal_name"],
    ]
    return OutgoingMail(
        to=[buyer["email"]],
        subject=f"{title} {number} – {seller['legal_name']}",
        text="\n".join(lines),
        attachments=([Attachment(file_name(number, "xml").replace(".xml", "_XRechnung.xml"), "application/xml", documents.xrechnung),
                      Attachment(file_name(number, "pdf"), "application/pdf", documents.pdf)] if documents.xrechnung else [
            Attachment(file_name(number, "pdf"), "application/pdf", documents.pdf),
            Attachment(file_name(number, "xml"), "application/xml", documents.xml),
        ]),
        reply_to=seller.get("email") or None,
    )


async def send_invoice(
    conn: AsyncConnection,
    mailer: WorkspaceMailer,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
    number: str,
    snapshot: dict[str, Any],
    documents: StoredDocuments,
    actor: str,
) -> dict[str, Any]:
    mail = compose_mail(number, snapshot, documents)
    message_id = await mailer.send(mail)  # MailError → Aufrufer
    row = (await conn.execute(
        text("INSERT INTO invoice_deliveries (invoice_id, channel, recipient, message_id, sent_by) "
             "VALUES (:id, 'email', :to, :mid, :by) RETURNING id, recipient, message_id, sent_at, sent_by"),
        {"id": invoice_id, "to": mail.to[0], "mid": message_id, "by": actor},
    )).mappings().one()
    await conn.execute(text("UPDATE invoices SET sent_at = now() WHERE id = :id"), {"id": invoice_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.sent", entity_type="invoice",
                       entity_id=invoice_id, after={"to": mail.to[0], "message_id": message_id})
    return dict(row)


async def list_deliveries(conn: AsyncConnection, invoice_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(
        text("SELECT id, recipient, message_id, sent_at, sent_by FROM invoice_deliveries "
             "WHERE invoice_id = :id ORDER BY sent_at"),
        {"id": invoice_id},
    )
    return [dict(r) for r in result.mappings()]


async def send_peppol(conn: AsyncConnection, *, tenant_id: UUID, invoice_id: UUID, number: str, snapshot: dict[str, Any],
                      documents: StoredDocuments, actor: str, client: Any = None) -> dict[str, Any]:
    """Übergabe an einen Peppol-Access-Point (HTTP-API des Dienstleisters). Gesendet wird die XRechnung bzw. die EN-16931-XML."""
    import httpx

    from app.core.config import get_settings

    s = get_settings()
    receiver = snapshot["buyer"].get("peppol_id")
    if not s.peppol_ap_url or not s.peppol_ap_token.get_secret_value():
        raise ValueError("Peppol-Access-Point ist nicht eingerichtet (System & Integrationen → E-Rechnung)")
    if not receiver:
        raise ValueError("Peppol-ID des Kunden fehlt (z. B. 0204:991-12345-67 für eine Leitweg-ID)")
    xml = documents.xrechnung or documents.xml
    own = client is None
    http = client or httpx.AsyncClient(timeout=30)
    try:
        r = await http.post(s.peppol_ap_url, content=xml, headers={
            "Authorization": f"Bearer {s.peppol_ap_token.get_secret_value()}", "Content-Type": "application/xml",
            "X-Peppol-Receiver": receiver, "X-Peppol-Sender": s.peppol_sender_id or "",
            "X-Document-Number": number})
    finally:
        if own:
            await http.aclose()
    if r.status_code >= 300:
        raise ValueError(f"Access Point lehnte ab: HTTP {r.status_code} {r.text[:300]}")
    try:
        message_id = str(r.json().get("id") or r.json().get("message_id") or "")
    except ValueError:
        message_id = r.headers.get("x-message-id", "")
    row = (await conn.execute(
        text("INSERT INTO invoice_deliveries (invoice_id, channel, recipient, message_id, sent_by) "
             "VALUES (:id, 'peppol', :to, :mid, :by) RETURNING id, channel, recipient, message_id, sent_at, sent_by"),
        {"id": invoice_id, "to": receiver, "mid": message_id or f"peppol:{number}", "by": actor},
    )).mappings().one()
    await conn.execute(text("UPDATE invoices SET sent_at = now() WHERE id = :id"), {"id": invoice_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.sent", entity_type="invoice", entity_id=invoice_id,
                       after={"to": receiver, "channel": "peppol", "message_id": message_id})
    return dict(row)
