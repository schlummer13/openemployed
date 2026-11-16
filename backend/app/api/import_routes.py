"""Umstieg: Eröffnungsbilanz, offene Posten, DATEV-Buchungsstapel, Kunden/Lieferanten (Vorschau → Übernahme)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import importer as svc

router = APIRouter(prefix="/api/import", tags=["import"])
MAX_FILE = 10 * 1024 * 1024


async def _read(file: UploadFile) -> bytes:
    content = await file.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 10 MB)")
    if not content:
        raise HTTPException(status_code=422, detail="Datei ist leer")
    return content


@router.get("")
async def overview(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return {"opening": await svc.opening_state(conn, tenant()), "history": await svc.history(conn, tenant())}
    return money_json(await run(request, fn))


class BalanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str = Field(pattern=r"^[0-9]{4}$")
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)

    @field_validator("debit", "credit")
    @classmethod
    def _amt(cls, v: Decimal) -> Decimal:
        if v < 0 or v != v.quantize(Decimal("0.01")):
            raise ValueError("Betrag positiv mit höchstens 2 Nachkommastellen")
        return v


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["receivable", "payable"]
    party: str = Field(min_length=1, max_length=200)
    document_number: str = Field(min_length=1, max_length=100)
    document_date: dt.date
    due_date: dt.date | None = None
    amount: Decimal = Field(gt=0)


class OpeningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opening_date: dt.date
    balances: list[BalanceIn] = Field(default_factory=list, max_length=500)
    items: list[ItemIn] = Field(default_factory=list, max_length=5000)


@router.post("/opening")
async def post_opening(request: Request, data: OpeningIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.post_opening(
        conn, ledger, tenant(), opening_date=data.opening_date, balances=[b.model_dump() for b in data.balances],
        items=[i.model_dump() for i in data.items], actor=user.email)))


@router.post("/opening/items-csv")
async def items_csv(_u: CurrentUser, file: Annotated[UploadFile, File()]) -> Any:
    """Offene Posten aus CSV lesen (nur Vorschau; gebucht wird mit der Eröffnungsbilanz)."""
    items, errors = svc.parse_items_csv(await _read(file))
    return money_json({"items": items, "errors": errors})


class PaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid_on: dt.date


@router.post("/opening/items/{item_id}/paid", status_code=204)
async def item_paid(request: Request, item_id: UUID, data: PaidIn, user: CurrentUser) -> None:
    ledger = request.app.state.ledger
    await run(request, lambda conn: svc.pay_item(conn, ledger, tenant(), item_id, data.paid_on, user.email))


@router.post("/datev")
async def datev(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()],
                dry_run: Annotated[bool, Form()] = True) -> Any:
    content = await _read(file)
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.import_datev(conn, ledger, tenant(), content, file.filename, user.email,
                                                                      dry_run=dry_run)))


@router.post("/{kind}")
async def parties(request: Request, kind: Literal["customers", "suppliers"], user: CurrentUser, file: Annotated[UploadFile, File()],
                  dry_run: Annotated[bool, Form()] = True) -> Any:
    content = await _read(file)
    return money_json(await run(request, lambda conn: svc.import_parties(conn, tenant(), content, file.filename, kind, user.email,
                                                                        dry_run=dry_run)))
