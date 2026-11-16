"""API: DATEV-Export und Einstellungen."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.services import datev as dv
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api/datev", tags=["datev"])


class TaxKeyIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # „required“ aus der GET-Antwort ist nur Anzeige
    tax_case: str
    account: str | None = Field(default=None, pattern=r"^[0-9]{4,8}$")
    bu_key: str | None = Field(default=None, pattern=r"^[0-9]{1,4}$")


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consultant_number: int | None = Field(default=None, ge=1001, le=9999999)
    client_number: int | None = Field(default=None, ge=1, le=99999)
    chart: Literal["SKR03", "SKR04"] = "SKR04"
    account_length: int = Field(default=4, ge=4, le=8)
    fiscal_year_start: int = Field(default=1, ge=1, le=12)
    bank_account: str = Field(default="1800", pattern=r"^[0-9]{4,8}$")
    receivables_account: str = Field(default="1200", pattern=r"^[0-9]{4,8}$")
    payables_account: str = Field(default="3300", pattern=r"^[0-9]{4,8}$")
    use_personal_accounts: bool = True
    confirmed_by_advisor: bool = False
    tax_keys: list[TaxKeyIn] = []


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_from: dt.date
    period_to: dt.date
    final: bool = False


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except dv.DatevError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


@router.get("/settings")
async def get_settings(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: dv.get_settings(conn, tenant())))


@router.put("/settings")
async def save_settings(request: Request, data: SettingsIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return jsonable_encoder(await run(request, _guard(lambda conn: dv.save_settings(conn, tenant(), payload, user.email))))


@router.post("/export")
async def export(request: Request, data: ExportIn, user: CurrentUser) -> Response:
    ledger = request.app.state.ledger
    result = await run(request, _guard(lambda conn: dv.export(conn, ledger, tenant(), data.period_from, data.period_to,
                                                               final=data.final, actor=user.email)))
    name = f"DATEV_{data.period_from:%Y%m%d}-{data.period_to:%Y%m%d}_{result['kind']}.zip"
    return Response(result["archive"], media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", "X-Export-Kind": result["kind"],
        "X-Bookings": str(result["bookings"]), "X-Problems": str(len(result["problems"])), "X-Content-SHA256": result["sha256"]})


@router.post("/check")
async def check(request: Request, data: ExportIn, _u: CurrentUser) -> Any:
    """Vorabprüfung ohne Datei: Anzahl Buchungen und offene Probleme."""
    async def fn(conn: Any) -> Any:
        settings = await dv.get_settings(conn, tenant())
        bookings, problems, _ = await dv.collect(conn, tenant(), data.period_from, data.period_to, settings)
        return {"bookings": len(bookings), "problems": problems,
                "total": sum((b.amount for b in bookings if b.item_type == "invoice"), start=0)}
    return jsonable_encoder(to_ledger_payload(await run(request, _guard(fn))))


@router.get("/exports")
async def list_exports(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: dv.list_exports(conn, tenant())))


@router.get("/exports/{export_id}")
async def download(request: Request, export_id: UUID, _u: CurrentUser) -> Response:
    meta, archive = await run(request, lambda conn: dv.get_archive(conn, tenant(), export_id))
    name = f"DATEV_{meta['period_from']:%Y%m%d}-{meta['period_to']:%Y%m%d}_{meta['kind']}.zip"
    return Response(archive, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                                    "Cache-Control": "no-store"})
