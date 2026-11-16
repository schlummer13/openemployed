"""API: Bankumsätze und Zahlungsabgleich."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.services import bank as bk
from app.services import receivables as rv
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api/bank", tags=["bank"])
MAX_CSV = 5 * 1024 * 1024


def _guard(fn: Any) -> Any:
    async def wrapped(conn: Any) -> Any:
        try:
            return await fn(conn)
        except bk.BankError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except rv.ReceivableStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except rv.ReceivableError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wrapped


@router.get("/transactions")
async def transactions(request: Request, _u: CurrentUser) -> Any:
    data = await run(request, lambda conn: bk.suggestions(conn, tenant(), today_berlin()))
    data["fints_ready"] = bool(get_settings().fints_product_id)
    return jsonable_encoder(to_ledger_payload(data))


@router.post("/import")
async def import_csv(request: Request, user: CurrentUser, file: Annotated[UploadFile, File()]) -> Any:
    content = await file.read(MAX_CSV + 1)
    if len(content) > MAX_CSV:
        raise HTTPException(status_code=413, detail="Datei zu groß (max. 5 MB)")
    # CAMT.053 (XML), MT940 oder CSV – Format wird erkannt
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        from app.services import finance
        from app.services.bank_formats import camt_closing_balances, parse_any

        stats: dict[str, Any] = dict(await bk.store(conn, tenant(), parse_any(content, file.filename or ""), "file", user.email))
        for b in camt_closing_balances(content) if content.lstrip()[:5] in (b"<?xml", b"<Docu") else []:
            await finance.record_balance(conn, tenant(), b["balance_on"], b["amount"], "camt", user.email, b["iban"])
            stats["balance"] = str(b["amount"])
        stats["auto_matched"] = await bk.auto_match(conn, ledger, tenant(), user.email, today_berlin())
        return stats
    return await run(request, _guard(fn))


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: UUID


class IgnoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str | None = Field(default=None, max_length=500)


@router.post("/transactions/{tx_id}/match")
async def match(request: Request, tx_id: UUID, data: MatchIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return jsonable_encoder(await run(request, _guard(lambda conn: bk.match(conn, ledger, tenant(), tx_id, data.invoice_id, user.email,
                                                                             today_berlin()))))


class MatchReceiptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: UUID


@router.post("/transactions/{tx_id}/match-receipt", status_code=204)
async def match_receipt(request: Request, tx_id: UUID, data: MatchReceiptIn, user: CurrentUser) -> Response:
    """Abbuchung einer gebuchten Eingangsrechnung zuordnen (Beleg wird als bezahlt markiert)."""
    await run(request, _guard(lambda conn: bk.match_receipt(conn, tenant(), tx_id, data.receipt_id, user.email)))
    return Response(status_code=204)


@router.post("/transactions/{tx_id}/ignore", status_code=204)
async def ignore(request: Request, tx_id: UUID, data: IgnoreIn, user: CurrentUser) -> Response:
    await run(request, _guard(lambda conn: bk.ignore(conn, tenant(), tx_id, data.note, user.email)))
    return Response(status_code=204)


# ───────────────────────── Bankverbindungen & FinTS ─────────────────────────

class ConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=100)
    bank_code: str = Field(pattern=r"^[0-9]{8}$")
    server_url: str = Field(pattern=r"^https://[^\s]+$", max_length=300)
    login_name: str = Field(min_length=1, max_length=100)
    iban: str = Field(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9 ]{10,32}$")


class FintsStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: str = Field(min_length=1, max_length=100)
    days: int = Field(default=30, ge=1, le=90)


class FintsTanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=10, max_length=100)
    tan: str = Field(default="", max_length=50)


@router.get("/connections")
async def list_connections(request: Request, _u: CurrentUser) -> Any:
    from sqlalchemy import text

    async def fn(conn: Any) -> Any:
        rows = await conn.execute(text("SELECT id, label, kind, bank_code, server_url, login_name, iban, last_sync_at "
                                       "FROM bank_connections WHERE tenant_id = :t ORDER BY label"), {"t": tenant()})
        return [dict(r) for r in rows.mappings()]
    return {"connections": jsonable_encoder(await run(request, fn)), "fints_ready": bool(get_settings().fints_product_id)}


@router.post("/connections", status_code=201)
async def add_connection(request: Request, data: ConnectionIn, user: CurrentUser) -> Any:
    from sqlalchemy import text

    async def fn(conn: Any) -> Any:
        return await conn.scalar(text(
            "INSERT INTO bank_connections (tenant_id, label, kind, bank_code, server_url, login_name, iban, created_by) "
            "VALUES (:t, :l, 'fints', :b, :s, :u, :i, :a) RETURNING id"),
            {"t": tenant(), "l": data.label, "b": data.bank_code, "s": data.server_url, "u": data.login_name,
             "i": data.iban.replace(" ", ""), "a": user.email})
    return {"id": str(await run(request, fn))}


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(request: Request, connection_id: UUID, _u: CurrentUser) -> Response:
    from sqlalchemy import text

    await run(request, lambda conn: conn.execute(text("DELETE FROM bank_connections WHERE tenant_id = :t AND id = :id"),
                                                 {"t": tenant(), "id": connection_id}))
    return Response(status_code=204)


async def _store_fints(request: Request, result: dict[str, Any], connection_id: UUID | None, actor: str) -> Any:
    if result["status"] != "ok":
        return {k: v for k, v in result.items() if k != "rows"}
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        from sqlalchemy import text

        stats = await bk.store(conn, tenant(), result["rows"], "fints", actor)
        stats["auto_matched"] = await bk.auto_match(conn, ledger, tenant(), actor, today_berlin())
        if connection_id:
            await conn.execute(text("UPDATE bank_connections SET last_sync_at = now() WHERE id = :id"), {"id": connection_id})
        return {"status": "ok"} | stats
    return await run(request, _guard(fn))


_TOKEN_CONNECTION: dict[str, UUID] = {}


@router.post("/connections/{connection_id}/fints")
async def fints_start(request: Request, connection_id: UUID, data: FintsStartIn, user: CurrentUser) -> Any:
    from sqlalchemy import text

    from app.services import fints_bank

    row = await run(request, lambda conn: conn.execute(text(
        "SELECT bank_code, server_url, login_name, iban FROM bank_connections WHERE tenant_id = :t AND id = :id AND kind = 'fints'"),
        {"t": tenant(), "id": connection_id}))
    rec = row.mappings().one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Bankverbindung nicht gefunden")
    start, end = fints_bank.default_range(today_berlin(), data.days)
    params = {"bank_code": rec["bank_code"], "server_url": rec["server_url"], "login": rec["login_name"], "iban": rec["iban"],
              "pin": data.pin, "product_id": get_settings().fints_product_id, "start": start, "end": end}
    try:
        result = await asyncio.to_thread(fints_bank.start, params)
    except bk.BankError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result["status"] == "tan":
        _TOKEN_CONNECTION[result["token"]] = connection_id
    return await _store_fints(request, result, connection_id, user.email)


@router.post("/fints/tan")
async def fints_tan(request: Request, data: FintsTanIn, user: CurrentUser) -> Any:
    from app.services import fints_bank

    connection_id = _TOKEN_CONNECTION.pop(data.token, None)
    try:
        result = await asyncio.to_thread(fints_bank.proceed, data.token, data.tan)
    except bk.BankError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result["status"] == "tan" and connection_id:
        _TOKEN_CONNECTION[result["token"]] = connection_id
    return await _store_fints(request, result, connection_id, user.email)



class BalanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    balance_on: dt.date
    amount: Decimal


@router.post("/balance", status_code=204)
async def add_balance(request: Request, data: BalanceIn, user: CurrentUser) -> Response:
    from app.services import finance

    if data.balance_on > today_berlin():
        raise HTTPException(status_code=422, detail="Datum liegt in der Zukunft")
    await run(request, lambda conn: finance.record_balance(conn, tenant(), data.balance_on, data.amount.quantize(Decimal("0.01")),
                                                           "manual", user.email))
    return Response(status_code=204)
