"""Steuerkalender: gesetzliche Fristen mit Verschiebung auf den nächsten Werktag (§ 108 Abs. 3 AO, Feiertage NRW)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

VatFiling = Literal["monthly", "quarterly"]


def easter_sunday(year: int) -> dt.date:
    """Gaußsche Osterformel (gregorianisch, anonymer Algorithmus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def holidays_nrw(year: int) -> set[dt.date]:
    easter = easter_sunday(year)
    return {
        dt.date(year, 1, 1),                       # Neujahr
        easter - dt.timedelta(days=2),             # Karfreitag
        easter + dt.timedelta(days=1),             # Ostermontag
        dt.date(year, 5, 1),                       # Tag der Arbeit
        easter + dt.timedelta(days=39),            # Christi Himmelfahrt
        easter + dt.timedelta(days=50),            # Pfingstmontag
        easter + dt.timedelta(days=60),            # Fronleichnam (NRW)
        dt.date(year, 10, 3),                      # Tag der Deutschen Einheit
        dt.date(year, 11, 1),                      # Allerheiligen (NRW)
        dt.date(year, 12, 25),
        dt.date(year, 12, 26),
    }


def next_business_day(day: dt.date) -> dt.date:
    while day.weekday() >= 5 or day in holidays_nrw(day.year):
        day += dt.timedelta(days=1)
    return day


@dataclass(frozen=True, slots=True)
class Deadline:
    due: dt.date
    kind: str
    label: str
    period: str | None = None


def _month_end_plus(year: int, month: int, day: int, extra_months: int) -> dt.date:
    total = month + extra_months
    y, m = year + (total - 1) // 12, (total - 1) % 12 + 1
    return dt.date(y, m, day)


def vat_periods(year: int, filing: VatFiling) -> list[tuple[str, list[int]]]:
    if filing == "monthly":
        return [(f"{year}-{m:02d}", [m]) for m in range(1, 13)]
    return [(f"{year}-Q{q}", [3 * q - 2, 3 * q - 1, 3 * q]) for q in range(1, 5)]


def deadlines(year: int, *, filing: VatFiling, extension: bool) -> list[Deadline]:
    """Fristen eines Jahres: UStVA (+ ggf. Dauerfristverlängerung), Sondervorauszahlung, KSt-/GewSt-Vorauszahlungen."""
    result: list[Deadline] = []
    for label, months in vat_periods(year, filing):
        last = months[-1]
        due = _month_end_plus(year, last, 10, 1 + (1 if extension else 0))
        result.append(Deadline(next_business_day(due), "ustva", f"Umsatzsteuer-Voranmeldung {label}", label))
    if extension and filing == "monthly":
        result.append(Deadline(next_business_day(dt.date(year, 2, 10)), "svz", "Sondervorauszahlung (1/11 des Vorjahres)"))
    for month in (3, 6, 9, 12):
        result.append(Deadline(next_business_day(dt.date(year, month, 10)), "kst",
                               f"Körperschaftsteuer-Vorauszahlung Q{month // 3} (falls festgesetzt)"))
    for month in (2, 5, 8, 11):
        result.append(Deadline(next_business_day(dt.date(year, month, 15)), "gewst",
                               f"Gewerbesteuer-Vorauszahlung Q{(month + 1) // 3} (falls festgesetzt)"))
    return sorted(result, key=lambda d: d.due)
