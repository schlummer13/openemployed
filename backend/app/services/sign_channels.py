"""Versandwege für Verträge, die Documenso rechtlich nicht abdeckt (Schriftform) – oder wenn Papier gewünscht ist.

- ``qes``: qualifizierte elektronische Signatur über den eingerichteten Anbieter (ersetzt die Schriftform, § 126a BGB).
- ``paper``: ausdrucken, eigenhändig unterschreiben lassen, unterschriebenes Exemplar einscannen und hochladen.
- ``email``: PDF per E-Mail an die Gegenseite mit der Bitte, auszudrucken, zu unterschreiben und das Original zurückzusenden.

Bei Papier/E-Mail gilt: Der Scan ist eine Kopie – das **Original** mit Unterschrift muss aufbewahrt werden (Schriftform-Nachweis).
Bei Erklärungen, für die die elektronische Form ausgeschlossen ist (Kündigung, Aufhebungsvertrag, Bürgschaft), ist die E-Mail nur
Vorab-Information; wirksam wird erst das zugegangene Original.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit, qes
from app.services.masterdata import ConflictError, NotFoundError


async def _doc(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(
        "SELECT id, title, status, form_level, form_message, signer_name, signer_email, counterparty, content, filename, channel, "
        "qes_request_id FROM sign_documents WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    if row["status"] in ("pending", "signed"):
        raise ConflictError("Bereits in Signatur oder unterschrieben")
    if row["content"] is None:
        raise ConflictError("Kein PDF vorhanden")
    return dict(row)


async def send_qes(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, actor: str) -> dict[str, Any]:
    p = qes.provider()
    if p is None:
        raise ConflictError("Kein Anbieter für qualifizierte Signatur eingerichtet (System & Integrationen → Qualifizierte Signatur)")
    d = await _doc(conn, tenant_id, doc_id)
    if d["form_level"] == "block":
        raise ConflictError(f"{d['form_message']} Auch eine qualifizierte elektronische Signatur genügt hier nicht – bitte ausdrucken.")
    from app.services.invoices import today_berlin
    from app.services.masterdata import current_company_profile

    company = await current_company_profile(conn, tenant_id, today_berlin())
    signers = [d["signer_email"]]
    me = await conn.scalar(text("SELECT email FROM users WHERE tenant_id = :t AND email = :e"), {"t": tenant_id, "e": actor})
    if me and me not in signers:
        signers.append(me)  # Gegenzeichnung durch die Geschäftsführung
    try:
        req = await p.create(title=d["title"], pdf=bytes(d["content"]), signers=signers,
                             message=f"Bitte unterschreiben Sie „{d['title']}“ qualifiziert elektronisch. "
                                     f"{company['legal_name'] if company else ''}")
    except qes.QesError as exc:
        raise ConflictError(str(exc)) from exc
    await conn.execute(text("UPDATE sign_documents SET status = 'pending', channel = 'qes', qes_provider = :p, qes_request_id = :r, "
                            "qes_document_id = :d WHERE id = :id"), {"p": p.name, "r": req.request_id, "d": req.document_id, "id": doc_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.qes_requested", entity_type="sign_document",
                       entity_id=doc_id, after={"provider": p.name, "request": req.request_id, "signers": signers})
    return {"status": "pending", "provider": p.name, "signing_url": req.signing_url}


async def sync_qes(conn: AsyncConnection, tenant_id: UUID) -> dict[str, int]:
    """Fragt offene QES-Anfragen ab; unterschriebene werden heruntergeladen und archiviert."""
    from app.services.sign_documents import mark

    p = qes.provider()
    counts = {"signed": 0, "rejected": 0, "open": 0}
    if p is None:
        return counts
    rows = (await conn.execute(text("SELECT id, qes_request_id, qes_document_id FROM sign_documents WHERE tenant_id = :t "
                                    "AND channel = 'qes' AND status = 'pending' AND qes_provider = :p"),
                               {"t": tenant_id, "p": p.name})).mappings().all()
    for r in rows:
        try:
            state, document_id = await p.status(r["qes_request_id"])
            if state == "signed":
                pdf = await p.download(document_id or r["qes_document_id"])
                await mark(conn, tenant_id, r["id"], "signed", pdf)
                counts["signed"] += 1
            elif state == "rejected":
                await mark(conn, tenant_id, r["id"], "rejected")
                counts["rejected"] += 1
            else:
                counts["open"] += 1
        except qes.QesError:
            counts["open"] += 1
    return counts


async def send_mail(conn: AsyncConnection, mailer: Any, tenant_id: UUID, doc_id: UUID, actor: str, note: str | None) -> dict[str, Any]:
    from app.services.invoices import today_berlin
    from app.services.mail import Attachment, OutgoingMail
    from app.services.masterdata import current_company_profile

    if mailer is None:
        raise ConflictError("E-Mail-Versand ist nicht eingerichtet")
    d = await _doc(conn, tenant_id, doc_id)
    company = await current_company_profile(conn, tenant_id, today_berlin())
    address = f"{company['legal_name']}, {company['street']}, {company['postal_code']} {company['city']}" if company else "uns"
    excluded = d["form_level"] == "block"
    body = (f"Guten Tag {d['signer_name']},\n\nanbei erhalten Sie „{d['title']}“.\n\n"
            + ("Diese Erklärung ist nur im Original mit eigenhändiger Unterschrift wirksam; die E-Mail dient der Vorab-Information.\n\n"
               if excluded else "")
            + f"Bitte drucken Sie das Dokument aus, unterschreiben Sie es eigenhändig und senden Sie das Original an:\n{address}\n"
            + (f"\n{note}\n" if note else "") + f"\nMit freundlichen Grüßen\n{company['legal_name'] if company else ''}")
    await mailer.send(OutgoingMail(to=[d["signer_email"]], subject=f"Bitte unterschreiben: {d['title']}", text=body,
                                   reply_to=actor,
                                   attachments=(Attachment(d["filename"] or "vertrag.pdf", "application/pdf", bytes(d["content"])),)))
    await conn.execute(text("UPDATE sign_documents SET status = 'awaiting_paper', channel = 'email', mailed_to = :m, mailed_at = now(), "
                            "paper_note = :n WHERE id = :id"), {"m": d["signer_email"], "n": note, "id": doc_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.mailed", entity_type="sign_document", entity_id=doc_id,
                       after={"to": d["signer_email"]})
    return {"status": "awaiting_paper", "to": d["signer_email"]}


async def mark_paper(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, actor: str, note: str | None) -> dict[str, Any]:
    await _doc(conn, tenant_id, doc_id)
    await conn.execute(text("UPDATE sign_documents SET status = 'awaiting_paper', channel = 'paper', paper_note = :n WHERE id = :id"),
                       {"n": note, "id": doc_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.printed", entity_type="sign_document",
                       entity_id=doc_id)
    return {"status": "awaiting_paper"}


async def upload_signed(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, content: bytes, actor: str) -> dict[str, Any]:
    """Eingescanntes, eigenhändig unterschriebenes Exemplar übernehmen (Original aufbewahren)."""
    from app.services.sign_documents import ensure_readable_pdf, mark

    row = (await conn.execute(text("SELECT status FROM sign_documents WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": doc_id})).one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    if row[0] == "signed":
        raise ConflictError("Bereits unterschrieben")
    if not content.startswith(b"%PDF"):
        raise ConflictError("Bitte den Scan als PDF hochladen")
    ensure_readable_pdf(content)
    await conn.execute(text("UPDATE sign_documents SET channel = coalesce(channel, 'paper') WHERE id = :id"), {"id": doc_id})
    await mark(conn, tenant_id, doc_id, "signed", content)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.paper_received", entity_type="sign_document",
                       entity_id=doc_id, after={"note": "Original in Papierform aufbewahren"})
    return {"status": "signed"}
