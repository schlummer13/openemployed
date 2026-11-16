from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.expenses import ExpenseError, compute_expense

D = Decimal


def test_domestic_19() -> None:
    a = compute_expense("IN_DE_19", D("45.00"), D("8.55"), "hosting")
    assert (a.gross, a.input_tax, a.rc_tax, a.expense, a.deductible) == (D("53.55"), D("8.55"), D("0"), D("45.00"), D("45.00"))
    assert a.warnings == ()


def test_domestic_tax_mismatch_warns() -> None:
    a = compute_expense("IN_DE_19", D("45.00"), D("3.15"), "hosting")  # 7 % statt 19 %
    assert a.warnings and "weicht" in a.warnings[0]


def test_reverse_charge_anthropic() -> None:
    """Claude-Abo aus Irland: keine USt auf dem Beleg → 19 % selbst schulden und als Vorsteuer abziehen (neutral)."""
    a = compute_expense("IN_13B_EU_SERVICE", D("300.00"), D("0"), "software")
    assert (a.gross, a.rc_tax, a.input_tax, a.expense) == (D("300.00"), D("57.00"), D("57.00"), D("300.00"))


def test_reverse_charge_rejects_vat_on_document() -> None:
    with pytest.raises(ExpenseError, match="Reverse Charge"):
        compute_expense("IN_13B_EU_SERVICE", D("300.00"), D("57.00"), "software")


def test_bewirtung_70_percent() -> None:
    a = compute_expense("IN_BEWIRTUNG", D("100.00"), D("19.00"), "hospitality")
    assert (a.input_tax, a.expense, a.deductible) == (D("19.00"), D("100.00"), D("70.00"))


def test_non_deductible_gift() -> None:
    a = compute_expense("IN_NICHT_ABZIEHBAR", D("80.00"), D("15.20"), "gifts")
    assert (a.input_tax, a.expense, a.deductible) == (D("0"), D("95.20"), D("0"))


def test_without_input_tax_expense_is_gross() -> None:
    a = compute_expense("IN_OHNE_VST", D("20.00"), D("0"), "fees")
    assert (a.input_tax, a.expense, a.deductible) == (D("0"), D("20.00"), D("20.00"))


def test_gwg_limit_warning() -> None:
    a = compute_expense("IN_DE_19", D("950.00"), D("180.50"), "hardware_gwg")
    assert any("GWG" in w for w in a.warnings)


@pytest.mark.parametrize(
    ("case", "net", "tax", "cat", "msg"),
    [
        ("OUT_DE_19", "1", "0", "other", "kein Eingangs"),
        ("IN_DE_19", "0", "0", "other", "nicht 0"),
        ("IN_DE_19", "1.001", "0", "other", "2 Nachkommastellen"),
        ("IN_DE_19", "10", "-1.90", "other", "Vorzeichen"),
        ("IN_DE_19", "10", "1.90", "unbekannt", "Kategorie"),
    ],
)
def test_invalid(case: str, net: str, tax: str, cat: str, msg: str) -> None:
    with pytest.raises(ExpenseError, match=msg):
        compute_expense(case, D(net), D(tax), cat)


cents = st.integers(min_value=1, max_value=10_000_000)


@settings(max_examples=300, deadline=None)
@given(cents, st.sampled_from(["IN_DE_19", "IN_DE_7", "IN_13B_EU_SERVICE", "IN_OHNE_VST", "IN_BEWIRTUNG"]))
def test_reversal_is_exact_negative(c: int, case: str) -> None:
    net = D(c) / 100
    rate = {"IN_DE_19": D("0.19"), "IN_DE_7": D("0.07"), "IN_BEWIRTUNG": D("0.19")}.get(case, D(0))
    tax = (net * rate).quantize(D("0.01"))
    a = compute_expense(case, net, tax, "other")
    b = compute_expense(case, -net, -tax, "other")
    assert (b.gross, b.input_tax, b.rc_tax, b.expense, b.deductible) == (-a.gross, -a.input_tax, -a.rc_tax, -a.expense, -a.deductible)
    assert a.gross == a.net + a.tax
