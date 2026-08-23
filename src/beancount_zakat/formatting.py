"""Exact formatting helpers.

Money is never routed through ``float``.  Every displayed figure is produced by
quantizing a :class:`~decimal.Decimal` and grouping its digits as text, so what
you read is exactly what was calculated.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from .constants import MONEY_QUANTUM

#: Headroom above the default 28-digit context. ``Decimal.quantize`` raises
#: InvalidOperation when the result would need more digits than the context
#: allows, which a plain 28-digit context hits on large amounts in
#: high-denomination currencies. Widening the context is exact -- it adds
#: capacity, never rounding.
_QUANTIZE_PRECISION = 80


def quantize_money(value: Decimal, places: int = 2) -> Decimal:
    """Round *value* to *places* decimal places, half away from zero.

    Uses a widened decimal context so that large amounts -- a balance in a
    high-denomination currency, say -- round rather than raising
    ``InvalidOperation``.
    """
    quantum = Decimal(1).scaleb(-places) if places else Decimal("1")
    with localcontext() as ctx:
        ctx.prec = max(_QUANTIZE_PRECISION, len(value.as_tuple().digits) + places + 2)
        return value.quantize(quantum, rounding=ROUND_HALF_UP)


def group_digits(digits: str, separator: str = ",") -> str:
    """Insert thousands separators into a run of digits."""
    if len(digits) <= 3:
        return digits
    head = len(digits) % 3 or 3
    parts = [digits[:head]]
    parts.extend(digits[i : i + 3] for i in range(head, len(digits), 3))
    return separator.join(parts)


def format_decimal(
    value: Decimal,
    places: int = 2,
    *,
    grouping: bool = True,
    separator: str = ",",
    decimal_point: str = ".",
    signed: bool = False,
) -> str:
    """Format a Decimal exactly, without ever converting it to ``float``.

    Args:
        value: The amount.
        places: Decimal places to show.
        grouping: Whether to insert thousands separators.
        separator: Thousands separator.
        decimal_point: Decimal separator.
        signed: Force a leading ``+`` on positive values.
    """
    rounded = quantize_money(value, places)
    negative = rounded < 0
    # `copy_abs` rather than `abs`: unary arithmetic re-rounds to the ambient
    # decimal context, which would silently truncate a large amount back to 28
    # significant digits.
    text = format(rounded.copy_abs(), "f")
    if "." in text:
        whole, _, frac = text.partition(".")
    else:
        whole, frac = text, ""
    frac = frac.ljust(places, "0")[:places] if places else ""
    if grouping:
        whole = group_digits(whole, separator)
    body = f"{whole}{decimal_point}{frac}" if places else whole
    if negative:
        return f"-{body}"
    if signed:
        return f"+{body}"
    return body


def format_money(
    value: Decimal,
    currency: str = "",
    places: int = 2,
    *,
    signed: bool = False,
) -> str:
    """Format an amount with an optional trailing currency code."""
    text = format_decimal(value, places, signed=signed)
    return f"{text} {currency}".rstrip()


def format_signed_balance(value: Decimal, currency: str, places: int = 2) -> str:
    """Render a signed balance with its meaning made explicit."""
    if value > 0:
        return f"{format_money(value, currency, places)} outstanding"
    if value == 0:
        return f"{format_money(value, currency, places)} (settled)"
    return f"{format_money(-value, currency, places)} paid in excess"


def format_rate(rate: Decimal) -> str:
    """Render a rate fraction as a percentage, e.g. ``2.5%``."""
    percent = (rate * 100).normalize()
    text = format(percent, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def format_years(value: Decimal, places: int = 4) -> str:
    return format_decimal(value, places, grouping=False)


def raw(value: Decimal) -> str:
    """The exact decimal string, for CSV export and audit trails."""
    return format(value, "f")


__all__ = [
    "MONEY_QUANTUM",
    "format_decimal",
    "format_money",
    "format_rate",
    "format_signed_balance",
    "format_years",
    "group_digits",
    "quantize_money",
    "raw",
]
