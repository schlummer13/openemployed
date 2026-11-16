import datetime as dt
import itertools
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.contracts import (
    ContractRuleError,
    add_months,
    billing_periods,
    earliest_end,
    initial_term_end,
    monthly_value,
    validate_terms,
)

D = dt.date


def test_add_months_clamps_to_month_end() -> None:
    assert add_months(D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert add_months(D(2028, 1, 31), 1) == D(2028, 2, 29)
    assert add_months(D(2026, 11, 15), 3) == D(2027, 2, 15)


def test_initial_term_end() -> None:
    assert initial_term_end(D(2026, 12, 1), 12) == D(2027, 11, 30)
    assert initial_term_end(D(2026, 12, 1), 0) is None


def test_earliest_end_within_minimum_term() -> None:
    # 12 Monate ab 01.12.2026, 3 Monate Frist; Kündigung im Januar → Ende der Mindestlaufzeit
    assert earliest_end(start=D(2026, 12, 1), min_term_months=12, notice_months=3, renewal="monthly",
                        notice_received=D(2027, 1, 15)) == D(2027, 11, 30)


def test_earliest_end_after_minimum_term_monthly() -> None:
    # nach Mindestlaufzeit: Frist 1 Monat zum Monatsende
    assert earliest_end(start=D(2026, 1, 1), min_term_months=12, notice_months=1, renewal="monthly",
                        notice_received=D(2027, 3, 10)) == D(2027, 4, 30)


def test_earliest_end_with_term_renewal() -> None:
    # Verlängerung um jeweils 12 Monate; Kündigung zu spät für 1. Laufzeitende → Ende 2. Periode
    assert earliest_end(start=D(2026, 1, 1), min_term_months=12, notice_months=3, renewal="term",
                        notice_received=D(2026, 11, 1)) == D(2027, 12, 31)
    assert earliest_end(start=D(2026, 1, 1), min_term_months=12, notice_months=3, renewal="term",
                        notice_received=D(2026, 9, 1)) == D(2026, 12, 31)


def test_consumer_rules() -> None:
    validate_terms(customer_type="b2c", min_term_months=0, notice_months=1, renewal="monthly")
    for kwargs in ({"min_term_months": 25, "notice_months": 1, "renewal": "monthly"},
                   {"min_term_months": 12, "notice_months": 3, "renewal": "monthly"},
                   {"min_term_months": 12, "notice_months": 1, "renewal": "term"}):
        with pytest.raises(ContractRuleError, match="Verbraucher"):
            validate_terms(customer_type="b2c", **kwargs)  # type: ignore[arg-type]
    validate_terms(customer_type="b2b", min_term_months=36, notice_months=6, renewal="term")


def test_billing_periods_monthly_advance_with_proration() -> None:
    periods = billing_periods(start=D(2026, 11, 16), end=None, interval="monthly", mode="advance", until=D(2027, 1, 31))
    assert [(p.start, p.end, p.factor, p.bill_on) for p in periods] == [
        (D(2026, 11, 16), D(2026, 11, 30), Decimal("0.5"), D(2026, 11, 16)),
        (D(2026, 12, 1), D(2026, 12, 31), Decimal(1), D(2026, 12, 1)),
        (D(2027, 1, 1), D(2027, 1, 31), Decimal(1), D(2027, 1, 1)),
    ]


def test_billing_periods_arrears_and_end() -> None:
    periods = billing_periods(start=D(2026, 10, 1), end=D(2026, 12, 15), interval="monthly", mode="arrears",
                              until=D(2027, 6, 1))
    assert [(p.start, p.end, p.bill_on) for p in periods][-1] == (D(2026, 12, 1), D(2026, 12, 15), D(2026, 12, 16))
    assert periods[-1].factor == Decimal("0.4839")  # 15/31


def test_quarterly_and_yearly_alignment() -> None:
    q = billing_periods(start=D(2026, 11, 1), end=None, interval="quarterly", mode="advance", until=D(2027, 1, 1))
    assert (q[0].start, q[0].end, q[0].factor) == (D(2026, 11, 1), D(2026, 12, 31), Decimal("0.6630"))
    assert (q[1].start, q[1].end) == (D(2027, 1, 1), D(2027, 3, 31))
    y = billing_periods(start=D(2026, 1, 1), end=None, interval="yearly", mode="advance", until=D(2026, 12, 31))
    assert len(y) == 1 and y[0].end == D(2026, 12, 31)


def test_monthly_value() -> None:
    assert monthly_value(Decimal("1200.00"), "yearly") == Decimal("100.00")
    assert monthly_value(Decimal("100.00"), "quarterly") == Decimal("33.33")


@settings(max_examples=200, deadline=None)
@given(st.dates(min_value=D(2026, 1, 1), max_value=D(2030, 12, 31)), st.integers(0, 400),
       st.sampled_from(["monthly", "quarterly", "yearly"]))
def test_periods_cover_contract_without_gaps(start: dt.date, length: int, interval: str) -> None:
    end = start + dt.timedelta(days=length)
    periods = billing_periods(start=start, end=end, interval=interval, mode="arrears", until=D(2040, 1, 1))  # type: ignore[arg-type]
    assert periods[0].start == start and periods[-1].end == end
    for a, b in itertools.pairwise(periods):
        assert b.start == a.end + dt.timedelta(days=1)
    assert all(Decimal(0) < p.factor <= 1 for p in periods)
