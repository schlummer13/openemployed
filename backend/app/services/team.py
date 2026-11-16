"""Gesellschafter, Geschäftsführergehälter (Planung), Tragfähigkeit und Ausschüttung."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.payroll import PARAMS_2026, TaxProfile, distribution, gross_for_employer_cost, salary
from app.services import audit
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal


async def list_shareholders(conn: AsyncConnection, tenant_id: UUID, on: dt.date) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT id, name, email, share_percent, is_managing_director, phone, birth_date, street, postal_code, city, tax_id, "
        "social_security_number, iban, role_title, joined_on, color, photo IS NOT NULL AS has_photo, updated_at "
        "FROM shareholders WHERE tenant_id = :t ORDER BY name"),
        {"t": tenant_id})).mappings().all()
    out = []
    for r in rows:
        comp = (await conn.execute(text(
            "SELECT valid_from, gross_month, salaries_per_year, sv_status, tax_class, church_tax, childless FROM md_compensations "
            "WHERE shareholder_id = :s AND valid_from <= :on ORDER BY valid_from DESC LIMIT 1"),
            {"s": r["id"], "on": on})).mappings().one_or_none()
        upcoming = (await conn.execute(text(
            "SELECT valid_from, gross_month FROM md_compensations WHERE shareholder_id = :s AND valid_from > :on ORDER BY valid_from"),
            {"s": r["id"], "on": on})).mappings().all()
        planned = False
        if comp is None and upcoming:
            # noch kein gültiges Gehalt → nächstes geplantes Gehalt durchrechnen (für die Planung)
            comp = (await conn.execute(text(
                "SELECT valid_from, gross_month, salaries_per_year, sv_status, tax_class, church_tax, childless FROM md_compensations "
                "WHERE shareholder_id = :s AND valid_from > :on ORDER BY valid_from LIMIT 1"), {"s": r["id"], "on": on})).mappings().one()
            planned = True
            upcoming = upcoming[1:]
        item = dict(r) | {"compensation": dict(comp) if comp else None, "upcoming": [dict(u) for u in upcoming],
                          "share_percent": D(r["share_percent"]).quantize(D("0.01")), "planned": planned}
        prof = await tax_profile(conn, r["id"], on)
        item["tax_profile"] = prof
        if comp:
            s = salary(comp["gross_month"], salaries_per_year=comp["salaries_per_year"], sv_status=comp["sv_status"],
                       profile=to_profile(prof) if prof else TaxProfile(tax_class=comp["tax_class"], church_tax=comp["church_tax"],
                                                                         children_under_25=0 if comp["childless"] else 1))
            item["salary"] = asdict(s)
        else:
            item["salary"] = None
        out.append(item)
    return out


PROFILE_FIELDS = ("tax_class", "child_allowances", "church_tax", "state", "birth_year", "children_under_25", "health_insurance",
                  "health_insurer", "kv_additional_rate", "pkv_premium")


async def tax_profile(conn: AsyncConnection, shareholder_id: UUID, on: dt.date) -> dict[str, Any] | None:
    row = (await conn.execute(text(
        f"SELECT valid_from, {', '.join(PROFILE_FIELDS)} FROM md_tax_profiles WHERE shareholder_id = :s "
        "ORDER BY (valid_from <= :on) DESC, CASE WHEN valid_from <= :on THEN valid_from END DESC, valid_from LIMIT 1"),
        {"s": shareholder_id, "on": on})).mappings().one_or_none()
    return dict(row) if row else None


def to_profile(p: dict[str, Any]) -> TaxProfile:
    return TaxProfile(tax_class=p["tax_class"], child_allowances=D(p["child_allowances"]), church_tax=p["church_tax"],
                      state=p["state"], birth_year=p["birth_year"], children_under_25=p["children_under_25"],
                      health_insurance=p["health_insurance"],
                      kv_additional_rate=D(p["kv_additional_rate"]) if p["kv_additional_rate"] is not None else None,
                      pkv_premium=D(p["pkv_premium"]))


async def save_tax_profile(conn: AsyncConnection, tenant_id: UUID, shareholder_id: UUID, data: dict[str, Any], actor: str,
                           today: dt.date) -> None:
    exists = await conn.scalar(text("SELECT 1 FROM shareholders WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": shareholder_id})
    if not exists:
        raise NotFoundError("Gesellschafter nicht gefunden")
    if data["valid_from"] < today.replace(day=1) - dt.timedelta(days=366):
        raise ConflictError("Gültig-ab-Datum liegt zu weit zurück")
    dup = await conn.scalar(text("SELECT 1 FROM md_tax_profiles WHERE shareholder_id = :s AND valid_from = :v"),
                            {"s": shareholder_id, "v": data["valid_from"]})
    if dup:
        raise ConflictError(f"Für {data['valid_from']:%d.%m.%Y} sind bereits Steuerdaten hinterlegt – anderes Datum wählen")
    if data["health_insurance"] == "gkv" and data.get("pkv_premium"):
        data = data | {"pkv_premium": D(0)}
    await conn.execute(text(
        f"INSERT INTO md_tax_profiles (shareholder_id, valid_from, {', '.join(PROFILE_FIELDS)}, created_by) "
        f"VALUES (:s, :valid_from, {', '.join(':' + f for f in PROFILE_FIELDS)}, :a)"), data | {"s": shareholder_id, "a": actor})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="md_tax_profile.added", entity_type="shareholder",
                       entity_id=shareholder_id, after=data)


async def save_shareholder(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str,
                           shareholder_id: UUID | None = None) -> UUID:
    total = await conn.scalar(text(
        "SELECT COALESCE(SUM(share_percent), 0) FROM shareholders WHERE tenant_id = :t "
        "AND (CAST(:id AS uuid) IS NULL OR id <> CAST(:id AS uuid))"),
        {"t": tenant_id, "id": shareholder_id})
    if D(total) + data["share_percent"] > 100:
        new_total = (D(total) + data["share_percent"]).quantize(D("0.01"))
        raise ConflictError(f"Summe der Anteile wäre {str(new_total).replace('.', ',')} % (max. 100 %)")
    params = {f: data.get(f) for f in HOLDER_FIELDS} | {"t": tenant_id, "id": shareholder_id}
    if shareholder_id is None:
        shareholder_id = await conn.scalar(text(
            f"INSERT INTO shareholders (tenant_id, {', '.join(HOLDER_FIELDS)}) VALUES (:t, {', '.join(':' + f for f in HOLDER_FIELDS)}) "
            "RETURNING id"), params)
    else:
        res = await conn.execute(text(
            f"UPDATE shareholders SET {', '.join(f'{f} = :{f}' for f in HOLDER_FIELDS)}, updated_at = now() "
            "WHERE tenant_id = :t AND id = :id"), params)
        if res.rowcount == 0:
            raise NotFoundError("Gesellschafter nicht gefunden")
    # Sensible Kennungen nicht im Klartext ins Protokoll
    logged = {k: ("***" if k in ("tax_id", "social_security_number", "iban") and v else v) for k, v in data.items()}
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="shareholder.saved", entity_type="shareholder",
                       entity_id=shareholder_id, after=logged)
    return shareholder_id


HOLDER_FIELDS = ("name", "email", "share_percent", "is_managing_director", "phone", "birth_date", "street", "postal_code", "city",
                 "tax_id", "social_security_number", "iban", "role_title", "joined_on", "color")
PHOTO_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def detect_photo(content: bytes) -> str:
    for magic, mime in PHOTO_TYPES.items():
        if content.startswith(magic):
            return mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    raise ConflictError("Foto bitte als PNG, JPEG oder WebP")


async def set_photo(conn: AsyncConnection, tenant_id: UUID, shareholder_id: UUID, content: bytes | None, actor: str) -> None:
    mime = None
    if content is not None:
        if len(content) > 2 * 1024 * 1024:
            raise ConflictError("Foto darf höchstens 2 MB groß sein")
        mime = detect_photo(content)
    res = await conn.execute(text("UPDATE shareholders SET photo = :p, photo_mime = :m, updated_at = now() "
                                  "WHERE tenant_id = :t AND id = :id"),
                             {"p": content, "m": mime, "t": tenant_id, "id": shareholder_id})
    if res.rowcount == 0:
        raise NotFoundError("Gesellschafter nicht gefunden")
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="shareholder.photo", entity_type="shareholder",
                       entity_id=shareholder_id, after={"removed": content is None})


async def add_compensation(conn: AsyncConnection, tenant_id: UUID, shareholder_ids: list[UUID], data: dict[str, Any],
                           actor: str, today: dt.date) -> None:
    if data["valid_from"] < today.replace(day=1):
        raise ConflictError("Gehaltsänderungen nur für die Zukunft bzw. ab dem laufenden Monat (Rückwirkungsverbot, vGA-Risiko)")
    for sid in shareholder_ids:
        exists = await conn.scalar(text("SELECT 1 FROM shareholders WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": sid})
        if not exists:
            raise NotFoundError("Gesellschafter nicht gefunden")
        dup = await conn.scalar(text("SELECT 1 FROM md_compensations WHERE shareholder_id = :s AND valid_from = :v"),
                                {"s": sid, "v": data["valid_from"]})
        if dup:
            raise ConflictError(f"Für {data['valid_from']:%d.%m.%Y} gibt es bereits eine Gehaltsfestlegung")
        await conn.execute(text(
            "INSERT INTO md_compensations (shareholder_id, valid_from, gross_month, salaries_per_year, sv_status, tax_class, "
            "church_tax, childless, created_by) VALUES (:s, :valid_from, :gross_month, :salaries_per_year, :sv_status, :tax_class, "
            ":church_tax, :childless, :a)"), data | {"s": sid, "a": actor})
        await audit.record(conn, tenant_id=tenant_id, actor=actor, action="md_compensation.added", entity_type="shareholder",
                           entity_id=sid, after=data)


async def capacity(conn: AsyncConnection, tenant_id: UUID, today: dt.date, shareholders: list[dict[str, Any]]) -> dict[str, Any]:
    """Tragfähigkeit: MRR − Ø Ausgaben (3 Monate) − Puffer → max. Arbeitgeberkosten je GF."""
    from app.services.contracts import list_contracts

    mrr = sum((c["mrr"] for c in await list_contracts(conn, tenant_id)), start=D(0))
    since = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    since = (since - dt.timedelta(days=1)).replace(day=1)
    since = (since - dt.timedelta(days=1)).replace(day=1)
    expenses = D(await conn.scalar(text(
        "SELECT COALESCE(SUM(expense_amount), 0) FROM receipts WHERE tenant_id = :t AND status = 'booked' AND receipt_date >= :s "
        "AND receipt_date < :e"), {"t": tenant_id, "s": since, "e": today.replace(day=1)}))
    avg_expenses = (expenses / 3).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    buffer_rate = D("0.10")
    available = mrr - avg_expenses
    budget = max(D(0), (available * (1 - buffer_rate)).quantize(D("0.01")))
    mds = [s for s in shareholders if s["is_managing_director"]]
    per_md = (budget / len(mds)).quantize(D("0.01")) if mds else D(0)
    current_cost = sum((D(s["salary"]["employer_cost_month"]) for s in mds if s["salary"]), start=D(0))
    return {
        "mrr": mrr, "avg_expenses": avg_expenses, "buffer_percent": buffer_rate * 100, "budget": budget,
        "managing_directors": len(mds), "per_md_employer_cost": per_md,
        "per_md_gross_estimate": gross_for_employer_cost(per_md) if mds else D(0),
        "current_employer_cost": current_cost,
        "usage_percent": (current_cost / budget * 100).quantize(D("0.1")) if budget > 0 else None,
        "note": "Gehälter mindern den steuerpflichtigen Gewinn. Puffer 10 % für Schwankungen; Steuerrücklagen siehe Steuern.",
    }


def distribution_plan(profit_after_tax: D, ug_reserve: D, shareholders: list[dict[str, Any]]) -> dict[str, Any]:
    distributable = max(D(0), profit_after_tax - ug_reserve)
    rows = []
    for s in shareholders:
        share = (distributable * D(s["share_percent"]) / 100).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        comp = s.get("compensation") or {}
        prof = s.get("tax_profile") or {}
        d = distribution(share, church=bool(prof.get("church_tax", comp.get("church_tax"))))
        rows.append({"shareholder_id": s["id"], "name": s["name"], "share_percent": s["share_percent"], "gross": d.gross,
                     "kapest": d.kapest, "solz": d.solz, "church": d.church, "net": d.net})
    return {"distributable": distributable, "ug_reserve": ug_reserve, "rows": rows,
            "note": "Ausschüttung erst nach festgestelltem Jahresabschluss und Gesellschafterbeschluss; Kapitalertragsteuer "
                    "führt die Gesellschaft ab. Teileinkünfteverfahren auf Antrag möglich (Steuerberater)."}


PARAMS_INFO = {"year": PARAMS_2026.year, "basic_allowance": PARAMS_2026.basic_allowance,
               "bbg_rv_month": PARAMS_2026.bbg_rv_month, "bbg_kv_month": PARAMS_2026.bbg_kv_month,
               "kv_additional_rate": PARAMS_2026.kv_additional_rate}
