"""Lohnbuchhaltung: Mitarbeiter, Lohnläufe, Abrechnungen (PDF), Zahlungen und Lohnsteuer-Anmeldung."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.core_routes import run, tenant
from app.api.encoding import money_json
from app.auth.deps import CurrentUser
from app.domain.payroll import MIDIJOB_UPPER_2026, MINIJOB_LIMIT_2026, MINIMUM_WAGE_2026
from app.services import payroll as svc
from app.services.invoices import today_berlin

router = APIRouter(prefix="/api/payroll", tags=["payroll"])
PERIOD = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
STATES = ("BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH")


def _money(v: Decimal | None) -> Decimal | None:
    if v is not None and (v < 0 or v != v.quantize(Decimal("0.01"))):
        raise ValueError("Betrag positiv mit höchstens 2 Nachkommastellen")
    return v


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=50)
    birth_date: dt.date | None = None
    street: str | None = Field(default=None, max_length=200)
    postal_code: str | None = Field(default=None, max_length=10)
    city: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=100)
    entry_date: dt.date
    exit_date: dt.date | None = None
    employment_type: Literal["regular", "minijob", "werkstudent"] = "regular"
    salary_type: Literal["monthly", "hourly"] = "monthly"
    gross_month: Decimal | None = None
    hourly_rate: Decimal | None = None
    weekly_hours: Decimal | None = Field(default=None, ge=0, le=60)
    salaries_per_year: int = Field(default=12, ge=12, le=14)
    vacation_days: int | None = Field(default=None, ge=0, le=60)
    tax_id: str | None = Field(default=None, pattern=r"^[0-9]{11}$")
    tax_class: int = Field(default=1, ge=1, le=6)
    child_allowances: Decimal = Field(default=Decimal(0), ge=0, le=10)
    church_tax: bool = False
    church_denomination: Literal["ev", "rk", "other"] = "ev"
    state: Literal[STATES] = "NW"  # type: ignore[valid-type]
    social_security_number: str | None = Field(default=None, pattern=r"^[0-9]{2}[0-9]{6}[A-Z][0-9]{3}$")
    health_insurance: Literal["gkv", "pkv"] = "gkv"
    health_insurer: str | None = Field(default=None, max_length=100)
    kv_additional_rate: Decimal | None = Field(default=None, ge=0, le=10)
    pkv_premium: Decimal = Decimal(0)
    children_under_25: int = Field(default=0, ge=0, le=20)
    rv_exempt: bool = False
    iban: str | None = Field(default=None, max_length=42)
    account_holder: str | None = Field(default=None, max_length=70)
    portal_enabled: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1, max_length=7)
    vacation_carryover: Decimal = Field(default=Decimal(0), ge=0, le=60)
    time_account_start: dt.date | None = None
    overtime_rule: Literal["account", "payout", "included"] = "account"
    overtime_cap_hours: Decimal | None = Field(default=None, ge=0, le=1000)
    tax_factor: Decimal | None = Field(default=None, gt=0, lt=1)
    tax_allowance_month: Decimal = Field(default=Decimal(0), ge=0, le=100_000)
    tax_addition_month: Decimal = Field(default=Decimal(0), ge=0, le=100_000)
    activity_key: str | None = Field(default=None, pattern=r"^[0-9]{9}$")
    nationality: str = Field(default="DE", min_length=2, max_length=3)
    birth_name: str | None = Field(default=None, max_length=100)
    birth_place: str | None = Field(default=None, max_length=100)
    car_list_price: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    car_distance_km: Decimal | None = Field(default=None, ge=0, le=500)
    car_kind: Literal["combustion", "hybrid", "electric"] | None = None
    car_copayment: Decimal = Field(default=Decimal(0), ge=0, le=10_000)

    @field_validator("work_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("Wochentage 0 (Mo) bis 6 (So)")
        return sorted(set(v))

    @field_validator("gross_month", "hourly_rate", "pkv_premium")
    @classmethod
    def _m(cls, v: Decimal | None) -> Decimal | None:
        return _money(v)

    @field_validator("iban")
    @classmethod
    def _iban(cls, v: str | None) -> str | None:
        if not v:
            return None
        from app.api.core_schemas import iban_valid

        iban = v.replace(" ", "").upper()
        if not iban_valid(iban):
            raise ValueError("IBAN ungültig (Prüfziffer)")
        return iban

    @field_validator("social_security_number", mode="before")
    @classmethod
    def _ssn(cls, v: str | None) -> str | None:
        return v.replace(" ", "").upper() if v else None


@router.get("/employees")
async def employees(request: Request, _u: CurrentUser, archived: bool = False) -> Any:
    async def fn(conn: Any) -> Any:
        return {"employees": await svc.list_employees(conn, tenant(), archived=archived),
                "limits": {"minijob": MINIJOB_LIMIT_2026, "midijob": MIDIJOB_UPPER_2026, "minimum_wage": MINIMUM_WAGE_2026}}
    return money_json(await run(request, fn))


@router.post("/employees", status_code=201)
async def create_employee(request: Request, data: EmployeeIn, user: CurrentUser) -> Any:
    eid, hints = await run(request, lambda conn: svc.save_employee(conn, tenant(), data.model_dump(), user.email))
    return {"id": str(eid), "hints": hints}


@router.put("/employees/{employee_id}")
async def update_employee(request: Request, employee_id: UUID, data: EmployeeIn, user: CurrentUser) -> Any:
    _, hints = await run(request, lambda conn: svc.save_employee(conn, tenant(), data.model_dump(), user.email, employee_id))
    return {"hints": hints}


@router.delete("/employees/{employee_id}", status_code=204)
async def archive_employee(request: Request, employee_id: UUID, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.archive_employee(conn, tenant(), employee_id, user.email))


@router.get("/employees/{employee_id}/preview")
async def preview(request: Request, employee_id: UUID, _u: CurrentUser) -> Any:
    """Probeabrechnung für den aktuellen Monat (ohne Speicherung)."""
    period = f"{today_berlin():%Y-%m}"

    async def fn(conn: Any) -> Any:
        slips = [s for s in await svc.calculate(conn, tenant(), period) if s["employee_id"] == employee_id]
        return slips[0] if slips else None
    return money_json(await run(request, fn))


@router.get("/runs")
async def runs(request: Request, _u: CurrentUser) -> Any:
    return money_json(await run(request, lambda conn: svc.list_runs(conn, tenant())))


@router.get("/runs/{period}")
async def get_run(request: Request, period: str, _u: CurrentUser) -> Any:
    svc.period_bounds(period)

    async def fn(conn: Any) -> Any:
        r = await svc.get_run(conn, tenant(), period)
        if r is None:
            return {"period": period, "status": "none", "payslips": await svc.calculate(conn, tenant(), period), "preview": True}
        return r
    out = await run(request, fn)
    if out.get("preview"):
        out["totals"] = svc.totals(out["payslips"])
    return money_json(out)


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: dict[UUID, Decimal] | None = None

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: dict[UUID, Decimal] | None) -> dict[UUID, Decimal] | None:
        if v and any(h < 0 or h > 400 for h in v.values()):
            raise ValueError("Stunden zwischen 0 und 400")
        return v


@router.post("/runs/{period}")
async def save_run(request: Request, period: str, data: RunIn, user: CurrentUser) -> Any:
    svc.period_bounds(period)
    return money_json(await run(request, lambda conn: svc.save_run(conn, tenant(), period, user.email, data.hours)))


@router.delete("/runs/{period}", status_code=204)
async def delete_run(request: Request, period: str, user: CurrentUser) -> None:
    await run(request, lambda conn: svc.delete_run(conn, tenant(), period, user.email))


@router.post("/runs/{period}/approve")
async def approve(request: Request, period: str, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.approve_run(conn, ledger, tenant(), period, user.email)))


class PaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: Literal["salaries", "taxes", "sv"]
    paid_on: dt.date


@router.post("/runs/{period}/paid")
async def paid(request: Request, period: str, data: PaidIn, user: CurrentUser) -> Any:
    ledger = request.app.state.ledger
    return money_json(await run(request, lambda conn: svc.mark_paid(conn, ledger, tenant(), period, data.what, data.paid_on,
                                                                     user.email)))


@router.get("/payslips/{payslip_id}/pdf")
async def payslip_pdf(request: Request, payslip_id: UUID, _u: CurrentUser) -> Response:
    async def fn(conn: Any) -> Any:
        slip = await svc.get_payslip(conn, tenant(), payslip_id)
        return slip, await svc.payslip_pdf(conn, tenant(), slip)
    slip, pdf = await run(request, fn)
    name = f"Entgeltabrechnung_{slip['period']}_{slip['name'].replace(' ', '_')}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"',
                                                                 "Cache-Control": "no-store"})


@router.get("/runs/{period}/lsta")
async def lsta(request: Request, period: str, _u: CurrentUser) -> Any:
    from app.services import elster

    async def fn(conn: Any) -> Any:
        r = await svc.get_run(conn, tenant(), period)
        return {"values": svc.lsta_values(r) if r and r["status"] == "approved" else None, "status": elster.status(),
                "history": await elster.history(conn, tenant(), period, kind="lsta")}
    return money_json(await run(request, fn))


@router.get("/runs/{period}/lsta.xml")
async def lsta_download(request: Request, period: str, _u: CurrentUser) -> Response:
    xml, _ = await run(request, lambda conn: svc.lsta_xml(conn, tenant(), period, test=True))
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="LStA_{period}_Test.xml"'})


@router.post("/runs/{period}/lsta/validate")
async def lsta_validate(request: Request, period: str, user: CurrentUser) -> Any:
    return await run(request, lambda conn: svc.lsta_validate(conn, tenant(), period, user.email))


class PinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: str = Field(min_length=4, max_length=64)


@router.post("/runs/{period}/lsta/send")
async def lsta_send(request: Request, period: str, data: PinIn, user: CurrentUser) -> Any:
    return await run(request, lambda conn: svc.lsta_send(conn, tenant(), period, data.pin, user.email))
