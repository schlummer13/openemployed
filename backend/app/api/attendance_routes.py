"""Stempeluhr und Abwesenheiten: eigene Daten (/api/me), Personalverwaltung (/api/absences, /api/timeclock) und Mitarbeiter-Portal."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.api.portal_routes import PortalSession
from app.auth.deps import CurrentUser
from app.services import attendance as svc
from app.services.mail import OutgoingMail

router = APIRouter(tags=["zeiten-urlaub"])
log = logging.getLogger("selbstlite.attendance")
Kind = Literal["vacation", "sick", "special", "unpaid", "overtime", "school", "parental", "maternity"]


class AbsenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Kind = "vacation"
    start_date: dt.date
    end_date: dt.date
    half_start: bool = False
    half_end: bool = False
    note: str | None = Field(default=None, max_length=500)


class OutIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str | None = Field(default=None, max_length=300)


async def _notify(request: Request, to: list[str], subject: str, body: str) -> None:
    mailer = getattr(request.app.state, "mailer", None)
    if mailer is None or not to:
        return
    try:
        await mailer.send(OutgoingMail(to=to, subject=subject, text=body))
    except Exception:  # Benachrichtigung ist eine Zugabe
        log.warning("Benachrichtigung nicht versendet: %s", subject)


async def _approvers(conn: Any) -> list[str]:
    from sqlalchemy import text

    rows = await conn.execute(text("SELECT email FROM users WHERE tenant_id = :t AND active AND role = 'admin'"), {"t": tenant()})
    return [r[0] for r in rows]


async def _me_view(conn: Any, emp: dict[str, Any]) -> dict[str, Any]:
    from app.domain.worktime import STATE_NAMES
    from app.services.holidays import merged

    year = svc._now().astimezone(svc.BERLIN).year
    cal = await merged(conn, tenant(), emp["state"], dt.date(year - 1, 1, 1), dt.date(year + 1, 12, 31))
    return {"balance": await svc.balance(conn, tenant(), emp, year), "next_balance": await svc.balance(conn, tenant(), emp, year + 1),
            "absences": await svc.list_absences(conn, tenant(), employee_id=emp["id"], start=dt.date(year - 1, 1, 1)),
            "holidays": cal["holidays"], "half_days": cal["half_days"],
            "kinds": svc.KINDS, "employee": {"name": f"{emp['first_name']} {emp['last_name']}", "work_days": list(svc._work_days(emp)),
                                             "state": emp["state"], "state_name": STATE_NAMES.get(emp["state"], emp["state"])}}


async def _request(request: Request, emp_loader: Any, data: AbsenceIn, actor: str) -> Any:
    async def fn(conn: Any) -> Any:
        emp = await emp_loader(conn)
        aid = await svc.request(conn, tenant(), emp, data.model_dump(), actor)
        return aid, emp, await _approvers(conn)
    aid, emp, approvers = await run(request, fn)
    if data.kind not in ("sick",):
        await _notify(request, approvers, f"Abwesenheitsantrag: {emp['first_name']} {emp['last_name']}",
                      f"{emp['first_name']} {emp['last_name']} beantragt {svc.KINDS[data.kind]} vom {data.start_date:%d.%m.%Y} bis "
                      f"{data.end_date:%d.%m.%Y}.\n\nGenehmigen unter Team → Team-Kalender.")
    return {"id": str(aid)}


# ───────── eigene Daten (angemeldete Benutzer mit Mitarbeiterprofil) ─────────

def _mine(user: Any) -> Any:
    return lambda conn: svc.employee_by_email(conn, tenant(), user.email)


@router.get("/api/me/clock")
async def my_clock(request: Request, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return await svc.clock_status(conn, tenant(), await svc.employee_by_email(conn, tenant(), user.email))
    return money_json(await run(request, fn))


@router.post("/api/me/clock/in", status_code=204)
async def my_in(request: Request, user: CurrentUser) -> None:
    async def fn(conn: Any) -> Any:
        await svc.check_in(conn, tenant(), await svc.employee_by_email(conn, tenant(), user.email), "app", user.email)
    await run(request, fn)


@router.post("/api/me/clock/out", status_code=204)
async def my_out(request: Request, data: OutIn, user: CurrentUser) -> None:
    async def fn(conn: Any) -> Any:
        await svc.check_out(conn, tenant(), await svc.employee_by_email(conn, tenant(), user.email), user.email, data.note)
    await run(request, fn)


@router.get("/api/me/absences")
async def my_absences(request: Request, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        return await _me_view(conn, await svc.employee_by_email(conn, tenant(), user.email))
    return money_json(await run(request, fn))


@router.post("/api/me/absences", status_code=201)
async def my_request(request: Request, data: AbsenceIn, user: CurrentUser) -> Any:
    return await _request(request, _mine(user), data, user.email)


@router.post("/api/me/absences/{absence_id}/cancel", status_code=204)
async def my_cancel(request: Request, absence_id: UUID, user: CurrentUser) -> None:
    async def fn(conn: Any) -> Any:
        await svc.cancel(conn, tenant(), await svc.employee_by_email(conn, tenant(), user.email), absence_id, user.email)
    await run(request, fn)


# ───────── Personalverwaltung (Bereich Lohn & Personal) ─────────

@router.get("/api/absences/calendar")
async def calendar(request: Request, _u: CurrentUser, month: str) -> Any:
    if not (len(month) == 7 and month[4] == "-"):
        raise HTTPException(status_code=422, detail="Monat im Format JJJJ-MM")
    return money_json(await run(request, lambda conn: svc.team_calendar(conn, tenant(), month)))


class AdminAbsenceIn(AbsenceIn):
    employee_id: UUID


@router.post("/api/absences", status_code=201)
async def create_for(request: Request, data: AdminAbsenceIn, user: CurrentUser) -> Any:
    async def fn(conn: Any) -> Any:
        emp = await svc.employee(conn, tenant(), data.employee_id)
        return await svc.request(conn, tenant(), emp, data.model_dump(exclude={"employee_id"}), user.email, approved=True)
    return {"id": str(await run(request, fn))}


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    approve: bool
    note: str | None = Field(default=None, max_length=500)


@router.post("/api/absences/{absence_id}/decide")
async def decide(request: Request, absence_id: UUID, data: DecideIn, user: CurrentUser) -> Any:
    r = await run(request, lambda conn: svc.decide(conn, tenant(), absence_id, data.approve, data.note, user.email))
    emp, a = r["employee"], r["absence"]
    if emp.get("email"):
        await _notify(request, [emp["email"]], f"Dein Antrag wurde {'genehmigt' if data.approve else 'abgelehnt'}",
                      f"{svc.KINDS.get(a['kind'], a['kind'])} vom {a['start_date']:%d.%m.%Y} bis {a['end_date']:%d.%m.%Y} "
                      f"({a['days']} Tage) wurde {'genehmigt' if data.approve else 'abgelehnt'}."
                      + (f"\n\nHinweis: {data.note}" if data.note else ""))
    return {"status": r["status"]}


@router.get("/api/timeclock")
async def timeclock(request: Request, _u: CurrentUser, month: str, employee_id: UUID | None = None) -> Any:
    return money_json(await run(request, lambda conn: svc.entries(conn, tenant(), month, employee_id)))


class CorrectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    started_at: dt.datetime
    ended_at: dt.datetime
    reason: str = Field(min_length=3, max_length=300)


@router.put("/api/timeclock/{entry_id}", status_code=204)
async def correct(request: Request, entry_id: UUID, data: CorrectIn, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.correct(conn, tenant(), entry_id, data.started_at, data.ended_at, data.reason, user.email))


# ───────── Mitarbeiter-Portal ─────────

def _portal_emp(session: dict[str, Any]) -> Any:
    emp = session.get("employee")
    if not emp:
        raise HTTPException(status_code=403, detail="Kein Mitarbeiter-Zugang")
    return lambda conn: svc.employee(conn, tenant(), emp["id"])


@router.get("/api/portal/employee/clock")
async def portal_clock(request: Request, session: PortalSession) -> Any:
    loader = _portal_emp(session)
    return money_json(await run(request, lambda conn: _with(conn, loader, svc.clock_status)))


async def _with(conn: Any, loader: Any, fn: Any) -> Any:
    return await fn(conn, tenant(), await loader(conn))


@router.post("/api/portal/employee/clock/in", status_code=204)
async def portal_in(request: Request, session: PortalSession) -> None:
    loader = _portal_emp(session)

    async def fn(conn: Any) -> Any:
        await svc.check_in(conn, tenant(), await loader(conn), "portal", f"portal:{session['email']}")
    await run(request, fn)


@router.post("/api/portal/employee/clock/out", status_code=204)
async def portal_out(request: Request, data: OutIn, session: PortalSession) -> None:
    loader = _portal_emp(session)

    async def fn(conn: Any) -> Any:
        await svc.check_out(conn, tenant(), await loader(conn), f"portal:{session['email']}", data.note)
    await run(request, fn)


@router.get("/api/portal/employee/absences")
async def portal_absences(request: Request, session: PortalSession) -> Any:
    loader = _portal_emp(session)

    async def fn(conn: Any) -> Any:
        return await _me_view(conn, await loader(conn))
    return money_json(await run(request, fn))


@router.post("/api/portal/employee/absences", status_code=201)
async def portal_request(request: Request, data: AbsenceIn, session: PortalSession) -> Any:
    return await _request(request, _portal_emp(session), data, f"portal:{session['email']}")


@router.post("/api/portal/employee/absences/{absence_id}/cancel", status_code=204)
async def portal_cancel(request: Request, absence_id: UUID, session: PortalSession) -> None:
    loader = _portal_emp(session)

    async def fn(conn: Any) -> Any:
        await svc.cancel(conn, tenant(), await loader(conn), absence_id, f"portal:{session['email']}")
    await run(request, fn)


# ───────── Urlaubsrechner (Vorschau beim Anlegen) ─────────

class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_date: dt.date
    exit_date: dt.date | None = None
    vacation_days: int = Field(ge=0, le=60)
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1, max_length=7)


@router.post("/api/absences/preview")
async def preview(data: PreviewIn, _u: CurrentUser) -> Any:
    from decimal import Decimal

    from app.domain.worktime import entitlement

    today = svc._now().astimezone(svc.BERLIN).date()
    years = sorted({today.year, data.entry_date.year, today.year + 1} | ({data.exit_date.year} if data.exit_date else set()))
    years = [y for y in years if y >= data.entry_date.year and (not data.exit_date or y <= data.exit_date.year)][:4]
    return money_json({"years": [{"year": y, **entitlement(y, Decimal(data.vacation_days), entry=data.entry_date, exit_=data.exit_date,
                                                         work_days_per_week=len(set(data.work_days)))} for y in years]})
