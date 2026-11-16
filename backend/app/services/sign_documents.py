"""Beliebige Verträge zur digitalen Signatur (Documenso) – mit Formprüfung vor dem Versand und Archivierung nach Abschluss."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.signature_form import RULES, check
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

MAX_BYTES = 20 * 1024 * 1024


def ensure_readable_pdf(content: bytes) -> None:
    """Documenso bekommt eine Signaturseite angehängt – dafür muss das PDF lesbar und nicht verschlüsselt sein."""
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or len(reader.pages) == 0:
            raise ConflictError("Das PDF ist verschlüsselt oder leer – bitte ohne Kennwortschutz hochladen")
    except ConflictError:
        raise
    except Exception as exc:  # pypdf wirft unterschiedliche Fehlertypen
        raise ConflictError("Das PDF ist beschädigt und kann nicht signiert werden") from exc
COLUMNS = ("id, title, kind, counterparty, signer_name, signer_email, filename, sha256, status, form_level, form_message, "
           "form_acknowledged_by, signed_sha256, archive_document_id, notes, created_by, created_at, completed_at, source, documenso_id, "
           "documenso_status, template_id, recipients, employee_id, synced_at, channel, qes_provider, mailed_to, mailed_at, paper_note, "
           "personnel_document_id")


async def create(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], content: bytes, actor: str) -> dict[str, Any]:
    if data["kind"] not in RULES:
        raise ConflictError("Unbekannte Vertragsart")
    if not content.startswith(b"%PDF") or len(content) > MAX_BYTES:
        raise ConflictError("Bitte ein PDF bis 20 MB hochladen")
    ensure_readable_pdf(content)
    form = check(data["kind"], data["title"])  # „block“ wird angelegt, kann aber nur ausgedruckt/per E-Mail versendet werden
    did: UUID = await conn.scalar(text(
        "INSERT INTO sign_documents (tenant_id, title, kind, counterparty, signer_name, signer_email, filename, sha256, content, "
        "form_level, form_message, notes, created_by, employee_id, personnel_document_id) VALUES (:t, :ti, :k, :cp, :sn, :se, :f, :h, :c, "
        ":fl, :fm, :n, :a, :e, :pd) RETURNING id"),
        {"t": tenant_id, "ti": data["title"], "k": form.kind, "cp": data["counterparty"], "sn": data["signer_name"],
         "se": data["signer_email"].lower(), "f": data["filename"][:200], "h": hashlib.sha256(content).hexdigest(), "c": content,
         "fl": form.level, "fm": form.message, "n": data.get("notes"), "a": actor, "e": data.get("employee_id"),
         "pd": data.get("personnel_document_id")})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="sign_document.created", entity_type="sign_document", entity_id=did,
                       after={"title": data["title"], "kind": form.kind, "counterparty": data["counterparty"]})
    return await get(conn, tenant_id, did)


async def get(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {COLUMNS} FROM sign_documents WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    return dict(row) | {"kind_label": RULES.get(row["kind"], RULES["general"]).label}


async def list_all(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(f"SELECT {COLUMNS} FROM sign_documents WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 300"),
                              {"t": tenant_id})
    return [dict(r._mapping) | {"kind_label": RULES.get(r.kind, RULES["general"]).label} for r in rows]


async def file(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, signed: bool) -> tuple[str, bytes]:
    row = (await conn.execute(text("SELECT filename, content, signed_content FROM sign_documents WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": doc_id})).one_or_none()
    if row is not None and not signed and row.content is None and row.signed_content is not None:
        signed = True  # in Documenso angelegt: nur das signierte Exemplar liegt vor
    if row is None or (signed and row.signed_content is None) or (not signed and row.content is None):
        raise NotFoundError("Dokument nicht gefunden")
    return (row.filename.replace(".pdf", "-signiert.pdf") if signed else row.filename), bytes(row.signed_content if signed else row.content)


async def subject(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID) -> dict[str, Any]:
    """Für documenso.create_request."""
    row = (await conn.execute(text("SELECT title, content, status, form_level, form_acknowledged_by FROM sign_documents "
                                   "WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    if row["status"] in ("pending", "signed"):
        raise ConflictError("Bereits signiert oder in Signatur")
    return {"title": row["title"], "reference": row["title"][:60], "b2c": False, "customer_id": None, "pdf": bytes(row["content"])}


async def acknowledge(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, actor: str) -> None:
    await conn.execute(text("UPDATE sign_documents SET form_acknowledged_by = :a WHERE tenant_id = :t AND id = :id"),
                       {"t": tenant_id, "id": doc_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="signature.form_acknowledged", entity_type="sign_document",
                       entity_id=doc_id)


async def mark(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, status: str, signed_pdf: bytes | None = None) -> None:
    """Status aus Documenso übernehmen; signiertes PDF revisionssicher archivieren (Kategorie Vertrag, 10 Jahre)."""
    from app.services import archive

    archive_id = None
    if signed_pdf:
        row = (await conn.execute(text("SELECT title, counterparty, filename FROM sign_documents WHERE id = :id"), {"id": doc_id})).one()
        archive_id = await archive.store(conn, tenant_id, category="contract", title=f"{row.title} (signiert)",
                                         filename=row.filename.replace(".pdf", "-signiert.pdf"), mime_type="application/pdf",
                                         content=signed_pdf, document_date=dt.date.today(), actor="system:documenso",  # noqa: DTZ011
                                         source_type="sign_document", source_id=str(doc_id), party=row.counterparty)
    if signed_pdf:
        emp = await conn.scalar(text("SELECT employee_id FROM sign_documents WHERE id = :id"), {"id": doc_id})
        if emp:  # Vorlage für einen Mitarbeiter → signiertes Exemplar in die Personalakte
            from app.services import hr

            title = await conn.scalar(text("SELECT title FROM sign_documents WHERE id = :id"), {"id": doc_id})
            pid = await hr.upload(conn, tenant_id, emp, {"category": "contract", "title": f"{title} (signiert)",
                                                        "filename": f"{title}-signiert.pdf".replace("/", "-")[:200],
                                                        "visible_to_employee": True}, "application/pdf", signed_pdf, "system:documenso")
            await hr.mark_signature(conn, pid, "signed", signed_pdf)
    await conn.execute(text(
        "UPDATE sign_documents SET status = :s, signed_content = coalesce(:p, signed_content), "
        "signed_sha256 = coalesce(:h, signed_sha256), archive_document_id = coalesce(:ar, archive_document_id), "
        "completed_at = CASE WHEN :s IN ('signed', 'rejected') THEN now() ELSE completed_at END WHERE id = :id"),
        {"s": status, "p": signed_pdf, "h": hashlib.sha256(signed_pdf).hexdigest() if signed_pdf else None, "ar": archive_id, "id": doc_id})
