import datetime as dt
from decimal import Decimal

import pytest

from app.domain.tax_calendar import deadlines, easter_sunday, holidays_nrw, next_business_day
from app.domain.tax_rates import estimate_income_taxes, kst_rate
from app.services.tax_report import PeriodSpecError, months_for, truncate_euro

D = Decimal


def test_income_taxes_from_spreadsheet_example() -> None:
    """Zahlen aus der bisherigen Kostentabelle: 42.192,00 € Erlöse, 5.355,84 € Kosten, Düsseldorf 440 %."""
    e = estimate_income_taxes(year=2026, revenue=D("42192.00"), expense=D("5355.84"), deductible=D("5355.84"),
                              trade_tax_multiplier=D(440), is_ug=True)
    assert e.profit_before_tax == D("36836.16")
    assert e.kst == D("5525.42")          # 15 %
    assert e.solz == D("303.90")          # 5,5 % der KSt
    assert e.gewst_base == D("36800")     # auf volle 100 € abgerundet (Tabelle rechnete ohne Abrundung)
    assert e.gewst == D("5667.20")        # 36.800 × 3,5 % × 440 %
    assert e.total == D("11496.52")
    assert e.ug_reserve == D("6334.91")   # 25 % des Gewinns nach Steuern


def test_loss_means_no_tax_and_no_reserve() -> None:
    e = estimate_income_taxes(year=2026, revenue=D("100"), expense=D("500"), deductible=D("500"),
                              trade_tax_multiplier=D(440), is_ug=True)
    assert (e.kst, e.solz, e.gewst, e.ug_reserve) == (0, 0, 0, 0)


def test_non_deductible_expenses_increase_taxable_income() -> None:
    e = estimate_income_taxes(year=2026, revenue=D("1000"), expense=D("100"), deductible=D("70"),
                              trade_tax_multiplier=D(440), is_ug=False)
    assert e.taxable_income == D("930") and e.profit_before_tax == D("900") and e.ug_reserve == 0


def test_kst_rates_by_year() -> None:
    assert [kst_rate(y) for y in (2026, 2027, 2028, 2031, 2032, 2040)] == [15, 15, 14, 11, 10, 10]


def test_easter_and_nrw_holidays() -> None:
    assert easter_sunday(2026) == dt.date(2026, 4, 5)
    assert easter_sunday(2027) == dt.date(2027, 3, 28)
    h = holidays_nrw(2026)
    assert dt.date(2026, 6, 4) in h          # Fronleichnam 2026
    assert dt.date(2026, 11, 1) in h         # Allerheiligen
    assert dt.date(2026, 4, 3) in h          # Karfreitag


def test_deadline_shift_to_business_day() -> None:
    assert next_business_day(dt.date(2027, 1, 10)) == dt.date(2027, 1, 11)   # Sonntag → Montag
    assert next_business_day(dt.date(2026, 5, 1)) == dt.date(2026, 5, 4)     # Feiertag (Fr) → Montag


def test_quarterly_deadlines_with_and_without_extension() -> None:
    q = {d.period: d.due for d in deadlines(2026, filing="quarterly", extension=False) if d.kind == "ustva"}
    assert q["2026-Q4"] == dt.date(2027, 1, 11)
    assert q["2026-Q1"] == dt.date(2026, 4, 10)
    ext = {d.period: d.due for d in deadlines(2026, filing="quarterly", extension=True) if d.kind == "ustva"}
    assert ext["2026-Q1"] == dt.date(2026, 5, 11)       # 10.05. ist Sonntag
    monthly = deadlines(2026, filing="monthly", extension=True)
    assert any(d.kind == "svz" for d in monthly)
    assert len([d for d in monthly if d.kind == "ustva"]) == 12


def test_period_specs() -> None:
    assert months_for("2026-11") == ["2026-11"]
    assert months_for("2026-Q4") == ["2026-10", "2026-11", "2026-12"]
    for bad in ("2026-13", "2026-Q5", "26-11", "2026-1"):
        with pytest.raises(PeriodSpecError):
            months_for(bad)


def test_truncate_euro_toward_zero() -> None:
    assert truncate_euro(D("1758.99")) == D("1758")
    assert truncate_euro(D("-1758.99")) == D("-1758")


def test_loss_carryforward_reduces_taxes_and_ug_reserve() -> None:
    base = estimate_income_taxes(year=2027, revenue=D("100000"), expense=D("40000"), deductible=D("40000"),
                                 trade_tax_multiplier=D("440"), is_ug=True)
    with_loss = estimate_income_taxes(year=2027, revenue=D("100000"), expense=D("40000"), deductible=D("40000"),
                                      trade_tax_multiplier=D("440"), is_ug=True, tax_loss_carryforward=D("20000"),
                                      commercial_loss_carryforward=D("20000"))
    assert with_loss.tax_loss_used == D("20000") and with_loss.taxable_income == D("40000")
    assert with_loss.kst < base.kst and with_loss.gewst < base.gewst
    # UG-Rücklage: 25 % des um den Verlustvortrag geminderten Jahresüberschusses (§ 5a Abs. 3 GmbHG)
    assert with_loss.ug_reserve == ((with_loss.profit_after_tax - D("20000")) * D("0.25")).quantize(D("0.01"))
    small = estimate_income_taxes(year=2027, revenue=D("10000"), expense=D("0"), deductible=D("0"), trade_tax_multiplier=D("440"),
                                  is_ug=True, tax_loss_carryforward=D("50000"), commercial_loss_carryforward=D("50000"))
    assert small.kst == 0 and small.ug_reserve == 0 and small.tax_loss_used == D("10000")


def test_minimum_taxation_above_one_million() -> None:
    e = estimate_income_taxes(year=2027, revenue=D("3000000"), expense=D("0"), deductible=D("0"), trade_tax_multiplier=D("440"),
                              is_ug=False, tax_loss_carryforward=D("5000000"))
    assert e.tax_loss_used == D("1000000") + D("2000000") * D("0.70")
