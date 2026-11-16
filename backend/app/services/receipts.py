"""Lieferanten und Belege: Entwurf → Buchung (Festschreibung im Ledger) → ggf. Storno."""

from __future__ import annotations

import hashlib
import re
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.expenses import ExpenseError, compute_expense, get_category
from app.services import audit
from app.services.invoices import today_berlin
from app.services.ledger_client import LedgerClient
from app.services.masterdata import ConflictError, NotFoundError
from app.services.periods import resolve_vat_period

MAX_FILE_BYTES = 10 * 1024 * 1024
REVERSAL_NAMESPACE = uuid.UUID("0b8f0a9e-3c55-4d0c-8d6e-6f1f0c3d2a77")


class ReceiptError(ValueError):
    pass


class ReceiptStateError(RuntimeError):
    pass


# ───────────────────────── Lieferanten ─────────────────────────

SUPPLIER_COLUMNS = (
    "id, name, country_code, vat_id, default_tax_case, default_category, default_account, needs_review, notes, archived_at, iban, bic"
)


def supplier_out(row: RowMapping) -> dict[str, Any]:
    data = dict(row)
    data["archived"] = data.pop("archived_at") is not None
    return data


async def list_suppliers(conn: AsyncConnection, tenant_id: UUID) -> list[RowMapping]:
    result = await conn.execute(
        text(f"SELECT {SUPPLIER_COLUMNS} FROM suppliers WHERE tenant_id = :t AND archived_at IS NULL ORDER BY name"),
        {"t": tenant_id},
    )
    return list(result.mappings())


