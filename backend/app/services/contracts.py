"""Verträge: Entwurf → aktiv → (gekündigt) beendet; Abrechnungslauf erzeugt Rechnungen je Zeitraum."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api.core_schemas import InvoiceDraftIn, InvoiceLineIn
from app.domain.contracts import (
    ContractRuleError,
    billing_periods,
    earliest_end,
    initial_term_end,
    monthly_value,
    validate_terms,
)
from app.domain.invoice_calc import LineInput, PriceMode, calculate_invoice
from app.services import audit
from app.services import invoices as inv
from app.services.ledger_client import LedgerClient
from app.services.masterdata import NotFoundError, get_customer

log = logging.getLogger("openemployed.billing")
BILLING_LOCK_KEY = 0x53_4C_42_49_4C_4C  # "SLBILL"
PERIOD_WORD = {"monthly": "Monat", "quarterly": "Quartal", "yearly": "Jahr"}


class ContractStateError(RuntimeError):
    pass


class ContractValidationError(ValueError):
    pass


COLUMNS = (
    "c.id, c.number, c.customer_id, c.title, c.status, c.start_date, c.min_term_months, c.notice_months, c.renewal, "
    "c.billing_interval, c.billing_mode, c.auto_issue, c.end_date, c.notice_received, c.notes, c.activated_at, "
    "cu.customer_type, cu.company_name, cu.first_name, cu.last_name"
)


async def _lines(conn: AsyncConnection, contract_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        "SELECT position, product_id, description, quantity, unit_code, unit_price, tax_case FROM contract_lines "
        "WHERE contract_id = :id ORDER BY position"), {"id": contract_id})
    return [dict(r) for r in result.mappings()]


def _price_mode(customer_type: str) -> PriceMode:
    return PriceMode.GROSS if customer_type == "b2c" else PriceMode.NET


async def _enrich(conn: AsyncConnection, row: dict[str, Any]) -> dict[str, Any]:
    from app.services.masterdata import customer_display_name

    row["customer_name"] = customer_display_name(row)
    row["lines"] = await _lines(conn, row["id"])
    row["term_end"] = initial_term_end(row["start_date"], row["min_term_months"])
    amount = Decimal(0)
    if row["lines"]:
        totals = calculate_invoice([LineInput(x["description"], x["quantity"], x["unit_code"], x["unit_price"], x["tax_case"])
                                    for x in row["lines"]], _price_mode(row["customer_type"]))
        amount = totals.total_net
    row["amount_net"] = amount
    row["mrr"] = monthly_value(amount, row["billing_interval"]) if row["status"] == "active" else Decimal("0.00")
    billed = await conn.execute(text(
        "SELECT b.period_start, b.period_end, b.invoice_id, i.number, i.status FROM contract_billings b "
        "JOIN invoices i ON i.id = b.invoice_id WHERE b.contract_id = :id ORDER BY b.period_start"), {"id": row["id"]})
    row["billings"] = [dict(b) for b in billed.mappings()]
    return row


async def list_contracts(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(text(
        f"SELECT {COLUMNS} FROM contracts c JOIN customers cu ON cu.id = c.customer_id WHERE c.tenant_id = :t "
        "ORDER BY c.status, c.start_date DESC"), {"t": tenant_id})
    return [await _enrich(conn, dict(r)) for r in result.mappings()]


async def get_contract(conn: AsyncConnection, tenant_id: UUID, contract_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    lock_sql = " FOR UPDATE OF c" if lock else ""
    row = (await conn.execute(text(
        f"SELECT {COLUMNS} FROM contracts c JOIN customers cu ON cu.id = c.customer_id "
        f"WHERE c.tenant_id = :t AND c.id = :id{lock_sql}"), {"t": tenant_id, "id": contract_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Vertrag nicht gefunden")
    return await _enrich(conn, dict(row))


async def _validate(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any]) -> dict[str, Any]:
    customer = await get_customer(conn, tenant_id, data["customer_id"])
    if customer["archived_at"] is not None:
        raise ContractValidationError("Kunde ist archiviert")
    try:
        validate_terms(customer_type=customer["customer_type"], min_term_months=data["min_term_months"],
                       notice_months=data["notice_months"], renewal=data["renewal"])
        calculate_invoice([LineInput(x["description"], x["quantity"], x["unit_code"], x["unit_price"], x["tax_case"])
                           for x in data["lines"]], _price_mode(customer["customer_type"]))
    except (ContractRuleError, ValueError) as exc:
        raise ContractValidationError(str(exc)) from exc
    return dict(customer)


async def _write_lines(conn: AsyncConnection, contract_id: UUID, lines: list[dict[str, Any]]) -> None:
    await conn.execute(text("DELETE FROM contract_lines WHERE contract_id = :id"), {"id": contract_id})
    for pos, ln in enumerate(lines, start=1):
        await conn.execute(text(
            "INSERT INTO contract_lines (contract_id, position, product_id, description, quantity, unit_code, unit_price, tax_case) "
            "VALUES (:c, :p, :pid, :d, :q, :u, :pr, :t)"),
            {"c": contract_id, "p": pos, "pid": ln.get("product_id"), "d": ln["description"], "q": ln["quantity"],
             "u": ln["unit_code"], "pr": ln["unit_price"], "t": ln["tax_case"]})


FIELDS = ("customer_id", "title", "start_date", "min_term_months", "notice_months", "renewal", "billing_interval",
          "billing_mode", "auto_issue", "notes")


async def save_contract(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                        contract_id: UUID | None = None) -> dict[str, Any]:
    await _validate(conn, tenant_id, data)
    params = {k: data.get(k) for k in FIELDS} | {"t": tenant_id, "actor": actor}
    if contract_id is None:
        contract_id = await conn.scalar(text(
            "INSERT INTO contracts (tenant_id, customer_id, title, start_date, min_term_months, notice_months, renewal, "
            "billing_interval, billing_mode, auto_issue, notes, created_by) VALUES (:t, :customer_id, :title, :start_date, "
            ":min_term_months, :notice_months, :renewal, :billing_interval, :billing_mode, :auto_issue, :notes, :actor) "
            "RETURNING id"), params)
        action = "contract.created"
    else:
        current = await get_contract(conn, tenant_id, contract_id, lock=True)
        if current["status"] != "draft":
            raise ContractStateError("Nur Vertragsentwürfe sind änderbar – aktive Verträge per Nachtrag/Kündigung ändern")
        await conn.execute(text(
            "UPDATE contracts SET customer_id = :customer_id, title = :title, start_date = :start_date, "
            "min_term_months = :min_term_months, notice_months = :notice_months, renewal = :renewal, "
            "billing_interval = :billing_interval, billing_mode = :billing_mode, auto_issue = :auto_issue, notes = :notes, "
            "updated_at = now() WHERE id = :id"), params | {"id": contract_id})
        action = "contract.updated"
    await _write_lines(conn, contract_id, data["lines"])
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="contract", entity_id=contract_id,
                       after={k: data.get(k) for k in (*FIELDS, "lines")})
    return await get_contract(conn, tenant_id, contract_id)


async def activate(conn: AsyncConnection, tenant_id: UUID, contract_id: UUID, actor: str) -> dict[str, Any]:
    c = await get_contract(conn, tenant_id, contract_id, lock=True)
    if c["status"] != "draft":
        raise ContractStateError("Vertrag ist bereits aktiv oder beendet")
    if not c["lines"]:
        raise ContractValidationError("Vertrag hat keine Positionen")
    end = initial_term_end(c["start_date"], c["min_term_months"]) if c["renewal"] == "none" else None
    await conn.execute(text(
        "UPDATE contracts SET status = 'active', activated_at = now(), activated_by = :a, end_date = :e, updated_at = now() "
        "WHERE id = :id"), {"a": actor, "e": end, "id": contract_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="contract.activated", entity_type="contract",
                       entity_id=contract_id)
    return await get_contract(conn, tenant_id, contract_id)


async def terminate(conn: AsyncConnection, tenant_id: UUID, contract_id: UUID, notice_received: dt.date,
                    actor: str, requested_end: dt.date | None = None) -> dict[str, Any]:
    c = await get_contract(conn, tenant_id, contract_id, lock=True)
    if c["status"] != "active":
        raise ContractStateError("Nur aktive Verträge können gekündigt werden")
    if c["notice_received"] is not None:
        raise ContractStateError("Kündigung ist bereits erfasst")
    earliest = earliest_end(start=c["start_date"], min_term_months=c["min_term_months"], notice_months=c["notice_months"],
                            renewal=c["renewal"], notice_received=notice_received)
    end = max(earliest, requested_end) if requested_end else earliest
    if c["end_date"] is not None:
        end = min(end, c["end_date"])
    await conn.execute(text("UPDATE contracts SET notice_received = :n, end_date = :e, updated_at = now() WHERE id = :id"),
                       {"n": notice_received, "e": end, "id": contract_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="contract.terminated", entity_type="contract",
                       entity_id=contract_id, after={"notice_received": notice_received, "end_date": end})
    result = await get_contract(conn, tenant_id, contract_id)
    result["earliest_end"] = earliest
    return result


def schedule(c: dict[str, Any], until: dt.date) -> list[dict[str, Any]]:
    periods = billing_periods(start=c["start_date"], end=c["end_date"], interval=c["billing_interval"],
                              mode=c["billing_mode"], until=until)
    billed = {b["period_start"] for b in c.get("billings", [])}
    return [{"start": p.start, "end": p.end, "factor": p.factor, "bill_on": p.bill_on, "billed": p.start in billed}
            for p in periods]


def _intro(c: dict[str, Any], start: dt.date, end: dt.date, factor: Decimal) -> str:
    text_ = f"Abrechnung zu Vertrag {c['number']} ({c['title']}) für den Zeitraum {start:%d.%m.%Y} – {end:%d.%m.%Y}."
    if factor != 1:
        text_ += f" Anteilige Berechnung ({(factor * 100).quantize(Decimal('0.01'))} % des {PERIOD_WORD[c['billing_interval']]}s)."
    return text_


async def bill_contract(conn: AsyncConnection, ledger: LedgerClient, tenant_id: UUID, contract_id: UUID, today: dt.date,
                        actor: str) -> list[dict[str, Any]]:
    """Erzeugt fehlende Rechnungen für alle fälligen Zeiträume. Erste Rechnung eines Vertrags bleibt Entwurf."""
    c = await get_contract(conn, tenant_id, contract_id, lock=True)
    if c["status"] != "active":
        return []
    created: list[dict[str, Any]] = []
    for period in schedule(c, today):
        if period["billed"]:
            continue
        factor: Decimal = period["factor"]
        lines = [InvoiceLineIn(
            product_id=ln["product_id"], description=ln["description"],
            quantity=(Decimal(ln["quantity"]) * factor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            unit_code=ln["unit_code"], unit_price=ln["unit_price"], tax_case=ln["tax_case"],
        ) for ln in c["lines"]]
        draft = InvoiceDraftIn(customer_id=c["customer_id"], service_date_from=period["start"], service_date_to=period["end"],
                               intro_text=_intro(c, period["start"], period["end"], factor), lines=lines)
        invoice = await inv.create_draft(conn, tenant_id, draft, actor)
        await conn.execute(text(
            "INSERT INTO contract_billings (contract_id, period_start, period_end, invoice_id) VALUES (:c, :s, :e, :i)"),
            {"c": contract_id, "s": period["start"], "e": period["end"], "i": invoice["id"]})
        first_billing = not c["billings"] and not created
        if c["auto_issue"] and not first_billing:
            invoice = await inv.issue_invoice(conn, ledger, tenant_id, invoice["id"], actor)
        created.append({"invoice_id": invoice["id"], "status": invoice["status"], "number": invoice.get("number"),
                        "period_start": period["start"], "period_end": period["end"]})
    if c["end_date"] is not None and c["end_date"] < today:
        await conn.execute(text("UPDATE contracts SET status = 'ended', updated_at = now() WHERE id = :id"), {"id": contract_id})
    return created


async def run_billing(engine: AsyncEngine, ledger: LedgerClient, tenant_id: UUID, today: dt.date, actor: str) -> dict[str, Any]:
    """Abrechnungslauf über alle aktiven Verträge; jeder Vertrag in eigener Transaktion, Lauf exklusiv (Advisory-Lock)."""
    async with engine.connect() as lock_conn:
        got = await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": BILLING_LOCK_KEY})
        if not got:
            return {"skipped": True, "reason": "Abrechnungslauf läuft bereits", "created": [], "errors": []}
        try:
            async with engine.connect() as conn:
                ids = [r[0] for r in await conn.execute(text(
                    "SELECT id FROM contracts WHERE tenant_id = :t AND status = 'active' ORDER BY number"), {"t": tenant_id})]
            created: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            for cid in ids:
                try:
                    async with engine.begin() as conn:
                        created += await bill_contract(conn, ledger, tenant_id, cid, today, actor)
                except Exception as exc:  # ein fehlerhafter Vertrag stoppt nicht den ganzen Lauf
                    log.exception("Abrechnung Vertrag %s fehlgeschlagen", cid)
                    errors.append({"contract_id": str(cid), "error": str(exc)})
            return {"skipped": False, "created": created, "errors": errors}
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BILLING_LOCK_KEY})
