"""API: Budgetplanung je Bereich."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal
from app.services import budgets as bg
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api/budgets", tags=["budget"])


class AreaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    monthly_budget: StrictDecimal
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    categories: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("monthly_budget")
    @classmethod
    def _budget(cls, v: Decimal) -> Decimal:
        if v < 0 or v > Decimal("10000000"):
            raise ValueError("Budget zwischen 0 und 10 Mio. €")
        return v.quantize(Decimal("0.01"))

    @field_validator("description", mode="before")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v or None


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area_id: UUID | None
    receipt_ids: list[UUID] = Field(min_length=1, max_length=500)


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


def _year_range(year: int | None) -> tuple[int, dt.date, dt.date]:
    y = year or today_berlin().year
    if not 2000 <= y <= 2100:
        raise HTTPException(status_code=422, detail="Jahr ungültig")
    return y, dt.date(y, 1, 1), dt.date(y, 12, 31)


@router.get("")
async def get_overview(request: Request, _u: CurrentUser, year: int | None = None) -> Any:
    y, _, _ = _year_range(year)
    return _out(await run(request, lambda conn: bg.overview(conn, tenant(), y, today_berlin())))


@router.post("", status_code=201)
async def create_area(request: Request, data: AreaIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return {"id": str(await run(request, lambda conn: bg.save_area(conn, tenant(), payload, user.email)))}


@router.put("/{area_id}", status_code=204)
async def update_area(request: Request, area_id: UUID, data: AreaIn, user: CurrentUser) -> Response:
    payload = data.model_dump()
    await run(request, lambda conn: bg.save_area(conn, tenant(), payload, user.email, area_id))
    return Response(status_code=204)


@router.delete("/{area_id}", status_code=204)
async def archive_area(request: Request, area_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: bg.archive_area(conn, tenant(), area_id, user.email))
    return Response(status_code=204)


@router.get("/expenses")
async def list_expenses(request: Request, _u: CurrentUser, year: int | None = None, area_id: UUID | None = None,
                        unassigned: bool = False) -> Any:
    _, s, e = _year_range(year)
    return _out(await run(request, lambda conn: bg.expenses(conn, tenant(), s, e, area_id, unassigned)))


@router.post("/assign")
async def assign(request: Request, data: AssignIn, user: CurrentUser) -> Any:
    return {"assigned": await run(request, lambda conn: bg.assign(conn, tenant(), data.area_id, data.receipt_ids, user.email))}


@router.post("/apply-suggestions")
async def apply_suggestions(request: Request, user: CurrentUser, year: int | None = None) -> Any:
    _, s, e = _year_range(year)
    return {"assigned": await run(request, lambda conn: bg.apply_suggestions(conn, tenant(), s, e, user.email))}
