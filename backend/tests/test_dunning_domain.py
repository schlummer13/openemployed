"""Verzugszinsen, § 367-Verrechnung, Verzugsbeginn, Mahnstufen."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.dunning import BaseRate, Cost, Payment, claim_state, default_start, next_level

RATES = [BaseRate(dt.date(2025, 1, 1), D("2.27")), BaseRate(dt.date(2025, 7, 1), D("1.27")),
         BaseRate(dt.date(2026, 1, 1), D("1.27"))]
day = dt.date


def test_interest_b2b_simple() -> None:
    # 1.000 € × 10,27 % × 30/365 = 8,44 €
    s = claim_state(principal=D("1000.00"), default_from=day(2026, 3, 1), as_of=day(2026, 3, 30), payments=[], costs=[],
                    rates=RATES, b2b=True)
    assert s.interest_open == D("8.44") and s.principal_open == D("1000.00")


def test_interest_b2c_and_rate_change_split() -> None:
    # 20.06.–09.07.2025: 11 Tage zu 7,27 %, 9 Tage zu 6,27 %
    s = claim_state(principal=D("3650.00"), default_from=day(2025, 6, 20), as_of=day(2025, 7, 9), payments=[], costs=[],
                    rates=RATES, b2b=False)
    expected = D("3650") * D("7.27") / 100 * 11 / 365 + D("3650") * D("6.27") / 100 * 9 / 365
    assert s.interest_open == expected.quantize(D("0.01"))
    assert [seg[2] for seg in s.rate_segments] == [D("7.27"), D("6.27")]


def test_no_interest_before_default() -> None:
    s = claim_state(principal=D("500"), default_from=None, as_of=day(2026, 5, 1), payments=[], costs=[], rates=RATES, b2b=True)
    assert s.interest_open == 0 and s.total_open == D("500")


def test_section_367_order_costs_interest_principal() -> None:
    s = claim_state(principal=D("1000.00"), default_from=day(2026, 3, 1), as_of=day(2026, 3, 31),
                    payments=[Payment(day(2026, 3, 31), D("100.00"))], costs=[Cost(day(2026, 3, 15), D("40.00"))],
                    rates=RATES, b2b=True)
    # Kosten 40 → Zinsen (30 Tage ≈ 8,44) → Rest auf Hauptforderung
    assert s.costs_open == 0
    # am Zahlungstag selbst laufen Zinsen nur noch auf die verbleibende Hauptforderung
    assert s.interest_open == (D("948.44") * D("10.27") / 100 / 365).quantize(D("0.01"))
    assert s.principal_open == D("1000.00") - (D("100.00") - D("40.00") - D("8.44"))


def test_full_payment_before_default_settles() -> None:
    s = claim_state(principal=D("119.00"), default_from=day(2026, 4, 1), as_of=day(2026, 5, 1),
                    payments=[Payment(day(2026, 3, 20), D("119.00"))], costs=[], rates=RATES, b2b=True)
    assert s.settled and s.interest_total == 0


def test_overpayment_is_credit() -> None:
    s = claim_state(principal=D("100"), default_from=None, as_of=day(2026, 5, 1),
                    payments=[Payment(day(2026, 4, 1), D("150"))], costs=[], rates=RATES, b2b=True)
    assert s.principal_open == D("-50") and s.settled


def test_default_start_rules() -> None:
    due = day(2026, 3, 1)
    assert default_start(due_date=due, b2b=True, b2c_notice_on_invoice=False, first_reminder_received=None) == day(2026, 4, 1)
    assert default_start(due_date=due, b2b=False, b2c_notice_on_invoice=False, first_reminder_received=None) is None
    assert default_start(due_date=due, b2b=False, b2c_notice_on_invoice=False,
                         first_reminder_received=day(2026, 3, 8)) == day(2026, 3, 9)
    assert default_start(due_date=due, b2b=True, b2c_notice_on_invoice=False,
                         first_reminder_received=day(2026, 3, 8)) == day(2026, 3, 9)


def test_levels() -> None:
    due = day(2026, 3, 1)
    assert next_level(due_date=due, today=day(2026, 3, 7), last_level=0, last_notice_on=None) is None
    assert next_level(due_date=due, today=day(2026, 3, 8), last_level=0, last_notice_on=None) == 1
    assert next_level(due_date=due, today=day(2026, 3, 22), last_level=1, last_notice_on=day(2026, 3, 8)) == 2
    # Mindestabstand 7 Tage, auch wenn die Stufe nach Tagen fällig wäre
    assert next_level(due_date=due, today=day(2026, 4, 10), last_level=1, last_notice_on=day(2026, 4, 5)) is None
    assert next_level(due_date=due, today=day(2026, 6, 1), last_level=3, last_notice_on=day(2026, 4, 5)) is None


@settings(max_examples=300, deadline=None)
@given(principal=st.decimals(min_value="0.01", max_value="100000", places=2),
       pays=st.lists(st.tuples(st.integers(0, 120), st.decimals(min_value="0.01", max_value="50000", places=2)), max_size=5),
       fee=st.sampled_from([D(0), D("2.50"), D("40.00")]), b2b=st.booleans())
def test_conservation(principal: D, pays: list[tuple[int, D]], fee: D, b2b: bool) -> None:
    """Gezahlt = getilgte Kosten + getilgte Zinsen + getilgte Hauptforderung (bis auf Rundung je Zahlung)."""
    start = day(2026, 1, 10)
    payments = [Payment(start + dt.timedelta(days=d), a) for d, a in pays]
    costs = [Cost(start + dt.timedelta(days=5), fee)] if fee else []
    s = claim_state(principal=principal, default_from=start, as_of=start + dt.timedelta(days=150), payments=payments,
                    costs=costs, rates=RATES, b2b=b2b)
    owed = principal + fee + s.interest_total
    assert abs((owed - s.paid_total) - s.total_open) <= D("0.01") * (len(payments) + 1)
    assert s.costs_open >= 0 and s.interest_open >= 0
