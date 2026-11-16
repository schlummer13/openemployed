"""Zeiterfassung (inkl. Abrechnung) und Reisekosten."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal
from app.services import time_tracking as tt
from app.services import travel as tr
from app.services.invoices import today_berlin
from app.services.masterdata import NotFoundError

router = APIRouter(prefix="/api", tags=["time", "travel"])


class TimeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_id: UUID
    task_id: UUID | None = None
    shareholder_id: UUID | None = None
    work_date: dt.date
    minutes: int = Field(ge=1, le=1440)
    description: str = Field(min_length=1, max_length=500)
    billable: bool = True


class BillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_ids: list[UUID] = Field(min_length=1, max_length=500)
    hourly_rate: StrictDecimal | None = None


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hourly_rate: StrictDecimal | None = None
    customer_id: UUID | None = None


class TripIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    shareholder_id: UUID
    purpose: str = Field(min_length=1, max_length=300)
    destination: str = Field(min_length=1, max_length=200)
    start_date: dt.date
    start_time: dt.time
    end_date: dt.date
    end_time: dt.time
    km: StrictDecimal = Decimal("0")
    breakfasts: int = Field(default=0, ge=0, le=100)
    lunches: int = Field(default=0, ge=0, le=100)
    dinners: int = Field(default=0, ge=0, le=100)
    other_costs: StrictDecimal = Decimal("0")
    other_note: str | None = Field(default=None, max_length=500)


class PaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid_on: dt.date


# ───────────── Zeiterfassung ─────────────

@router.get("/time")
async def list_time(request: Request, _u: CurrentUser, project_id: UUID | None = None, start: dt.date | None = None,
                    end: dt.date | None = None, open_only: bool = False) -> Any:
    return money_json(await run(request, lambda conn: tt.list_entries(conn, tenant(), project_id=project_id, start=start, end=end,
                                                                            open_only=open_only)))


@router.post("/time", status_code=201)
async def create_time(request: Request, data: TimeIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: tt.save_entry(conn, tenant(), data.model_dump(), user.email)))}


@router.put("/time/{entry_id}", status_code=204)
async def update_time(request: Request, entry_id: UUID, data: TimeIn, user: CurrentUser) -> None:
    await run(request, lambda conn: tt.save_entry(conn, tenant(), data.model_dump(), user.email, entry_id))


@router.delete("/time/{entry_id}", status_code=204)
async def delete_time(request: Request, entry_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: tt.delete_entry(conn, tenant(), entry_id, user.email))
    return Response(status_code=204)


@router.post("/projects/{project_id}/bill", status_code=201)
async def bill_project(request: Request, project_id: UUID, data: BillIn, user: CurrentUser) -> Any:
    """Offene Stunden als Rechnungsentwurf an den Projektkunden (Freigabe wie jede Rechnung per Klick)."""
    result = await run(request, lambda conn: tt.bill(conn, tenant(), project_id, data.entry_ids, user.email, data.hourly_rate))
    return money_json(result)


@router.put("/projects/{project_id}/billing", status_code=204)
async def project_billing(request: Request, project_id: UUID, data: RateIn, _u: CurrentUser) -> None:
    async def fn(conn: Any) -> None:
        res = await conn.execute(text("UPDATE projects SET hourly_rate = :r, customer_id = :c WHERE tenant_id = :t AND id = :id"),
                                 {"r": data.hourly_rate, "c": data.customer_id, "t": tenant(), "id": project_id})
        if res.rowcount == 0:
            raise NotFoundError("Projekt nicht gefunden")
    await run(request, fn)


# ───────────── Reisekosten ─────────────

@router.get("/travel")
async def list_travel(request: Request, _u: CurrentUser, year: int | None = None) -> Any:
    y = year or today_berlin().year
    return money_json({"year": y, "trips": await run(request, lambda conn: tr.list_trips(conn, tenant(), y))})


@router.post("/travel/preview")
async def preview_travel(data: TripIn, _u: CurrentUser) -> Any:
    return money_json(tr.preview(data.model_dump()))


@router.post("/travel", status_code=201)
async def create_travel(request: Request, data: TripIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: tr.save_trip(conn, tenant(), data.model_dump(), user.email)))}


@router.put("/travel/{trip_id}", status_code=204)
async def update_travel(request: Request, trip_id: UUID, data: TripIn, user: CurrentUser) -> None:
    await run(request, lambda conn: tr.save_trip(conn, tenant(), data.model_dump(), user.email, trip_id))


@router.delete("/travel/{trip_id}", status_code=204)
async def delete_travel(request: Request, trip_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: tr.delete_trip(conn, tenant(), trip_id, user.email))
    return Response(status_code=204)


@router.post("/travel/{trip_id}/book", status_code=204)
async def book_travel(request: Request, trip_id: UUID, user: CurrentUser) -> None:
    ledger = request.app.state.ledger
    await run(request, lambda conn: tr.book(conn, ledger, tenant(), trip_id, user.email))


@router.post("/travel/{trip_id}/paid", status_code=204)
async def paid_travel(request: Request, trip_id: UUID, data: PaidIn, user: CurrentUser) -> None:
    ledger = request.app.state.ledger
    await run(request, lambda conn: tr.mark_paid(conn, ledger, tenant(), trip_id, data.paid_on, user.email))
