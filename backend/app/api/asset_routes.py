"""Anlagenbuchhaltung: Verzeichnis, Anlagenspiegel, Übernahme aus Belegen und Abschreibungslauf."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal
from app.services import assets as svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    account: str = Field(pattern=r"^[0-9]{4}$")
    asset_class: Literal["digital", "furniture", "vehicle", "machine", "intangible", "other"] = "other"
    method: Literal["linear", "digital_full", "pool", "gwg"] = "linear"
    acquisition_date: dt.date
    cost: StrictDecimal
    useful_life_months: int = Field(ge=1, le=600)
    receipt_id: UUID | None = None
    disposal_date: dt.date | None = None
    disposal_note: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("cost")
    @classmethod
    def _cost(cls, v: Decimal) -> Decimal:
        if v <= 0 or v != v.quantize(Decimal("0.01")):
            raise ValueError("Anschaffungskosten (netto) positiv mit höchstens 2 Nachkommastellen")
        return v


class YearIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)


@router.get("")
async def list_assets(request: Request, _u: CurrentUser, year: int | None = None) -> Any:
    y = year or today_berlin().year

    async def fn(conn: Any) -> Any:
        return {"year": y, "assets": await svc.list_assets(conn, tenant(), y), "candidates": await svc.candidates(conn, tenant()),
                "accounts": [{"number": n, "name": a} for n, a in (await svc.asset_accounts(conn, tenant())).items()],
                "useful_life": svc.useful_life_suggestions()}
    return money_json(await run(request, fn))


@router.post("", status_code=201)
async def create_asset(request: Request, data: AssetIn, user: CurrentUser) -> Any:
    aid = await run(request, lambda conn: svc.save_asset(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(aid)}


@router.put("/{asset_id}", status_code=204)
async def update_asset(request: Request, asset_id: UUID, data: AssetIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.save_asset(conn, tenant(), data.model_dump(), user.email, asset_id))


@router.post("/depreciation")
async def post_depreciation(request: Request, data: YearIn, user: CurrentUser) -> Any:
    """Abschreibungslauf: bucht die AfA des Jahres zum 31.12. in die Abschlussperiode (einmal je Anlagegut)."""
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.post_depreciation(conn, ledger, tenant(), data.year, user.email)))
