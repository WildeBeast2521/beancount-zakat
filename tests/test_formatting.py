"""Exact Decimal formatting. Money must never pass through float."""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from beancount_zakat import cli, csv_export, formatting, reporting
from beancount_zakat.formatting import (
    format_decimal,
    format_money,
    format_rate,
    format_signed_balance,
    group_digits,
    quantize_money,
    raw,
)


class TestGrouping:
    @pytest.mark.parametrize(
        ("digits", "expected"),
        [
            ("1", "1"),
            ("12", "12"),
            ("123", "123"),
            ("1234", "1,234"),
            ("12345", "12,345"),
            ("123456", "123,456"),
            ("1234567", "1,234,567"),
        ],
    )
    def test_group_digits(self, digits, expected):
        assert group_digits(digits) == expected


class TestFormatDecimal:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0", "0.00"),
            ("1234567.891", "1,234,567.89"),
            ("-1234.5", "-1,234.50"),
            ("0.005", "0.01"),
            ("-0.005", "-0.01"),
            ("999.995", "1,000.00"),
            ("1E+3", "1,000.00"),
        ],
    )
    def test_values(self, value, expected):
        assert format_decimal(Decimal(value)) == expected

    def test_places(self):
        assert format_decimal(Decimal("1.23456"), 4) == "1.2346"
        assert format_decimal(Decimal("1234.5"), 0) == "1,235"

    def test_signed(self):
        assert format_decimal(Decimal("5"), signed=True) == "+5.00"
        assert format_decimal(Decimal("-5"), signed=True) == "-5.00"

    def test_no_grouping(self):
        assert format_decimal(Decimal("1234567"), grouping=False) == "1234567.00"

    def test_rounding_is_half_up_not_bankers(self):
        """Python's default Decimal rounding is HALF_EVEN; ours is HALF_UP."""
        assert format_decimal(Decimal("2.345"), 2) == "2.35"
        assert format_decimal(Decimal("2.355"), 2) == "2.36"
        assert quantize_money(Decimal("0.5"), 0) == Decimal("1")
        assert quantize_money(Decimal("1.5"), 0) == Decimal("2")


class TestPrecisionIsNotLost:
    def test_a_value_float_cannot_represent(self):
        value = Decimal("0.1") + Decimal("0.2")
        assert value == Decimal("0.3")
        assert format_decimal(value, 20) == "0.30000000000000000000"

    def test_very_large_values_stay_exact(self):
        value = Decimal("123456789012345678901234567890.12")
        assert format_decimal(value, 2) == "123,456,789,012,345,678,901,234,567,890.12"

    def test_raw_is_the_plain_decimal_string(self):
        assert raw(Decimal("1E+3")) == "1000"
        assert raw(Decimal("0.10")) == "0.10"


class TestMoney:
    def test_currency_suffix(self):
        assert format_money(Decimal("1000"), "PKR") == "1,000.00 PKR"

    def test_no_currency(self):
        assert format_money(Decimal("1000")) == "1,000.00"


class TestSignedBalance:
    def test_positive_is_outstanding(self):
        assert format_signed_balance(Decimal("100"), "PKR") == "100.00 PKR outstanding"

    def test_zero_is_settled(self):
        assert format_signed_balance(Decimal("0"), "PKR") == "0.00 PKR (settled)"

    def test_negative_is_excess_and_shown_as_a_positive_amount(self):
        assert (
            format_signed_balance(Decimal("-100"), "PKR") == "100.00 PKR paid in excess"
        )


class TestRate:
    @pytest.mark.parametrize(
        ("rate", "expected"),
        [("0.025", "2.5%"), ("0.02", "2%"), ("0.001", "0.1%"), ("1", "100%")],
    )
    def test_rate(self, rate, expected):
        assert format_rate(Decimal(rate)) == expected


class TestNoFloatInMoneyPaths:
    """`float` is permitted only in chart geometry."""

    @pytest.mark.parametrize("module", [formatting, csv_export, reporting, cli])
    def test_module_never_converts_money_to_float(self, module):
        source = inspect.getsource(module)
        assert "float(" not in source, (
            f"{module.__name__} converts a value to float; money must stay "
            "Decimal all the way to the string"
        )

    def test_the_chart_is_the_only_float_boundary(self):
        from beancount_zakat import chart

        source = inspect.getsource(chart)
        assert "float(" in source
        assert "SVG coordinates are geometry, never" in source
