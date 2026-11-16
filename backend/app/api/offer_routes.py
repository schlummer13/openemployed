"""API: Angebote."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.api.core_schemas import InvoiceLineIn
from app.auth.deps import CurrentUser
from app.services import contracts as ct
from app.services import offers as of
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api/offers", tags=["angebote"])


class OfferLineIn(InvoiceLineIn):
    recurring: bool = False


class OfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: UUID
    title: str = Field(min_length=1, max_length=200)
    valid_until: dt.date
    intro_text: str | None = Field(default=None, max_length=5000)
    closing_text: str | None = Field(default=None, max_length=5000)
    lines: list[OfferLineIn] = Field(min_length=1, max_length=500)

    @field_validator("intro_text", "closing_text", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    accept: bool
    name: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid_until: dt.date


class ConvertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_start: dt.date | None = None


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except (of.OfferStateError, ct.ContractStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (of.OfferValidationError, ct.ContractValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


@router.get("")
async def list_offers(request: Request, _u: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: of.list_offers(conn, tenant(), today_berlin())))


@router.get("/{offer_id}")
async def get_offer(request: Request, offer_id: UUID, _u: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: of.get_offer(conn, tenant(), offer_id, today_berlin())))


@router.post("", status_code=201)
async def create_offer(request: Request, data: OfferIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return _out(await run(request, _guard(lambda conn: of.save_draft(conn, tenant(), payload, user.email, today_berlin()))))


@router.put("/{offer_id}")
async def update_offer(request: Request, offer_id: UUID, data: OfferIn, user: CurrentUser) -> Any:
    payload = data.model_dump()
    return _out(await run(request, _guard(lambda conn: of.save_draft(conn, tenant(), payload, user.email, today_berlin(), offer_id))))


@router.delete("/{offer_id}", status_code=204)
async def delete_offer(request: Request, offer_id: UUID, _u: CurrentUser) -> Response:
    await run(request, _guard(lambda conn: of.delete_draft(conn, tenant(), offer_id, today_berlin())))
    return Response(status_code=204)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: bool = False


@router.post("/{offer_id}/send")
async def send_offer(request: Request, offer_id: UUID, user: CurrentUser, data: SendIn | None = None) -> Any:
    """Markiert als versendet (Inhalt wird unveränderlich) und versendet auf Wunsch per E-Mail – beides oder nichts."""
    mailer = getattr(request.app.state, "mailer", None)
    if data and data.email and mailer is None:
        raise HTTPException(status_code=503, detail="E-Mail nicht eingerichtet – Angebot ohne E-Mail als versendet markieren")

    async def fn(conn: Any) -> Any:
        result = await of.mark_sent(conn, tenant(), offer_id, user.email, today_berlin())
        if data and data.email:
            await of.email_offer(conn, mailer, tenant(), offer_id, today_berlin(), user.email)
            result["emailed"] = True
        return result
    return _out(await run(request, _guard(fn)))


@router.post("/{offer_id}/decision")
async def decide_offer(request: Request, offer_id: UUID, data: DecisionIn, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: of.decide(
        conn, tenant(), offer_id, accept=data.accept, name=data.name, channel="manual", note=data.note, actor=user.email,
        today=today_berlin()))))


@router.post("/{offer_id}/revision", status_code=201)
async def revise_offer(request: Request, offer_id: UUID, data: RevisionIn, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: of.new_revision(
        conn, tenant(), offer_id, user.email, today_berlin(), data.valid_until))))


@router.post("/{offer_id}/convert")
async def convert_offer(request: Request, offer_id: UUID, data: ConvertIn, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: of.convert(
        conn, tenant(), offer_id, user.email, today_berlin(), data.contract_start))))


@router.get("/{offer_id}/pdf")
async def offer_pdf(request: Request, offer_id: UUID, _u: CurrentUser) -> Response:
    number, pdf = await run(request, _guard(lambda conn: of.render_pdf(conn, tenant(), offer_id, today_berlin())))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Angebot_{number}.pdf"', "Cache-Control": "no-store"})
