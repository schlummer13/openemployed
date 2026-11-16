"""Abschreibungsregeln (reine Rechenlogik)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.assets import Asset, AssetError, accumulated_until, suggest_method, validate, year_result

D = Decimal


def test_linear_pro_rata_and_exact_total() -> None:
    # Büromöbel 13 Jahre, angeschafft im Oktober: 3 Monate im ersten Jahr (Okt–Dez)
    a = Asset("linear", dt.date(2026, 10, 15), D("1300.00"), 156)
    r = year_result(a, 2026)
    assert r.depreciation == D("25.00") and r.book_value_end == D("1275.00")
    assert year_result(a, 2027).depreciation == D("100.00")
    total = sum((year_result(a, y).depreciation for y in range(2026, 2041)), D(0))
    assert total == D("1300.00"), "Summe aller Jahre = Anschaffungskosten"
    assert year_result(a, 2040).depreciation == 0


def test_linear_rounding_odd_amounts() -> None:
    a = Asset("linear", dt.date(2026, 1, 1), D("1000.00"), 36)
    years = [year_result(a, y).depreciation for y in (2026, 2027, 2028, 2029)]
    assert years == [D("333.33"), D("333.34"), D("333.33"), D("0.00")] and sum(years, D(0)) == D("1000.00")


def test_disposal_writes_off_remaining_book_value() -> None:
    a = Asset("linear", dt.date(2026, 1, 1), D("1200.00"), 60, disposal_date=dt.date(2027, 3, 10))
    r = year_result(a, 2027)
    assert r.depreciation == D("60.00"), "Jan–Mär 2027 (3 Monate à 20 €)"
    assert r.disposal_book_value == D("900.00") and r.book_value_end == 0
    assert year_result(a, 2028).depreciation == 0 and year_result(a, 2028).disposal_book_value == 0


def test_digital_full_and_gwg_in_year_of_acquisition() -> None:
    laptop = Asset("digital_full", dt.date(2026, 11, 20), D("2199.00"), 12)
    assert year_result(laptop, 2026).depreciation == D("2199.00") and year_result(laptop, 2027).depreciation == 0
    chair = Asset("gwg", dt.date(2026, 5, 1), D("640.00"), 156)
    assert year_result(chair, 2026).depreciation == D("640.00")


def test_pool_twenty_percent_regardless_of_month_and_disposal() -> None:
    p = Asset("pool", dt.date(2026, 12, 30), D("900.00"), 60, disposal_date=dt.date(2027, 6, 1))
    assert [year_result(p, y).depreciation for y in range(2026, 2032)] == [D("180.00")] * 5 + [D("0.00")]
    assert year_result(p, 2027).disposal_book_value == 0, "Sammelposten bleibt bei Abgang bestehen"
    assert accumulated_until(p, 2030) == D("900.00")


def test_rules_and_suggestions() -> None:
    with pytest.raises(AssetError, match="Sammelposten"):
        validate("pool", D("1200.00"))
    with pytest.raises(AssetError, match="800"):
        validate("gwg", D("900.00"))
    assert suggest_method(D("600"), "furniture") == "gwg"
    assert suggest_method(D("2199"), "digital") == "digital_full"
    assert suggest_method(D("2199"), "furniture") == "linear"
