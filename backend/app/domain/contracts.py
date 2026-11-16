"""Vertragslogik: Laufzeit, Kündigung, Abrechnungszeiträume, anteilige Abrechnung (docs/konzept/02 §3, 14 §3.2).

Verbraucherverträge (B2C, § 309 Nr. 9 BGB): Erstlaufzeit höchstens 24 Monate, Kündigungsfrist höchstens 1 Monat,
stillschweigende Verlängerung nur auf unbestimmte Zeit mit monatlicher Kündbarkeit.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Renewal = Literal["none", "monthly", "term"]
Interval = Literal["monthly", "quarterly", "yearly"]
BillingMode = Literal["advance", "arrears"]
INTERVAL_MONTHS: dict[str, int] = {"monthly": 1, "quarterly": 3, "yearly": 12}


class ContractRuleError(ValueError):
    pass


def add_months(day: dt.date, months: int) -> dt.date:
    """Monatsarithmetik mit Begrenzung auf das Monatsende (31.01. + 1 Monat = 28./29.02.)."""
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_end(day: dt.date) -> dt.date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def validate_terms(*, customer_type: str, min_term_months: int, notice_months: int, renewal: Renewal) -> None:
    if min_term_months < 0 or min_term_months > 120:
        raise ContractRuleError("Mindestlaufzeit 0–120 Monate")
    if notice_months < 0 or notice_months > 24:
        raise ContractRuleError("Kündigungsfrist 0–24 Monate")
    if customer_type == "b2c":
        if min_term_months > 24:
            raise ContractRuleError("Verbraucher: Erstlaufzeit höchstens 24 Monate (§ 309 Nr. 9 BGB)")
        if notice_months > 1:
            raise ContractRuleError("Verbraucher: Kündigungsfrist höchstens 1 Monat (§ 309 Nr. 9 BGB)")
        if renewal == "term":
            raise ContractRuleError("Verbraucher: Verlängerung nur auf unbestimmte Zeit mit monatlicher Kündbarkeit")


def initial_term_end(start: dt.date, min_term_months: int) -> dt.date | None:
    if min_term_months == 0:
        return None
    return add_months(start, min_term_months) - dt.timedelta(days=1)


def earliest_end(*, start: dt.date, min_term_months: int, notice_months: int, renewal: Renewal,
                 notice_received: dt.date) -> dt.date:
    """Frühestmögliches Vertragsende bei Zugang der Kündigung am ``notice_received``.

    - Kündigungsfrist läuft „zum Monatsende“ (Ende frühestens am Monatsende nach Ablauf der Frist).
    - Während der Mindestlaufzeit: frühestens zum Ende der Mindestlaufzeit.
    - Verlängerung „term“: nur zum Ende einer (verlängerten) Laufzeitperiode.
    """
    if notice_received < start:
        notice_received = start
    earliest = month_end(add_months(notice_received, notice_months)) if notice_months else notice_received
    term_end = initial_term_end(start, min_term_months)
    if term_end is None:
        return earliest
    if renewal in ("none", "monthly"):
        return max(earliest, term_end)
    # renewal == "term": Laufzeit verlängert sich jeweils um die Mindestlaufzeit
    k = 1
    candidate = term_end
    while candidate < earliest:
        k += 1
        candidate = add_months(start, k * min_term_months) - dt.timedelta(days=1)
    return candidate


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    start: dt.date
    end: dt.date
    factor: Decimal          # 1 = voller Zeitraum; < 1 anteilig (tagesgenau)
    bill_on: dt.date         # Tag, an dem die Rechnung erstellt wird


def _period_bounds(anchor: dt.date, interval: Interval) -> tuple[dt.date, dt.date]:
    """Kalenderausgerichteter Zeitraum (Monat/Quartal/Jahr), der ``anchor`` enthält."""
    months = INTERVAL_MONTHS[interval]
    first_month = (anchor.month - 1) // months * months + 1
    start = dt.date(anchor.year, first_month, 1)
    return start, add_months(start, months) - dt.timedelta(days=1)


def billing_periods(*, start: dt.date, end: dt.date | None, interval: Interval, mode: BillingMode,
                    until: dt.date) -> list[BillingPeriod]:
    """Alle Abrechnungszeiträume, deren Rechnungstag ≤ ``until`` liegt.

    Zeiträume sind kalenderausgerichtet; Beginn/Ende mitten im Zeitraum werden tagesgenau anteilig berechnet.
    Vorauszahlung: Rechnung am ersten Leistungstag des Zeitraums. Nachträglich: am Tag nach Zeitraumende.
    """
    periods: list[BillingPeriod] = []
    cursor = start
    while end is None or cursor <= end:
        p_start, p_end = _period_bounds(cursor, interval)
        s = max(p_start, start)
        e = min(p_end, end) if end else p_end
        full_days = (p_end - p_start).days + 1
        days = (e - s).days + 1
        factor = (Decimal(days) / Decimal(full_days)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        bill_on = s if mode == "advance" else e + dt.timedelta(days=1)
        if bill_on > until:
            break
        periods.append(BillingPeriod(s, e, Decimal(1) if days == full_days else factor, bill_on))
        cursor = p_end + dt.timedelta(days=1)
        if len(periods) > 1200:  # pragma: no cover – Schutz gegen Endlosschleifen
            break
    return periods


def monthly_value(amount_per_interval: Decimal, interval: Interval) -> Decimal:
    """Normierung auf Monat (MRR)."""
    return (amount_per_interval / INTERVAL_MONTHS[interval]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
