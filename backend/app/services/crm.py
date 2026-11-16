"""Vertriebs-Pipeline: Lead → qualifiziert → Angebot → gewonnen (Kunde, Angebot, Vertrag) bzw. verloren.

Angebote, die aus einem Lead entstehen, schieben den Lead automatisch weiter (Ereignisse „Angebot versendet/angenommen“).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services import audit, events
from app.services.masterdata import ConflictError, NotFoundError

D = Decimal
STAGES: dict[str, tuple[str, int]] = {"new": ("Neu", 10), "contacted": ("Kontaktiert", 20), "qualified": ("Qualifiziert", 40),
                                      "offer": ("Angebot", 60), "won": ("Gewonnen", 100), "lost": ("Verloren", 0)}
FIELDS = ("title", "company", "contact_name", "email", "phone", "source", "value", "recurring", "probability", "expected_close",
          "owner_id", "notes")
COLUMNS = ("l.id, l.number, l.title, l.company, l.contact_name, l.email, l.phone, l.source, l.stage, l.value, l.recurring, l.probability, "
           "l.expected_close, l.owner_id, l.customer_id, l.offer_id, l.lost_reason, l.notes, l.position, l.won_at, l.lost_at, "
           "l.created_at, "
           "l.updated_at")


def _weight(row: dict[str, Any]) -> int:
    return int(row["probability"]) if row.get("probability") is not None else STAGES[row["stage"]][1]


async def board(conn: AsyncConnection, tenant_id: UUID, today: dt.date) -> dict[str, Any]:
    rows = [dict(r) for r in (await conn.execute(text(
        f"SELECT {COLUMNS}, s.name AS owner_name, s.color AS owner_color, c.number AS customer_number, o.number AS offer_number, "
        "o.status AS offer_status, (SELECT min(due_date) FROM lead_activities a WHERE a.lead_id = l.id AND a.done_at IS NULL "
        "AND a.due_date IS NOT NULL) AS next_due FROM leads l LEFT JOIN shareholders s ON s.id = l.owner_id "
        "LEFT JOIN customers c ON c.id = l.customer_id LEFT JOIN offers o ON o.id = l.offer_id WHERE l.tenant_id = :t "
        "AND (l.stage NOT IN ('won', 'lost') OR COALESCE(l.won_at, l.lost_at) > now() - interval '90 days') ORDER BY l.position, "
        "l.created_at"),
        {"t": tenant_id})).mappings()]
    open_ = [r for r in rows if r["stage"] not in ("won", "lost")]
    year_start = dt.date(today.year, 1, 1)
    won = await conn.scalar(text("SELECT count(*) FROM leads WHERE tenant_id = :t AND stage = 'won' AND won_at >= :y"),
                            {"t": tenant_id, "y": year_start})
    lost = await conn.scalar(text("SELECT count(*) FROM leads WHERE tenant_id = :t AND stage = 'lost' AND lost_at >= :y"),
                             {"t": tenant_id, "y": year_start})
    won_value = await conn.scalar(text("SELECT COALESCE(sum(value), 0) FROM leads WHERE tenant_id = :t AND stage = 'won' AND won_at >= :y"),
                                  {"t": tenant_id, "y": year_start})
    for r in rows:
        r["weight"] = _weight(r)
        r["overdue"] = bool(r["next_due"] and r["next_due"] < today)
    return {"stages": [{"key": k, "label": v[0], "probability": v[1]} for k, v in STAGES.items()], "leads": rows,
            "stats": {"open": len(open_), "pipeline": sum((D(r["value"]) for r in open_), D(0)),
                      "weighted": sum((D(r["value"]) * r["weight"] / 100 for r in open_), D(0)).quantize(D("0.01")),
                      "won_year": won, "lost_year": lost, "won_value_year": won_value,
                      "win_rate": round(100 * won / (won + lost)) if (won or lost) else None}}


async def get(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID) -> dict[str, Any]:
    row = (await conn.execute(text(f"SELECT {COLUMNS} FROM leads l WHERE l.tenant_id = :t AND l.id = :id"),
                              {"t": tenant_id, "id": lead_id})).mappings().one_or_none()
    if row is None:
        raise NotFoundError("Lead nicht gefunden")
    acts = await conn.execute(text("SELECT id, kind, text, due_date, done_at, created_by, created_at FROM lead_activities "
                                   "WHERE lead_id = :id ORDER BY created_at DESC"), {"id": lead_id})
    return dict(row) | {"activities": [dict(a) for a in acts.mappings()]}


async def _log(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, text_: str, actor: str, kind: str = "system") -> None:
    await conn.execute(text("INSERT INTO lead_activities (tenant_id, lead_id, kind, text, created_by) VALUES (:t, :l, :k, :x, :a)"),
                       {"t": tenant_id, "l": lead_id, "k": kind, "x": text_[:5000], "a": actor})


async def save(conn: AsyncConnection, tenant_id: UUID, data: dict[str, Any], actor: str, lead_id: UUID | None = None) -> UUID:
    params = {k: data.get(k) for k in FIELDS} | {"t": tenant_id, "id": lead_id, "a": actor}
    if lead_id is None:
        number = await conn.scalar(text("SELECT COALESCE(max(number), 0) + 1 FROM leads WHERE tenant_id = :t"), {"t": tenant_id})
        pos = await conn.scalar(text("SELECT COALESCE(min(position), 0) - 1024 FROM leads WHERE tenant_id = :t AND stage = 'new'"),
                                {"t": tenant_id})
        lead_id = await conn.scalar(text(
            f"INSERT INTO leads (tenant_id, number, {', '.join(FIELDS)}, position, created_by) "
            f"VALUES (:t, :n, {', '.join(':' + f for f in FIELDS)}, :pos, :a) RETURNING id"), params | {"n": number, "pos": pos})
        await _log(conn, tenant_id, lead_id, "Lead angelegt", actor)
        action = "lead.created"
    else:
        await get(conn, tenant_id, lead_id)
        await conn.execute(text(f"UPDATE leads SET {', '.join(f'{f} = :{f}' for f in FIELDS)}, updated_at = now() "
                                "WHERE tenant_id = :t AND id = :id"), params)
        action = "lead.updated"
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action=action, entity_type="lead", entity_id=lead_id,
                       after={k: str(v) if v is not None else None for k, v in data.items()})
    assert lead_id is not None  # noqa: S101
    return lead_id


async def move(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, stage: str, actor: str, *, before_id: UUID | None = None,
               lost_reason: str | None = None) -> None:
    if stage not in STAGES:
        raise ConflictError("Unbekannte Phase")
    lead = await get(conn, tenant_id, lead_id)
    if stage == "lost" and not lost_reason:
        raise ConflictError("Bitte einen Grund für den Verlust angeben")
    if before_id:
        ref = await conn.scalar(text("SELECT position FROM leads WHERE tenant_id = :t AND id = :id"), {"t": tenant_id, "id": before_id})
        prev = await conn.scalar(text("SELECT max(position) FROM leads WHERE tenant_id = :t AND stage = :s AND position < :p"),
                                 {"t": tenant_id, "s": stage, "p": ref})
        pos = ((prev if prev is not None else ref - 2048) + ref) / 2 if ref is not None else 0
    else:
        pos = (await conn.scalar(text("SELECT COALESCE(max(position), 0) + 1024 FROM leads WHERE tenant_id = :t AND stage = :s"),
                                 {"t": tenant_id, "s": stage}))
    await conn.execute(text(
        "UPDATE leads SET stage = :s, position = :p, lost_reason = CASE WHEN :s = 'lost' THEN :r ELSE NULL END, "
        "won_at = CASE WHEN :s = 'won' THEN COALESCE(won_at, now()) ELSE NULL END, "
        "lost_at = CASE WHEN :s = 'lost' THEN COALESCE(lost_at, now()) ELSE NULL END, updated_at = now() WHERE id = :id"),
        {"s": stage, "p": pos, "r": lost_reason, "id": lead_id})
    if stage != lead["stage"]:
        await _log(conn, tenant_id, lead_id, f"Phase: {STAGES[lead['stage']][0]} → {STAGES[stage][0]}"
                   + (f" ({lost_reason})" if stage == "lost" else ""), actor)
        await audit.record(conn, tenant_id=tenant_id, actor=actor, action="lead.moved", entity_type="lead", entity_id=lead_id,
                           after={"stage": stage, "title": lead["title"], "value": str(lead["value"])})


async def add_activity(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, kind: str, text_: str, due: dt.date | None,
                       actor: str) -> None:
    await get(conn, tenant_id, lead_id)
    await conn.execute(text("INSERT INTO lead_activities (tenant_id, lead_id, kind, text, due_date, created_by) "
                            "VALUES (:t, :l, :k, :x, :d, :a)"), {"t": tenant_id, "l": lead_id, "k": kind, "x": text_, "d": due, "a": actor})
    if kind != "task":
        await conn.execute(text("UPDATE leads SET stage = 'contacted', updated_at = now() WHERE id = :id AND stage = 'new'"),
                           {"id": lead_id})


async def complete_activity(conn: AsyncConnection, tenant_id: UUID, activity_id: UUID, done: bool) -> None:
    n = (await conn.execute(text("UPDATE lead_activities SET done_at = CASE WHEN :d THEN now() END WHERE tenant_id = :t AND id = :id"),
                            {"t": tenant_id, "id": activity_id, "d": done})).rowcount
    if not n:
        raise NotFoundError("Aktivität nicht gefunden")


async def delete(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, actor: str) -> None:
    lead = await get(conn, tenant_id, lead_id)
    if lead["customer_id"] or lead["offer_id"]:
        raise ConflictError("Lead mit Kunde oder Angebot bitte auf „verloren“ setzen statt löschen")
    await conn.execute(text("DELETE FROM leads WHERE id = :id"), {"id": lead_id})
    await audit.record(conn, tenant_id=tenant_id, actor=actor, action="lead.deleted", entity_type="lead", entity_id=lead_id,
                       before={"title": lead["title"]})


async def convert_to_customer(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, address: dict[str, Any], actor: str) -> UUID:
    from app.api.core_schemas import CustomerIn
    from app.services.masterdata import create_customer

    lead = await get(conn, tenant_id, lead_id)
    if lead["customer_id"]:
        cid: UUID = lead["customer_id"]
        return cid
    names = (lead["contact_name"] or "").split(" ", 1)
    data = CustomerIn.model_validate({
        "customer_type": "b2b" if lead["company"] else "b2c", "company_name": lead["company"],
        "first_name": names[0] if not lead["company"] and len(names) > 1 else None,
        "last_name": names[-1] if not lead["company"] else None, "email": address.get("email") or lead["email"] or "",
        "phone": lead["phone"], **{k: address[k] for k in ("street", "postal_code", "city", "country_code", "vat_id") if address.get(k)},
        "notes": f"Aus Lead #{lead['number']} „{lead['title']}“"})
    row = await create_customer(conn, tenant_id, data, actor)
    await conn.execute(text("UPDATE leads SET customer_id = :c, stage = CASE WHEN stage IN ('new', 'contacted') THEN 'qualified' "
                            "ELSE stage END, updated_at = now() WHERE id = :id"), {"c": row["id"], "id": lead_id})
    await _log(conn, tenant_id, lead_id, f"Kunde {row['number']} angelegt", actor)
    cid = row["id"]
    return cid


async def create_offer(conn: AsyncConnection, tenant_id: UUID, lead_id: UUID, actor: str, today: dt.date) -> UUID:
    from app.services import offers

    lead = await get(conn, tenant_id, lead_id)
    if not lead["customer_id"]:
        raise ConflictError("Bitte zuerst einen Kunden aus dem Lead anlegen")
    if lead["offer_id"]:
        oid: UUID = lead["offer_id"]
        return oid
    country = await conn.scalar(text("SELECT country_code FROM customers WHERE id = :c"), {"c": lead["customer_id"]})
    tax = "OUT_DE_19"
    if country and country != "DE":
        from app.domain.international import EU

        tax = "OUT_EU_B2B_SERVICE" if country in EU else "OUT_DRITTLAND_SERVICE"
    offer = await offers.save_draft(conn, tenant_id, {
        "customer_id": lead["customer_id"], "title": lead["title"][:200], "valid_until": today + dt.timedelta(days=30),
        "intro_text": None, "closing_text": None,
        "lines": [{"description": lead["title"], "quantity": D(1), "unit_code": "MON" if lead["recurring"] else "LS",
                   "unit_price": D(lead["value"] or 0), "tax_case": tax}]}, actor, today)
    await conn.execute(text("UPDATE leads SET offer_id = :o, stage = 'offer', updated_at = now() WHERE id = :id"),
                       {"o": offer["id"], "id": lead_id})
    await _log(conn, tenant_id, lead_id, "Angebotsentwurf erstellt", actor)
    oid = offer["id"]
    return oid


@events.listen
async def _offer_events(conn: AsyncConnection, tenant_id: UUID, event: str, payload: dict[str, Any]) -> None:
    oid = payload.get("entity_id")
    if event not in ("offer.accepted", "offer.sent", "offer.declined") or not oid:
        return
    if event == "offer.declined":
        rows = (await conn.execute(text(
            "UPDATE leads SET stage = 'lost', lost_at = COALESCE(lost_at, now()), lost_reason = 'Angebot abgelehnt', updated_at = now() "
            "WHERE tenant_id = :t AND offer_id = CAST(:o AS uuid) AND stage NOT IN ('won', 'lost') RETURNING id"),
            {"t": tenant_id, "o": oid})).all()
        text_ = "Angebot abgelehnt – verloren"
    elif event == "offer.accepted":
        rows = (await conn.execute(text(
            "UPDATE leads SET stage = 'won', won_at = COALESCE(won_at, now()), updated_at = now() WHERE tenant_id = :t "
            "AND offer_id = CAST(:o AS uuid) AND stage <> 'won' RETURNING id"), {"t": tenant_id, "o": oid})).all()
        text_ = "Angebot angenommen – gewonnen"
    else:
        rows = (await conn.execute(text(
            "UPDATE leads SET stage = 'offer', updated_at = now() WHERE tenant_id = :t AND offer_id = CAST(:o AS uuid) "
            "AND stage IN ('new', 'contacted', 'qualified') RETURNING id"), {"t": tenant_id, "o": oid})).all()
        text_ = "Angebot versendet"
    for (lid,) in rows:
        await _log(conn, tenant_id, lid, text_, "system")
