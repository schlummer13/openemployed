"""Auswertungen und Meldungen: BWA, Zusammenfassende Meldung, Steuervorauszahlungen, Monatsabschluss."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import reports as svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/reports", tags=["reports"])
PERIOD = r"^[0-9]{4}-(0[1-9]|1[0-2])$"


def _period(p: str) -> str:
    import re

    if not re.fullmatch(PERIOD, p):
        raise HTTPException(status_code=422, detail="Zeitraum im Format JJJJ-MM")
    return p


@router.get("/bwa/{period}")
async def bwa(request: Request, period: str, _u: CurrentUser) -> Any:
    from app.services.accounting import ensure_postings

    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        await ensure_postings(conn, ledger, tenant())  # Regelbuchungen vollständig
        return await svc.bwa(conn, tenant(), _period(period))
    return money_json(await run(request, fn))


@router.get("/zm/{spec}.csv")
async def zm_csv(request: Request, spec: str, _u: CurrentUser) -> Response:
    data = await run(request, lambda conn: svc.zm(conn, tenant(), spec))
    if data["missing_vat_id"]:
        raise HTTPException(status_code=409, detail=f"USt-IdNr. fehlt bei: {', '.join(data['missing_vat_id'])}")
    return Response(svc.zm_csv(data), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="ZM_{spec}.csv"'})


@router.get("/zm/{spec}")
async def zm(request: Request, spec: str, _u: CurrentUser) -> Any:
    from app.services.tax_report import PeriodSpecError

    try:
        return money_json(await run(request, lambda conn: svc.zm(conn, tenant(), spec)))
    except PeriodSpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/prepayments/{year}")
async def prepayments(request: Request, year: int, _u: CurrentUser) -> Any:
    from app.core.config import get_settings

    async def fn(conn: Any) -> Any:
        ext = await svc.vat_extension(conn, tenant())
        return {"year": year, "items": await svc.prepayments(conn, tenant(), year), "vat_extension": ext,
                "vat_filing": get_settings().vat_filing,
                "svz_proposal": await svc.svz_proposal(conn, tenant(), year) if ext and get_settings().vat_filing == "monthly" else None}
    return money_json(await run(request, fn))


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tax: str = Field(pattern=r"^(kst|gewst)$")
    year: int = Field(ge=2000, le=2100)
    quarterly: Decimal = Field(ge=0, le=Decimal("10000000"))
    soli: Decimal = Field(default=Decimal(0), ge=0)


@router.put("/prepayments/schedule", status_code=204)
async def schedule(request: Request, data: ScheduleIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.set_schedule(conn, tenant(), tax=data.tax, year=data.year, quarterly=data.quarterly,
                                                     soli=data.soli, actor=user.email))


class SvzIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    amount: Decimal = Field(ge=0)


@router.put("/prepayments/svz", status_code=204)
async def svz(request: Request, data: SvzIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.set_svz(conn, tenant(), data.year, data.amount, user.email))


class ExtensionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.put("/vat-extension", status_code=204)
async def vat_extension(request: Request, data: ExtensionIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.set_vat_extension(conn, tenant(), data.enabled, user.email))


class PaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid_on: dt.date


@router.post("/prepayments/{prepayment_id}/paid", status_code=204)
async def paid(request: Request, prepayment_id: UUID, data: PaidIn, user: CurrentUser) -> None:
    ledger = request.app.state.ledger
    await run(request, lambda conn: svc.pay_prepayment(conn, ledger, tenant(), prepayment_id, data.paid_on, user.email))


@router.get("/month/{period}")
async def month(request: Request, period: str, _u: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.month_checklist(conn, tenant(), ledger, _period(period), today_berlin())))


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_open: bool = False


@router.post("/month/{period}/close")
async def close(request: Request, period: str, data: CloseIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.close_month(conn, ledger, tenant(), _period(period), user.email, today_berlin(),
                                                                       data.confirm_open)))
