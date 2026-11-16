from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.money import CENT, ZERO, round_cent
from app.domain.invoice_calc import (
    InvoiceCalculationError,
    LineInput,
    PriceMode,
    calculate_invoice,
    is_gross_price_representable,
    net_from_gross,
    tax_for,
)

D = Decimal


def line(price: str, qty: str = "1", case: str = "OUT_DE_19", unit: str = "MON") -> LineInput:
    return LineInput(description="Software-Miete", quantity=D(qty), unit_code=unit, unit_price=D(price), tax_case=case)


# ───────────────────────── feste Beispiele ─────────────────────────

def test_monthly_rent_hr1() -> None:
    """Beispiel aus der Kostentabelle: 1.758,00 € netto + 19 % = 2.092,02 €."""
    t = calculate_invoice([line("1758.00")], PriceMode.NET)
    assert (t.total_net, t.total_tax, t.total_gross) == (D("1758.00"), D("334.02"), D("2092.02"))
    assert t.tax_breakdown[0].category.value == "S"


def test_tax_is_calculated_on_group_sum_not_per_line() -> None:
    # 3 × 0,10 € netto: pro Position 0,019 → 0,02 ×3 = 0,06 (falsch); auf Summe 0,30 × 19 % = 0,057 → 0,06
    # 3 × 0,05 €: pro Position 0,0095 → 0,01 ×3 = 0,03 (falsch); auf Summe 0,15 × 19 % = 0,0285 → 0,03
    # 7 × 0,03 €: pro Position 0,0057 → 0,01 ×7 = 0,07 (falsch); Summe 0,21 → 0,0399 → 0,04 (richtig)
    t = calculate_invoice([line("0.03") for _ in range(7)], PriceMode.NET)
    assert t.total_net == D("0.21")
    assert t.total_tax == D("0.04")


def test_mixed_rates_are_separate_groups() -> None:
    t = calculate_invoice([line("100.00"), line("100.00", case="OUT_DE_7")], PriceMode.NET)
    rates = sorted((b.rate, b.tax_amount) for b in t.tax_breakdown)
    assert rates == [(D("7"), D("7.00")), (D("19"), D("19.00"))]
    assert t.total_gross == D("226.00")


def test_hourly_work_with_fractional_quantity() -> None:
    t = calculate_invoice([line("95.00", qty="7.25", unit="HUR")], PriceMode.NET)
    assert t.lines[0].line_net == D("688.75")
    assert t.total_tax == D("130.86")  # 130.8625 → 130.86


def test_credit_line_via_negative_quantity() -> None:
    t = calculate_invoice([line("1758.00"), line("1758.00", qty="-1")], PriceMode.NET)
    assert t.total_net == ZERO and t.total_tax == ZERO


def test_storno_is_exact_negative_of_original() -> None:
    orig = calculate_invoice([line("33.33", qty="3"), line("0.05", case="OUT_DE_7")], PriceMode.NET)
    storno = calculate_invoice([line("33.33", qty="-3"), line("0.05", qty="-1", case="OUT_DE_7")], PriceMode.NET)
    assert storno.total_gross == -orig.total_gross
    assert storno.total_tax == -orig.total_tax


def test_gross_price_b2c_exact() -> None:
    t = calculate_invoice([line("29.00")], PriceMode.GROSS)
    assert (t.total_net, t.total_tax, t.total_gross) == (D("24.37"), D("4.63"), D("29.00"))
    assert t.gross_deviation == ZERO and not t.warnings


def test_gross_price_not_representable_is_one_cent_cheaper_never_more() -> None:
    t = calculate_invoice([line("1.10")], PriceMode.GROSS)
    assert t.total_gross == D("1.09")
    assert t.gross_deviation == D("0.01")
    assert t.warnings and "nicht exakt darstellbar" in t.warnings[0]


def test_representability_helper() -> None:
    assert is_gross_price_representable(D("29.00"), D("19"))
    assert is_gross_price_representable(D("11.90"), D("19"))
    assert not is_gross_price_representable(D("1.10"), D("19"))


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([], "Mindestens eine Position"),
        ([line("-1.00")], "nicht negativ"),
        ([line("1.00", case="IN_DE_19")], "kein Ausgangs-Steuerfall"),
        ([line("1.00", case="IN_DE_19")], "kein Ausgangs-Steuerfall"),
        ([line("1.00", unit="XYZ")], "unbekannte Einheit"),
        ([LineInput("  ", D(1), "C62", D(1), "OUT_DE_19")], "Bezeichnung fehlt"),
        ([line("1.00001")], "4 Nachkommastellen"),
    ],
)
def test_invalid_input(lines: list[LineInput], message: str) -> None:
    with pytest.raises(InvoiceCalculationError, match=message):
        calculate_invoice(lines, PriceMode.NET)


def test_unknown_tax_case() -> None:
    with pytest.raises(ValueError, match="Unbekannter Steuerfall"):
        calculate_invoice([line("1.00", case="GIBT_ES_NICHT")], PriceMode.NET)


