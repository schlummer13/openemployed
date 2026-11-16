"""API: Steuer-Dashboard, UStVA, Periodensperre, Steuerkalender."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.services import tax_report
from app.services.invoices import today_berlin
from app.services.ledger_client import LedgerClient, LedgerError, to_ledger_payload
from app.services.periods import month_of

router = APIRouter(prefix="/api/tax", tags=["steuern"])


async def _is_ug(conn: AsyncConnection) -> bool:
    form = await conn.scalar(text("SELECT legal_form FROM company_profiles WHERE tenant_id = :t ORDER BY valid_from DESC LIMIT 1"),
                             {"t": tenant()})
    return bool(form != "GmbH")


def _money(data: Any) -> Any:
    """Decimals als Strings mit 2 Nachkommastellen ausgeben."""
    return jsonable_encoder(to_ledger_payload(data))


@router.get("/overview/{year}")
async def overview(request: Request, year: int, _u: CurrentUser) -> Any:
    if not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Jahr ungültig")
    s = get_settings()

    async def op(conn: AsyncConnection) -> dict[str, Any]:
        return await tax_report.year_overview(conn, tenant(), year, trade_tax_multiplier=s.trade_tax_multiplier,
                                              is_ug=await _is_ug(conn))
    return _money(await run(request, op))


@router.get("/vat/{spec}")
async def vat_return(request: Request, spec: str, _u: CurrentUser) -> Any:
    ledger: LedgerClient = request.app.state.ledger
    try:
        months = tax_report.months_for(spec)
    except tax_report.PeriodSpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = await run(request, lambda conn: tax_report.ustva(conn, tenant(), spec))
    locked = set(await ledger.locked_periods(tenant_id=tenant()))
    result["locked"] = all(m in locked for m in months)
    result["closable"] = months[-1] < month_of(today_berlin()) and not result["locked"]
    return _money(result)


@router.post("/vat/{spec}/lock")
async def lock_vat_period(request: Request, spec: str, user: CurrentUser, confirm_open_items: bool = False) -> Any:
    """Schreibt die UStVA-Werte fest und sperrt die Monate. Spätere Vorgänge fallen in den nächsten offenen Monat."""
    ledger: LedgerClient = request.app.state.ledger
    try:
        months = tax_report.months_for(spec)
    except tax_report.PeriodSpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if months[-1] >= month_of(today_berlin()):
        raise HTTPException(status_code=409, detail="Der Zeitraum ist noch nicht abgelaufen")
    locked = set(await ledger.locked_periods(tenant_id=tenant()))
    if all(m in locked for m in months):
        raise HTTPException(status_code=409, detail="Der Zeitraum ist bereits festgeschrieben")
    # Monate, die schon per Monatsabschluss gesperrt sind, bleiben unverändert; die UStVA wird trotzdem festgeschrieben
    from app.services import closing
    from app.services.accounting import ensure_postings

    async def prepare(conn: Any) -> Any:
        await ensure_postings(conn, ledger, tenant(), f"user:{user.email}")  # Journal vollständig vor der Sperre
        return await closing.open_items(conn, tenant(), months)
    items = await run(request, prepare)
    if items and not confirm_open_items:
        raise HTTPException(status_code=409, detail={"message": "Offene Punkte im Zeitraum – bitte erledigen oder ausdrücklich bestätigen",
                                                     "open_items": items})
    report = await run(request, lambda conn: tax_report.ustva(conn, tenant(), spec))
    summary = {"period": spec, "months": months, "payable": report["payable"],
               "open_items_confirmed": [i["text"] for i in items],
               "kennzahlen": {line["kz"]: {k: line[k] for k in ("base", "tax") if k in line} for line in report["lines"]}}
    try:
        sealed = await ledger.seal(tenant_id=tenant(), record_type="vat_return.locked", payload=summary,
                                   sealed_by=f"user:{user.email}")
        for m in months:
            if m not in locked:
                await ledger.lock_period(tenant_id=tenant(), period=m, sealed_by=f"user:{user.email}")
    except LedgerError as exc:
        raise HTTPException(status_code=409 if exc.status_code == 409 else 502, detail=exc.message) from exc
    return _money({"period": spec, "locked": True, "ledger_hash": sealed.hash, "payable": report["payable"]})


@router.get("/calendar/{year}")
async def tax_calendar(request: Request, year: int, _u: CurrentUser) -> Any:
    if not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Jahr ungültig")
    s = get_settings()
    ledger: LedgerClient = request.app.state.ledger
    locked = set(await ledger.locked_periods(tenant_id=tenant()))
    from app.services import reports

    async def fn(conn: Any) -> Any:
        return (await tax_report.active_months(conn, tenant()), await reports.vat_extension(conn, tenant()),
                await reports.prepayments(conn, tenant(), year))
    active, extension, prepay = await run(request, fn)
    deadlines = tax_report.calendar(year, filing=s.vat_filing, extension=extension, locked=locked, active=active, today=today_berlin())
    # Beträge aus erfassten Vorauszahlungsbescheiden an die Termine hängen
    for d in deadlines:
        kind = str(d["kind"])
        match = next((p for p in prepay if p["due_date"] == d["due"] and (
            {"kst": "kst", "gewst": "gewst", "svz": "ust_svz"}.get(kind) == p["tax"])), None)
        if match:
            d["amount"] = str(match["amount"] + match["soli"])
            d["paid_on"] = match["paid_on"].isoformat() if match["paid_on"] else None
    return jsonable_encoder({"filing": s.vat_filing, "extension": extension, "deadlines": deadlines})
