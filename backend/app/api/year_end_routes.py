"""Jahresabschluss-Vorbereitung: Bilanz/GuV-Entwurf, Checkliste, Abschlussbuchungen, Festschreibung, Übergabepaket."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.core.money import StrictDecimal
from app.services import year_end as svc

router = APIRouter(prefix="/api/year-end", tags=["year-end"])


class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["tax_provision", "closing_costs", "other_provision", "prepaid", "deferred"]
    description: str = Field(min_length=1, max_length=200)
    amount: StrictDecimal | None = None
    account: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    kst: StrictDecimal | None = None
    solz: StrictDecimal | None = None
    gewst: StrictDecimal | None = None


class LockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_open: bool = False


@router.get("/{year}")
async def overview(request: Request, year: int, _u: CurrentUser) -> Any:
    ledger = request.app.state.ledger

    async def fn(conn: Any) -> Any:
        from app.services.accounting import ensure_postings

        await ensure_postings(conn, ledger, tenant(), "system:jahresabschluss")  # Regelbuchungen zuerst vollständig
        return {"statements": await svc.statements(conn, tenant(), year), "checklist": await svc.checklist(conn, tenant(), ledger, year),
                "entries": await svc.list_entries(conn, tenant(), year),
                "tax_proposal": await svc.tax_provision_proposal(conn, tenant(), year)}
    return money_json(await run(request, fn))


@router.post("/{year}/entries", status_code=201)
async def add_entry(request: Request, year: int, data: EntryIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    body = data.model_dump()
    if body["amount"] is None:
        body["amount"] = "0"
    return money_json(await run(request, lambda conn: svc.add_entry(conn, ledger, tenant(), year, body, user.email)))


@router.post("/{year}/lock")
async def lock(request: Request, year: int, data: LockIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.lock_year(conn, ledger, tenant(), year, user.email, data.confirm_open)))


@router.get("/{year}/export", response_class=Response)
async def export(request: Request, year: int, _u: CurrentUser) -> Response:
    ledger = request.app.state.ledger
    async def fn(conn: Any) -> bytes:
        from app.services.accounting import ensure_postings

        await ensure_postings(conn, ledger, tenant(), "system:jahresabschluss")
        return await svc.export_package(conn, ledger, tenant(), year)
    content = await run(request, fn)
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="jahresabschluss_{year}.zip"', "Cache-Control": "no-store"})
