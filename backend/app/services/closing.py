"""Periodenabschluss (Festschreibung nach GoBD): Vollständigkeitsprüfung vor dem Sperren eines Zeitraums.

Festgeschrieben wird spätestens mit der Umsatzsteuer-Voranmeldung (GoBD Rz. 89 i. V. m. § 146 Abs. 4 AO). Vor dem
Sperren werden alle Regelbuchungen erzeugt; offene Punkte des Zeitraums blockieren die Sperre, außer sie werden
ausdrücklich bestätigt (die Bestätigung wird mit festgeschrieben).
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    y, m = (int(x) for x in month.split("-"))
    start = dt.date(y, m, 1)
    end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return start, end


async def open_items(conn: AsyncConnection, tenant_id: UUID, months: list[str]) -> list[dict[str, Any]]:
    start, _ = month_bounds(months[0])
    _, end = month_bounds(months[-1])
    p = {"t": tenant_id, "s": start, "e": end}
    items: list[dict[str, Any]] = []

    async def count(sql: str) -> int:
        return int(await conn.scalar(text(sql), p) or 0)

    n = await count("SELECT count(*) FROM receipts WHERE tenant_id = :t AND status = 'draft' AND receipt_date BETWEEN :s AND :e")
    if n:
        items.append({"kind": "receipt_drafts", "count": n, "text": f"{n} Belegentwurf/-entwürfe aus dem Zeitraum noch nicht gebucht"})
    n = await count("SELECT count(*) FROM invoices WHERE tenant_id = :t AND status = 'draft' "
                    "AND COALESCE(service_date_to, service_date_from) BETWEEN :s AND :e")
    if n:
        items.append({"kind": "invoice_drafts", "count": n,
                      "text": f"{n} Rechnungsentwurf/-entwürfe für Leistungen im Zeitraum nicht ausgestellt"})
    n = await count("SELECT count(*) FROM bank_transactions WHERE tenant_id = :t AND status = 'open' AND booked_on BETWEEN :s AND :e")
    if n:
        items.append({"kind": "bank_open", "count": n, "text": f"{n} Bankumsatz/-umsätze im Zeitraum nicht zugeordnet"})
    n = await count("SELECT count(*) FROM receipt_files f WHERE f.tenant_id = :t AND f.uploaded_at::date BETWEEN :s AND :e "
                    "AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.file_id = f.id)")
    if n:
        items.append({"kind": "files_unassigned", "count": n, "text": f"{n} Belegdatei(en) aus dem Zeitraum ohne Beleg"})
    return items
