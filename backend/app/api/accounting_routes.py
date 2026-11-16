"""API: Buchungsjournal, Summen- und Saldenliste, KI-Kontierung."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.services import accounting as ac
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api", tags=["buchhaltung"])


def _range(start: dt.date | None, end: dt.date | None) -> tuple[dt.date, dt.date]:
    today = today_berlin()
    s, e = start or today.replace(month=1, day=1), end or today
    if e < s or (e - s).days > 731:
        raise HTTPException(status_code=422, detail="Zeitraum ungültig")
    return s, e


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except ac.AccountingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


@router.get("/accounting/journal")
async def get_journal(request: Request, user: CurrentUser, start: dt.date | None = None, end: dt.date | None = None) -> Any:
    s, e = _range(start, end)
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        created = await ac.ensure_postings(conn, ledger, tenant())
        return {"created": created, "chart": await ac.chart_of(conn, tenant()), "entries": await ac.journal(conn, tenant(), s, e)}
    return jsonable_encoder(to_ledger_payload(await run(request, _guard(fn))))


@router.get("/accounting/balances")
async def get_balances(request: Request, _u: CurrentUser, start: dt.date | None = None, end: dt.date | None = None) -> Any:
    s, e = _range(start, end)
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        await ac.ensure_postings(conn, ledger, tenant())
        return await ac.balances(conn, tenant(), s, e)
    return jsonable_encoder(to_ledger_payload(await run(request, _guard(fn))))


@router.get("/accounting/accounts")
async def get_accounts(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        chart = await ac.chart_of(conn, tenant())
        return {"chart": chart, "accounts": await ac.accounts(conn, chart)}
    return jsonable_encoder(await run(request, fn))


@router.post("/receipts/{receipt_id}/ai-book")
@router.post("/receipts/{receipt_id}/claude-book", include_in_schema=False)
async def ai_book(request: Request, receipt_id: UUID, user: CurrentUser) -> Any:
    from app.services import receipts as rc

    provider = getattr(request.app.state, "ai", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="KI ist nicht eingerichtet (OLLAMA_URL für lokale KI) – Beleg regelbasiert buchen")
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        try:
            return await ac.book_receipt_with_ai(conn, ledger, provider, tenant(), receipt_id, user.email)
        except (rc.ReceiptError, rc.ReceiptStateError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return jsonable_encoder(to_ledger_payload(await run(request, _guard(fn))))


@router.get("/gobd/export")
async def gobd_export(request: Request, user: CurrentUser, start: dt.date | None = None, end: dt.date | None = None) -> Any:
    """Datenträgerüberlassung (Z3) für die Betriebsprüfung im GDPdU-Format."""
    from fastapi import Response

    from app.services import gobd_export as gx

    s, e = _range(start, end)
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        await ac.ensure_postings(conn, ledger, tenant())
        return await gx.export(conn, ledger, tenant(), s, e, user.email)
    archive, sha = await run(request, _guard(fn))
    return Response(archive, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="GoBD-Export_{s:%Y%m%d}-{e:%Y%m%d}.zip"', "Cache-Control": "no-store",
        "X-Content-SHA256": sha})


@router.get("/gobd/status")
async def gobd_status(request: Request, _u: CurrentUser) -> Any:
    """Hash-Kette und Festschreibungsstand für die Übersicht."""
    ledger = request.app.state.ledger
    report = await ledger.verify()
    locked = await ledger.locked_periods(tenant_id=tenant())
    return {"chain_ok": report.ok, "records_checked": report.records_checked, "locked_periods": locked,
            "last_locked": locked[-1] if locked else None}
