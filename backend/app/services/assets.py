"""Anlagenbuchhaltung: Anlagenverzeichnis, Anlagenspiegel und jährlicher Abschreibungslauf (Abschlussbuchungen 31.12.)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.assets import USEFUL_LIFE, Asset, AssetError, year_result
from app.domain.assets import validate as validate_rules
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
ZERO = D("0.00")
COLUMNS = ("id, number, name, account, asset_class, method, acquisition_date, cost, useful_life_months, receipt_id, disposal_date, "
           "disposal_note, notes, created_at")


def _asset(r: dict[str, Any]) -> Asset:
    return Asset(r["method"], r["acquisition_date"], D(r["cost"]), r["useful_life_months"], r["disposal_date"])


async def asset_accounts(conn: AsyncConnection, tenant_id: UUID) -> dict[str, str]:
    """Anlagekonten (Kontenklasse 0) des eingestellten Kontenrahmens: Nummer → Name."""
    from app.services.accounting import accounts, chart_of

    chart = await chart_of(conn, tenant_id)
    return {str(a["number"]): a["name"] for a in await accounts(conn, chart) if a["type"] == "asset" and str(a["number"]).startswith("0")}


def depreciation_role(row: dict[str, Any], chart: str) -> str:
    if row["method"] == "pool":
        return "depreciation_pool"
    if row["method"] == "gwg":
        return "depreciation_gwg"
    if row["asset_class"] == "intangible" or row["account"] in ("0135", "0027"):
        return "depreciation_intangible"
    return "depreciation"


async def list_assets(conn: AsyncConnection, tenant_id: UUID, year: int) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(f"SELECT {COLUMNS} FROM fixed_assets WHERE tenant_id = :t ORDER BY number"),
                               {"t": tenant_id})).mappings().all()
    posted = {(r.asset_id, r.year): r for r in await conn.execute(text(
        "SELECT asset_id, year, amount, disposal_amount FROM asset_depreciations WHERE tenant_id = :t"), {"t": tenant_id})}
    out = []
    for row in rows:
        r = dict(row)
        a = _asset(r)
        y = year_result(a, year)
        prev = year_result(a, year - 1) if year > a.acquisition_date.year else None
        start_value = prev.book_value_end if prev else ZERO  # im Anschaffungsjahr: Zugang statt Anfangsbestand
        r |= {
            "year": year, "depreciation": y.depreciation, "disposal_book_value": y.disposal_book_value, "book_value_end": y.book_value_end,
            "accumulated_end": y.accumulated, "book_value_start": start_value,
            "accumulated_start": prev.accumulated if prev else ZERO, "posted": (r["id"], year) in posted,
            "addition": a.cost if a.acquisition_date.year == year else ZERO,
            "disposal": a.cost if a.disposal_date and a.disposal_date.year == year and a.method != "pool" else ZERO,
            "has_postings": any(k[0] == r["id"] for k in posted),
        }
        out.append(r)
    return out


async def candidates(conn: AsyncConnection, tenant_id: UUID) -> list[dict[str, Any]]:
    """Gebuchte Belege auf Anlagekonten, die noch nicht im Anlagenverzeichnis stehen."""
    rows = await conn.execute(text(
        "SELECT r.id, r.receipt_date, r.description, r.net_amount, r.expense_account, r.category, s.name AS supplier_name "
        "FROM receipts r JOIN suppliers s ON s.id = r.supplier_id WHERE r.tenant_id = :t AND r.status = 'booked' AND r.kind = 'expense' "
        "AND r.reversed_by IS NULL AND (r.expense_account LIKE '0%' OR r.category = 'fixed_asset') "
        "AND NOT EXISTS (SELECT 1 FROM fixed_assets f WHERE f.receipt_id = r.id) ORDER BY r.receipt_date DESC"), {"t": tenant_id})
    return [dict(r) for r in rows.mappings()]


async def save_asset(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                     asset_id: UUID | None = None) -> UUID:
    accounts = await asset_accounts(conn, tenant_id)
    if data["account"] not in accounts:
        raise ConflictError(f"Konto {data['account']} ist kein Anlagekonto")
    try:
        validate_rules(data["method"], D(data["cost"]))
    except AssetError as exc:
        raise ConflictError(str(exc)) from exc
    if data.get("receipt_id"):
        r = await conn.scalar(text("SELECT 1 FROM receipts WHERE tenant_id = :t AND id = :id AND status = 'booked'"),
                              {"t": tenant_id, "id": data["receipt_id"]})
        if not r:
            raise NotFoundError("Beleg nicht gefunden oder nicht gebucht")
    params = {**data, "t": tenant_id, "id": asset_id, "a": actor}
    if asset_id is None:
        number = await conn.scalar(text("SELECT COALESCE(max(number), 0) + 1 FROM fixed_assets WHERE tenant_id = :t"), {"t": tenant_id})
        asset_id = await conn.scalar(text(
            "INSERT INTO fixed_assets (tenant_id, number, name, account, asset_class, method, acquisition_date, cost, useful_life_months, "
            "receipt_id, disposal_date, disposal_note, notes, created_by) VALUES (:t, :n, :name, :account, :asset_class, :method, "
            ":acquisition_date, :cost, :useful_life_months, :receipt_id, :disposal_date, :disposal_note, :notes, :a) RETURNING id"),
            params | {"n": number})
        action = "asset.created"
    else:
        cur = (await conn.execute(text(f"SELECT {COLUMNS} FROM fixed_assets WHERE tenant_id = :t AND id = :id FOR UPDATE"),
                                  {"t": tenant_id, "id": asset_id})).mappings().one_or_none()
        if cur is None:
            raise NotFoundError("Anlagegut nicht gefunden")
        posted = await conn.scalar(text("SELECT 1 FROM asset_depreciations WHERE asset_id = :id LIMIT 1"), {"id": asset_id})
        fixed = ("account", "asset_class", "method", "acquisition_date", "cost", "useful_life_months")
        if posted and any(str(cur[k]) != str(data[k]) for k in fixed):
            raise ConflictError("Nach der ersten Abschreibung sind Konto, Methode, Datum, Kosten und Nutzungsdauer fest")
        await conn.execute(text(
            "UPDATE fixed_assets SET name = :name, account = :account, asset_class = :asset_class, method = :method, "
            "acquisition_date = :acquisition_date, cost = :cost, useful_life_months = :useful_life_months, receipt_id = :receipt_id, "
            "disposal_date = :disposal_date, disposal_note = :disposal_note, notes = :notes WHERE id = :id"), params)
        action = "asset.updated"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="fixed_asset", entity_id=asset_id,
                       after={k: str(v) if v is not None else None for k, v in data.items()})
    return asset_id


async def post_depreciation(conn: AsyncConnection, ledger: Any, tenant_id: UUID, year: int, actor: str) -> dict[str, Any]:
    """Bucht die Abschreibung des Jahres als Abschlussbuchungen (31.12., Periode JJJJ-13) – je Anlagegut einmal."""
    from app.services.accounting import Line, acc, chart_of, post

    chart = await chart_of(conn, tenant_id)
    posted: list[dict[str, Any]] = []
    for r in await list_assets(conn, tenant_id, year):
        if r["posted"] or (r["depreciation"] <= 0 and r["disposal_book_value"] <= 0):
            continue
        dep_id = uuid.uuid4()
        lines = []
        if r["depreciation"] > 0:
            lines += [Line(acc(chart, depreciation_role(r, chart)), debit=r["depreciation"], note="Abschreibung"),
                      Line(r["account"], credit=r["depreciation"])]
        if r["disposal_book_value"] > 0:
            lines += [Line(acc(chart, "asset_disposal"), debit=r["disposal_book_value"], note="Restbuchwert bei Abgang"),
                      Line(r["account"], credit=r["disposal_book_value"])]
        entry = await post(conn, ledger, tenant_id, chart=chart, entry_date=dt.date(year, 12, 31),
                           description=f"Abschreibung {year}: AV-{r['number']:04d} {r['name']}"[:300], document_ref=f"AV-{r['number']:04d}",
                           source_type="depreciation", source_id=dep_id, lines=lines, method="rule", actor=actor, closing=True)
        await conn.execute(text(
            "INSERT INTO asset_depreciations (id, asset_id, tenant_id, year, amount, disposal_amount, journal_entry_id, created_by) "
            "VALUES (:id, :a, :t, :y, :amt, :dis, :e, :by)"),
            {"id": dep_id, "a": r["id"], "t": tenant_id, "y": year, "amt": r["depreciation"], "dis": r["disposal_book_value"],
             "e": entry, "by": actor})
        posted.append({"number": r["number"], "name": r["name"], "amount": r["depreciation"], "disposal": r["disposal_book_value"]})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="asset.depreciation_posted", entity_type="year",
                       entity_id=str(year), after={"assets": len(posted)})
    return {"year": year, "posted": posted, "total": sum((p["amount"] + p["disposal"] for p in posted), ZERO)}


def useful_life_suggestions() -> dict[str, int]:
    return dict(USEFUL_LIFE)
