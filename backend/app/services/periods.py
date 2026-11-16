"""Voranmeldungszeiträume und Periodensperre."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from app.services.ledger_client import LedgerClient


def month_of(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def next_month(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year + month // 12:04d}-{month % 12 + 1:02d}"


async def resolve_vat_period(ledger: LedgerClient, tenant_id: UUID, natural: dt.date) -> str:
    """Monat des Vorgangs; ist er bereits gesperrt (gemeldet), der nächste offene Monat."""
    locked = set(await ledger.locked_periods(tenant_id=tenant_id))
    period = month_of(natural)
    while period in locked:
        period = next_month(period)
    return period
