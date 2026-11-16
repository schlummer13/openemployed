"""Feiertage: Kalender je Jahr und Bundesland (beliebig vorausberechnet), betriebliche freie Tage, iCal-Export."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.api.portal_routes import PortalSession
from app.auth.deps import CurrentUser
from app.services import holidays as svc

router = APIRouter(tags=["feiertage"])
Year = Annotated[int, Query(ge=1900, le=2200)]


@router.get("/api/holidays")
async def calendar(request: Request, _u: CurrentUser, year: Year, state: str = "NW") -> Any:
    return money_json(await run(request, lambda conn: svc.calendar(conn, tenant(), year, state.upper())))


@router.get("/api/holidays/ical")
async def ical(request: Request, _u: CurrentUser, start: Year, end: Year, state: str = "NW") -> Response:
    if end < start or end - start > 30:
        raise HTTPException(status_code=422, detail="Höchstens 30 Jahre auf einmal")
    body = await run(request, lambda conn: svc.ics(conn, tenant(), state.upper(), start, end))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="feiertage-{state.lower()}-{start}-{end}.ics"'})


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    rule: Literal["fixed", "easter"] = "fixed"
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    easter_offset: int | None = Field(default=None, ge=-80, le=80)
    year: int | None = Field(default=None, ge=2000, le=2200)
    half_day: bool = False

    @model_validator(mode="after")
    def _check(self) -> RuleIn:
        if self.rule == "fixed" and (self.month is None or self.day is None):
            raise ValueError("Tag und Monat angeben")
        if self.rule == "easter" and self.easter_offset is None:
            raise ValueError("Abstand zu Ostern angeben")
        return self


@router.post("/api/holidays/company", status_code=201)
async def add(request: Request, data: RuleIn, user: CurrentUser) -> Any:
    return {"id": str(await run(request, lambda conn: svc.add_rule(conn, tenant(), data.model_dump(), user.email)))}


@router.delete("/api/holidays/company/{rule_id}", status_code=204)
async def delete(request: Request, rule_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.delete_rule(conn, tenant(), rule_id, user.email))


@router.get("/api/portal/employee/holidays")
async def portal_calendar(request: Request, session: PortalSession, year: Year) -> Any:
    emp = session.get("employee")
    if not emp:
        raise HTTPException(status_code=403, detail="Kein Mitarbeiter-Zugang")

    async def fn(conn: Any) -> Any:
        from app.services.attendance import employee

        e = await employee(conn, tenant(), emp["id"])
        return await svc.calendar(conn, tenant(), year, e["state"])
    return money_json(await run(request, fn))
