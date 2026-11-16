from decimal import Decimal

import pytest

from app.core.money import parse_strict_decimal, round_cent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.005", "0.01"),
        ("0.004", "0.00"),
        ("2.675", "2.68"),   # mit float wäre das 2.67 (Binärdarstellung) – deshalb Decimal
        ("-0.005", "-0.01"),  # symmetrisch: Storno ergibt exakt den negativen Betrag
        ("-2.675", "-2.68"),
        ("334.02", "334.02"),
    ],
)
def test_round_cent_commercial(value: str, expected: str) -> None:
    assert round_cent(Decimal(value)) == Decimal(expected)


@pytest.mark.parametrize("value", ["1758.00", "0", "-5", "12.3456", " 7.5 ", 12, Decimal("1.10")])
def test_parse_accepts(value: object) -> None:
    assert isinstance(parse_strict_decimal(value), Decimal)


@pytest.mark.parametrize(
    "value",
    [1.5, True, "1,50", "1e3", "NaN", "Infinity", "1.23456", "", "abc", "01.5", "1000000000000", None, [1]],
)
def test_parse_rejects(value: object) -> None:
    with pytest.raises(ValueError):
        parse_strict_decimal(value)
