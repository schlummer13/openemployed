"""Feiertagskalender: gesetzlich (je Bundesland, beliebig weit vorausberechnet), regional, betrieblich; Brückentage, iCal-Export."""

from __future__ import annotations

import datetime as dt
import zlib
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.worktime import CUSTOMARY, STATE_NAMES, bridge_days, easter, holidays, regional_holidays, rule_date
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


async def company_rules(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        "SELECT id, name, rule, month, day, easter_offset, year, half_day FROM company_holidays WHERE tenant_id = :t "
        "ORDER BY rule, month NULLS LAST, day, easter_offset"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


def _company_days(rules: list[dict[str, Any]], years: range) -> dict[dt.date, tuple[str, bool]]:
    out: dict[dt.date, tuple[str, bool]] = {}
    for y in years:
        for r in rules:
            if r["year"] and r["year"] != y:
                continue
            d = rule_date(y, r["rule"], month=r["month"], day=r["day"], easter_offset=r["easter_offset"])
            if d:
                out[d] = (r["name"], bool(r["half_day"]))
    return out


async def company_days(conn: AsyncConnection, tenant_id: UUID, first_year: int, last_year: int) -> dict[dt.date, tuple[str, bool]]:
    return _company_days(await company_rules(conn, tenant_id), range(first_year, last_year + 1))


async def off_map(conn: AsyncConnection, tenant_id: UUID, first_year: int, last_year: int) -> dict[dt.date, D]:
    """Betriebliche freie Tage für die Urlaubszählung: 1 = ganz frei, 0.5 = halb frei."""
    return {d: D("0.5") if half else D(1) for d, (_, half) in (await company_days(conn, tenant_id, first_year, last_year)).items()}


async def merged(conn: AsyncConnection, tenant_id: UUID, state: str, first: dt.date, last: dt.date) -> dict[str, Any]:
    """Für Kalender: Datum → Name (gesetzlich + betrieblich ganztägig) und Liste halber Tage."""
    out: dict[str, str] = {}
    half: dict[str, str] = {}
    for y in range(first.year, last.year + 1):
        out |= {d.isoformat(): n for d, n in holidays(y, state).items() if first <= d <= last}
    for d, (n, h) in (await company_days(conn, tenant_id, first.year, last.year)).items():
        if first <= d <= last and d.isoformat() not in out:
            (half if h else out)[d.isoformat()] = n
    return {"holidays": out, "half_days": half}


async def calendar(conn: AsyncConnection, tenant_id: UUID, year: int, state: str) -> dict[str, Any]:
    if state not in STATE_NAMES:
        raise ConflictError("Unbekanntes Bundesland")
    rules = await company_rules(conn, tenant_id)
    legal = holidays(year, state)
    company = _company_days(rules, range(year, year + 1))
    items: list[dict[str, Any]] = []
    for d, n in legal.items():
        items.append({"date": d, "name": n, "kind": "statutory", "half_day": False})
    for d, n in regional_holidays(year, state).items():
        if d not in legal:
            items.append({"date": d, "name": n, "kind": "regional", "half_day": False})
    for d, (n, h) in company.items():
        if d not in legal:
            items.append({"date": d, "name": n, "kind": "company", "half_day": h})
    for i in items:
        i["weekday"] = WEEKDAYS[i["date"].weekday()]
        i["on_weekend"] = i["date"].weekday() >= 5
    items.sort(key=lambda i: (i["date"], i["kind"] != "statutory"))
    off = set(legal) | {d for d, (_, h) in company.items() if not h}
    per_month: list[D] = []
    for m in range(1, 13):
        day = dt.date(year, m, 1)
        count = D(0)
        while day.month == m:
            if day.weekday() < 5 and day not in off:
                count += D("0.5") if day in company else D(1)
            day += dt.timedelta(days=1)
        per_month.append(count)
    months = [{"month": i + 1, "workdays": c} for i, c in enumerate(per_month)]
    taken = {(r["rule"], r["month"], r["day"], r["easter_offset"]) for r in rules}
    suggestions = []
    for c in CUSTOMARY:
        states = c.get("states")
        key = (c["rule"], c.get("month"), c.get("day"), c.get("easter_offset"))
        if (isinstance(states, tuple) and state not in states) or key in taken:
            continue
        when = rule_date(year, str(c["rule"]), month=_int(c.get("month")), day=_int(c.get("day")),
                         easter_offset=_int(c.get("easter_offset")))
        suggestions.append({k: v for k, v in c.items() if k != "states"} | {"date": when})
    return {"year": year, "state": state, "state_name": STATE_NAMES[state], "states": STATE_NAMES, "items": items, "easter": easter(year),
            "statutory_count": len(legal), "weekday_count": sum(1 for d in legal if d.weekday() < 5),
            "bridges": bridge_days(year, off), "months": months, "workdays": sum(per_month, D(0)),
            "rules": rules, "suggestions": suggestions}


async def add_rule(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str) -> UUID:
    if data["rule"] == "fixed" and rule_date(2024, "fixed", month=data.get("month"), day=data.get("day")) is None:
        raise ConflictError("Ungültiges Datum")
    rid: UUID = await conn.scalar(text(
        "INSERT INTO company_holidays (tenant_id, name, rule, month, day, easter_offset, year, half_day, created_by) "
        "VALUES (:t, :n, :r, :m, :d, :e, :y, :h, :a) RETURNING id"),
        {"t": tenant_id, "n": data["name"], "r": data["rule"], "m": data.get("month"), "d": data.get("day"),
         "e": data.get("easter_offset"), "y": data.get("year"), "h": bool(data.get("half_day")), "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="company_holiday.created", entity_type="company_holiday",
                       entity_id=rid, after={k: v for k, v in data.items() if v is not None})
    return rid


async def delete_rule(conn: AsyncConnection, tenant_id: UUID, rule_id: UUID, actor: str) -> None:
    row = await conn.scalar(text("DELETE FROM company_holidays WHERE tenant_id = :t AND id = :id RETURNING name"),
                            {"t": tenant_id, "id": rule_id})
    if row is None:
        raise NotFoundError("Freier Tag nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="company_holiday.deleted", entity_type="company_holiday",
                       entity_id=rule_id, before={"name": row})


def _int(v: object) -> int | None:
    return v if isinstance(v, int) else None


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


async def ics(conn: AsyncConnection, tenant_id: UUID, state: str, first_year: int, last_year: int) -> str:
    """iCalendar-Datei (RFC 5545) mit ganztägigen Terminen – zum Import in Outlook, Google, Apple Kalender."""
    if state not in STATE_NAMES:
        raise ConflictError("Unbekanntes Bundesland")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OpenEmployed//Feiertage//DE", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:Feiertage {STATE_NAMES[state]}", "X-WR-TIMEZONE:Europe/Berlin"]
    company = await company_days(conn, tenant_id, first_year, last_year)
    events: list[tuple[dt.date, str, str]] = []
    for y in range(first_year, last_year + 1):
        events += [(d, n, "Feiertag") for d, n in holidays(y, state).items()]
    events += [(d, n + (" (halber Tag)" if h else ""), "Betrieblich frei") for d, (n, h) in company.items()]
    for d, n, cat in sorted(events):
        lines += ["BEGIN:VEVENT", f"UID:{d:%Y%m%d}-{zlib.crc32(n.encode()):08x}-{state}@openemployed", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}", f"SUMMARY:{_esc(n)}",
                  f"CATEGORIES:{cat}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
