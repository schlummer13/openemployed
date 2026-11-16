"""SEPA-Zahlungsverkehr: Überweisungsdateien (Lieferanten, Löhne), Lastschriftmandate und Lastschriftdateien."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.domain.sepa import next_business_day
from app.services import sepa as svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/sepa", tags=["sepa"])


@router.get("")
async def overview(request: Request, _u: CurrentUser) -> Any:
    today = today_berlin()

    async def fn(conn: Any) -> Any:
        return {"settings": await svc.settings(conn, tenant()), "transfers": await svc.transfer_candidates(conn, tenant()),
                "debits": await svc.debit_candidates(conn, tenant(), today), "mandates": await svc.mandates(conn, tenant()),
                "files": await svc.files(conn, tenant()), "earliest_collection": next_business_day(today, 1),
                "next_execution": next_business_day(today, 0) if today.weekday() < 5 else next_business_day(today, 1)}
    return money_json(await run(request, fn))


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    creditor_id: str | None = Field(default=None, max_length=35)
    batch_booking: bool = True


@router.put("/settings", status_code=204)
async def save_settings(request: Request, data: SettingsIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.save_settings(conn, tenant(), data.creditor_id, data.batch_booking, user.email))


class TransferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_ids: list[UUID] = Field(min_length=1, max_length=500)
    execution_date: dt.date


@router.post("/transfers")
async def transfers(request: Request, data: TransferIn, user: CurrentUser) -> Any:
    fid, _ = await run(request, lambda conn: svc.supplier_transfers(conn, tenant(), data.receipt_ids, data.execution_date, user.email))
    return {"id": str(fid)}


class SalaryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    execution_date: dt.date


@router.post("/salaries")
async def salaries(request: Request, data: SalaryIn, user: CurrentUser) -> Any:
    fid, _ = await run(request, lambda conn: svc.salary_transfers(conn, tenant(), data.period, data.execution_date, user.email))
    return {"id": str(fid)}


class MandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: UUID
    reference: str | None = Field(default=None, max_length=35)
    scheme: Literal["CORE", "B2B"] = "CORE"
    signed_on: dt.date
    account_holder: str = Field(min_length=2, max_length=70)
    iban: str = Field(min_length=15, max_length=42)
    bic: str | None = Field(default=None, max_length=11)
    recurring: bool = True


@router.post("/mandates", status_code=201)
async def create_mandate(request: Request, data: MandateIn, user: CurrentUser) -> Any:
    mid = await run(request, lambda conn: svc.save_mandate(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(mid)}


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revoked_on: dt.date


@router.post("/mandates/{mandate_id}/revoke", status_code=204)
async def revoke(request: Request, mandate_id: UUID, data: RevokeIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.revoke_mandate(conn, tenant(), mandate_id, data.revoked_on, user.email))


class DebitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_ids: list[UUID] = Field(min_length=1, max_length=500)
    collection_date: dt.date


@router.post("/debits")
async def debits(request: Request, data: DebitIn, user: CurrentUser) -> Any:
    fid, _ = await run(request, lambda conn: svc.direct_debits(conn, tenant(), data.invoice_ids, data.collection_date, user.email))
    return {"id": str(fid)}


@router.get("/files/{file_id}.xml")
async def download(request: Request, file_id: UUID, _u: CurrentUser) -> Response:
    msg_id, xml = await run(request, lambda conn: svc.file_xml(conn, tenant(), file_id))
    return Response(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{msg_id}.xml"',
                                                                 "Cache-Control": "no-store"})
