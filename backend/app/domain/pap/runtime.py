"""Laufzeit für die aus dem amtlichen Programmablaufplan (PAP, ITZBund/BMF) erzeugten Lohnsteuer-Klassen.

Der PAP ist in Java-Syntax mit ``java.math.BigDecimal`` formuliert. ``BD`` bildet genau die dort genutzten Methoden nach
(exakte Addition/Multiplikation, Division mit Skala und Rundungsart, ``setScale``, ``compareTo``, ``longValue``), sodass die
erzeugten Ausdrücke unverändert aus dem PAP übernommen werden können.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext
from typing import Any

PREC = 80


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


class BD(Decimal):
    """java.math.BigDecimal-Teilmenge (exakt, mit hoher Rechengenauigkeit)."""

    def add(self, o: Any) -> BD:
        with localcontext() as c:
            c.prec = PREC
            return BD(Decimal.__add__(self, _d(o)))

    def subtract(self, o: Any) -> BD:
        with localcontext() as c:
            c.prec = PREC
            return BD(Decimal.__sub__(self, _d(o)))

    def multiply(self, o: Any) -> BD:
        with localcontext() as c:
            c.prec = PREC
            return BD(Decimal.__mul__(self, _d(o)))

    def divide(self, o: Any, scale: int | None = None, mode: str | None = None) -> BD:
        with localcontext() as c:
            c.prec = PREC
            q = Decimal.__truediv__(self, _d(o))
            if scale is not None:
                q = q.quantize(Decimal(1).scaleb(-scale), rounding=mode or ROUND_DOWN)
            return BD(q)

    def setScale(self, scale: int, mode: str = ROUND_DOWN) -> BD:
        with localcontext() as c:
            c.prec = PREC
            return BD(self.quantize(Decimal(1).scaleb(-scale), rounding=mode))

    def compareTo(self, o: Any) -> int:
        other = _d(o)
        return (Decimal.__gt__(self, other)) - (Decimal.__lt__(self, other))

    def longValue(self) -> int:
        return int(self)


class BigDecimal:
    ZERO = BD(0)
    ONE = BD(1)
    TEN = BD(10)
    ROUND_DOWN = ROUND_DOWN
    ROUND_UP = ROUND_UP

    @staticmethod
    def valueOf(v: Any) -> BD:
        return BD(str(v))
