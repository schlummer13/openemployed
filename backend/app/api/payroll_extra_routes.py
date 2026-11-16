# ruff: noqa: E501 – SQL-Anweisungen
"""Lohn: Positionen je Monat (Einmalzahlung, Sachbezug, Vorschuss …), Pfändungen, Lohn-Einstellungen, SV-Meldungen,
Beitragsnachweise und Lohnsteuerbescheinigungen."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import audit
from app.services import payroll_reports as rep
from app.services.invoices import today_berlin
from app.services.masterdata import ConflictError, NotFoundError

router = APIRouter(prefix="/api/payroll", tags=["lohn"])
ItemKind = Literal["overtime", "bonus", "one_time", "benefit", "tax_free", "advance", "deduction"]


async def _approved(conn: Any, period: str) -> bool:
    return bool(await conn.scalar(text("SELECT 1 FROM payroll_runs WHERE tenant_id = :t AND period = :p AND status = 'approved'"),
                                  {"t": tenant(), "p": period}))


# ───────── Positionen ─────────

class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    employee_id: UUID
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    kind: ItemKind
    label: str = Field(min_length=1, max_length=120)
    quantity: Decimal | None = Field(default=None, ge=0, le=10_000)
    rate: Decimal | None = Field(default=None, ge=0, le=100_000)
    amount: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    voucher: bool = False  # Gutschein/Sachbezug bis 50 €: Freigrenze § 8 Abs. 2 Satz 11 EStG


@router.get("/items")
async def items(request: Request, _u: CurrentUser, period: str) -> Any:
    async def fn(conn: Any) -> Any:
        rows = await conn.execute(text(
            "SELECT i.id, i.employee_id, e.first_name || ' ' || e.last_name AS name, i.kind, i.label, i.quantity, i.rate, i.amount, "
            "i.source, i.created_by, i.created_at FROM payroll_items i JOIN employees e ON e.id = i.employee_id WHERE i.tenant_id = :t "
            "AND i.period = :p ORDER BY name, i.created_at"), {"t": tenant(), "p": period})
        return [dict(r) for r in rows.mappings()]
    return money_json(await run(request, fn))


@router.post("/items", status_code=201)
async def add_item(request: Request, data: ItemIn, user: CurrentUser) -> Any:
    amount = data.amount if data.amount is not None else (data.quantity or Decimal(0)) * (data.rate or Decimal(0))
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Betrag oder Menge × Satz angeben")
    kind, label = data.kind, data.label
    if data.kind == "benefit" and data.voucher and amount <= 50:
        kind, label = "tax_free", f"{label} (Sachbezug bis 50 €, steuerfrei)"

    async def fn(conn: Any) -> Any:
        if await _approved(conn, data.period):
            raise ConflictError("Der Lohnlauf dieses Monats ist bereits freigegeben")
        if data.kind == "benefit" and data.voucher:
            used = await conn.scalar(text("SELECT coalesce(sum(amount), 0) FROM payroll_items WHERE tenant_id = :t AND employee_id = :e "
                                          "AND period = :p AND label LIKE '%Sachbezug bis 50 €%'"), {"t": tenant(), "e": data.employee_id,
                                                                                                       "p": data.period})
            if Decimal(used) + amount > 50:
                raise ConflictError("Sachbezugsfreigrenze von 50 € im Monat überschritten – als steuerpflichtigen Sachbezug erfassen")
        iid = await conn.scalar(text(
            "INSERT INTO payroll_items (tenant_id, employee_id, period, kind, label, quantity, rate, amount, created_by) "
            "VALUES (:t, :e, :p, :k, :l, :q, :r, :a, :u) RETURNING id"),
            {"t": tenant(), "e": data.employee_id, "p": data.period, "k": kind, "l": label, "q": data.quantity, "r": data.rate,
             "a": amount, "u": user.email})
        await audit.record(conn, tenant_id=tenant(), actor=user.email, action="payroll.item_added", entity_type="employee",
                           entity_id=data.employee_id, after={"period": data.period, "kind": kind, "amount": str(amount), "label": label})
        return iid
    return {"id": str(await run(request, fn))}


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(request: Request, item_id: UUID, user: CurrentUser) -> None:
    async def fn(conn: Any) -> None:
        row = (await conn.execute(text("SELECT period, source FROM payroll_items WHERE tenant_id = :t AND id = :id"),
                                  {"t": tenant(), "id": item_id})).one_or_none()
        if row is None:
            raise NotFoundError("Position nicht gefunden")
        if await _approved(conn, row[0]):
            raise ConflictError("Der Lohnlauf dieses Monats ist bereits freigegeben")
        if row[1]:
            raise ConflictError("Automatische Position – im Zeitkonto entfernen")
        await conn.execute(text("DELETE FROM payroll_items WHERE id = :id"), {"id": item_id})
        await audit.record(conn, tenant_id=tenant(), actor=user.email, action="payroll.item_deleted", entity_type="payroll_item",
                           entity_id=item_id)
    await run(request, fn)


# ───────── Pfändungen ─────────

class GarnishmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    employee_id: UUID
    creditor: str = Field(min_length=1, max_length=200)
    reference: str = Field(min_length=1, max_length=100)
    creditor_iban: str | None = Field(default=None, max_length=42)
    kind: Literal["table", "fixed", "assignment"] = "table"
    fixed_amount: Decimal | None = Field(default=None, gt=0, le=100_000)
    dependents: int = Field(default=0, ge=0, le=10)
    claim_total: Decimal = Field(gt=0, le=10_000_000)
    served_on: dt.date
    active: bool = True
    notes: str | None = Field(default=None, max_length=1000)


@router.get("/garnishments")
async def garnishments(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        rows = await conn.execute(text(
            "SELECT g.*, e.first_name || ' ' || e.last_name AS name, coalesce((SELECT sum(CAST(x->>'amount' AS numeric)) FROM payslips p "
            "JOIN payroll_runs r ON r.id = p.run_id, jsonb_array_elements(coalesce(p.details->'garnishments', '[]')) x "
            "WHERE r.status = 'approved' AND x->>'id' = g.id::text), 0) AS paid FROM garnishments g JOIN employees e ON e.id = g.employee_id "
            "WHERE g.tenant_id = :t ORDER BY g.active DESC, g.served_on"), {"t": tenant()})
        from app.services.payroll import _garnish_params

        p = await _garnish_params(conn, tenant())
        return {"garnishments": [dict(r) for r in rows.mappings()],
                "params": {"base": p.base, "first_dependent": p.first_dependent, "further_dependent": p.further_dependent, "cap": p.cap}}
    return money_json(await run(request, fn))


@router.post("/garnishments", status_code=201)
async def add_garnishment(request: Request, data: GarnishmentIn, user: CurrentUser) -> Any:
    if data.kind == "fixed" and not data.fixed_amount:
        raise HTTPException(status_code=422, detail="Fester Monatsbetrag fehlt (z. B. Unterhaltspfändung nach § 850d ZPO)")

    async def fn(conn: Any) -> Any:
        gid = await conn.scalar(text(
            "INSERT INTO garnishments (tenant_id, employee_id, creditor, reference, creditor_iban, kind, fixed_amount, dependents, claim_total, "
            "served_on, active, notes, created_by) VALUES (:t, :e, :c, :r, :i, :k, :f, :d, :ct, :s, :a, :n, :u) RETURNING id"),
            {"t": tenant(), "e": data.employee_id, "c": data.creditor, "r": data.reference,
             "i": data.creditor_iban.replace(" ", "").upper() if data.creditor_iban else None, "k": data.kind, "f": data.fixed_amount,
             "d": data.dependents, "ct": data.claim_total, "s": data.served_on, "a": data.active, "n": data.notes, "u": user.email})
        await audit.record(conn, tenant_id=tenant(), actor=user.email, action="payroll.garnishment_added", entity_type="employee",
                           entity_id=data.employee_id, after={"creditor": data.creditor, "reference": data.reference})
        return gid
    return {"id": str(await run(request, fn))}


@router.put("/garnishments/{gid}", status_code=204)
async def put_garnishment(request: Request, gid: UUID, data: GarnishmentIn, user: CurrentUser) -> None:
    async def fn(conn: Any) -> None:
        res = await conn.execute(text(
            "UPDATE garnishments SET creditor = :c, reference = :r, creditor_iban = :i, kind = :k, fixed_amount = :f, dependents = :d, "
            "claim_total = :ct, served_on = :s, active = :a, notes = :n WHERE tenant_id = :t AND id = :id"),
            {"t": tenant(), "id": gid, "c": data.creditor, "r": data.reference,
             "i": data.creditor_iban.replace(" ", "").upper() if data.creditor_iban else None, "k": data.kind, "f": data.fixed_amount,
             "d": data.dependents, "ct": data.claim_total, "s": data.served_on, "a": data.active, "n": data.notes})
        if not res.rowcount:
            raise NotFoundError("Pfändung nicht gefunden")
        await audit.record(conn, tenant_id=tenant(), actor=user.email, action="payroll.garnishment_updated", entity_type="garnishment",
                           entity_id=gid, after={"active": data.active})
    await run(request, fn)


# ───────── Einstellungen ─────────

class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employer_number: str | None = Field(default=None, pattern=r"^\d{8}$")
    garnishment_base: Decimal | None = Field(default=None, gt=0)
    garnishment_first: Decimal | None = Field(default=None, ge=0)
    garnishment_more: Decimal | None = Field(default=None, ge=0)
    garnishment_cap: Decimal | None = Field(default=None, gt=0)


@router.get("/settings")
async def settings(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        row = (await conn.execute(text("SELECT * FROM payroll_settings WHERE tenant_id = :t"), {"t": tenant()})).mappings().one_or_none()
        from app.domain.lohnsteuer import available_years

        return {"settings": dict(row) if row else {}, "pap_years": available_years()}
    return money_json(await run(request, fn))


@router.put("/settings", status_code=204)
async def put_settings(request: Request, data: SettingsIn, user: CurrentUser) -> None:
    async def fn(conn: Any) -> None:
        await conn.execute(text(
            "INSERT INTO payroll_settings (tenant_id, employer_number, garnishment_base, garnishment_first, garnishment_more, garnishment_cap) "
            "VALUES (:t, :e, :b, :f, :m, :c) ON CONFLICT (tenant_id) DO UPDATE SET employer_number = EXCLUDED.employer_number, "
            "garnishment_base = EXCLUDED.garnishment_base, garnishment_first = EXCLUDED.garnishment_first, "
            "garnishment_more = EXCLUDED.garnishment_more, garnishment_cap = EXCLUDED.garnishment_cap, updated_at = now()"),
            {"t": tenant(), "e": data.employer_number, "b": data.garnishment_base, "f": data.garnishment_first, "m": data.garnishment_more,
             "c": data.garnishment_cap})
        await audit.record(conn, tenant_id=tenant(), actor=user.email, action="payroll.settings", entity_type="payroll", entity_id=None,
                           after=json.loads(data.model_dump_json()))
    await run(request, fn)


# ───────── Meldewesen ─────────

@router.get("/sv-reports")
async def sv_reports(request: Request, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: rep.sv_reports(conn, tenant(), today_berlin())))


class MarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["submitted", "cancelled", "open"] = "submitted"
    reference: str | None = Field(default=None, max_length=100)


@router.post("/sv-reports/{report_id}", status_code=204)
async def mark_report(request: Request, report_id: UUID, data: MarkIn, user: CurrentUser) -> None:
    await run(request, lambda conn: rep.mark_report(conn, tenant(), report_id, data.status, data.reference, user.email))


@router.get("/statements")
async def statements(request: Request, _u: CurrentUser, period: str) -> Any:
    return money_json(await run(request, lambda conn: rep.statements(conn, tenant(), period)))


@router.post("/statements/{statement_id}/submitted", status_code=204)
async def mark_statement(request: Request, statement_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: rep.mark_statement(conn, tenant(), statement_id, user.email))


@router.post("/certificates/{year}")
async def build_certificates(request: Request, year: int, user: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: rep.build_certificates(conn, tenant(), year, user.email)))


@router.get("/certificates")
async def certificates(request: Request, _u: CurrentUser, year: int) -> Any:
    return money_json(await run(request, lambda conn: rep.certificates(conn, tenant(), year)))


@router.get("/certificates/{cert_id}/pdf")
async def certificate_pdf(request: Request, cert_id: UUID, _u: CurrentUser) -> Response:
    name, pdf = await run(request, lambda conn: rep.certificate_pdf(conn, tenant(), cert_id))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"',
                                                                 "Cache-Control": "private, no-store"})
