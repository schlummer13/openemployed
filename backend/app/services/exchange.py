"""Wechselkurse: EZB-Referenzkurse (täglich, Fremdwährung je 1 EUR) mit manueller Eingabe als Rückfall."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx
from lxml import etree
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.masterdata import ConflictError

ECB_90D = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"


def parse_ecb(xml: bytes) -> list[tuple[str, dt.date, Decimal]]:
    root = etree.fromstring(xml)
    out = []
    for day in root.iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
        if day.get("time"):
            d = dt.date.fromisoformat(day.get("time"))
            for c in day:
                out.append((c.get("currency"), d, Decimal(c.get("rate"))))
    return out


async def fetch_ecb(conn: AsyncConnection, client: httpx.AsyncClient | None = None) -> int:
    own = client is None
    http = client or httpx.AsyncClient(timeout=20)
    try:
        r = await http.get(ECB_90D)
        r.raise_for_status()
    finally:
        if own:
            await http.aclose()
    rows = parse_ecb(r.content)
    for cur, d, rate in rows:
        await conn.execute(text("INSERT INTO exchange_rates (currency, rate_date, rate) VALUES (:c, :d, :r) "
                                "ON CONFLICT (currency, rate_date) DO NOTHING"), {"c": cur, "d": d, "r": rate})
    return len(rows)


async def set_rate(conn: AsyncConnection, currency: str, on: dt.date, rate: Decimal) -> None:
    if rate <= 0:
        raise ConflictError("Kurs muss positiv sein")
    await conn.execute(text("INSERT INTO exchange_rates (currency, rate_date, rate, source) VALUES (:c, :d, :r, 'manuell') "
                            "ON CONFLICT (currency, rate_date) DO UPDATE SET rate = :r, source = 'manuell'"),
                       {"c": currency, "d": on, "r": rate})


async def rate_for(conn: AsyncConnection, currency: str, on: dt.date) -> Decimal | None:
    if currency == "EUR":
        return None
    row = (await conn.execute(text("SELECT rate, rate_date FROM exchange_rates WHERE currency = :c AND rate_date <= :d "
                                   "ORDER BY rate_date DESC LIMIT 1"), {"c": currency, "d": on})).one_or_none()
    if row is None or (on - row[1]).days > 7:
        raise ConflictError(f"Kein aktueller EZB-Kurs für {currency} – unter Einstellungen → Währungen abrufen oder eintragen")
    return Decimal(row[0])


async def latest(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = await conn.execute(text("SELECT DISTINCT ON (currency) currency, rate_date, rate, source FROM exchange_rates "
                                   "ORDER BY currency, rate_date DESC"))
    return [dict(r) for r in rows.mappings()]
