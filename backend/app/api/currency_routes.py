"""Fremdwährungen: EZB-Referenzkurse abrufen, manuell eintragen, aktuelle Kurse anzeigen."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.domain.international import CURRENCIES
from app.services import exchange

router = APIRouter(prefix="/api/currencies", tags=["waehrungen"])


@router.get("")
async def rates(request: Request, _u: CurrentUser) -> Any:
    return money_json({"currencies": CURRENCIES, "rates": await run(request, exchange.latest)})


@router.post("/fetch")
async def fetch(request: Request, _u: CurrentUser) -> Any:
    import httpx

    try:
        n = await run(request, exchange.fetch_ecb)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"EZB nicht erreichbar: {type(exc).__name__}") from exc
    return {"rates": n}


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_date: dt.date
    rate: Decimal = Field(gt=0, le=Decimal("100000"))


@router.put("/{currency}", status_code=204)
async def set_rate(request: Request, currency: str, data: RateIn, _u: CurrentUser) -> None:
    if currency not in CURRENCIES or currency == "EUR":
        raise HTTPException(status_code=422, detail="Währung nicht unterstützt")
    await run(request, lambda conn: exchange.set_rate(conn, currency, data.rate_date, data.rate))
