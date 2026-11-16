"""Budgetplanung je Bereich (Kostenstelle): Monatsbudget, Ist-Ausgaben aus gebuchten Belegen, Auslastung.

Ist-Wert ist der betriebliche Aufwand des Belegs (``expense_amount``: netto bei Vorsteuerabzug, sonst brutto) – also das,
was das Unternehmen tatsächlich kostet. Stornobelege mindern automatisch den Bereich des Originalbelegs.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.expenses import CATEGORIES
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
ZERO = D("0.00")
CATEGORY_CODES = {c.code for c in CATEGORIES}


def _months(year: int, until: dt.date) -> list[str]:
    last = until.month if until.year == year else 12
    return [f"{year}-{m:02d}" for m in range(1, last + 1)] if until.year >= year else []


async def overview(conn: AsyncConnection, tenant_id: UUID, year: int, today: dt.date) -> dict[str, Any]:
    areas = [dict(r) for r in (await conn.execute(text(
        "SELECT id, name, description, monthly_budget, color, categories FROM budget_areas WHERE tenant_id = :t AND archived_at IS NULL "
        "ORDER BY name"), {"t": tenant_id})).mappings()]
    spent = {(r.area_id, r.month): D(r.amount) for r in await conn.execute(text(
        "SELECT a.area_id, to_char(r.receipt_date, 'YYYY-MM') AS month, SUM(r.expense_amount) AS amount FROM receipts r "
        "JOIN budget_assignments a ON a.receipt_id = COALESCE(r.reverses_id, r.id) "
        "WHERE r.tenant_id = :t AND r.status = 'booked' AND r.receipt_date BETWEEN :s AND :e GROUP BY 1, 2"),
        {"t": tenant_id, "s": dt.date(year, 1, 1), "e": dt.date(year, 12, 31)})}
    months = _months(year, today)
    current = f"{today:%Y-%m}" if today.year == year else (months[-1] if months else f"{year}-12")
    out = []
    for a in areas:
        budget = D(a["monthly_budget"])
        per_month: list[dict[str, Any]] = [{"month": m, "budget": budget, "spent": spent.get((a["id"], m), ZERO)} for m in months]
        ytd_spent: Decimal = sum((spent.get((a["id"], m), ZERO) for m in months), ZERO)
        ytd_budget = budget * len(months)
        month_spent = spent.get((a["id"], current), ZERO)
        out.append(a | {
            "months": per_month, "current_month": current, "month_spent": month_spent, "month_remaining": budget - month_spent,
            "month_usage": (month_spent / budget * 100).quantize(D("0.1")) if budget > 0 else None,
            "ytd_budget": ytd_budget, "ytd_spent": ytd_spent, "ytd_remaining": ytd_budget - ytd_spent,
            "ytd_usage": (ytd_spent / ytd_budget * 100).quantize(D("0.1")) if ytd_budget > 0 else None,
            "over_budget": month_spent > budget,
        })
    totals = {"monthly_budget": sum((D(a["monthly_budget"]) for a in areas), ZERO),
              "month_spent": sum((a["month_spent"] for a in out), ZERO),
              "ytd_budget": sum((a["ytd_budget"] for a in out), ZERO), "ytd_spent": sum((a["ytd_spent"] for a in out), ZERO)}
    unassigned = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(r.expense_amount), 0) FROM receipts r WHERE r.tenant_id = :t AND r.status = 'booked' "
        "AND r.receipt_date BETWEEN :s AND :e "
        "AND NOT EXISTS (SELECT 1 FROM budget_assignments a WHERE a.receipt_id = COALESCE(r.reverses_id, r.id))"),
        {"t": tenant_id, "s": dt.date(year, 1, 1), "e": dt.date(year, 12, 31)}))
    return {"year": year, "areas": out, "totals": totals, "unassigned_spent": unassigned}


async def save_area(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, area_id: UUID | None = None) -> UUID:
    unknown = set(data.get("categories") or []) - CATEGORY_CODES
    if unknown:
        raise ConflictError(f"Unbekannte Kategorie: {', '.join(sorted(unknown))}")
    dup = await conn.scalar(text("SELECT 1 FROM budget_areas WHERE tenant_id = :t AND lower(name) = lower(:n) AND archived_at IS NULL "
                                 "AND (CAST(:id AS uuid) IS NULL OR id <> CAST(:id AS uuid))"),
                            {"t": tenant_id, "n": data["name"], "id": area_id})
    if dup:
        raise ConflictError(f"Bereich „{data['name']}“ gibt es bereits")
    params = {"t": tenant_id, "id": area_id, "n": data["name"], "d": data.get("description"), "b": data["monthly_budget"],
              "c": data.get("color") or "#6366f1", "cat": list(data.get("categories") or []), "a": actor}
    if area_id is None:
        area_id = await conn.scalar(text(
            "INSERT INTO budget_areas (tenant_id, name, description, monthly_budget, color, categories, created_by) "
            "VALUES (:t, :n, :d, :b, :c, :cat, :a) RETURNING id"), params)
    else:
        res = await conn.execute(text(
            "UPDATE budget_areas SET name = :n, description = :d, monthly_budget = :b, color = :c, categories = :cat, updated_at = now() "
            "WHERE tenant_id = :t AND id = :id AND archived_at IS NULL"), params)
        if res.rowcount == 0:
            raise NotFoundError("Bereich nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="budget_area.saved", entity_type="budget_area", entity_id=area_id,
                       after=data)
    return area_id


async def archive_area(conn: AsyncConnection, tenant_id: UUID, area_id: UUID, actor: str) -> None:
    res = await conn.execute(text("UPDATE budget_areas SET archived_at = now() WHERE tenant_id = :t AND id = :id AND archived_at IS NULL"),
                             {"t": tenant_id, "id": area_id})
    if res.rowcount == 0:
        raise NotFoundError("Bereich nicht gefunden")
    # Zuordnungen lösen, damit die Ausgaben einem anderen Bereich zugeordnet werden können
    await conn.execute(text("DELETE FROM budget_assignments WHERE area_id = :id"), {"id": area_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="budget_area.archived", entity_type="budget_area", entity_id=area_id)


async def expenses(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date, area_id: UUID | None = None,
                   unassigned: bool = False) -> list[dict[str, Any]]:
    """Gebuchte Ausgaben (ohne Stornobelege) mit aktueller Zuordnung und Vorschlag anhand der Kategorie."""
    rows = (await conn.execute(text(
        "SELECT r.id, r.receipt_date, r.invoice_number, r.description, r.category, r.expense_amount, r.reversed_by, "
        "s.name AS supplier_name, "
        "a.area_id, ba.name AS area_name FROM receipts r JOIN suppliers s ON s.id = r.supplier_id "
        "LEFT JOIN budget_assignments a ON a.receipt_id = r.id LEFT JOIN budget_areas ba ON ba.id = a.area_id "
        "WHERE r.tenant_id = :t AND r.status = 'booked' AND r.kind = 'expense' AND r.receipt_date BETWEEN :s AND :e "
        "AND (CAST(:area AS uuid) IS NULL OR a.area_id = CAST(:area AS uuid)) AND (NOT :un OR a.area_id IS NULL) "
        "ORDER BY r.receipt_date DESC LIMIT 1000"),
        {"t": tenant_id, "s": start, "e": end, "area": area_id, "un": unassigned})).mappings().all()
    areas = (await conn.execute(text("SELECT id, name, categories FROM budget_areas WHERE tenant_id = :t AND archived_at IS NULL"),
                                {"t": tenant_id})).mappings().all()
    by_cat = {cat: a for a in areas for cat in a["categories"]}
    out = []
    for r in rows:
        item = dict(r)
        hint = by_cat.get(r["category"])
        item["suggested_area_id"] = hint["id"] if hint and not r["area_id"] else None
        item["suggested_area_name"] = hint["name"] if hint and not r["area_id"] else None
        out.append(item)
    return out


async def assign(conn: AsyncConnection, tenant_id: UUID, area_id: UUID | None, receipt_ids: list[UUID], actor: str) -> int:
    if area_id is not None:
        ok = await conn.scalar(text("SELECT 1 FROM budget_areas WHERE tenant_id = :t AND id = :id AND archived_at IS NULL"),
                               {"t": tenant_id, "id": area_id})
        if not ok:
            raise NotFoundError("Bereich nicht gefunden")
    count = 0
    for rid in receipt_ids:
        valid = await conn.scalar(text("SELECT 1 FROM receipts WHERE tenant_id = :t AND id = :id AND status = 'booked' "
                                       "AND kind = 'expense'"),
                                  {"t": tenant_id, "id": rid})
        if not valid:
            raise ConflictError("Nur gebuchte Ausgaben (keine Entwürfe oder Stornos) können zugeordnet werden")
        if area_id is None:
            await conn.execute(text("DELETE FROM budget_assignments WHERE receipt_id = :r"), {"r": rid})
        else:
            await conn.execute(text(
                "INSERT INTO budget_assignments (receipt_id, area_id, tenant_id, assigned_by) VALUES (:r, :a, :t, :by) "
                "ON CONFLICT (receipt_id) DO UPDATE SET area_id = EXCLUDED.area_id, assigned_by = EXCLUDED.assigned_by, "
                "assigned_at = now()"), {"r": rid, "a": area_id, "t": tenant_id, "by": actor})
        count += 1
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="budget.assigned", entity_type="budget_area", entity_id=area_id,
                       after={"receipts": [str(r) for r in receipt_ids]})
    return count


async def apply_suggestions(conn: AsyncConnection, tenant_id: UUID, start: dt.date, end: dt.date, actor: str) -> int:
    """Ordnet nicht zugeordnete Ausgaben anhand der Kategorie-Regeln der Bereiche zu."""
    items = [e for e in await expenses(conn, tenant_id, start, end, unassigned=True) if e["suggested_area_id"]]
    for e in items:
        await assign(conn, tenant_id, e["suggested_area_id"], [e["id"]], actor)
    return len(items)
