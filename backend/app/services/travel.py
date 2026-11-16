"""Reisekosten der Geschäftsführer: Pauschalen berechnen, als Verbindlichkeit buchen, Auszahlung buchen."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.travel import TravelError, calculate
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
COLUMNS = ("t.id, t.number, t.shareholder_id, s.name AS person_name, s.color AS person_color, t.purpose, t.destination, t.start_date, "
           "t.start_time, t.end_date, t.end_time, t.km, t.breakfasts, t.lunches, t.dinners, t.other_costs, t.other_note, t.meals_amount, "
           "t.km_amount, t.total, t.status, t.paid_on, t.created_at")


def _calc(data: dict[str, Any]) -> dict[str, Any]:
    try:
        r = calculate(dt.datetime.combine(data["start_date"], data["start_time"]), dt.datetime.combine(data["end_date"], data["end_time"]),
                      km=D(data.get("km") or 0), breakfasts=data.get("breakfasts", 0), lunches=data.get("lunches", 0),
                      dinners=data.get("dinners", 0), other=D(data.get("other_costs") or 0))
    except TravelError as exc:
        raise ConflictError(str(exc)) from exc
    return {"meals_amount": r.meals, "km_amount": r.km_amount, "total": r.total, "days": r.days, "gross_meals": r.gross_meals,
            "cuts": r.cuts}


async def list_trips(conn: AsyncConnection, tenant_id: UUID, year: int) -> list[dict[str, Any]]:
    rows = await conn.execute(text(
        f"SELECT {COLUMNS} FROM travel_expenses t JOIN shareholders s ON s.id = t.shareholder_id WHERE t.tenant_id = :t "
        "AND extract(year FROM t.start_date) = :y ORDER BY t.start_date DESC, t.number DESC"), {"t": tenant_id, "y": year})
    return [dict(r) for r in rows.mappings()]


async def save_trip(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, trip_id: UUID | None = None) -> UUID:
    if not await conn.scalar(text("SELECT 1 FROM shareholders WHERE tenant_id = :t AND id = :id"),
                             {"t": tenant_id, "id": data["shareholder_id"]}):
        raise NotFoundError("Person nicht gefunden")
    amounts = _calc(data)
    params = {**data, **amounts, "t": tenant_id, "id": trip_id, "a": actor}
    if trip_id is None:
        number = await conn.scalar(text("SELECT COALESCE(max(number), 0) + 1 FROM travel_expenses WHERE tenant_id = :t"), {"t": tenant_id})
        trip_id = await conn.scalar(text(
            "INSERT INTO travel_expenses (tenant_id, number, shareholder_id, purpose, destination, start_date, start_time, end_date, "
            "end_time, "
            "km, breakfasts, lunches, dinners, other_costs, other_note, meals_amount, km_amount, total, created_by) VALUES (:t, :n, "
            ":shareholder_id, :purpose, :destination, :start_date, :start_time, :end_date, :end_time, :km, :breakfasts, :lunches, "
            ":dinners, "
            ":other_costs, :other_note, :meals_amount, :km_amount, :total, :a) RETURNING id"), params | {"n": number})
    else:
        res = await conn.execute(text(
            "UPDATE travel_expenses SET shareholder_id = :shareholder_id, purpose = :purpose, destination = :destination, "
            "start_date = :start_date, start_time = :start_time, end_date = :end_date, end_time = :end_time, km = :km, "
            "breakfasts = :breakfasts, lunches = :lunches, dinners = :dinners, other_costs = :other_costs, other_note = :other_note, "
            "meals_amount = :meals_amount, km_amount = :km_amount, total = :total WHERE tenant_id = :t AND id = :id AND status = 'draft'"),
            params)
        if res.rowcount == 0:
            raise ConflictError("Gebuchte Reisekosten können nicht geändert werden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="travel.saved", entity_type="travel", entity_id=trip_id,
                       after={k: str(v) for k, v in data.items()})
    return trip_id


async def _get(conn: AsyncConnection, tenant_id: UUID, trip_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {COLUMNS} FROM travel_expenses t JOIN shareholders s ON s.id = t.shareholder_id "
                                   "WHERE t.tenant_id = :t AND t.id = :id FOR UPDATE OF t"),
                                   {"t": tenant_id, "id": trip_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Reisekostenabrechnung nicht gefunden")
    return dict(row)


async def book(conn: AsyncConnection, ledger: Any, tenant_id: UUID, trip_id: UUID, actor: str) -> None:
    """Bucht die Erstattung: Verpflegung, Kilometer, Nebenkosten an Verbindlichkeit gegenüber dem Geschäftsführer."""
    from app.services.accounting import Line, acc, chart_of, post

    t = await _get(conn, tenant_id, trip_id)
    if t["status"] != "draft":
        raise ConflictError("Bereits gebucht")
    if D(t["total"]) <= 0:
        raise ConflictError("Keine erstattungsfähigen Beträge")
    chart = await chart_of(conn, tenant_id)
    lines = [Line(acc(chart, "travel_meals"), debit=D(t["meals_amount"]), note="Verpflegungsmehraufwand"),
             Line(acc(chart, "travel_km"), debit=D(t["km_amount"]), note="Kilometerpauschale"),
             Line(acc(chart, "travel_other"), debit=D(t["other_costs"]), note="Nebenkosten"),
             Line(acc(chart, "other_liabilities"), credit=D(t["total"]), note=t["person_name"])]
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=t["end_date"],
                       description=f"Reisekosten RK-{t['number']:04d} {t['person_name']}: {t['destination']}"[:300],
                       document_ref=f"RK-{t['number']:04d}",
                       source_type="travel", source_id=trip_id, lines=lines, method="rule", actor=actor)
    await conn.execute(text("UPDATE travel_expenses SET status = 'booked', journal_entry_id = :e WHERE id = :id"),
                       {"e": entry, "id": trip_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="travel.booked", entity_type="travel", entity_id=trip_id)


async def mark_paid(conn: AsyncConnection, ledger: Any, tenant_id: UUID, trip_id: UUID, paid_on: dt.date, actor: str) -> None:
    import uuid as _uuid

    from app.services.accounting import Line, acc, chart_of, post

    t = await _get(conn, tenant_id, trip_id)
    if t["status"] != "booked":
        raise ConflictError("Erst buchen, dann als ausgezahlt markieren")
    chart = await chart_of(conn, tenant_id)
    total = D(t["total"])
    entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=paid_on,
                       description=f"Erstattung Reisekosten RK-{t['number']:04d} {t['person_name']}"[:300],
                       document_ref=f"RK-{t['number']:04d}", source_type="travel_payment", source_id=_uuid.uuid5(trip_id, "payment"),
                       lines=[Line(acc(chart, "other_liabilities"), debit=total), Line(acc(chart, "bank"), credit=total)],
                       method="rule", actor=actor)
    await conn.execute(text("UPDATE travel_expenses SET status = 'paid', paid_on = :d, payment_entry_id = :e WHERE id = :id"),
                       {"d": paid_on, "e": entry, "id": trip_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="travel.paid", entity_type="travel", entity_id=trip_id)


async def delete_trip(conn: AsyncConnection, tenant_id: UUID, trip_id: UUID, actor: str) -> None:
    res = await conn.execute(text("DELETE FROM travel_expenses WHERE tenant_id = :t AND id = :id AND status = 'draft'"),
                             {"t": tenant_id, "id": trip_id})
    if res.rowcount == 0:
        raise ConflictError("Nur Entwürfe können gelöscht werden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="travel.deleted", entity_type="travel", entity_id=trip_id)


def preview(data: dict[str, Any]) -> dict[str, Any]:
    return _calc(data)
