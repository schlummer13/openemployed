"""API: Verträge und Abrechnungslauf."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.api.core_schemas import InvoiceLineIn
from app.auth.deps import CurrentUser
from app.services import contracts as ct
from app.services.invoices import today_berlin
from app.services.ledger_client import LedgerClient, to_ledger_payload

router = APIRouter(prefix="/api/contracts", tags=["verträge"])


class ContractIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: UUID
    title: str = Field(min_length=1, max_length=200)
    start_date: dt.date
    min_term_months: int = Field(default=0, ge=0, le=120)
    notice_months: int = Field(default=1, ge=0, le=24)
    renewal: Literal["none", "monthly", "term"] = "monthly"
    billing_interval: Literal["monthly", "quarterly", "yearly"] = "monthly"
    billing_mode: Literal["advance", "arrears"] = "advance"
    auto_issue: bool = True
    notes: str | None = Field(default=None, max_length=5000)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=100)

    @field_validator("notes", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None


class TerminateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_received: dt.date
    requested_end: dt.date | None = None


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except ct.ContractStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ct.ContractValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


@router.get("")
async def list_contracts(request: Request, _u: CurrentUser) -> Any:
    rows = await run(request, lambda conn: ct.list_contracts(conn, tenant()))
    return _out({"contracts": rows, "mrr": sum((r["mrr"] for r in rows), start=Decimal("0.00"))})


@router.get("/{contract_id}")
async def get_contract(request: Request, contract_id: UUID, _u: CurrentUser) -> Any:
    c = await run(request, lambda conn: ct.get_contract(conn, tenant(), contract_id))
    c["schedule"] = ct.schedule(c, today_berlin() + dt.timedelta(days=366))[:24]
    return _out(c)


@router.post("", status_code=201)
async def create_contract(request: Request, data: ContractIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return _out(await run(request, _guard(lambda conn: ct.save_contract(conn, tenant(), payload, user.email))))


@router.put("/{contract_id}")
async def update_contract(request: Request, contract_id: UUID, data: ContractIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return _out(await run(request, _guard(lambda conn: ct.save_contract(conn, tenant(), payload, user.email, contract_id))))


@router.post("/{contract_id}/activate")
async def activate_contract(request: Request, contract_id: UUID, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: ct.activate(conn, tenant(), contract_id, user.email))))


@router.post("/{contract_id}/terminate")
async def terminate_contract(request: Request, contract_id: UUID, data: TerminateIn, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: ct.terminate(
        conn, tenant(), contract_id, data.notice_received, user.email, data.requested_end))))


@router.post("/run-billing")
async def run_billing(request: Request, user: CurrentUser) -> Any:
    ledger: LedgerClient = request.app.state.ledger
    return _out(await ct.run_billing(request.app.state.engine, ledger, tenant(), today_berlin(), user.email))
