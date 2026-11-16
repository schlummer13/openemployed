"""Stempeluhr und Abwesenheiten (Urlaub, Krankheit …) mit Genehmigung, Team-Kalender und Urlaubskonto."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.worktime import entitlement, is_minor, required_break, workdays
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
BERLIN = ZoneInfo("Europe/Berlin")
KINDS = {"vacation": "Urlaub", "sick": "Krankheit", "special": "Sonderurlaub", "unpaid": "Unbezahlt", "overtime": "Überstundenabbau",
         "school": "Berufsschule", "parental": "Elternzeit", "maternity": "Mutterschutz"}
EMP = ("id, number, first_name, last_name, email, birth_date, entry_date, exit_date, employment_type, weekly_hours, vacation_days, "
       "vacation_carryover, work_days, state")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def employee_by_email(conn: AsyncConnection, tenant_id: UUID, email: str) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {EMP} FROM employees WHERE tenant_id = :t AND lower(email) = :e AND archived_at IS NULL"),
                              {"t": tenant_id, "e": email.lower()})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Für dein Konto ist kein Mitarbeiterprofil hinterlegt (gleiche E-Mail-Adresse unter Mitarbeiter & Lohn)")
    return dict(row)


async def employee(conn: AsyncConnection, tenant_id: UUID, employee_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {EMP} FROM employees WHERE tenant_id = :t AND id = :id"),
                              {"t": tenant_id, "id": employee_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Mitarbeiter nicht gefunden")
    return dict(row)


# ───────────────────────── Stempeluhr ─────────────────────────

def _minutes(start: dt.datetime, end: dt.datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


async def clock_status(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    today = now.astimezone(BERLIN).date()
    week_start = today - dt.timedelta(days=today.weekday())
    open_ = (await conn.execute(text("SELECT id, started_at FROM time_clock WHERE employee_id = :e AND ended_at IS NULL"),
                                {"e": emp["id"]})).mappings().one_or_none()
    rows = [dict(r) for r in (await conn.execute(text(
        "SELECT id, started_at, ended_at, break_minutes, worked_minutes, source, note FROM time_clock WHERE employee_id = :e "
        "AND started_at >= :s ORDER BY started_at DESC"),
        {"e": emp["id"], "s": dt.datetime.combine(week_start - dt.timedelta(days=21), dt.time(), BERLIN)})).mappings()]
    minor = is_minor(emp.get("birth_date"), today)

    def day_of(r: dict[str, Any]) -> dt.date:
        d: dt.date = r["started_at"].astimezone(BERLIN).date()
        return d

    def net(day: dt.date) -> int:
        """Netto-Arbeitszeit eines Tages: Pausen auf die Tagessumme (mehrere Stempelungen zählen zusammen)."""
        presence = sum(_minutes(r["started_at"], r["ended_at"] or now) for r in rows if day_of(r) == day)
        return presence - required_break(presence, minor=minor)

    today_presence = sum(_minutes(r["started_at"], r["ended_at"] or now) for r in rows if day_of(r) == today)
    week_net = sum(net(week_start + dt.timedelta(days=i)) for i in range(7))
    target_week = int(D(emp["weekly_hours"] or 0) * 60)  # Soll je Woche aus dem Vertrag
    days = sorted({day_of(r) for r in rows}, reverse=True)[:14]
    return {
        "employee": {"id": emp["id"], "name": f"{emp['first_name']} {emp['last_name']}", "weekly_hours": emp["weekly_hours"],
                     "work_days": list(_work_days(emp))},
        "open": {"id": open_["id"], "started_at": open_["started_at"]} if open_ else None,
        "server_time": now, "minor": minor,
        "today": {"presence": today_presence, "break": required_break(today_presence, minor=minor), "net": net(today)},
        "week": {"net": week_net, "target": target_week, "diff": week_net - target_week if target_week else None},
        "days": [{"date": d, "net": net(d), "entries": [r for r in rows if day_of(r) == d]} for d in days],
        "warning": "Mehr als 10 Stunden Arbeitszeit heute (§ 3 ArbZG)" if net(today) > 600 else None,
    }


async def check_in(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], source: str, actor: str) -> None:
    if emp.get("exit_date") and emp["exit_date"] < _now().astimezone(BERLIN).date():
        raise ConflictError("Beschäftigung beendet")
    if await conn.scalar(text("SELECT 1 FROM time_clock WHERE employee_id = :e AND ended_at IS NULL"), {"e": emp["id"]}):
        raise ConflictError("Du bist bereits eingestempelt")
    await conn.execute(text("INSERT INTO time_clock (tenant_id, employee_id, started_at, source) VALUES (:t, :e, now(), :s)"),
                       {"t": tenant_id, "e": emp["id"], "s": source})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="timeclock.in", entity_type="employee", entity_id=emp["id"])


async def check_out(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], actor: str, note: str | None = None) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT id, started_at FROM time_clock WHERE employee_id = :e AND ended_at IS NULL FOR UPDATE"),
                              {"e": emp["id"]})).mappings().one_or_none()
    if row is None:
        raise ConflictError("Du bist nicht eingestempelt")
    await _close(conn, tenant_id, emp, row["id"], row["started_at"], _now(), note)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="timeclock.out", entity_type="employee", entity_id=emp["id"])
    return {"id": row["id"]}


async def _close(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], entry_id: UUID, start: dt.datetime, end: dt.datetime,
                 note: str | None) -> None:
    """Beendet einen Eintrag; Pause wird auf die Tagessumme berechnet und dem letzten Eintrag des Tages zugeordnet."""
    day = start.astimezone(BERLIN).date()
    lo = dt.datetime.combine(day, dt.time(), BERLIN)
    others = (await conn.execute(text(
        "SELECT started_at, ended_at, break_minutes FROM time_clock WHERE employee_id = :e AND id <> :id AND started_at >= :lo "
        "AND started_at < :hi AND ended_at IS NOT NULL"),
        {"e": emp["id"], "id": entry_id, "lo": lo, "hi": lo + dt.timedelta(days=1)})).all()
    presence_before = sum(_minutes(o[0], o[1]) for o in others)
    breaks_before = sum(o[2] for o in others)
    presence = _minutes(start, end)
    minor = is_minor(emp.get("birth_date"), day)
    brk = max(0, required_break(presence_before + presence, minor=minor) - breaks_before)
    await conn.execute(text("UPDATE time_clock SET ended_at = :end, break_minutes = :b, worked_minutes = :w, note = COALESCE(:n, note) "
                            "WHERE id = :id"), {"end": end, "b": brk, "w": max(0, presence - brk), "n": note, "id": entry_id})


async def entries(conn: AsyncConnection, tenant_id: UUID, month: str, employee_id: UUID | None = None) -> list[dict[str, Any]]:
    y, m = (int(x) for x in month.split("-"))
    lo = dt.datetime(y, m, 1, tzinfo=BERLIN)
    hi = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=BERLIN)
    rows = await conn.execute(text(
        "SELECT c.id, c.employee_id, e.first_name || ' ' || e.last_name AS name, c.started_at, c.ended_at, c.break_minutes, "
        "c.worked_minutes, c.source, c.note, c.corrected_by, c.correction_reason FROM time_clock c JOIN employees e ON e.id = "
        "c.employee_id "
        "WHERE c.tenant_id = :t AND c.started_at >= :lo AND c.started_at < :hi AND (CAST(:e AS uuid) IS NULL OR c.employee_id = CAST(:e "
        "AS uuid)) "
        "ORDER BY name, c.started_at"), {"t": tenant_id, "lo": lo, "hi": hi, "e": employee_id})
    return [dict(r) for r in rows.mappings()]


async def correct(conn: AsyncConnection, tenant_id: UUID, entry_id: UUID, start: dt.datetime, end: dt.datetime, reason: str,
                  actor: str) -> None:
    row = (await conn.execute(text("SELECT employee_id, started_at, ended_at FROM time_clock WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                              {"t": tenant_id, "id": entry_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Eintrag nicht gefunden")
    if end <= start or end - start > dt.timedelta(hours=16):
        raise ConflictError("Zeitraum ungültig (Ende nach Beginn, höchstens 16 Stunden)")
    emp = await employee(conn, tenant_id, row["employee_id"])
    await conn.execute(text("UPDATE time_clock SET started_at = :s, corrected_by = :a, correction_reason = :r, source = 'manual' WHERE id "
                            "= :id"),
                       {"s": start, "a": actor, "r": reason, "id": entry_id})
    await _close(conn, tenant_id, emp, entry_id, start, end, None)
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="timeclock.corrected", entity_type="time_clock", entity_id=entry_id,
                       before={"start": str(row["started_at"]), "end": str(row["ended_at"])}, after={"start": str(start), "end": str(end),
                                                                                                   "reason": reason})


async def month_hours(conn: AsyncConnection, tenant_id: UUID, period: str) -> dict[UUID, D]:
    """Gestempelte Netto-Stunden je Mitarbeiter im Monat (für Stundenlöhne im Lohnlauf)."""
    out: dict[UUID, D] = {}
    for r in await entries(conn, tenant_id, period):
        if r["worked_minutes"] is not None:
            out[r["employee_id"]] = out.get(r["employee_id"], D(0)) + D(r["worked_minutes"]) / 60
    return {k: v.quantize(D("0.01")) for k, v in out.items()}


# ───────────────────────── Abwesenheiten und Urlaubskonto ─────────────────────────

def _work_days(emp: dict[str, Any]) -> tuple[int, ...]:
    return tuple(emp.get("work_days") or (0, 1, 2, 3, 4))


async def balance(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], year: int) -> dict[str, Any]:
    annual = D(emp["vacation_days"] or 0)
    wd = _work_days(emp)
    ent = entitlement(year, annual, entry=emp["entry_date"], exit_=emp.get("exit_date"), work_days_per_week=len(wd))
    rows = (await conn.execute(text(
        "SELECT start_date, end_date, days, status FROM absences WHERE employee_id = :e AND kind = 'vacation' AND status IN ('approved', "
        "'requested') "
        "AND start_date <= :ye AND end_date >= :ys"),
        {"e": emp["id"], "ys": dt.date(year, 1, 1), "ye": dt.date(year, 12, 31)})).mappings().all()
    today = _now().astimezone(BERLIN).date()
    taken = sum((D(r["days"]) for r in rows if r["status"] == "approved" and r["end_date"] < today), D(0))
    planned = sum((D(r["days"]) for r in rows if r["status"] == "approved" and r["end_date"] >= today), D(0))
    requested = sum((D(r["days"]) for r in rows if r["status"] == "requested"), D(0))
    carry = D(emp.get("vacation_carryover") or 0) if year == today.year else D(0)
    carry_expired = carry and today > dt.date(year, 3, 31)
    # Übertrag zuerst verbrauchen (bis 31.3.)
    used_before_april = sum((D(r["days"]) for r in rows if r["status"] == "approved" and r["start_date"] <= dt.date(year, 3, 31)), D(0))
    carry_effective = min(carry, used_before_april) if carry_expired else carry
    total = D(str(ent["days"])) + carry_effective
    return {"year": year, "annual": annual, "entitlement": ent["days"], "rule": ent["rule"], "carryover": carry,
            "carryover_expires": dt.date(year, 3, 31) if carry else None, "carryover_effective": carry_effective, "total": total,
            "taken": taken, "planned": planned, "requested": requested, "remaining": total - taken - planned,
            "available": total - taken - planned - requested, "work_days": list(wd), "waiting_until": ent.get("waiting_until")}


async def list_absences(conn: AsyncConnection, tenant_id: UUID, *, employee_id: UUID | None = None, start: dt.date | None = None,
                        end: dt.date | None = None, status: str | None = None) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT a.id, a.employee_id, e.first_name || ' ' || e.last_name AS name, a.kind, a.start_date, a.end_date, a.half_start, "
        "a.half_end, "
        "a.days, a.status, a.note, a.decision_note, a.requested_by, a.decided_by, a.decided_at, a.created_at FROM absences a "
        "JOIN employees e ON e.id = a.employee_id WHERE a.tenant_id = :t AND (CAST(:e AS uuid) IS NULL OR a.employee_id = CAST(:e AS "
        "uuid)) "
        "AND (CAST(:s AS date) IS NULL OR a.end_date >= CAST(:s AS date)) AND (CAST(:x AS date) IS NULL OR a.start_date <= CAST(:x AS "
        "date)) "
        "AND (CAST(:st AS text) IS NULL OR a.status = CAST(:st AS text)) ORDER BY a.start_date"),
        {"t": tenant_id, "e": employee_id, "s": start, "x": end, "st": status})
    return [dict(r) | {"kind_label": KINDS.get(r["kind"], r["kind"])} for r in rows.mappings()]


async def request(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], data: dict[str, Any], actor: str, *,
                  approved: bool = False) -> UUID:
    if data["kind"] not in KINDS:
        raise ConflictError("Unbekannte Art")
    start, end = data["start_date"], data["end_date"]
    if end < start:
        raise ConflictError("Ende liegt vor dem Beginn")
    if (end - start).days > 366:
        raise ConflictError("Höchstens ein Jahr am Stück")
    if start == end and data.get("half_start") and data.get("half_end"):
        data = {**data, "half_end": False}
    from app.services.holidays import off_map

    days = workdays(start, end, state=emp["state"], work_days=_work_days(emp), half_start=bool(data.get("half_start")),
                    half_end=bool(data.get("half_end")), company=await off_map(conn, tenant_id, start.year, end.year))
    if days == 0 and data["kind"] in ("vacation", "overtime", "special"):
        raise ConflictError("Im Zeitraum liegen keine Arbeitstage (Wochenende/Feiertag)")
    overlap = await conn.scalar(text("SELECT 1 FROM absences WHERE employee_id = :e AND status IN ('requested', 'approved') "
                                     "AND start_date <= :x AND end_date >= :s"), {"e": emp["id"], "s": start, "x": end})
    if overlap:
        raise ConflictError("Für diesen Zeitraum gibt es bereits einen Antrag")
    if data["kind"] == "vacation" and not approved:
        bal = await balance(conn, tenant_id, emp, start.year)
        if days > bal["available"]:
            raise ConflictError(f"Nicht genug Urlaub: verfügbar {bal['available']} Tage, beantragt {days}")
    status = "approved" if approved or data["kind"] == "sick" else "requested"
    aid: UUID = await conn.scalar(text(
        "INSERT INTO absences (tenant_id, employee_id, kind, start_date, end_date, half_start, half_end, days, status, note, requested_by, "
        "decided_by, decided_at) VALUES (:t, :e, :k, :s, :x, :hs, :he, :d, :st, :n, :a, CASE WHEN :st = 'approved' THEN :a END, "
        "CASE WHEN :st = 'approved' THEN now() END) RETURNING id"),
        {"t": tenant_id, "e": emp["id"], "k": data["kind"], "s": start, "x": end, "hs": bool(data.get("half_start")),
         "he": bool(data.get("half_end")), "d": days, "st": status, "n": data.get("note"), "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="absence.requested", entity_type="absence", entity_id=aid,
                       after={"employee": f"{emp['first_name']} {emp['last_name']}", "kind": data["kind"], "start": str(start),
                              "end": str(end), "days": str(days), "status": status})
    return aid


async def decide(conn: AsyncConnection, tenant_id: UUID, absence_id: UUID, approve: bool, note: str | None, actor: str) -> dict[str, Any]:
    row = (await conn.execute(text("SELECT * FROM absences WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                              {"t": tenant_id, "id": absence_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Antrag nicht gefunden")
    if row["status"] != "requested":
        raise ConflictError("Der Antrag ist bereits entschieden")
    status = "approved" if approve else "rejected"
    await conn.execute(text("UPDATE absences SET status = :s, decision_note = :n, decided_by = :a, decided_at = now() WHERE id = :id"),
                       {"s": status, "n": note, "a": actor, "id": absence_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=f"absence.{status}", entity_type="absence", entity_id=absence_id,
                       after={"note": note})
    emp = await employee(conn, tenant_id, row["employee_id"])
    return {"status": status, "employee": emp, "absence": dict(row)}


async def cancel(conn: AsyncConnection, tenant_id: UUID, emp: dict[str, Any], absence_id: UUID, actor: str) -> None:
    row = (await conn.execute(text("SELECT status, start_date FROM absences WHERE tenant_id = :t AND id = :id AND employee_id = :e"),
                              {"t": tenant_id, "id": absence_id, "e": emp["id"]})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Antrag nicht gefunden")
    if row["status"] not in ("requested", "approved"):
        raise ConflictError("Antrag ist nicht mehr aktiv")
    if row["status"] == "approved" and row["start_date"] <= _now().astimezone(BERLIN).date():
        raise ConflictError("Begonnener Urlaub kann nur von der Personalverantwortung storniert werden")
    await conn.execute(text("UPDATE absences SET status = 'cancelled' WHERE id = :id"), {"id": absence_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="absence.cancelled", entity_type="absence", entity_id=absence_id)


async def team_calendar(conn: AsyncConnection, tenant_id: UUID, month: str) -> dict[str, Any]:
    y, m = (int(x) for x in month.split("-"))
    first = dt.date(y, m, 1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    emps = [dict(r) for r in (await conn.execute(text(
        f"SELECT {EMP} FROM employees WHERE tenant_id = :t AND archived_at IS NULL AND entry_date <= :l "
        "AND (exit_date IS NULL OR exit_date >= :f) ORDER BY last_name, first_name"), {"t": tenant_id, "f": first, "l": last})).mappings()]
    from app.services.holidays import merged

    states = {e["state"] for e in emps} or {"NW"}
    cal = {s: await merged(conn, tenant_id, s, first, last) for s in states}
    hol = {s: c["holidays"] for s, c in cal.items()}
    half = {s: c["half_days"] for s, c in cal.items()}
    abs_ = await list_absences(conn, tenant_id, start=first, end=last)
    pending = await list_absences(conn, tenant_id, status="requested")
    rows = []
    for e in emps:
        bal = await balance(conn, tenant_id, e, y)
        rows.append({"id": e["id"], "name": f"{e['first_name']} {e['last_name']}", "state": e["state"], "work_days": list(_work_days(e)),
                     "remaining": bal["remaining"], "total": bal["total"],
                     "absences": [a for a in abs_ if a["employee_id"] == e["id"] and a["status"] in ("approved", "requested")]})
    return {"month": month, "first": first, "last": last, "holidays": hol, "half_days": half, "employees": rows, "pending": pending,
            "kinds": KINDS}
