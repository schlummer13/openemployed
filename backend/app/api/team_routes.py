"""API: Gesellschafter & Geschäftsführergehälter (Planung) sowie Projekte/Kanban."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core.config import get_settings
from app.core.money import StrictDecimal
from app.services import projects as pj
from app.services import tax_report, team
from app.services.invoices import today_berlin
from app.services.ledger_client import to_ledger_payload

router = APIRouter(prefix="/api", tags=["team"])


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ShareholderIn(In):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=200)
    share_percent: StrictDecimal
    is_managing_director: bool = True
    phone: str | None = Field(default=None, max_length=50)
    birth_date: dt.date | None = None
    street: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    city: str | None = Field(default=None, max_length=100)
    tax_id: str | None = Field(default=None, description="Steuerliche Identifikationsnummer (11 Ziffern)")
    social_security_number: str | None = Field(default=None, description="Rentenversicherungsnummer, z. B. 12150385M123")
    iban: str | None = Field(default=None, max_length=40)
    role_title: str | None = Field(default=None, max_length=100)
    joined_on: dt.date | None = None
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("phone", "street", "city", "role_title", "postal_code", mode="before")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("tax_id", mode="before")
    @classmethod
    def _tax_id(cls, v: str | None) -> str | None:
        if not v:
            return None
        digits = "".join(ch for ch in str(v) if ch.isdigit())
        if len(digits) != 11 or digits[0] == "0" or not tax_id_check(digits):
            raise ValueError("Steuer-ID ungültig (11 Ziffern, Prüfziffer)")
        return digits

    @field_validator("social_security_number", mode="before")
    @classmethod
    def _svnr(cls, v: str | None) -> str | None:
        if not v:
            return None
        compact = str(v).replace(" ", "").upper()
        import re

        if not re.fullmatch(r"[0-9]{2}[0-9]{6}[A-Z][0-9]{3}", compact):
            raise ValueError("Rentenversicherungsnummer im Format 12 150385 M 123")
        return compact

    @field_validator("iban", mode="before")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        if not v:
            return None
        from app.api.core_schemas import iban_valid

        compact = str(v).replace(" ", "").upper()
        if not iban_valid(compact):
            raise ValueError("IBAN ungültig (Prüfsumme)")
        return compact


    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return v.strip().lower() or None if v else None

    @field_validator("share_percent")
    @classmethod
    def _share(cls, v: Decimal) -> Decimal:
        if not Decimal(0) < v <= 100:
            raise ValueError("Anteil zwischen 0 und 100 %")
        return v




def tax_id_check(digits: str) -> bool:
    """Prüfziffer der Steuer-ID (ISO 7064, MOD 11,10)."""
    product = 10
    for d in digits[:10]:
        total = (int(d) + product) % 10 or 10
        product = (total * 2) % 11
    check = (11 - product) % 10
    return check == int(digits[10])


class CompensationIn(In):
    shareholder_ids: list[UUID] = Field(min_length=1, max_length=20)
    valid_from: dt.date
    gross_month: StrictDecimal
    salaries_per_year: int = Field(default=12, ge=12, le=14)
    sv_status: Literal["pflichtig", "frei"] = "pflichtig"
    tax_class: int = Field(default=1, ge=1, le=6)
    church_tax: bool = False
    childless: bool = True

    @field_validator("gross_month")
    @classmethod
    def _gross(cls, v: Decimal) -> Decimal:
        if v < 0 or v > 100000:
            raise ValueError("Bruttogehalt zwischen 0 und 100.000 €")
        return v.quantize(Decimal("0.01"))


class TaxProfileIn(In):
    valid_from: dt.date
    tax_class: int = Field(ge=1, le=6)
    child_allowances: StrictDecimal = Decimal("0")
    church_tax: bool = False
    state: Literal["BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"] = "NW"
    birth_year: int | None = Field(default=None, ge=1930, le=2015)
    children_under_25: int = Field(default=0, ge=0, le=20)
    health_insurance: Literal["gkv", "pkv"] = "gkv"
    health_insurer: str | None = Field(default=None, max_length=100)
    kv_additional_rate: StrictDecimal | None = None
    pkv_premium: StrictDecimal = Decimal("0")

    @field_validator("child_allowances")
    @classmethod
    def _children(cls, v: Decimal) -> Decimal:
        if v < 0 or v > 20 or (v * 2) != (v * 2).to_integral_value():
            raise ValueError("Kinderfreibeträge in Schritten von 0,5")
        return v

    @field_validator("kv_additional_rate")
    @classmethod
    def _kv(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and not Decimal(0) <= v <= 6:
            raise ValueError("Zusatzbeitrag zwischen 0 und 6 %")
        return v


class ProjectIn(In):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    customer_id: UUID | None = None
    color: str = Field(default="#3b6cf6", pattern=r"^#[0-9a-fA-F]{6}$")


class TaskIn(In):
    column_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    assignee: str | None = Field(default=None, max_length=200)
    assignee_id: UUID | None = None
    due_date: dt.date | None = None
    priority: Literal["niedrig", "mittel", "hoch", "dringend"] = "mittel"

    @field_validator("assignee", "description", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("assignee")
    @classmethod
    def _lower(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else None


class TaskUpdateIn(In):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    assignee: str | None = Field(default=None, max_length=200)
    assignee_id: UUID | None = None
    due_date: dt.date | None = None
    priority: Literal["niedrig", "mittel", "hoch", "dringend"] = "mittel"

    @field_validator("assignee", "description", mode="before")
    @classmethod
    def _empty(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("assignee")
    @classmethod
    def _lower(cls, v: str | None) -> str | None:
        return v.strip().lower() if v else None


class MoveIn(In):
    column_id: UUID
    before_id: UUID | None = None


def _out(data: Any) -> Any:
    return jsonable_encoder(to_ledger_payload(data))


@router.get("/team")
async def team_overview(request: Request, _u: CurrentUser) -> Any:
    today = today_berlin()
    s = get_settings()

    async def op(conn: Any) -> dict[str, Any]:
        holders = await team.list_shareholders(conn, tenant(), today)
        cap = await team.capacity(conn, tenant(), today, holders)
        year = await tax_report.year_overview(conn, tenant(), today.year, trade_tax_multiplier=s.trade_tax_multiplier, is_ug=True)
        dist = team.distribution_plan(year["income_tax"]["profit_after_tax"], year["income_tax"]["ug_reserve"], holders)
        return {"shareholders": holders, "capacity": cap, "distribution": dist, "params": team.PARAMS_INFO,
                "share_total": sum((h["share_percent"] for h in holders), start=Decimal(0)).quantize(Decimal("0.01"))}
    return _out(await run(request, op))


@router.post("/shareholders", status_code=201)
async def create_shareholder(request: Request, data: ShareholderIn, user: CurrentUser) -> Any:
    sid = await run(request, lambda conn: team.save_shareholder(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(sid)}


@router.put("/shareholders/{shareholder_id}/photo", status_code=204)
async def upload_photo(request: Request, shareholder_id: UUID, user: CurrentUser, file: Annotated[UploadFile, File()]) -> Response:
    content = await file.read(2 * 1024 * 1024 + 1)
    await run(request, lambda conn: team.set_photo(conn, tenant(), shareholder_id, content, user.email))
    return Response(status_code=204)


@router.delete("/shareholders/{shareholder_id}/photo", status_code=204)
async def delete_photo(request: Request, shareholder_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: team.set_photo(conn, tenant(), shareholder_id, None, user.email))
    return Response(status_code=204)


@router.get("/shareholders/{shareholder_id}/photo")
async def get_photo(request: Request, shareholder_id: UUID, _u: CurrentUser) -> Response:
    from sqlalchemy import text

    row = await run(request, lambda conn: conn.execute(text("SELECT photo, photo_mime FROM shareholders WHERE tenant_id = :t AND id = :id"),
                                                       {"t": tenant(), "id": shareholder_id}))
    rec = row.one_or_none()
    if rec is None or rec.photo is None:
        raise HTTPException(status_code=404, detail="Kein Foto")
    return Response(bytes(rec.photo), media_type=rec.photo_mime, headers={"Cache-Control": "private, max-age=300",
                                                                           "X-Content-Type-Options": "nosniff"})


@router.put("/shareholders/{shareholder_id}", status_code=204)
async def update_shareholder(request: Request, shareholder_id: UUID, data: ShareholderIn, user: CurrentUser) -> Response:
    await run(request, lambda conn: team.save_shareholder(conn, tenant(), data.model_dump(), user.email, shareholder_id))
    return Response(status_code=204)


@router.post("/compensations", status_code=201)
async def add_compensation(request: Request, data: CompensationIn, user: CurrentUser) -> Response:
    payload = data.model_dump(exclude={"shareholder_ids"})
    await run(request, lambda conn: team.add_compensation(conn, tenant(), data.shareholder_ids, payload, user.email, today_berlin()))
    return Response(status_code=201)


@router.post("/shareholders/{shareholder_id}/tax-profile", status_code=201)
async def add_tax_profile(request: Request, shareholder_id: UUID, data: TaxProfileIn, user: CurrentUser) -> Response:
    payload = data.model_dump()
    await run(request, lambda conn: team.save_tax_profile(conn, tenant(), shareholder_id, payload, user.email, today_berlin()))
    return Response(status_code=201)


@router.get("/projects")
async def list_projects(request: Request, _u: CurrentUser) -> Any:
    return jsonable_encoder(await run(request, lambda conn: pj.list_projects(conn, tenant())))


@router.get("/projects/search")
async def search_projects(request: Request, _u: CurrentUser, q: str = Query(min_length=2, max_length=100)) -> Any:
    return jsonable_encoder(await run(request, lambda conn: pj.search(conn, tenant(), q.strip())))


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(request: Request, project_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: pj.delete_project(conn, tenant(), project_id, user.email))
    return Response(status_code=204)


@router.post("/projects", status_code=201)
async def create_project(request: Request, data: ProjectIn, user: CurrentUser) -> Any:
    pid = await run(request, lambda conn: pj.create_project(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(pid)}


@router.get("/projects/{project_id}/board")
async def get_board(request: Request, project_id: UUID, _u: CurrentUser) -> Any:
    # Positionen sind Gleitkommazahlen (Sortierung, kein Geld) → normale JSON-Ausgabe
    return jsonable_encoder(await run(request, lambda conn: pj.board(conn, tenant(), project_id)))


@router.post("/projects/{project_id}/tasks", status_code=201)
async def create_task(request: Request, project_id: UUID, data: TaskIn, user: CurrentUser) -> Any:
    tid = await run(request, lambda conn: pj.create_task(conn, tenant(), project_id, data.model_dump(), user.email))
    return {"id": str(tid)}


@router.put("/tasks/{task_id}", status_code=204)
async def update_task(request: Request, task_id: UUID, data: TaskUpdateIn, _u: CurrentUser) -> Response:
    await run(request, lambda conn: pj.update_task(conn, tenant(), task_id, data.model_dump()))
    return Response(status_code=204)


@router.post("/tasks/{task_id}/move", status_code=204)
async def move_task(request: Request, task_id: UUID, data: MoveIn, _u: CurrentUser) -> Response:
    await run(request, lambda conn: pj.move_task(conn, tenant(), task_id, data.column_id, data.before_id))
    return Response(status_code=204)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(request: Request, task_id: UUID, user: CurrentUser) -> Response:
    await run(request, lambda conn: pj.delete_task(conn, tenant(), task_id, user.email))
    return Response(status_code=204)
