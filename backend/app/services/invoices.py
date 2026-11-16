"""Rechnungen: Entwurf → Freigabe (Festschreibung im Ledger, lückenlose Nummer) → ggf. Storno.

Grundsätze (GoBD, § 14 UStG):
- Nummer und Inhalt werden erst bei Freigabe vergeben bzw. festgeschrieben – im Rust-Ledger, in einer Transaktion
  mit der Nummernvergabe. Die festgeschriebenen Nutzdaten (``snapshot``) sind die verbindliche Fassung.
- Ausgestellte Rechnungen werden nie geändert oder gelöscht. Korrektur nur per Stornorechnung (eigene Nummer aus
  demselben Nummernkreis, negative Beträge, Verweis auf das Original).
- Idempotent: Bricht die Freigabe nach der Festschreibung ab (z. B. Netzwerk), übernimmt der nächste Versuch den
  bereits festgeschriebenen Datensatz statt eine zweite Nummer zu ziehen.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.core_schemas import InvoiceDraftIn
from app.domain.invoice_calc import InvoiceTotals, LineInput, PriceMode, calculate_invoice
from app.services import audit, documents
from app.services.ledger_client import LedgerClient, SealedRecord, to_ledger_payload
from app.services.masterdata import (
    ConflictError,
    NotFoundError,
    current_company_profile,
    customer_display_name,
    get_customer,
    missing_invoice_fields,
)
from app.services.periods import resolve_vat_period

BERLIN = ZoneInfo("Europe/Berlin")
NUMBER_RANGE = ("invoice", "RE-{YYYY}-{SEQ:5}")
RECORD_TYPES = {"invoice": "invoice.issued", "cancellation": "invoice.cancellation"}
CANCELLATION_NAMESPACE = uuid.UUID("6f1c2a51-6b0e-4b0e-9a55-2f3c8f6d7a10")
B2C_DEFAULT_NOTE = (
    "Sie kommen spätestens 30 Tage nach Fälligkeit und Zugang dieser Rechnung in Verzug, "
    "sofern Sie nicht vorher zahlen."
)

INVOICE_COLUMNS = (
    "i.id, i.kind, i.status, i.number, i.customer_id, i.issue_date, i.due_date, i.total_net, i.total_tax, "
    "i.total_gross, i.cancelled_by, i.cancels_invoice_id, i.created_at, i.price_mode, i.service_date_from, "
    "i.service_date_to, i.due_days, i.intro_text, i.closing_text, i.cancellation_reason, i.snapshot, "
    "i.ledger_record_id, i.ledger_hash, i.issued_by, i.issued_at, i.vat_period, "
    "c.customer_type, c.company_name, c.first_name, c.last_name"
)


class InvoiceStateError(RuntimeError):
    """Aktion im aktuellen Zustand nicht erlaubt (z. B. Entwurf ändern, der schon ausgestellt ist)."""


class InvoiceValidationError(ValueError):
    pass


def today_berlin() -> dt.date:
    return dt.datetime.now(BERLIN).date()


def cancellation_id_for(original_id: UUID) -> UUID:
    """Deterministische ID der Stornorechnung → wiederholte Storno-Versuche erzeugen keinen zweiten Storno."""
    return uuid.uuid5(CANCELLATION_NAMESPACE, f"cancel:{original_id}")


# ───────────────────────── Lesen ─────────────────────────

def _summary(row: RowMapping) -> dict[str, Any]:
    data = dict(row)
    data["customer_name"] = customer_display_name(row)
    return data


async def list_invoices(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            f"SELECT {INVOICE_COLUMNS} FROM invoices i JOIN customers c ON c.id = i.customer_id "
            "WHERE i.tenant_id = :t ORDER BY i.status, i.issue_date DESC NULLS FIRST, i.number DESC, i.created_at DESC "
            "LIMIT 500"
        ),
        {"t": tenant_id},
    )
    return [_summary(r) for r in result.mappings()]


async def _lines(conn: AsyncConnection, invoice_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            "SELECT position, product_id, description, quantity, unit_code, unit_price, tax_case "
            "FROM invoice_lines WHERE invoice_id = :id ORDER BY position"
        ),
        {"id": invoice_id},
    )
    return [dict(r) for r in result.mappings()]


async def _row(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, *, lock: bool = False) -> RowMapping:
    lock_sql = " FOR UPDATE OF i" if lock else ""
    row = (
        await conn.execute(
            text(f"SELECT {INVOICE_COLUMNS} FROM invoices i JOIN customers c ON c.id = i.customer_id "
                 f"WHERE i.tenant_id = :t AND i.id = :id{lock_sql}"),
            {"t": tenant_id, "id": invoice_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Rechnung nicht gefunden")
    return row


async def get_invoice(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID) -> dict[str, Any]:
    row = await _row(conn, tenant_id, invoice_id)
    data = _summary(row)
    data["lines"] = await _lines(conn, invoice_id)
    return data


# ───────────────────────── Entwurf ─────────────────────────

async def _write_lines(conn: AsyncConnection, invoice_id: UUID, data: InvoiceDraftIn) -> None:
    await conn.execute(text("DELETE FROM invoice_lines WHERE invoice_id = :id"), {"id": invoice_id})
    for pos, line in enumerate(data.lines, start=1):
        await conn.execute(
            text(
                "INSERT INTO invoice_lines (invoice_id, position, product_id, description, quantity, unit_code, "
                "unit_price, tax_case) VALUES (:inv, :pos, :pid, :desc, :qty, :unit, :price, :tax)"
            ),
            {"inv": invoice_id, "pos": pos, "pid": line.product_id, "desc": line.description,
             "qty": line.quantity, "unit": line.unit_code, "price": line.unit_price, "tax": line.tax_case},
        )


def _validate_lines(data: InvoiceDraftIn, price_mode: PriceMode) -> InvoiceTotals:
    """Prüft die Positionen mit derselben Logik wie die Freigabe (früher Fehler statt später)."""
    try:
        return calculate_invoice(
            [LineInput(x.description, x.quantity, x.unit_code, x.unit_price, x.tax_case) for x in data.lines],
            price_mode,
        )
    except ValueError as exc:
        raise InvoiceValidationError(str(exc)) from exc


async def create_draft(conn: AsyncConnection, tenant_id: UUID, data: InvoiceDraftIn, actor: str) -> dict[str, Any]:
    customer = await get_customer(conn, tenant_id, data.customer_id)
    if customer["archived_at"] is not None:
        raise InvoiceValidationError("Kunde ist archiviert")
    price_mode = PriceMode.GROSS if customer["customer_type"] == "b2c" else PriceMode.NET
    _validate_lines(data, price_mode)
    invoice_id = await conn.scalar(
        text(
            "INSERT INTO invoices (tenant_id, customer_id, price_mode, service_date_from, service_date_to, due_days, "
            "intro_text, closing_text, created_by) VALUES (:t, :c, :pm, :sf, :st, :dd, :intro, :closing, :actor) "
            "RETURNING id"
        ),
        {"t": tenant_id, "c": data.customer_id, "pm": price_mode.value, "sf": data.service_date_from,
         "st": data.service_date_to, "dd": data.due_days if data.due_days is not None else customer["payment_terms_days"],
         "intro": data.intro_text, "closing": data.closing_text, "actor": actor},
    )
    await _write_lines(conn, invoice_id, data)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.draft_created", entity_type="invoice",
                       entity_id=invoice_id, after=data.model_dump())
    return await get_invoice(conn, tenant_id, invoice_id)


async def update_draft(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, data: InvoiceDraftIn,
                       actor: str) -> dict[str, Any]:
    row = await _row(conn, tenant_id, invoice_id, lock=True)
    if row["status"] != "draft" or row["kind"] != "invoice":
        raise InvoiceStateError("Nur Rechnungsentwürfe können bearbeitet werden")
    customer = await get_customer(conn, tenant_id, data.customer_id)
    if customer["archived_at"] is not None:
        raise InvoiceValidationError("Kunde ist archiviert")
    price_mode = PriceMode.GROSS if customer["customer_type"] == "b2c" else PriceMode.NET
    _validate_lines(data, price_mode)
    await conn.execute(
        text(
            "UPDATE invoices SET customer_id = :c, price_mode = :pm, service_date_from = :sf, service_date_to = :st, "
            "due_days = :dd, intro_text = :intro, closing_text = :closing WHERE id = :id"
        ),
        {"c": data.customer_id, "pm": price_mode.value, "sf": data.service_date_from, "st": data.service_date_to,
         "dd": data.due_days if data.due_days is not None else customer["payment_terms_days"],
         "intro": data.intro_text, "closing": data.closing_text, "id": invoice_id},
    )
    await _write_lines(conn, invoice_id, data)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.draft_updated", entity_type="invoice",
                       entity_id=invoice_id, after=data.model_dump())
    return await get_invoice(conn, tenant_id, invoice_id)


async def delete_draft(conn: AsyncConnection, tenant_id: UUID, invoice_id: UUID, actor: str) -> None:
    row = await _row(conn, tenant_id, invoice_id, lock=True)
    if row["status"] != "draft":
        raise InvoiceStateError("Ausgestellte Rechnungen können nicht gelöscht werden – nur storniert")
    await conn.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": invoice_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.draft_deleted", entity_type="invoice",
                       entity_id=invoice_id)


# ───────────────────────── Freigabe ─────────────────────────

def _seller(profile: RowMapping) -> dict[str, Any]:
    return {
        "legal_name": profile["legal_name"], "legal_form": profile["legal_form"],
        "street": profile["street"], "postal_code": profile["postal_code"], "city": profile["city"],
        "country_code": profile["country_code"], "register_court": profile["register_court"],
        "register_number": profile["register_number"], "managing_directors": list(profile["managing_directors"]),
        "tax_number": profile["tax_number"], "vat_id": profile["vat_id"], "iban": profile["iban"],
        "bic": profile["bic"], "bank_name": profile["bank_name"], "email": profile["email"],
        "phone": profile["phone"], "website": profile["website"],
    }


def _buyer(customer: RowMapping) -> dict[str, Any]:
    return {
        "customer_id": str(customer["id"]), "number": customer["number"], "type": customer["customer_type"],
        "name": customer_display_name(customer), "company_name": customer["company_name"],
        "first_name": customer["first_name"], "last_name": customer["last_name"],
        "street": customer["street"], "postal_code": customer["postal_code"], "city": customer["city"],
        "country_code": customer["country_code"], "email": customer["email"], "vat_id": customer["vat_id"],
        "buyer_reference": customer["buyer_reference"], "currency": customer["currency"],
        "einvoice_format": customer["einvoice_format"], "peppol_id": customer["peppol_id"],
    }


def build_snapshot(
    *,
    kind: str,
    issue_date: dt.date,
    due_date: dt.date,
    row: RowMapping | dict[str, Any],
    totals: InvoiceTotals,
    seller: dict[str, Any],
    buyer: dict[str, Any],
    cancels: dict[str, Any] | None,
    currency: str = "EUR",
    exchange_rate: Decimal | None = None,
) -> dict[str, Any]:
    from app.domain.international import to_eur

    notes: list[str] = []
    if kind == "invoice" and buyer["type"] == "b2c":
        notes.append(B2C_DEFAULT_NOTE)
    if cancels:
        original_date = dt.date.fromisoformat(cancels["issue_date"])
        notes.append(f"Stornorechnung zur Rechnung {cancels['number']} vom {original_date:%d.%m.%Y}.")
    snapshot: dict[str, Any] = {
        "schema": 1,
        "kind": kind,
        "currency": currency,
        "exchange_rate": exchange_rate,
        # § 14 Abs. 4 Nr. 8 UStG: Steuerbetrag in Euro bei Rechnungen in Fremdwährung
        "tax_eur": to_eur(totals.total_tax, exchange_rate) if exchange_rate else None,
        "issue_date": issue_date,
        "due_date": due_date,
        "service_period": {"from": row["service_date_from"], "to": row["service_date_to"]},
        "price_mode": totals.price_mode.value,
        "seller": seller,
        "buyer": buyer,
        "lines": [
            {
                "position": ln.position, "description": ln.description, "quantity": ln.quantity,
                "unit_code": ln.unit_code, "unit_price_net": ln.unit_price_net,
                "unit_price_gross": ln.unit_price_gross, "line_net": ln.line_net, "line_gross": ln.line_gross,
                "tax_case": ln.tax_case, "category": ln.category.value, "rate": ln.rate,
            }
            for ln in totals.lines
        ],
        "tax_breakdown": [
            {"category": b.category.value, "rate": b.rate, "taxable_amount": b.taxable_amount,
             "tax_amount": b.tax_amount, "exemption_reason": b.exemption_reason}
            for b in totals.tax_breakdown
        ],
        "totals": {
            "net": totals.total_net, "tax": totals.total_tax, "gross": totals.total_gross,
            "gross_target": totals.gross_target, "gross_deviation": totals.gross_deviation,
        },
        "warnings": list(totals.warnings),
        "notes": notes,
        "intro_text": row["intro_text"],
        "closing_text": row["closing_text"],
        "cancels": cancels,
        "cancellation_reason": row["cancellation_reason"],
    }
    result: dict[str, Any] = to_ledger_payload(snapshot)
    return result


async def _existing_seal(ledger: LedgerClient, tenant_id: UUID, invoice_id: UUID, kind: str) -> SealedRecord | None:
    records = await ledger.records_by_subject(tenant_id=tenant_id, subject_id=invoice_id)
    matching = [r for r in records if r.record_type == RECORD_TYPES[kind]]
    if len(matching) > 1:  # pragma: no cover – darf durch Idempotenz nie entstehen
        raise RuntimeError(f"Mehrere Festschreibungen für Rechnung {invoice_id}")
    return matching[0] if matching else None


async def _issue_locked(
    conn: AsyncConnection,
    ledger: LedgerClient,
    *,
    tenant_id: UUID,
    row: RowMapping | dict[str, Any],
    lines: list[dict[str, Any]],
    actor: str,
    buyer_override: dict[str, Any] | None = None,
    cancels: dict[str, Any] | None = None,
) -> None:
    """Gemeinsamer Kern für Rechnung und Storno. Die Zeile ist bereits gesperrt bzw. in dieser Transaktion angelegt."""
    invoice_id: UUID = row["id"]
    kind: str = row["kind"]
    existing = await _existing_seal(ledger, tenant_id, invoice_id, kind)

    if existing is None:
        issue_date = today_berlin()
        profile = await current_company_profile(conn, tenant_id, issue_date)
        if profile is None:
            raise InvoiceValidationError("Firmendaten fehlen – bitte unter Einstellungen → Firma erfassen")
        missing = missing_invoice_fields(profile)
        if missing:
            raise InvoiceValidationError("Pflichtangaben fehlen in den Firmendaten: " + ", ".join(missing))
        if row["service_date_from"] is None:
            raise InvoiceValidationError("Leistungsdatum bzw. -zeitraum fehlt (Pflichtangabe § 14 Abs. 4 Nr. 6 UStG)")
        if buyer_override is not None:
            buyer = buyer_override
        else:
            customer = await get_customer(conn, tenant_id, row["customer_id"])
            if customer["archived_at"] is not None:
                raise InvoiceValidationError("Kunde ist archiviert")
            buyer = _buyer(customer)
        totals = calculate_invoice(
            [LineInput(ln["description"], Decimal(ln["quantity"]), ln["unit_code"], Decimal(ln["unit_price"]),
                       ln["tax_case"]) for ln in lines],
            PriceMode(row["price_mode"]),
        )
        from app.domain.international import tax_case_problems
        from app.services import exchange

        problems = tax_case_problems(buyer, {ln["tax_case"] for ln in lines})
        if problems and kind == "invoice":
            raise InvoiceValidationError("; ".join(problems))
        currency = str(buyer.get("currency") or "EUR")
        if cancels and cancels.get("exchange_rate"):
            rate: Decimal | None = Decimal(str(cancels["exchange_rate"]))  # Storno zum Kurs der Originalrechnung
        else:
            try:
                rate = await exchange.rate_for(conn, currency, issue_date)
            except ConflictError as exc:
                raise InvoiceValidationError(str(exc)) from exc
        due_date = issue_date + dt.timedelta(days=int(row["due_days"]))
        payload = build_snapshot(kind=kind, issue_date=issue_date, due_date=due_date, row=row, totals=totals,
                                 seller=_seller(profile), buyer=buyer, cancels=cancels, currency=currency, exchange_rate=rate)
        sealed = await ledger.seal(
            tenant_id=tenant_id, record_type=RECORD_TYPES[kind], payload=payload, sealed_by=f"user:{actor}",
            subject_id=invoice_id, number_range=NUMBER_RANGE, issue_date=issue_date,
        )
    else:
        # Wiederaufnahme: festgeschriebene Fassung übernehmen und Positionen daraus wiederherstellen
        sealed = existing
        payload = existing.payload
        await conn.execute(text("DELETE FROM invoice_lines WHERE invoice_id = :id"), {"id": invoice_id})
        price_key = "unit_price_gross" if payload["price_mode"] == "gross" else "unit_price_net"
        for ln in payload["lines"]:
            await conn.execute(
                text("INSERT INTO invoice_lines (invoice_id, position, description, quantity, unit_code, unit_price, "
                     "tax_case) VALUES (:inv, :pos, :desc, :qty, :unit, :price, :tax)"),
                {"inv": invoice_id, "pos": ln["position"], "desc": ln["description"], "qty": Decimal(ln["quantity"]),
                 "unit": ln["unit_code"], "price": Decimal(ln[price_key]), "tax": ln["tax_case"]},
            )

    if sealed.document_number is None:  # pragma: no cover – Ledger vergibt immer eine Nummer
        raise RuntimeError("Ledger hat keine Rechnungsnummer vergeben")
    period_info = payload["service_period"]
    natural = dt.date.fromisoformat(period_info.get("to") or period_info.get("from") or payload["issue_date"])
    vat_period = await resolve_vat_period(ledger, tenant_id, natural)
    await conn.execute(
        text(
            """
            UPDATE invoices SET status = 'issued', number = :number, issue_date = :issue_date, due_date = :due_date,
                vat_period = :vat_period, currency = :currency, exchange_rate = :rate,
                total_net = :net, total_tax = :tax, total_gross = :gross, snapshot = CAST(:snapshot AS jsonb),
                ledger_record_id = :rid, ledger_hash = :hash, issued_by = :actor, issued_at = now()
            WHERE id = :id AND status = 'draft'
            """
        ),
        {
            "number": sealed.document_number,
            "issue_date": dt.date.fromisoformat(payload["issue_date"]),
            "due_date": dt.date.fromisoformat(payload["due_date"]),
            "net": Decimal(payload["totals"]["net"]),
            "tax": Decimal(payload["totals"]["tax"]),
            "gross": Decimal(payload["totals"]["gross"]),
            "snapshot": json.dumps(payload),
            "rid": sealed.id,
            "hash": sealed.hash,
            "actor": actor,
            "id": invoice_id,
            "vat_period": vat_period,
            "currency": payload.get("currency", "EUR"),
            "rate": Decimal(str(payload["exchange_rate"])) if payload.get("exchange_rate") else None,
        },
    )
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"{RECORD_TYPES[kind]}", entity_type="invoice",
                       entity_id=invoice_id, after={"number": sealed.document_number, "ledger_hash": sealed.hash})
    # ZUGFeRD-PDF + XML erzeugen, gegen EN 16931 prüfen, unveränderlich ablegen und Hashes festschreiben
    await documents.ensure_documents(conn, ledger, tenant_id=tenant_id, invoice_id=invoice_id,
                                     number=sealed.document_number, snapshot=payload, actor=actor)


async def issue_invoice(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, invoice_id: UUID,
                        actor: str) -> dict[str, Any]:
    row = await _row(conn, tenant_id, invoice_id, lock=True)
    if row["status"] != "draft":
        raise InvoiceStateError(f"Rechnung ist bereits ausgestellt ({row['number']})")
    lines = await _lines(conn, invoice_id)
    if not lines:
        raise InvoiceValidationError("Rechnung hat keine Positionen")
    await _issue_locked(conn, ledger, tenant_id=tenant_id, row=row, lines=lines, actor=actor)
    return await get_invoice(conn, tenant_id, invoice_id)


async def cancel_invoice(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, invoice_id: UUID,
                         reason: str, actor: str) -> dict[str, Any]:
    original = await _row(conn, tenant_id, invoice_id, lock=True)
    if original["status"] != "issued":
        raise InvoiceStateError("Nur ausgestellte Rechnungen können storniert werden (Entwürfe einfach löschen)")
    if original["kind"] != "invoice":
        raise InvoiceStateError("Eine Stornorechnung kann nicht storniert werden")
    if original["cancelled_by"] is not None:
        raise InvoiceStateError("Rechnung ist bereits storniert")
    snapshot: dict[str, Any] = original["snapshot"]
    cancellation_id = cancellation_id_for(invoice_id)
    if await conn.scalar(text("SELECT 1 FROM invoices WHERE id = :id"), {"id": cancellation_id}):
        raise ConflictError("Für diese Rechnung existiert bereits eine Stornorechnung")

    price_key = "unit_price_gross" if snapshot["price_mode"] == "gross" else "unit_price_net"
    lines = [
        {"position": ln["position"], "description": ln["description"], "quantity": -Decimal(ln["quantity"]),
         "unit_code": ln["unit_code"], "unit_price": Decimal(ln[price_key]), "tax_case": ln["tax_case"]}
        for ln in snapshot["lines"]
    ]
    intro = f"Hiermit stornieren wir die Rechnung {original['number']} vom {original['issue_date']:%d.%m.%Y}."
    await conn.execute(
        text(
            "INSERT INTO invoices (id, tenant_id, kind, customer_id, price_mode, service_date_from, service_date_to, "
            "due_days, intro_text, cancels_invoice_id, cancellation_reason, created_by) "
            "VALUES (:id, :t, 'cancellation', :c, :pm, :sf, :st, 0, :intro, :orig, :reason, :actor)"
        ),
        {"id": cancellation_id, "t": tenant_id, "c": original["customer_id"], "pm": snapshot["price_mode"],
         "sf": original["service_date_from"], "st": original["service_date_to"], "intro": intro,
         "orig": invoice_id, "reason": reason, "actor": actor},
    )
    for ln in lines:
        await conn.execute(
            text("INSERT INTO invoice_lines (invoice_id, position, description, quantity, unit_code, unit_price, "
                 "tax_case) VALUES (:inv, :pos, :desc, :qty, :unit, :price, :tax)"),
            {"inv": cancellation_id, "pos": ln["position"], "desc": ln["description"], "qty": ln["quantity"],
             "unit": ln["unit_code"], "price": ln["unit_price"], "tax": ln["tax_case"]},
        )
    new_row = await _row(conn, tenant_id, cancellation_id, lock=True)
    await _issue_locked(
        conn, ledger, tenant_id=tenant_id, row=new_row, lines=lines, actor=actor,
        buyer_override=snapshot["buyer"],  # Storno spiegelt die Empfängerdaten der Originalrechnung
        cancels={"invoice_id": str(invoice_id), "number": original["number"],
                 "issue_date": original["issue_date"].isoformat(), "ledger_hash": original["ledger_hash"],
                 "exchange_rate": snapshot.get("exchange_rate")},
    )
    issued = await get_invoice(conn, tenant_id, cancellation_id)
    if Decimal(issued["total_gross"]) != -Decimal(original["total_gross"]):
        raise RuntimeError("Invariante verletzt: Storno ist nicht das exakte Spiegelbild der Rechnung")
    await conn.execute(text("UPDATE invoices SET cancelled_by = :c WHERE id = :id"),
                       {"c": cancellation_id, "id": invoice_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="invoice.cancelled", entity_type="invoice",
                       entity_id=invoice_id, after={"cancelled_by": str(cancellation_id), "reason": reason})
    return issued
