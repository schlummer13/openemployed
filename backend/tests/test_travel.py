"""Reisekosten-Pauschalen (Inland)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.travel import TravelError, calculate

D = Decimal


def t(day: int, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, day, h, m)  # noqa: DTZ001 – Ortszeit der Reise (Wanduhr), keine Zeitzonenrechnung


def test_one_day_trip_needs_more_than_eight_hours() -> None:
    assert calculate(t(10, 8), t(10, 16)).meals == D("0.00"), "genau 8 Stunden reichen nicht"
    r = calculate(t(10, 7), t(10, 18), km=D("312"))
    assert r.meals == D("14.00") and r.km_amount == D("93.60") and r.total == D("107.60")


def test_multi_day_trip_with_meals() -> None:
    r = calculate(t(10, 15), t(12, 11), breakfasts=2, lunches=0, dinners=1, other=D("12.50"))
    assert r.days == 3 and r.gross_meals == D("56.00"), "An- und Abreisetag je 14 €, ein voller Tag 28 €"
    assert r.cuts == D("22.40") and r.meals == D("33.60") and r.total == D("46.10")


def test_cuts_never_below_zero_and_errors() -> None:
    assert calculate(t(10, 6), t(10, 20), breakfasts=1, lunches=1, dinners=1).meals == D("0.00")
    with pytest.raises(TravelError):
        calculate(t(10, 12), t(10, 9))
