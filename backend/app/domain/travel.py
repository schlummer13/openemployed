"""Reisekosten im Inland (§ 9 Abs. 4a EStG, Stand 2026): Verpflegungspauschalen, Kürzung bei Mahlzeiten, Kilometerpauschale.

- Eintägige Reise: 14 € bei mehr als 8 Stunden Abwesenheit, sonst 0 €.
- Mehrtägige Reise: An- und Abreisetag je 14 € (ohne Mindestdauer), jeder volle Tag dazwischen 28 €.
- Kürzung bei gestellten Mahlzeiten: Frühstück 20 %, Mittag-/Abendessen je 40 % von 28 € (5,60 € / 11,20 €);
  die Kürzung kann die Pauschale höchstens auf 0 € senken.
- Fahrt mit dem privaten Pkw: 0,30 € je gefahrenem Kilometer (§ 9 Abs. 1 Nr. 4a EStG).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PARTIAL_DAY = Decimal("14.00")
FULL_DAY = Decimal("28.00")
BREAKFAST_CUT = Decimal("5.60")
MEAL_CUT = Decimal("11.20")
KM_RATE = Decimal("0.30")


class TravelError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TravelResult:
    days: int
    gross_meals: Decimal
    cuts: Decimal
    meals: Decimal
    km_amount: Decimal
    other: Decimal
    total: Decimal


def calculate(start: dt.datetime, end: dt.datetime, *, km: Decimal = Decimal(0), breakfasts: int = 0, lunches: int = 0,
              dinners: int = 0, other: Decimal = Decimal(0)) -> TravelResult:
    if end <= start:
        raise TravelError("Ende der Reise liegt vor dem Beginn")
    days = (end.date() - start.date()).days + 1
    if days == 1:
        gross = PARTIAL_DAY if (end - start) > dt.timedelta(hours=8) else Decimal("0.00")
    else:
        gross = 2 * PARTIAL_DAY + (days - 2) * FULL_DAY
    cuts = breakfasts * BREAKFAST_CUT + (lunches + dinners) * MEAL_CUT
    meals = max(Decimal("0.00"), gross - cuts)
    km_amount = (km * KM_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    other = other.quantize(Decimal("0.01"))
    return TravelResult(days, gross, min(cuts, gross), meals, km_amount, other, meals + km_amount + other)
