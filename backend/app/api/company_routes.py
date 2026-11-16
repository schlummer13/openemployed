"""Mehrere Firmen: Firmen des Benutzers, Firma wechseln, neue Firma anlegen, Zugriffe je Firma."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.auth.deps import CurrentUser
from app.core import tenancy
from app.core.config import get_settings
from app.services import companies as svc

router = APIRouter(prefix="/api/companies", tags=["firmen"])


@router.get("")
async def mine(request: Request, user: CurrentUser) -> Any:
    rows = await run(request, lambda conn: svc.for_user(conn, user.id, get_settings().tenant_id))
    return {"current": str(tenant()), "companies": [dict(r, id=str(r["id"])) for r in rows]}


class SelectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_id: UUID


@router.post("/select")
async def select(request: Request, data: SelectIn, user: CurrentUser) -> Response:
    rows = await run(request, lambda conn: svc.for_user(conn, user.id, get_settings().tenant_id))
    if data.company_id not in {r["id"] for r in rows}:
        raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Firma")
    s = get_settings()
    resp = JSONResponse({"current": str(data.company_id)})
    resp.set_cookie(tenancy.COOKIE, str(data.company_id), httponly=True, secure=s.cookie_secure, samesite="lax", path="/",
                    max_age=365 * 24 * 3600)
    return resp


class CompanyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


@router.post("", status_code=201)
async def create(request: Request, data: CompanyIn, user: CurrentUser) -> Any:
    if not user.can("admin", "write"):
        raise HTTPException(status_code=403, detail="Nur die Geschäftsführung kann Firmen anlegen")
    cid = await run(request, lambda conn: svc.create(conn, data.name, user.id, user.email, tenant()))
    return {"id": str(cid)}


@router.get("/members")
async def members(request: Request, user: CurrentUser) -> Any:
    if not user.can("admin", "write"):
        raise HTTPException(status_code=403, detail="Nur die Geschäftsführung")
    return [dict(r, id=str(r["id"])) for r in await run(request, lambda conn: svc.members(conn, tenant()))]


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=254)
    role: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,39}$")


@router.put("/members", status_code=204)
async def set_member(request: Request, data: MemberIn, user: CurrentUser) -> None:
    if not user.can("admin", "write"):
        raise HTTPException(status_code=403, detail="Nur die Geschäftsführung")
    await run(request, lambda conn: svc.set_member(conn, tenant(), data.email, data.role, user.email))
