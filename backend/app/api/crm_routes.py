"""Vertrieb: Pipeline (Kanban), Leads, Aktivitäten, Umwandlung in Kunde und Angebot."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.services import crm as svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/crm", tags=["vertrieb"])


@router.get("")
async def board(request: Request, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: svc.board(conn, tenant(), today_berlin())))


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=50)
    source: str | None = Field(default=None, max_length=100)
    value: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("100000000"))
    recurring: bool = False
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close: dt.date | None = None
    owner_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)


@router.post("/leads", status_code=201)
async def create(request: Request, data: LeadIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: svc.save(conn, tenant(), data.model_dump(), user.email)))}


@router.get("/leads/{lead_id}")
async def detail(request: Request, lead_id: UUID, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: svc.get(conn, tenant(), lead_id)))


@router.put("/leads/{lead_id}", status_code=204)
async def update(request: Request, lead_id: UUID, data: LeadIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.save(conn, tenant(), data.model_dump(), user.email, lead_id))


@router.delete("/leads/{lead_id}", status_code=204)
async def delete(request: Request, lead_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.delete(conn, tenant(), lead_id, user.email))


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["new", "contacted", "qualified", "offer", "won", "lost"]
    before_id: UUID | None = None
    lost_reason: str | None = Field(default=None, max_length=300)


@router.post("/leads/{lead_id}/move", status_code=204)
async def move(request: Request, lead_id: UUID, data: MoveIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.move(conn, tenant(), lead_id, data.stage, user.email, before_id=data.before_id,
                                             lost_reason=data.lost_reason))


class ActivityIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["note", "call", "email", "meeting", "task"]
    text: str = Field(min_length=1, max_length=5000)
    due_date: dt.date | None = None


@router.post("/leads/{lead_id}/activities", status_code=201)
async def activity(request: Request, lead_id: UUID, data: ActivityIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.add_activity(conn, tenant(), lead_id, data.kind, data.text, data.due_date, user.email))


class DoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool = True


@router.post("/activities/{activity_id}/done", status_code=204)
async def done(request: Request, activity_id: UUID, data: DoneIn, _u: CurrentUser) -> None:
    await run(request, lambda conn: svc.complete_activity(conn, tenant(), activity_id, data.done))


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str | None = None
    street: str = Field(min_length=1, max_length=200)
    postal_code: str = Field(min_length=1, max_length=12)
    city: str = Field(min_length=1, max_length=100)
    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    vat_id: str | None = None


@router.post("/leads/{lead_id}/customer")
async def to_customer(request: Request, lead_id: UUID, data: AddressIn, user: CurrentUser) -> Any:
    return {"customer_id": str(await run(request, lambda conn: svc.convert_to_customer(conn, tenant(), lead_id, data.model_dump(),
                                                                                       user.email)))}


@router.post("/leads/{lead_id}/offer")
async def to_offer(request: Request, lead_id: UUID, user: CurrentUser) -> Any:
    return {"offer_id": str(await run(request, lambda conn: svc.create_offer(conn, tenant(), lead_id, user.email, today_berlin())))}