async def get_supplier(conn: AsyncConnection, tenant_id: UUID, supplier_id: UUID) -> RowMapping:
    row = (await conn.execute(text(f"SELECT {SUPPLIER_COLUMNS} FROM suppliers WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": supplier_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Lieferant nicht gefunden")
    return row


async def expense_accounts(conn: AsyncConnection, tenant_id: UUID) -> dict[str, str]:
    """Zulässige Konten für Eingangsbelege (Aufwand + Anlagevermögen) im eingestellten Kontenrahmen: Nummer → Name."""
    from app.services import accounting as acc

    chart = await acc.chart_of(conn, tenant_id)
    return {str(a["number"]): a["name"] for a in acc.ai_accounts(await acc.accounts(conn, chart))}


async def _check_account(conn: AsyncConnection, tenant_id: UUID, account: str | None) -> None:
    if account and account not in await expense_accounts(conn, tenant_id):
        raise ReceiptError(f"Konto {account} ist für Eingangsbelege nicht zulässig (nur Aufwands- und Anlagekonten)")


async def save_supplier(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                        supplier_id: UUID | None = None, *, needs_review: bool = False) -> RowMapping:
    """Speichert einen Lieferanten mit seiner Kontierungsregel. Manuelles Speichern gilt als „einsortiert“."""
    get_category(data["default_category"])
    data = {**data, "default_account": data.get("default_account") or None,
            "iban": (data.get("iban") or "").replace(" ", "").upper() or None,
            "bic": (data.get("bic") or "").replace(" ", "").upper() or None}
    await _check_account(conn, tenant_id, data["default_account"])
    taken = await conn.scalar(
        text("SELECT 1 FROM suppliers WHERE tenant_id = :t AND name = :n AND (CAST(:id AS uuid) IS NULL OR id <> CAST(:id AS uuid))"),
        {"t": tenant_id, "n": data["name"], "id": supplier_id},
    )
    if taken:
        raise ConflictError(f"Lieferant „{data['name']}“ existiert bereits")
    params = {**data, "t": tenant_id, "id": supplier_id, "review": needs_review}
    if supplier_id is None:
        row = (await conn.execute(text(
            "INSERT INTO suppliers (tenant_id, name, country_code, vat_id, default_tax_case, default_category, default_account, "
            "needs_review, notes, iban, bic) VALUES (:t, :name, :country_code, :vat_id, :default_tax_case, :default_category, "
            f":default_account, :review, :notes, :iban, :bic) RETURNING {SUPPLIER_COLUMNS}"
        ), params)).mappings().one()
        action = "supplier.created"
    else:
        await get_supplier(conn, tenant_id, supplier_id)
        row = (await conn.execute(text(
            f"UPDATE suppliers SET name = :name, country_code = :country_code, vat_id = :vat_id, "
            f"default_tax_case = :default_tax_case, default_category = :default_category, default_account = :default_account, "
            f"needs_review = :review, notes = :notes, iban = :iban, bic = :bic, updated_at = now() "
            f"WHERE tenant_id = :t AND id = :id RETURNING {SUPPLIER_COLUMNS}"
        ), params)).mappings().one()
        action = "supplier.updated"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="supplier",
                       entity_id=row["id"], after=data | {"needs_review": needs_review})
    if not needs_review and supplier_id is not None:
        await apply_supplier_rule(conn, tenant_id, row["id"], actor)
    return row


async def apply_supplier_rule(conn: AsyncConnection, tenant_id: UUID, supplier_id: UUID, actor: str,
                              except_id: UUID | None = None) -> int:
    """Überträgt die Regel des Lieferanten auf seine offenen Belegentwürfe aus dem Belegpostfach (nie auf gebuchte)."""
    s = await get_supplier(conn, tenant_id, supplier_id)
    rows = (await conn.execute(text(
        "SELECT id FROM receipts WHERE tenant_id = :t AND supplier_id = :s AND status = 'draft' AND source IN ('inbox', 'mobile') "
        "AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"), {"t": tenant_id, "s": supplier_id, "x": except_id})).all()
    for (rid,) in rows:
        r = await get_receipt(conn, tenant_id, rid, lock=True)
        data = {k: r[k] for k in ("supplier_id", "file_id", "receipt_date", "service_date", "invoice_number", "description",
                                  "net_amount", "tax_amount", "paid_on")}
        rc_case = s["default_tax_case"].startswith(("IN_13B", "IN_IG"))
        data |= {"category": s["default_category"], "tax_case": s["default_tax_case"], "expense_account": s["default_account"],
                 "tax_amount": Decimal("0") if rc_case else r["tax_amount"]}
        await save_draft(conn, tenant_id, data, actor, rid)
    return len(rows)


# ───────────────────────── Dateien ─────────────────────────

def detect_receipt_type(content: bytes) -> str:
    if not content:
        raise ReceiptError("Datei ist leer")
    if len(content) > MAX_FILE_BYTES:
        raise ReceiptError("Datei ist größer als 10 MB")
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    head = content[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<rsm:") or head.startswith(b"<Invoice"):
        if re.search(rb"<!DOCTYPE|<!ENTITY", content[:4096], re.IGNORECASE):
            raise ReceiptError("XML mit DOCTYPE/ENTITY wird aus Sicherheitsgründen abgelehnt")
        return "application/xml"
    raise ReceiptError("Nur PDF, PNG, JPEG oder E-Rechnungs-XML erlaubt")


async def store_file(conn: AsyncConnection, tenant_id: UUID, content: bytes, file_name: str, actor: str) -> dict[str, Any]:
    mime = detect_receipt_type(content)
    digest = hashlib.sha256(content).hexdigest()
    existing = (await conn.execute(
        text("SELECT f.id, r.id AS receipt_id FROM receipt_files f LEFT JOIN receipts r ON r.file_id = f.id "
             "WHERE f.tenant_id = :t AND f.sha256 = :h LIMIT 1"), {"t": tenant_id, "h": digest}
    )).one_or_none()
    if existing is not None:
        return {"id": existing.id, "mime_type": mime, "sha256": digest, "duplicate_of_receipt": existing.receipt_id}
    safe_name = re.sub(r"[^\w.\- ]", "_", file_name)[:200] or "beleg"
    file_id = await conn.scalar(text(
        "INSERT INTO receipt_files (tenant_id, content, mime_type, file_name, sha256, uploaded_by) "
        "VALUES (:t, :c, :m, :n, :h, :a) RETURNING id"),
        {"t": tenant_id, "c": content, "m": mime, "n": safe_name, "h": digest, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="receipt_file.uploaded", entity_type="receipt_file",
                       entity_id=file_id, after={"sha256": digest, "mime_type": mime, "bytes": len(content)})
    return {"id": file_id, "mime_type": mime, "sha256": digest, "duplicate_of_receipt": None}


MAX_PREVIEW_PAGES = 5


def render_preview(content: bytes, mime_type: str, page: int) -> tuple[bytes, str, int]:
    """Seitenvorschau als Bild (für die Prüfansicht neben dem Formular): PDF-Seite → PNG, Bilder unverändert."""
    if mime_type in ("image/png", "image/jpeg"):
        if page != 1:
            raise NotFoundError("Seite nicht vorhanden")
        return content, mime_type, 1
    if mime_type != "application/pdf":
        raise ReceiptError("Für E-Rechnungen (XML) gibt es keine Bildvorschau – die Daten werden exakt aus dem XML gelesen")
    import io

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(content)
    pages = min(len(pdf), MAX_PREVIEW_PAGES)
    if not 1 <= page <= pages:
        raise NotFoundError("Seite nicht vorhanden")
    img = pdf[page - 1].render(scale=1.6).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png", pages


async def get_file(conn: AsyncConnection, tenant_id: UUID, file_id: UUID) -> RowMapping:
    row = (await conn.execute(text(
        "SELECT content, mime_type, file_name, sha256 FROM receipt_files WHERE tenant_id = :t AND id = :id"),
        {"t": tenant_id, "id": file_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Datei nicht gefunden")
    return row


# ───────────────────────── Belege ─────────────────────────

RECEIPT_COLUMNS = (
    "r.id, r.status, r.kind, r.supplier_id, s.name AS supplier_name, r.file_id, r.receipt_date, r.service_date, "
    "r.invoice_number, r.description, r.category, r.tax_case, r.net_amount, r.tax_amount, r.gross_amount, "
    "r.rc_tax_amount, r.input_tax_amount, r.expense_amount, r.deductible_amount, r.paid_on, r.reverses_id, "
    "r.reversed_by, r.reversal_reason, r.ledger_hash, r.booked_at, r.booked_by, r.created_at, r.vat_period, r.expense_account, "
    "r.source, s.needs_review AS supplier_needs_review, s.default_account AS supplier_default_account"
)


GWG_LIMIT = Decimal("800.00")
DEVIATION = Decimal("0.5")  # Hinweis, wenn der Nettobetrag mehr als 50 % vom üblichen Betrag des Lieferanten abweicht


def review_state(r: dict[str, Any], usual: Decimal | None) -> dict[str, Any]:
    """Bereit zum Buchen? Plus Prüfhinweise (Anlagevermögen, ungewöhnlicher Betrag) – Grundlage der Sammelbuchung."""
    hints: list[str] = []
    net = Decimal(r["net_amount"])
    account = r.get("expense_account") or ""
    if r["kind"] == "expense" and net > GWG_LIMIT and r["category"] in ("hardware_gwg", "office") and not account.startswith("0"):
        hints.append("Über 800 € netto: Anlagevermögen statt Sofortaufwand prüfen (§ 6 Abs. 2 EStG).")
    if usual and usual > 0 and abs(net - usual) > usual * DEVIATION:
        hints.append(f"Betrag weicht deutlich vom Üblichen dieses Lieferanten ab (sonst ca. {usual:.2f} € netto).")
    blockers: list[str] = []
    if r["supplier_needs_review"]:
        blockers.append("Neuer Lieferant – bitte einmal einsortieren")
    if r["file_id"] is None:
        blockers.append("Belegdatei fehlt")
    ready = r["status"] == "draft" and r["kind"] == "expense" and not blockers and not hints
    return {"ready": ready, "hints": hints, "blockers": blockers if r["status"] == "draft" else []}


async def _usual_amounts(conn: AsyncConnection, tenant_id: UUID, supplier_id: UUID | None = None) -> dict[UUID, Decimal]:
    """Üblicher Nettobetrag je Lieferant (Median der gebuchten Belege der letzten ~13 Monate, ab 2 Belegen)."""
    rows = await conn.execute(text(
        "SELECT supplier_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY net_amount) AS median FROM receipts "
        "WHERE tenant_id = :t AND status = 'booked' AND kind = 'expense' AND reversed_by IS NULL "
        "AND receipt_date > CURRENT_DATE - 400 AND (CAST(:s AS uuid) IS NULL OR supplier_id = CAST(:s AS uuid)) "
        "GROUP BY supplier_id HAVING count(*) >= 2"), {"t": tenant_id, "s": supplier_id})
    return {row.supplier_id: Decimal(str(row.median)) for row in rows}


async def _account_info(conn: AsyncConnection, tenant_id: UUID) -> tuple[str, dict[str, str]]:
    from app.services import accounting as acc

    chart = await acc.chart_of(conn, tenant_id)
    return chart, {str(a["number"]): a["name"] for a in await acc.accounts(conn, chart)}


def _with_account(r: dict[str, Any], chart: str, names: dict[str, str]) -> dict[str, Any]:
    """Tatsächliches Aufwands-/Anlagekonto: Konto am Beleg, sonst Standardkonto der Kategorie (einheitliche Anzeige)."""
    from app.services.accounting import category_account

    number = r.get("expense_account") or category_account(chart, r["category"])
    return r | {"account": number, "account_name": names.get(number, "")}


async def list_receipts(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    chart, names = await _account_info(conn, tenant_id)
    result = await conn.execute(text(
        f"SELECT {RECEIPT_COLUMNS} FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.tenant_id = :t "
        "ORDER BY r.status, r.receipt_date DESC, r.created_at DESC LIMIT 1000"), {"t": tenant_id})
    usual = await _usual_amounts(conn, tenant_id)
    out = []
    for row in result.mappings():
        r = dict(row)
        r |= review_state(r, usual.get(r["supplier_id"]) if r["status"] == "draft" else None)
        out.append(_with_account(r, chart, names))
    return out


async def book_ready(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, receipt_ids: list[UUID],
                     actor: str) -> dict[str, Any]:
    """Bucht die ausgewählten Entwürfe, die „bereit zum Buchen“ sind. Nicht bereite werden übersprungen (mit Grund)."""
    booked, skipped = [], []
    drafts = {r["id"]: r for r in await list_receipts(conn, tenant_id) if r["status"] == "draft"}
    for rid in receipt_ids:
        r = drafts.get(rid)
        if r is None:
            skipped.append({"id": str(rid), "reason": "nicht (mehr) als Entwurf vorhanden"})
            continue
        if not r["ready"]:
            skipped.append({"id": str(rid), "reason": "; ".join(r["blockers"] + r["hints"])})
            continue
        locked = await get_receipt(conn, tenant_id, rid, lock=True)
        if locked["status"] != "draft":
            skipped.append({"id": str(rid), "reason": "bereits gebucht"})
            continue
        await _book_locked(conn, ledger, tenant_id, locked, actor)
        booked.append(str(rid))
    return {"booked": booked, "skipped": skipped}


async def get_receipt(conn: AsyncConnection, tenant_id: UUID, receipt_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    lock_sql = " FOR UPDATE OF r" if lock else ""
    row = (await conn.execute(text(
        f"SELECT {RECEIPT_COLUMNS} FROM receipts r JOIN suppliers s ON s.id = r.supplier_id "
        f"WHERE r.tenant_id = :t AND r.id = :id{lock_sql}"), {"t": tenant_id, "id": receipt_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Beleg nicht gefunden")
    r = dict(row)
    usual = None
    if r["status"] == "draft":
        usual = (await _usual_amounts(conn, tenant_id, r["supplier_id"])).get(r["supplier_id"])
    chart, names = await _account_info(conn, tenant_id)
    return _with_account(r | review_state(r, usual), chart, names)


def _amounts(data: dict[str, Any]) -> dict[str, Any]:
    try:
        a = compute_expense(data["tax_case"], Decimal(data["net_amount"]), Decimal(data["tax_amount"]), data["category"])
    except (ExpenseError, ValueError) as exc:
        raise ReceiptError(str(exc)) from exc
    return {"net_amount": a.net, "tax_amount": a.tax, "gross_amount": a.gross, "rc_tax_amount": a.rc_tax,
            "input_tax_amount": a.input_tax, "expense_amount": a.expense, "deductible_amount": a.deductible,
            "warnings": list(a.warnings)}


async def _check_duplicate_number(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any],
                                  except_id: UUID | None) -> None:
    if not data.get("invoice_number"):
        return
    dup = await conn.scalar(text(
        "SELECT 1 FROM receipts WHERE tenant_id = :t AND supplier_id = :s AND invoice_number = :n AND kind = 'expense' "
        "AND reversed_by IS NULL AND (CAST(:id AS uuid) IS NULL OR id <> CAST(:id AS uuid))"),
        {"t": tenant_id, "s": data["supplier_id"], "n": data["invoice_number"], "id": except_id})
    if dup:
        raise ConflictError(f"Rechnungsnummer {data['invoice_number']} dieses Lieferanten ist bereits erfasst (Dublette?)")


async def save_draft(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                     receipt_id: UUID | None = None) -> dict[str, Any]:
    supplier = await get_supplier(conn, tenant_id, data["supplier_id"])
    if data.get("file_id"):
        await get_file(conn, tenant_id, data["file_id"])
    remember = bool(data.pop("remember_rule", False))
    data = {**data, "expense_account": data.get("expense_account") or None}
    if data["expense_account"] is None and supplier["default_account"] and not supplier["needs_review"]:
        data["expense_account"] = supplier["default_account"]
    await _check_account(conn, tenant_id, data["expense_account"])
    amounts = _amounts(data)
    warnings = amounts.pop("warnings")
    await _check_duplicate_number(conn, tenant_id, data, receipt_id)
    params = {**data, **amounts, "t": tenant_id, "actor": actor}
    if receipt_id is None:
        receipt_id = await conn.scalar(text(
            "INSERT INTO receipts (tenant_id, supplier_id, file_id, receipt_date, service_date, invoice_number, description, "
            "category, tax_case, net_amount, tax_amount, gross_amount, rc_tax_amount, input_tax_amount, expense_amount, "
            "deductible_amount, paid_on, expense_account, created_by) VALUES (:t, :supplier_id, :file_id, :receipt_date, :service_date, "
            ":invoice_number, :description, :category, :tax_case, :net_amount, :tax_amount, :gross_amount, :rc_tax_amount, "
            ":input_tax_amount, :expense_amount, :deductible_amount, :paid_on, :expense_account, :actor) RETURNING id"), params)
        action = "receipt.draft_created"
    else:
        current = await get_receipt(conn, tenant_id, receipt_id, lock=True)
        if current["status"] != "draft":
            raise ReceiptStateError("Gebuchte Belege können nicht geändert werden – nur storniert")
        await conn.execute(text(
            "UPDATE receipts SET supplier_id = :supplier_id, file_id = :file_id, receipt_date = :receipt_date, "
            "service_date = :service_date, invoice_number = :invoice_number, description = :description, "
            "category = :category, tax_case = :tax_case, net_amount = :net_amount, tax_amount = :tax_amount, "
            "gross_amount = :gross_amount, rc_tax_amount = :rc_tax_amount, input_tax_amount = :input_tax_amount, "
            "expense_amount = :expense_amount, deductible_amount = :deductible_amount, paid_on = :paid_on, "
            "expense_account = :expense_account WHERE id = :id"), {**params, "id": receipt_id})
        action = "receipt.draft_updated"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="receipt", entity_id=receipt_id,
                       after=dict(data))
    if remember:
        # „Für diesen Lieferanten merken“: Regel speichern → weitere offene Belege dieses Lieferanten übernehmen sie
        await save_supplier(conn, tenant_id, {k: supplier[k] for k in ("name", "country_code", "vat_id", "notes")} | {
            "default_tax_case": data["tax_case"], "default_category": data["category"],
            "default_account": data["expense_account"]}, actor, data["supplier_id"])
    result = await get_receipt(conn, tenant_id, receipt_id)
    result["warnings"] = warnings
    return result


async def delete_draft(conn: AsyncConnection, tenant_id: UUID, receipt_id: UUID, actor: str) -> None:
    current = await get_receipt(conn, tenant_id, receipt_id, lock=True)
    if current["status"] != "draft":
        raise ReceiptStateError("Gebuchte Belege können nicht gelöscht werden – nur storniert")
    await conn.execute(text("DELETE FROM receipts WHERE id = :id"), {"id": receipt_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="receipt.draft_deleted", entity_type="receipt",
                       entity_id=receipt_id)


def _payload(r: dict[str, Any], file_sha: str) -> dict[str, Any]:
    keys = ("kind", "supplier_id", "supplier_name", "receipt_date", "service_date", "invoice_number", "description",
            "category", "tax_case", "net_amount", "tax_amount", "gross_amount", "rc_tax_amount", "input_tax_amount",
            "expense_amount", "deductible_amount", "reverses_id", "reversal_reason")
    payload = {k: r[k] for k in keys}
    payload["file_sha256"] = file_sha
    payload["skr04_account"] = get_category(r["category"]).skr04
    if r.get("expense_account"):
        payload["expense_account"] = r["expense_account"]
    return payload


async def _book_locked(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, r: dict[str, Any],
                       actor: str) -> None:
    if r["file_id"] is None:
        raise ReceiptError("Ohne Belegdatei keine Buchung (GoBD: kein Beleg, keine Buchung)")
    file_row = await get_file(conn, tenant_id, r["file_id"])
    record_type = "receipt.reversal" if r["kind"] == "reversal" else "receipt.booked"
    existing = [x for x in await ledger.records_by_subject(tenant_id=tenant_id, subject_id=r["id"])
                if x.record_type == record_type]
    sealed = existing[0] if existing else await ledger.seal(
        tenant_id=tenant_id, record_type=record_type, payload=_payload(r, file_row["sha256"]),
        sealed_by=f"user:{actor}", subject_id=r["id"],
    )
    vat_period = await resolve_vat_period(ledger, tenant_id, r["receipt_date"])
    await conn.execute(text(
        "UPDATE receipts SET status = 'booked', ledger_record_id = :rid, ledger_hash = :h, booked_at = now(), "
        "booked_by = :a, vat_period = :vp WHERE id = :id AND status = 'draft'"),
        {"rid": sealed.id, "h": sealed.hash, "a": actor, "id": r["id"], "vp": vat_period})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=record_type, entity_type="receipt",
                       entity_id=r["id"], after={"ledger_hash": sealed.hash})


async def book_receipt(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, receipt_id: UUID,
                       actor: str) -> dict[str, Any]:
    r = await get_receipt(conn, tenant_id, receipt_id, lock=True)
    if r["status"] != "draft":
        raise ReceiptStateError("Beleg ist bereits gebucht")
    await _book_locked(conn, ledger, tenant_id, r, actor)
    return await get_receipt(conn, tenant_id, receipt_id)


async def reverse_receipt(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, receipt_id: UUID,
                          reason: str, actor: str) -> dict[str, Any]:
    original = await get_receipt(conn, tenant_id, receipt_id, lock=True)
    if original["status"] != "booked" or original["kind"] != "expense":
        raise ReceiptStateError("Nur gebuchte Belege können storniert werden")
    if original["reversed_by"] is not None:
        raise ReceiptStateError("Beleg ist bereits storniert")
    reversal_id = uuid.uuid5(REVERSAL_NAMESPACE, f"reverse:{receipt_id}")
    amounts = ("net_amount", "tax_amount", "gross_amount", "rc_tax_amount", "input_tax_amount", "expense_amount",
               "deductible_amount")
    params = {k: -original[k] for k in amounts}
    await conn.execute(text(
        "INSERT INTO receipts (id, tenant_id, kind, supplier_id, file_id, receipt_date, service_date, invoice_number, "
        "description, category, tax_case, net_amount, tax_amount, gross_amount, rc_tax_amount, input_tax_amount, "
        "expense_amount, deductible_amount, reverses_id, reversal_reason, expense_account, created_by) VALUES (:id, :t, 'reversal', "
        ":s, :f, :rd, :sd, :inv, :desc, :cat, :tax, :net_amount, :tax_amount, :gross_amount, :rc_tax_amount, :input_tax_amount, "
        ":expense_amount, :deductible_amount, :orig, :reason, :acct, :actor)"),
        {**params, "id": reversal_id, "t": tenant_id, "s": original["supplier_id"], "f": original["file_id"],
         "rd": today_berlin(), "sd": original["service_date"], "inv": original["invoice_number"],
         "desc": f"Storno: {original['description']}"[:500], "cat": original["category"], "tax": original["tax_case"],
         "orig": receipt_id, "reason": reason, "acct": original["expense_account"], "actor": actor})
    reversal = await get_receipt(conn, tenant_id, reversal_id, lock=True)
    await _book_locked(conn, ledger, tenant_id, reversal, actor)
    await conn.execute(text("UPDATE receipts SET reversed_by = :r WHERE id = :id"), {"r": reversal_id, "id": receipt_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="receipt.reversed", entity_type="receipt",
                       entity_id=receipt_id, after={"reversed_by": str(reversal_id), "reason": reason})
    return await get_receipt(conn, tenant_id, reversal_id)
