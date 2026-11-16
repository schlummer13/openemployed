"""API: Offene Posten, Zahlungen, Mahnwesen."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal
from app.services import receivables as rv
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload
from app.services.mail import MailNotConfiguredError

router = APIRouter(prefix="/api", tags=["forderungen"])


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    paid_on: dt.date
    amount: StrictDecimal
    method: Literal["bank", "cash", "offset", "other"] = "bank"
    reference: str | None = Field(default=None, max_length=500)
    amount_eur: StrictDecimal | None = None   # nur bei Fremdwährung: gutgeschriebener Euro-Betrag


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=500)


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str | None = Field(default=None, min_length=3, max_length=500)
    until: dt.date | None = None


class MarkSentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["post", "persönlich", "fax"] = "post"


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    auto_send: bool = True
    b2c_fee: StrictDecimal = Field(ge=0, le=10)
    b2b_flat_fee: bool = True
    deadline_days: int = Field(default=7, ge=5, le=30)


class BaseRateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid_from: dt.date
    rate: StrictDecimal = Field(ge=-10, le=20)


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except rv.ReceivableStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except rv.ReceivableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


def _mailer(request: Request) -> Any:
    mailer = getattr(request.app.state, "mailer", None)
    if mailer is None:
        raise MailNotConfiguredError("Postfach ist nicht konfiguriert")
    return mailer


@router.get("/receivables")
async def list_receivables(request: Request, _u: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: rv.open_items(conn, tenant(), today_berlin())))


@router.get("/invoices/{invoice_id}/receivable")
async def get_receivable(request: Request, invoice_id: UUID, _u: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: rv.invoice_receivable(conn, tenant(), invoice_id, today_berlin()))))


@router.post("/invoices/{invoice_id}/payments", status_code=201)
async def add_payment(request: Request, invoice_id: UUID, data: PaymentIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return _out(await run(request, _guard(lambda conn: rv.record_payment(
        conn, ledger, tenant(), invoice_id, paid_on=data.paid_on, amount=data.amount, method=data.method,
        reference=data.reference, actor=user.email, today=today_berlin(), amount_eur=data.amount_eur))))


@router.post("/payments/{payment_id}/reverse", status_code=204)
async def reverse_payment(request: Request, payment_id: UUID, data: ReasonIn, user: CurrentUser) -> Response:
    ledger = request.app.state.ledger
    await run(request, _guard(lambda conn: rv.reverse_payment(conn, ledger, tenant(), payment_id, data.reason, user.email)))
    return Response(status_code=204)


@router.put("/invoices/{invoice_id}/dunning-block", status_code=204)
async def dunning_block(request: Request, invoice_id: UUID, data: BlockIn, user: CurrentUser) -> Response:
    await run(request, _guard(lambda conn: rv.set_block(conn, tenant(), invoice_id, data.reason, data.until, user.email)))
    return Response(status_code=204)


@router.post("/invoices/{invoice_id}/dunning", status_code=201)
async def create_notice(request: Request, invoice_id: UUID, user: CurrentUser) -> Any:
    return _out(await run(request, _guard(lambda conn: rv.create_notice(conn, tenant(), invoice_id, today_berlin(), user.email))))


@router.post("/dunning/{notice_id}/send")
async def send_notice(request: Request, notice_id: UUID, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return await rv.send_notice(conn, _mailer(request), tenant(), notice_id, user.email)
    return _out(await run(request, _guard(fn)))


@router.post("/dunning/{notice_id}/mark-sent", status_code=204)
async def mark_sent(request: Request, notice_id: UUID, data: MarkSentIn, user: CurrentUser) -> Response:
    await run(request, _guard(lambda conn: rv.mark_notice_sent(conn, tenant(), notice_id, data.channel, user.email)))
    return Response(status_code=204)


@router.get("/dunning/{notice_id}/pdf")
async def notice_pdf(request: Request, notice_id: UUID, _u: CurrentUser) -> Response:
    number, pdf = await run(request, lambda conn: rv.notice_pdf(conn, tenant(), notice_id))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{number}.pdf"', "Cache-Control": "no-store"})


@router.get("/dunning-settings")
async def get_settings(request: Request, _u: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        rates = await rv.base_rates(conn)
        return {"settings": await rv.get_settings(conn, tenant()),
                "base_rates": [{"valid_from": r.valid_from, "rate": r.rate} for r in reversed(rates)],
                "rate_missing": rv.missing_rate(rates, today_berlin()),
                "mail_configured": getattr(request.app.state, "mailer", None) is not None}
    return _out(await run(request, fn))


@router.put("/dunning-settings")
async def save_settings(request: Request, data: SettingsIn, user: CurrentUser) -> Any:
    return _out(await run(request, lambda conn: rv.save_settings(conn, tenant(), data.model_dump(), user.email)))


@router.post("/base-rates", status_code=204)
async def save_base_rate(request: Request, data: BaseRateIn, user: CurrentUser) -> Response:
    await run(request, _guard(lambda conn: rv.save_base_rate(conn, tenant(), data.valid_from, data.rate, user.email)))
    return Response(status_code=204)


@router.post("/dunning/run")
async def run_dunning(request: Request, user: CurrentUser) -> Any:
    return _out(await rv.run_dunning(request.app.state.engine, getattr(request.app.state, "mailer", None), tenant(), today_berlin(),
                                     user.email))
