"""Geldbeträge: ausschließlich ``Decimal``, kaufmännische Rundung, keine Fließkommazahlen.

- Beträge (Positionen, Steuern, Summen): 2 Nachkommastellen, ROUND_HALF_UP
  (bei negativen Beträgen symmetrisch: -0,005 → -0,01)
- Preise und Mengen: bis zu 4 Nachkommastellen
- API-Eingaben werden als String oder Ganzzahl akzeptiert, JSON-Fließkommazahlen werden abgelehnt,
  weil z. B. 0.1 als float nicht exakt darstellbar ist.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

CENT = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0001")
ZERO = Decimal("0.00")

# höchstens 12 Vorkommastellen (bis < 1 Billion), höchstens 4 Nachkommastellen, Punkt als Trenner
_DECIMAL_RE = re.compile(r"^-?(0|[1-9]\d{0,11})(\.\d{1,4})?$")


def round_cent(value: Decimal) -> Decimal:
    """Kaufmännisch auf Cent runden."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # NaN/Infinity
        raise ValueError("ungültige Zahl")
    return max(0, -exponent)


def parse_strict_decimal(value: Any) -> Decimal:
    """Wandelt API-Eingaben streng in ``Decimal``.

    Erlaubt: ``"1758.00"``, ``"-5"``, ``12`` (int), ``Decimal``.
    Verboten: float, bool, Komma als Dezimaltrenner, Exponent, NaN/Infinity, mehr als 4 Nachkommastellen.
    """
    if isinstance(value, (bool, float)):
        raise ValueError("Zahl bitte als String übergeben (z. B. \"1758.00\"), keine Fließkommazahl")
    if isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_RE.fullmatch(text):
            raise ValueError("ungültiger Betrag – Format z. B. \"1758.00\" (Punkt, max. 4 Nachkommastellen)")
        result = Decimal(text)
    else:
        raise ValueError("Betrag muss String oder Ganzzahl sein")
    if not result.is_finite():
        raise ValueError("ungültiger Betrag")
    if decimal_places(result) > 4:
        raise ValueError("höchstens 4 Nachkommastellen")
    if abs(result) >= Decimal(10) ** 12:
        raise ValueError("Betrag zu groß")
    return result


def _serialize(value: Decimal) -> str:
    return format(value, "f")


StrictDecimal = Annotated[
    Decimal,
    BeforeValidator(parse_strict_decimal),
    PlainSerializer(_serialize, return_type=str, when_used="json"),
]
"""Pydantic-Typ für Beträge, Preise und Mengen in der API."""

MoneyOut = Annotated[Decimal, PlainSerializer(_serialize, return_type=str, when_used="json")]
"""Ausgabe-Typ: immer als String mit festen Nachkommastellen (z. B. "2092.02")."""
