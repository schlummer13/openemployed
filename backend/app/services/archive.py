"""Revisionssicheres Dokumentenarchiv (GoBD Rz. 119 ff., § 147 AO, § 257 HGB).

- Unveränderlich: Inhalt, Hash, Datum, Kategorie und Frist sind per Datenbank-Trigger gesperrt; Löschen ist ausgeschlossen.
- Nachweisbar: jedes Dokument trägt SHA-256 und einen Kettenhash über alle Vorgänger (Lücken/Manipulation fallen auf).
- Fristen nach Kategorie (seit BEG IV, 1.1.2025: Buchungsbelege 8 Jahre; Bücher, Abschlüsse 10; Geschäftsbriefe 6);
  nach Ablauf kann der Inhalt vernichtet werden – Metadaten und Hash bleiben als Nachweis.
- Automatisch übernommen: Ausgangsrechnungen, gebuchte Belege, Mahnungen, signierte Verträge, versendete Angebote,
  Gehaltsabrechnungen, SEPA-Dateien, versendete E-Mails (.eml). Eigene Dokumente per Upload.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

log = logging.getLogger("openemployed.archive")
GENESIS = "0" * 64
MAX_BYTES = 25 * 1024 * 1024
CATEGORIES: dict[str, tuple[str, int]] = {
    "invoice_out": ("Ausgangsrechnung", 8),
    "receipt": ("Eingangsbeleg", 8),
    "bank": ("Bank / Zahlungsverkehr", 8),
    "payroll": ("Lohn und Gehalt", 8),
    "annual": ("Jahresabschluss / Bücher", 10),
    "tax": ("Steuern", 10),
    "contract": ("Vertrag", 10),
    "offer": ("Angebot", 6),
    "mail": ("E-Mail", 6),
    "mandate": ("Lastschriftmandat", 6),
    "correspondence": ("Geschäftsbrief", 6),
    "other": ("Sonstiges", 6),
}
ALLOWED_MIME = {"application/pdf", "image/png", "image/jpeg", "application/xml", "text/xml", "message/rfc822", "text/plain", "text/csv",
                "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
LIST_COLUMNS = ("id, seq, number, category, title, document_date, retention_years, retain_until, filename, mime_type, size, sha256, "
                "source_type, source_id, party, tags, notes, purged_at, created_by, created_at")


def retain_until(document_date: dt.date, years: int) -> dt.date:
    """Frist beginnt mit Schluss des Kalenderjahres (§ 147 Abs. 4 AO)."""
    return dt.date(document_date.year + years, 12, 31)


def extract_text(content: bytes, mime: str) -> str | None:
    try:
        if mime == "application/pdf":
            from app.services.ai import pdf_to_text_and_images

            txt, _ = pdf_to_text_and_images(content, max_pages=20)
            return txt[:200_000] or None
        if mime in ("application/xml", "text/xml", "text/plain", "text/csv"):
            return content.decode("utf-8", errors="replace")[:200_000]
        if mime == "message/rfc822":
            from app.services.mail import parse_incoming

            m = parse_incoming("0", content)
            return f"{m.sender}\n{m.subject}\n{m.text}"[:200_000]
    except Exception:  # Volltext ist eine Zugabe – Archivierung darf daran nicht scheitern
        log.warning("Volltext konnte nicht gelesen werden (%s)", mime)
    return None


async def store(conn: AsyncConnection, tenant_id: UUID, *, category: str, title: str, filename: str, mime_type: str, content: bytes,
                document_date: dt.date, actor: str, source_type: str | None = None, source_id: str | None = None,
                party: str | None = None, tags: list[str] | None = None, notes: str | None = None,
                text_content: str | None = None) -> UUID | None:
    """Legt ein Dokument ab. Gleiches Dokument derselben Quelle wird nicht doppelt archiviert (Rückgabe ``None``)."""
    if category not in CATEGORIES:
        raise ConflictError("Unbekannte Kategorie")
    if not content or len(content) > MAX_BYTES:
        raise ConflictError("Datei leer oder größer als 25 MB")
    if mime_type not in ALLOWED_MIME:
        raise ConflictError(f"Dateityp {mime_type} wird nicht archiviert")
    sha = hashlib.sha256(content).hexdigest()
    if source_type and await conn.scalar(text(
            "SELECT 1 FROM archive_documents WHERE tenant_id = :t AND source_type = :st AND source_id = :si AND sha256 = :h"),
            {"t": tenant_id, "st": source_type, "si": source_id, "h": sha}):
        return None
    # Serialisierung der Kette je Mandant
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"archive:{tenant_id}"})
    last = (await conn.execute(text("SELECT seq, chain_hash FROM archive_documents WHERE tenant_id = :t ORDER BY seq DESC LIMIT 1"),
                               {"t": tenant_id})).one_or_none()
    seq = (last[0] + 1) if last else 1
    chain = hashlib.sha256(f"{last[1] if last else GENESIS}:{seq}:{sha}".encode()).hexdigest()
    years = CATEGORIES[category][1]
    number = f"DOK-{document_date.year}-{seq:06d}"
    doc_id: UUID = await conn.scalar(text(
        "INSERT INTO archive_documents (tenant_id, seq, number, category, title, document_date, retention_years, retain_until, filename, "
        "mime_type, size, sha256, chain_hash, content, text_content, source_type, source_id, party, tags, notes, created_by) VALUES "
        "(:t, :seq, :num, :cat, :title, :d, :y, :ru, :fn, :mime, :size, :sha, :chain, :content, :txt, :st, :si, :party, :tags, :notes, :a) "
        "RETURNING id"),
        {"t": tenant_id, "seq": seq, "num": number, "cat": category, "title": title[:300], "d": document_date, "y": years,
         "ru": retain_until(document_date, years), "fn": filename[:200], "mime": mime_type, "size": len(content), "sha": sha,
         "chain": chain, "content": content, "txt": text_content if text_content is not None else extract_text(content, mime_type),
         "st": source_type, "si": source_id, "party": (party or "")[:200] or None, "tags": tags or [], "notes": notes, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.stored", entity_type="archive_document", entity_id=doc_id,
                       after={"number": number, "category": category, "sha256": sha, "source": f"{source_type}:{source_id}"})
    return doc_id


async def search(conn: AsyncConnection, tenant_id: UUID, *, q: str | None = None, category: str | None = None,
                 year: int | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    where = ["tenant_id = :t"]
    params: dict[str, Any] = {"t": tenant_id, "lim": min(limit, 500), "off": offset}
    order = "document_date DESC, seq DESC"
    if q and q.strip():
        where.append("(search @@ websearch_to_tsquery('german', :q) OR title ILIKE :like OR number ILIKE :like OR party ILIKE :like)")
        params |= {"q": q.strip()[:200], "like": f"%{q.strip()[:100]}%"}
        order = "ts_rank(search, websearch_to_tsquery('german', :q)) DESC, " + order
    if category:
        where.append("category = :cat")
        params["cat"] = category
    if year:
        where.append("extract(year FROM document_date) = :y")
        params["y"] = year
    cond = " AND ".join(where)
    total = await conn.scalar(text(f"SELECT count(*) FROM archive_documents WHERE {cond}"), params)
    snippet = ("ts_headline('german', coalesce(text_content, ''), websearch_to_tsquery('german', :q), "
               "'MaxWords=18, MinWords=6, StartSel=«, StopSel=»') AS snippet") if q and q.strip() else "NULL AS snippet"
    rows = await conn.execute(text(
        f"SELECT {LIST_COLUMNS}, {snippet} FROM archive_documents WHERE {cond} "
        f"ORDER BY {order} LIMIT :lim OFFSET :off"), params)
    stats = {r[0]: r[1] for r in await conn.execute(text(
        "SELECT category, count(*) FROM archive_documents WHERE tenant_id = :t GROUP BY category"), {"t": tenant_id})}
    return {"total": total, "documents": [dict(r) for r in rows.mappings()], "by_category": stats,
            "categories": {k: {"label": v[0], "years": v[1]} for k, v in CATEGORIES.items()}}


async def get(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, *, with_content: bool = False) -> dict[str, Any]:
    cols = LIST_COLUMNS + (", content, chain_hash" if with_content else ", chain_hash")
    row = (await conn.execute(text(f"SELECT {cols} FROM archive_documents WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": doc_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Dokument nicht gefunden")
    return dict(row)


async def update_meta(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, data: dict[str, Any], actor: str) -> None:
    """Nur beschreibende Angaben (Titel, Schlagworte, Notiz) – jede Änderung wird protokolliert."""
    before = await get(conn, tenant_id, doc_id)
    await conn.execute(text("UPDATE archive_documents SET title = :title, tags = :tags, notes = :notes WHERE tenant_id = :t AND id = :id"),
                       {"t": tenant_id, "id": doc_id, "title": data["title"], "tags": data.get("tags") or [], "notes": data.get("notes")})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.meta_updated", entity_type="archive_document",
                       entity_id=doc_id, before={k: before[k] for k in ("title", "tags", "notes")},
                       after={k: data.get(k) for k in ("title", "tags", "notes")})


async def purge(conn: AsyncConnection, tenant_id: UUID, doc_id: UUID, actor: str, today: dt.date) -> None:
    doc = await get(conn, tenant_id, doc_id)
    if doc["purged_at"]:
        raise ConflictError("Bereits vernichtet")
    if today <= doc["retain_until"]:
        raise ConflictError(f"Aufbewahrungsfrist läuft bis {doc['retain_until']:%d.%m.%Y}")
    await conn.execute(text("UPDATE archive_documents SET content = NULL, text_content = NULL, purged_at = now(), purged_by = :a "
                            "WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": doc_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="archive.purged", entity_type="archive_document", entity_id=doc_id,
                       after={"number": doc["number"], "sha256": doc["sha256"]})


async def verify(conn: AsyncConnection, tenant_id: UUID) -> dict[str, Any]:
    """Prüft Kette und Inhalts-Hashes aller (nicht vernichteten) Dokumente."""
    prev, expected_seq, broken = GENESIS, 1, []
    checked = 0
    rows = await conn.stream(text("SELECT seq, number, sha256, chain_hash, content IS NOT NULL AS has, "
                                  "CASE WHEN content IS NOT NULL THEN encode(sha256(content), 'hex') END AS actual "
                                  "FROM archive_documents WHERE tenant_id = :t ORDER BY seq"), {"t": tenant_id})
    async for r in rows:
        checked += 1
        if r.seq != expected_seq:
            broken.append(f"Lücke vor {r.number}")
        chain = hashlib.sha256(f"{prev}:{r.seq}:{r.sha256}".encode()).hexdigest()
        if chain != r.chain_hash:
            broken.append(f"Kettenhash {r.number}")
        if r.has and r.actual != r.sha256:
            broken.append(f"Inhalt verändert: {r.number}")
        prev, expected_seq = r.chain_hash, r.seq + 1
    return {"ok": not broken, "checked": checked, "problems": broken[:50]}


async def retention_due(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> int:
    return int(await conn.scalar(text("SELECT count(*) FROM archive_documents WHERE tenant_id = :t AND purged_at IS NULL "
                                      "AND retain_until < :d"), {"t": tenant_id, "d": today}) or 0)


# ───────────────────────── Automatische Übernahme ─────────────────────────

async def sync(conn: AsyncConnection, tenant_id: UUID, actor: str = "system:archiv", limit: int = 200) -> dict[str, int]:
    """Übernimmt neue Dokumente aus den Fachbereichen (idempotent über Quelle + Hash)."""
    counts: dict[str, int] = {}

    def bump(key: str, added: UUID | None) -> None:
        if added:
            counts[key] = counts.get(key, 0) + 1

    async def missing(sql: str, st: str) -> list[Any]:
        return list((await conn.execute(text(sql + f" AND NOT EXISTS (SELECT 1 FROM archive_documents a WHERE a.tenant_id = :t "
                                                   f"AND a.source_type = '{st}' AND a.source_id = CAST(x.id AS text)) LIMIT :lim"),
                                        {"t": tenant_id, "lim": limit})).mappings())

    for r in await missing(
            "SELECT x.id, x.number, x.issue_date, x.kind, d.pdf, d.xml, COALESCE(c.company_name, trim(coalesce(c.first_name,'') || ' ' || "
            "coalesce(c.last_name,''))) AS party FROM invoices x JOIN invoice_documents d ON d.invoice_id = x.id "
            "JOIN customers c ON c.id = x.customer_id WHERE x.tenant_id = :t", "invoice"):
        label = "Stornorechnung" if r["kind"] == "cancellation" else "Rechnung"
        bump("invoice_out", await store(conn, tenant_id, category="invoice_out", title=f"{label} {r['number']}",
                                        filename=f"{r['number']}.pdf",
                                        mime_type="application/pdf", content=bytes(r["pdf"]), document_date=r["issue_date"], actor=actor,
                                        source_type="invoice", source_id=str(r["id"]), party=r["party"]))
    for r in await missing(
            "SELECT x.id, x.receipt_date, x.invoice_number, x.description, f.content, f.mime_type, f.file_name, s.name AS party "
            "FROM receipts x JOIN receipt_files f ON f.id = x.file_id JOIN suppliers s ON s.id = x.supplier_id "
            "WHERE x.tenant_id = :t AND x.status = 'booked'", "receipt"):
        title = f"Beleg {r['party']} {r['invoice_number'] or r['description'] or ''}"
        bump("receipt", await store(conn, tenant_id, category="receipt", title=title, filename=r["file_name"], mime_type=r["mime_type"],
                                    content=bytes(r["content"]), document_date=r["receipt_date"],
                                    actor=actor, source_type="receipt", source_id=str(r["id"]), party=r["party"]))
    for r in await missing(
            "SELECT x.id, x.number, x.notice_date, x.level, x.pdf FROM dunning_notices x WHERE x.tenant_id = :t AND x.pdf IS NOT NULL",
            "dunning"):
        bump("correspondence", await store(conn, tenant_id, category="correspondence", title=f"Mahnung Stufe {r['level']} {r['number']}",
                                           filename=f"{r['number']}.pdf", mime_type="application/pdf", content=bytes(r["pdf"]),
                                           document_date=r["notice_date"], actor=actor, source_type="dunning", source_id=str(r["id"])))
    for r in await missing(
            "SELECT x.id, x.title, x.signer_name, x.completed_at, x.signed_pdf FROM signature_requests x WHERE x.tenant_id = :t "
            "AND x.status = 'completed' AND x.signed_pdf IS NOT NULL", "signature"):
        bump("contract", await store(conn, tenant_id, category="contract", title=f"{r['title']} (signiert)", filename="signiert.pdf",
                                     mime_type="application/pdf", content=bytes(r["signed_pdf"]), document_date=r["completed_at"].date(),
                                     actor=actor, source_type="signature", source_id=str(r["id"]), party=r["signer_name"]))
    for r in await missing(
            "SELECT x.id, x.purpose, x.kind, x.msg_id, x.created_at, x.xml FROM sepa_files x WHERE x.tenant_id = :t", "sepa"):
        kind = "Überweisung" if r["kind"] == "transfer" else "Lastschrift"
        bump("bank", await store(conn, tenant_id, category="bank", title=f"SEPA-{kind} "
                                 f"{r['msg_id']}", filename=f"{r['msg_id']}.xml", mime_type="application/xml", content=r["xml"].encode(),
                                 document_date=r["created_at"].date(), actor=actor, source_type="sepa", source_id=str(r["id"])))
    await _sync_rendered(conn, tenant_id, actor, limit, missing, bump)
    return counts


async def _sync_rendered(conn: AsyncConnection, tenant_id: UUID, actor: str, limit: int, missing: Any, bump: Any) -> None:
    """Dokumente, die bei Bedarf erzeugt werden (Gehaltsabrechnungen, versendete Angebote) – Stand zum Archivierungszeitpunkt."""
    from app.services import offers, payroll
    from app.services.invoices import today_berlin

    for r in await missing(
            "SELECT x.id FROM payslips x JOIN payroll_runs r ON r.id = x.run_id WHERE x.tenant_id = :t AND r.status = 'approved'",
            "payslip"):
        slip = await payroll.get_payslip(conn, tenant_id, r["id"])
        try:
            pdf = await payroll.payslip_pdf(conn, tenant_id, slip)
        except OSError:  # WeasyPrint-Systembibliotheken fehlen (nur Entwicklungsrechner)
            return
        bump("payroll", await store(conn, tenant_id, category="payroll", title=f"Entgeltabrechnung {slip['period']} {slip['name']}",
                                    filename=f"Entgeltabrechnung_{slip['period']}.pdf", mime_type="application/pdf", content=pdf,
                                    document_date=payroll.period_bounds(slip["period"])[1], actor=actor, source_type="payslip",
                                    source_id=str(r["id"]), party=slip["name"]))
    for r in await missing("SELECT x.id, x.number, x.sent_at FROM offers x WHERE x.tenant_id = :t AND x.status <> 'draft' "
                           "AND x.sent_at IS NOT NULL", "offer"):
        try:
            number, pdf = await offers.render_pdf(conn, tenant_id, r["id"], today_berlin())
        except OSError:
            return
        bump("offer", await store(conn, tenant_id, category="offer", title=f"Angebot {number}", filename=f"{number}.pdf",
                                  mime_type="application/pdf", content=pdf, document_date=r["sent_at"].date(), actor=actor,
                                  source_type="offer", source_id=str(r["id"])))


class ArchivingMailer:
    """Wrapper um den Mailer: jede versendete Nachricht wird als .eml archiviert (Handels- und Geschäftsbriefe)."""

    def __init__(self, inner: Any, engine: Any, tenant_id: UUID) -> None:
        self._inner = inner
        self._engine = engine
        self._tenant = tenant_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def send(self, mail: Any) -> str:
        from app.services.mail import build_message

        message_id: str = await self._inner.send(mail)
        try:
            msg = build_message(mail, mailbox=self._inner.mailbox, from_name=self._inner.from_name)
            msg.replace_header("Message-ID", message_id)
            raw = bytes(msg)
            from app.core.tenancy import current

            async with self._engine.begin() as conn:
                await store(conn, current(), category="mail", title=f"E-Mail: {mail.subject}", filename="nachricht.eml",
                            mime_type="message/rfc822", content=raw, document_date=dt.datetime.now(dt.UTC).date(),
                            actor="system:mail", source_type="mail", source_id=message_id[:200], party=", ".join(mail.to)[:200],
                            text_content=f"An: {', '.join(mail.to)}\n{mail.subject}\n{mail.text}")
        except Exception:  # Versand ist erfolgt – Archivfehler nur protokollieren
            log.exception("Versendete E-Mail konnte nicht archiviert werden")
        return message_id
