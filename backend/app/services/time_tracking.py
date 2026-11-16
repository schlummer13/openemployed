"""Zeiterfassung je Projekt und Person; Abrechnung offener Stunden als Rechnungsentwurf an den Projektkunden."""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal


async def list_entries(conn: AsyncConnection, tenant_id: UUID, *, project_id: UUID | None = None, start: dt.date | None = None,
                       end: dt.date | None = None, open_only: bool = False) -> dict[str, Any]:
    rows = (await conn.execute(text(
        "SELECT e.id, e.project_id, p.name AS project_name, p.color AS project_color, p.hourly_rate, p.customer_id, e.task_id, "
        "t.title AS task_title, e.shareholder_id, s.name AS person_name, s.color AS person_color, e.work_date, e.minutes, e.description, "
        "e.billable, e.invoice_id, i.number AS invoice_number, i.status AS invoice_status FROM time_entries e "
        "JOIN projects p ON p.id = e.project_id LEFT JOIN tasks t ON t.id = e.task_id LEFT JOIN shareholders s ON s.id = e.shareholder_id "
        "LEFT JOIN invoices i ON i.id = e.invoice_id WHERE e.tenant_id = :t AND p.archived_at IS NULL "
        "AND (CAST(:p AS uuid) IS NULL OR e.project_id = CAST(:p AS uuid)) AND (CAST(:s AS date) IS NULL OR e.work_date >= CAST(:s AS "
        "date)) "
        "AND (CAST(:e AS date) IS NULL OR e.work_date <= CAST(:e AS date)) AND (NOT :o OR (e.billable AND e.invoice_id IS NULL)) "
        "ORDER BY e.work_date DESC, e.created_at DESC LIMIT 2000"),
        {"t": tenant_id, "p": project_id, "s": start, "e": end, "o": open_only})).mappings().all()
    entries = [dict(r) for r in rows]
    total = sum(e["minutes"] for e in entries)
    open_ = [e for e in entries if e["billable"] and e["invoice_id"] is None]
    return {"entries": entries, "total_minutes": total, "open_minutes": sum(e["minutes"] for e in open_),
            "open_value": sum((D(e["minutes"]) / 60 * D(e["hourly_rate"] or 0) for e in open_), D(0)).quantize(D("0.01"))}


async def save_entry(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, entry_id: UUID | None = None) -> UUID:
    if not await conn.scalar(text("SELECT 1 FROM projects WHERE tenant_id = :t AND id = :p AND archived_at IS NULL"),
                             {"t": tenant_id, "p": data["project_id"]}):
        raise NotFoundError("Projekt nicht gefunden")
    if data.get("task_id") and not await conn.scalar(text("SELECT 1 FROM tasks WHERE id = :id AND project_id = :p"),
                                                     {"id": data["task_id"], "p": data["project_id"]}):
        raise ConflictError("Aufgabe gehört nicht zu diesem Projekt")
    params = {**data, "t": tenant_id, "id": entry_id, "a": actor}
    if entry_id is None:
        entry_id = await conn.scalar(text(
            "INSERT INTO time_entries (tenant_id, project_id, task_id, shareholder_id, work_date, minutes, description, billable, "
            "created_by) "
            "VALUES (:t, :project_id, :task_id, :shareholder_id, :work_date, :minutes, :description, :billable, :a) RETURNING id"), params)
    else:
        res = await conn.execute(text(
            "UPDATE time_entries SET project_id = :project_id, task_id = :task_id, shareholder_id = :shareholder_id, work_date = "
            ":work_date, "
            "minutes = :minutes, description = :description, billable = :billable "
            "WHERE tenant_id = :t AND id = :id AND invoice_id IS NULL"),
            params)
        if res.rowcount == 0:
            raise ConflictError("Bereits abgerechnete Zeiten können nicht geändert werden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="time.saved", entity_type="time_entry", entity_id=entry_id,
                       after={k: str(v) for k, v in data.items()})
    return entry_id


async def delete_entry(conn: AsyncConnection, tenant_id: UUID, entry_id: UUID, actor: str) -> None:
    res = await conn.execute(text("DELETE FROM time_entries WHERE tenant_id = :t AND id = :id AND invoice_id IS NULL"),
                             {"t": tenant_id, "id": entry_id})
    if res.rowcount == 0:
        raise ConflictError("Zeit nicht gefunden oder bereits abgerechnet")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="time.deleted", entity_type="time_entry", entity_id=entry_id)


async def bill(conn: AsyncConnection, tenant_id: UUID, project_id: UUID, entry_ids: list[UUID], actor: str,
               hourly_rate: Decimal | None = None) -> dict[str, Any]:
    """Erzeugt einen Rechnungsentwurf (je Zeit eine Position in Stunden) und markiert die Zeiten als abgerechnet."""
    from app.api.core_schemas import InvoiceDraftIn, InvoiceLineIn
    from app.services import invoices as inv

    p = (await conn.execute(text("SELECT id, name, customer_id, hourly_rate FROM projects WHERE tenant_id = :t AND id = :id"),
                            {"t": tenant_id, "id": project_id})).mappings().one_or_none()
    if p is None:
        raise NotFoundError("Projekt nicht gefunden")
    if p["customer_id"] is None:
        raise ConflictError("Dem Projekt ist kein Kunde zugeordnet")
    rate = hourly_rate if hourly_rate is not None else p["hourly_rate"]
    if rate is None or D(rate) <= 0:
        raise ConflictError("Bitte einen Stundensatz für das Projekt festlegen")
    rows = (await conn.execute(text(
        "SELECT e.id, e.work_date, e.minutes, e.description, s.name AS person FROM time_entries e LEFT JOIN shareholders s ON s.id = "
        "e.shareholder_id "
        "WHERE e.tenant_id = :t AND e.project_id = :p AND e.id = ANY(:ids) AND e.billable AND e.invoice_id IS NULL ORDER BY e.work_date "
        "FOR UPDATE OF e"),
        {"t": tenant_id, "p": project_id, "ids": list(entry_ids)})).mappings().all()
    if not rows:
        raise ConflictError("Keine offenen, abrechenbaren Zeiten ausgewählt")
    lines = [InvoiceLineIn(description=f"{r['work_date']:%d.%m.%Y} – {r['description']}" + (f" ({r['person']})" if r["person"] else ""),
                           quantity=(D(r["minutes"]) / 60).quantize(D("0.0001"), rounding=ROUND_HALF_UP), unit_code="HUR",
                           unit_price=D(rate), tax_case="OUT_DE_19") for r in rows]
    hours = sum((D(r["minutes"]) for r in rows), D(0)) / 60
    draft = InvoiceDraftIn(customer_id=p["customer_id"], service_date_from=rows[0]["work_date"], service_date_to=rows[-1]["work_date"],
                           intro_text=f"Für das Projekt „{p['name']}“ berechnen wir {hours:.2f} Stunden.".replace(".", ","), lines=lines)
    invoice = await inv.create_draft(conn, tenant_id, draft, actor)
    await conn.execute(text("UPDATE time_entries SET invoice_id = :i WHERE id = ANY(:ids)"),
                       {"i": invoice["id"], "ids": [r["id"] for r in rows]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="time.billed", entity_type="project", entity_id=project_id,
                       after={"invoice_id": str(invoice["id"]), "entries": len(rows)})
    return {"invoice_id": invoice["id"], "entries": len(rows), "hours": hours.quantize(D("0.01"))}