# ───────────────────────── Eigenschaften (Hypothesis) ─────────────────────────

prices = st.decimals(min_value=D("0"), max_value=D("99999.9999"), places=4, allow_nan=False, allow_infinity=False)
quantities = st.decimals(min_value=D("-500"), max_value=D("500"), places=4, allow_nan=False, allow_infinity=False)
cases = st.sampled_from(["OUT_DE_19", "OUT_DE_7"])
lines_strategy = st.lists(
    st.builds(lambda p, q, c: LineInput("x", q, "C62", p, c), prices, quantities, cases), min_size=1, max_size=25
)


@settings(max_examples=400, deadline=None)
@given(lines_strategy)
def test_net_mode_invariants(lines: list[LineInput]) -> None:
    t = calculate_invoice(lines, PriceMode.NET)
    assert sum((r.line_net for r in t.lines), ZERO) == t.total_net
    assert t.total_gross == t.total_net + t.total_tax
    for b in t.tax_breakdown:
        assert b.tax_amount == tax_for(b.taxable_amount, b.rate)
    for r, inp in zip(t.lines, lines, strict=True):
        assert r.line_net == round_cent(inp.quantity * inp.unit_price)
        assert r.line_net.as_tuple().exponent == -2


@settings(max_examples=400, deadline=None)
@given(lines_strategy)
def test_gross_mode_never_charges_more_than_displayed(lines: list[LineInput]) -> None:
    t = calculate_invoice(lines, PriceMode.GROSS)
    assert t.gross_target is not None
    groups = len(t.tax_breakdown)
    # Abweichung je Gruppe höchstens 1 Cent, immer zugunsten des Kunden (Betrag kleiner oder gleich)
    assert abs(t.gross_deviation) <= groups * CENT
    if t.gross_target >= 0:
        assert t.total_gross <= t.gross_target, "Kunde darf nie mehr zahlen als angezeigt"
    else:
        assert t.total_gross >= t.gross_target, "Gutschrift nie höher als angezeigt (Spiegelbild)"
    assert sum((r.line_net for r in t.lines), ZERO) == t.total_net
    assert t.total_gross == t.total_net + t.total_tax
    for r in t.lines:
        assert r.unit_price_net >= 0


@settings(max_examples=400, deadline=None)
@given(lines_strategy, st.sampled_from([PriceMode.NET, PriceMode.GROSS]))
def test_storno_is_exact_mirror(lines: list[LineInput], mode: PriceMode) -> None:
    original = calculate_invoice(lines, mode)
    mirrored = [LineInput(x.description, -x.quantity, x.unit_code, x.unit_price, x.tax_case) for x in lines]
    storno = calculate_invoice(mirrored, mode)
    assert storno.total_net == -original.total_net
    assert storno.total_tax == -original.total_tax
    assert storno.total_gross == -original.total_gross
    assert [r.line_net for r in storno.lines] == [-r.line_net for r in original.lines]


def test_mixed_sign_groups_found_by_hypothesis() -> None:
    """Rabatt auf 7 %-Positionen + 19 %-Position: Gutschriftgruppe wird zugunsten des Kunden aufgerundet."""
    lines = [
        LineInput("x", D("-1.3523"), "C62", D("0.5879"), "OUT_DE_7"),
        LineInput("x", D("-1.3523"), "C62", D("0.5879"), "OUT_DE_7"),
        LineInput("x", D("0.0004"), "C62", D("6812.5000"), "OUT_DE_19"),
    ]
    t = calculate_invoice(lines, PriceMode.GROSS)
    assert t.gross_target is not None
    assert t.total_gross <= t.gross_target


@settings(max_examples=2000, deadline=None)
@given(st.integers(min_value=-10_000_000, max_value=10_000_000), st.sampled_from([D("19"), D("7")]))
def test_net_from_gross_is_optimal(cents: int, rate: Decimal) -> None:
    gross = D(cents) / 100
    net, tax = net_from_gross(gross, rate)
    total = net + tax
    assert abs(total) <= abs(gross)
    assert abs(gross) - abs(total) <= CENT
    assert tax == tax_for(net, rate)
    # optimal: ein Cent mehr Netto würde den Bruttobetrag überschreiten
    bigger = abs(net) + CENT
    assert bigger + tax_for(bigger, rate) > abs(gross)
    # symmetrisch für Storno
    assert net_from_gross(-gross, rate) == (-net, -tax)


@settings(max_examples=300, deadline=None)
@given(st.integers(min_value=1, max_value=10_000_000))
def test_single_line_gross_has_consistent_unit_price(cents: int) -> None:
    t = calculate_invoice([LineInput("x", D(1), "C62", D(cents) / 100, "OUT_DE_19")], PriceMode.GROSS)
    r = t.lines[0]
    assert round_cent(r.quantity * r.unit_price_net) == r.line_net
