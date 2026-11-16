"""Arbeitszeitkonto: Soll/Ist je Tag, Überstunden-Saldo, Buchungen (Übertrag, Auszahlung, Verfall, Korrektur).

Soll je Arbeitstag = Wochenstunden / Anzahl Arbeitstage. Kein Soll an Feiertagen und betrieblich freien Tagen (halber Tag = halbes Soll).
Urlaub, Krankheit, Sonderurlaub und Berufsschule gelten als erfüllt (Soll wird gutgeschrieben); unbezahlter Urlaub setzt das Soll auf 0;
Überstundenabbau lässt das Soll stehen und mindert damit den Saldo. Ist = Netto-Arbeitszeit aus der Stempeluhr (nach Pause).
Auszahlungen erzeugen eine Lohnposition „Überstundenvergütung“ im gewählten Abrechnungsmonat.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.worktime import holidays
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
BERLIN = ZoneInfo("Europe/Berlin")
CREDITED = ("vacation", "sick", "special", "school")
WEEKS_PER_MONTH = D(13) / D(3)


def daily_target(emp: dict[str, Any]) -> int:
    wd = list(emp.get("work_days") or [0, 1, 2, 3, 4])
    return int((D(emp.get("weekly_hours") or 0) * 60 / max(1, len(wd))).to_integral_value(rounding=ROUND_HALF_UP))


def hourly_value(emp: dict[str, Any]) -> D:
    """Stundenwert für die Auszahlung: Stundenlohn bzw. Monatsgehalt / (Wochenstunden × 13/3)."""
    if emp["salary_type"] == "hourly":
        return D(emp["hourly_rate"])
    hours = D(emp.get("weekly_hours") or 0) * WEEKS_PER_MONTH
    if not hours:
        raise ConflictError("Wochenstunden fehlen – Stundenwert kann nicht berechnet werden")
    return (D(emp["gross_month"]) / hours).quantize(D("0.01"), rounding=ROUND_HALF_UP)


async def _employee(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(
        "SELECT id, first_name, last_name, entry_date, exit_date, weekly_hours, work_days, state, salary_type, hourly_rate, gross_month, "
        "time_account_start, overtime_rule, overtime_cap_hours FROM employees WHERE tenant_id = :t AND id = :id"),
        {"t": tenant_id, "id": employee_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Mitarbeiter nicht gefunden")
    return dict(row)


async def _start(conn: AsyncConnection, emp: dict[str, Any]) -> dt.date | None:
    if emp["time_account_start"]:
        start: dt.date = max(emp["time_account_start"], emp["entry_date"])
        return start
    first = await conn.scalar(text("SELECT min(started_at) FROM time_clock WHERE employee_id = :e"), {"e": emp["id"]})
    if first is None:
        return None
    begin: dt.date = max(first.astimezone(BERLIN).date(), emp["entry_date"])
    return begin


async def compute(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, until: dt.date | None = None) -> dict[str, Any]:
    from app.services.holidays import off_map

    emp = await _employee(conn, tenant_id, employee_id)
    today = dt.datetime.now(dt.UTC).astimezone(BERLIN).date()
    end = min(until or today, emp["exit_date"] or today)
    start = await _start(conn, emp)
    bookings = [dict(r) for r in (await conn.execute(text(
        "SELECT id, booked_on, minutes, kind, reason, payroll_period, created_by, created_at FROM time_account_bookings "
        "WHERE employee_id = :e ORDER BY booked_on, created_at"), {"e": employee_id})).mappings()]
    base = {"employee": {"id": emp["id"], "name": f"{emp['first_name']} {emp['last_name']}", "rule": emp["overtime_rule"],
                         "cap_hours": emp["overtime_cap_hours"], "daily_target": daily_target(emp),
                         "work_days": list(emp["work_days"] or []), "salary_type": emp["salary_type"]},
            "start": start, "end": end, "bookings": bookings}
    if start is None or start > end:
        total = sum(b["minutes"] for b in bookings)
        return base | {"months": [], "balance": total, "target": 0, "actual": 0, "today": None}
    clock = {r[0]: int(r[1]) for r in (await conn.execute(text(
        "SELECT (started_at AT TIME ZONE 'Europe/Berlin')::date AS d, sum(coalesce(worked_minutes, 0)) FROM time_clock "
        "WHERE employee_id = :e AND ended_at IS NOT NULL AND started_at >= :s GROUP BY 1"),
        {"e": employee_id, "s": dt.datetime.combine(start, dt.time(), BERLIN)})).all()}
    absences = (await conn.execute(text(
        "SELECT kind, start_date, end_date, half_start, half_end FROM absences WHERE employee_id = :e AND status = 'approved' "
        "AND end_date >= :s AND start_date <= :x"), {"e": employee_id, "s": start, "x": end})).mappings().all()
    hol: set[dt.date] = set()
    for y in range(start.year, end.year + 1):
        hol |= set(holidays(y, emp["state"]))
    company = await off_map(conn, tenant_id, start.year, end.year)
    wd = set(emp["work_days"] or [0, 1, 2, 3, 4])
    per_day = daily_target(emp)
    months: dict[str, dict[str, Any]] = {}
    day = start
    while day <= end:
        target = 0
        if day.weekday() in wd and day not in hol:
            target = int(per_day * (1 - company.get(day, D(0))))
        credited = 0
        for a in absences:
            if a["start_date"] <= day <= a["end_date"] and target:
                share = D("0.5") if (a["half_start"] and day == a["start_date"]) or (a["half_end"] and day == a["end_date"]) else D(1)
                part = int(target * share)
                if a["kind"] in CREDITED:
                    credited += part
                elif a["kind"] in ("unpaid", "parental", "maternity"):
                    target -= part
        actual = clock.get(day, 0)
        m = months.setdefault(f"{day:%Y-%m}", {"month": f"{day:%Y-%m}", "target": 0, "actual": 0, "credited": 0, "bookings": 0})
        m["target"] += target
        m["actual"] += actual
        m["credited"] += min(credited, target)
        day += dt.timedelta(days=1)
    for b in bookings:
        key = f"{b['booked_on']:%Y-%m}"
        if key in months:
            months[key]["bookings"] += b["minutes"]
    balance = sum(b["minutes"] for b in bookings if b["booked_on"] < start)
    out = []
    for key in sorted(months):
        m = months[key]
        m["diff"] = m["actual"] + m["credited"] - m["target"]
        balance += m["diff"] + m["bookings"]
        m["balance"] = balance
        out.append(m)
    balance += sum(b["minutes"] for b in bookings if b["booked_on"] > end)
    cap = emp["overtime_cap_hours"]
    return base | {"months": out, "balance": balance, "target": sum(m["target"] for m in out), "actual": sum(m["actual"] for m in out),
                   "over_cap": bool(cap is not None and balance > int(D(cap) * 60)), "hourly_value": str(hourly_value(emp))
                   if emp["salary_type"] == "hourly" or emp.get("weekly_hours") else None}


async def book(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID, data: dict[str, Any], actor: str) -> UUID:
    emp = await _employee(conn, tenant_id, employee_id)
    minutes = int(data["minutes"])
    kind = data["kind"]
    if kind == "payout":
        if minutes <= 0:
            raise ConflictError("Auszahlung in Minuten angeben (positiv)")
        if not data.get("payroll_period"):
            raise ConflictError("Abrechnungsmonat für die Auszahlung angeben")
        if await conn.scalar(text("SELECT 1 FROM payroll_runs WHERE tenant_id = :t AND period = :p AND status = 'approved'"),
                             {"t": tenant_id, "p": data["payroll_period"]}):
            raise ConflictError("Der Lohnlauf dieses Monats ist bereits freigegeben")
        minutes = -minutes
    bid: UUID = await conn.scalar(text(
        "INSERT INTO time_account_bookings (tenant_id, employee_id, booked_on, minutes, kind, reason, payroll_period, created_by) "
        "VALUES (:t, :e, :d, :m, :k, :r, :p, :a) RETURNING id"),
        {"t": tenant_id, "e": employee_id, "d": data["booked_on"], "m": minutes, "k": kind, "r": data["reason"],
         "p": data.get("payroll_period"), "a": actor})
    if kind == "payout":
        hours = (D(-minutes) / 60).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        rate = hourly_value(emp) * (1 + D(data.get("surcharge_percent") or 0) / 100)
        rate = rate.quantize(D("0.01"), rounding=ROUND_HALF_UP)
        await conn.execute(text(
            "INSERT INTO payroll_items (tenant_id, employee_id, period, kind, label, quantity, rate, amount, source, created_by) "
            "VALUES (:t, :e, :p, 'overtime', :l, :q, :r, :amt, :s, :a)"),
            {"t": tenant_id, "e": employee_id, "p": data["payroll_period"], "l": "Überstundenvergütung", "q": hours, "r": rate,
             "amt": (hours * rate).quantize(D("0.01"), rounding=ROUND_HALF_UP), "s": f"time_account:{bid}", "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"time_account.{kind}", entity_type="employee",
                       entity_id=employee_id, after={"minutes": minutes, "reason": data["reason"], "period": data.get("payroll_period")})
    return bid


async def delete_booking(conn: AsyncConnection, tenant_id: UUID, booking_id: UUID, actor: str) -> None:
    row = (await conn.execute(text("SELECT employee_id, kind, payroll_period FROM time_account_bookings WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": booking_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Buchung nicht gefunden")
    if row["payroll_period"] and await conn.scalar(text(
            "SELECT 1 FROM payroll_runs WHERE tenant_id = :t AND period = :p AND status = 'approved'"),
            {"t": tenant_id, "p": row["payroll_period"]}):
        raise ConflictError("Bereits abgerechnet – Korrektur als Gegenbuchung erfassen")
    await conn.execute(text("DELETE FROM payroll_items WHERE tenant_id = :t AND source = :s"),
                       {"t": tenant_id, "s": f"time_account:{booking_id}"})
    await conn.execute(text("DELETE FROM time_account_bookings WHERE id = :id"), {"id": booking_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="time_account.deleted", entity_type="employee",
                       entity_id=row["employee_id"], before={"booking": str(booking_id), "kind": row["kind"]})


async def overview(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    """Salden aller aktiven Mitarbeiter (für die Team-Übersicht)."""
    ids = [r[0] for r in (await conn.execute(text(
        "SELECT id FROM employees WHERE tenant_id = :t AND archived_at IS NULL ORDER BY last_name, first_name"), {"t": tenant_id})).all()]
    out = []
    for i in ids:
        c = await compute(conn, tenant_id, i)
        out.append({"employee_id": i, "name": c["employee"]["name"], "balance": c["balance"], "over_cap": c.get("over_cap", False),
                    "rule": c["employee"]["rule"], "start": c["start"]})
    return out
