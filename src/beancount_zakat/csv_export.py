"""Deterministic CSV export.

``--csv PATH`` writes a **directory** of logically separate files (or a single
``.zip`` when *PATH* ends in ``.zip``):

===========================  ===============================================
``metadata.csv``             report-level facts and assumptions
``warnings.csv``             every validation finding
``nisab_history.csv``        every change to either threshold, with its price
``yearly_summary.csv``       one row per Hijri reporting year
``detail_gold.csv``          gold marginal slices and holding periods
``detail_silver.csv``        silver marginal slices and holding periods
``payments.csv``             signed payment detail
===========================  ===============================================

Every monetary column is the **exact decimal string** -- no rounding beyond
what the calculation itself did, no locale grouping, no currency symbol -- so a
spreadsheet reads it as a number and a test can round-trip it.  Files are
UTF-8 with ``\\r\\n`` line endings per RFC 4180, and rows are emitted in a
stable, documented order.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .formatting import quantize_money, raw
from .models import Basis, ZakatReport

#: Filenames written, in the order they are produced.
CSV_FILES = (
    "metadata.csv",
    "warnings.csv",
    "nisab_history.csv",
    "yearly_summary.csv",
    "detail_gold.csv",
    "detail_silver.csv",
    "payments.csv",
)

METADATA_COLUMNS = ("key", "value")

WARNING_COLUMNS = (
    "severity",
    "code",
    "message",
    "detail",
    "account",
    "commodity",
    "date",
)

YEARLY_COLUMNS = (
    "hijri_year",
    "gregorian_start",
    "gregorian_end",
    "gold_liability",
    "silver_liability",
    "payments",
    "gold_balance",
    "gold_balance_status",
    "silver_balance",
    "silver_balance_status",
)

NISAB_HISTORY_COLUMNS = (
    "in_force_from",
    "gold_commodity",
    "gold_price",
    "gold_price_date",
    "gold_price_age_days",
    "gold_nisab",
    "silver_commodity",
    "silver_price",
    "silver_price_date",
    "silver_price_age_days",
    "silver_nisab",
)

#: One row per holding period, grouped by marginal slice, slices ascending by
#: level. Gold and silver get one file each: the thresholds are far apart, and a
#: reset under one basis says nothing about the other.
#: ``nisab_low``/``nisab_high`` bracket the threshold in force during the period
#: -- they differ wherever the metal price moved inside it.
#: ``hawl`` is ``complete`` | ``incomplete`` | ``not running``. The last means
#: wealth was below this basis's nisab, so the clock was reset; ``lunar_years``
#: is then 0 because no elapsed time counts.
DETAIL_COLUMNS = (
    "basis",
    "level",
    "marginal_amount",
    "period_start",
    "period_end",
    "days",
    "lunar_years",
    "hijri_year_start",
    "hijri_year_end",
    "nisab_low",
    "nisab_high",
    "at_level",
    "above_nisab",
    "hawl",
    "qualifies",
    "zakat_due",
    "reason",
)

PAYMENT_COLUMNS = (
    "date",
    "account",
    "payee",
    "narration",
    "original_amount",
    "original_currency",
    "conversion_rate",
    "amount",
    "is_reversal",
    "running_total",
)


def money(value: Decimal | None) -> str:
    """Exact decimal string for a monetary column, at a consistent 2 places."""
    if value is None:
        return ""
    return raw(quantize_money(value, 2))


def _status(value: Decimal) -> str:
    if value > 0:
        return "outstanding"
    if value == 0:
        return "settled"
    return "excess"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _writer(buffer: io.StringIO) -> Any:
    return csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)


def metadata_rows(report: ZakatReport) -> list[Sequence[str]]:
    from .hijri import hijri_year_of

    return [
        ("as_of", report.as_of.isoformat()),
        ("as_of_hijri_year", str(hijri_year_of(report.as_of))),
        ("operating_currency", report.operating_currency),
        ("zakat_rate", raw(report.zakat_rate)),
        ("inception", _date(report.inception)),
        ("net_wealth_as_of", money(report.gold.net_wealth)),
        ("gold_nisab_at_as_of", money(report.gold.nisab)),
        ("gold_nisab_commodity_at_as_of", report.gold.price.commodity or ""),
        ("gold_nisab_price_at_as_of", money(report.gold.price.price)),
        ("gold_nisab_price_date_at_as_of", _date(report.gold.price.price_date)),
        ("gold_qualifies", _bool(report.gold.qualifies_now)),
        ("gold_cumulative_liability", money(report.gold.cumulative_liability)),
        ("gold_payments", money(report.gold.payments_total)),
        ("gold_remaining_or_excess", money(report.gold.remaining_or_excess)),
        ("gold_status", report.gold.status),
        ("silver_nisab_at_as_of", money(report.silver.nisab)),
        ("silver_nisab_commodity_at_as_of", report.silver.price.commodity or ""),
        ("silver_nisab_price_at_as_of", money(report.silver.price.price)),
        ("silver_nisab_price_date_at_as_of", _date(report.silver.price.price_date)),
        ("silver_qualifies", _bool(report.silver.qualifies_now)),
        ("silver_cumulative_liability", money(report.silver.cumulative_liability)),
        ("silver_payments", money(report.silver.payments_total)),
        ("silver_remaining_or_excess", money(report.silver.remaining_or_excess)),
        ("silver_status", report.silver.status),
        ("asset_accounts", "|".join(report.asset_accounts)),
        ("liability_accounts", "|".join(report.liability_accounts)),
        ("payment_accounts", "|".join(report.payment_accounts)),
        ("warning_count", str(len(report.warnings))),
        (
            "note",
            "Gold and silver are alternative bases, not values to be added "
            "together. The *_at_as_of fields describe only the report date; the "
            "nisab moves with the metal price, so see nisab_history.csv and the "
            "nisab_low / nisab_high columns of detail_gold.csv and "
            "detail_silver.csv for the thresholds actually used.",
        ),
    ]


def warning_rows(report: ZakatReport) -> list[Sequence[str]]:
    return [
        (
            warning.severity.value,
            warning.code,
            warning.message,
            warning.detail,
            warning.account or "",
            warning.commodity or "",
            _date(warning.when),
        )
        for warning in report.warnings
    ]


def yearly_rows(report: ZakatReport) -> list[Sequence[str]]:
    return [
        (
            str(row.hijri_year),
            row.start.isoformat(),
            row.end.isoformat(),
            money(row.gold_liability),
            money(row.silver_liability),
            money(row.payments),
            money(row.gold_balance),
            _status(row.gold_balance),
            money(row.silver_balance),
            _status(row.silver_balance),
        )
        for row in report.year_rows
    ]


def nisab_history_rows(report: ZakatReport) -> list[Sequence[str]]:
    """One row per change to either threshold, ascending."""
    rows: list[Sequence[str]] = []
    previous: tuple[Decimal | None, Decimal | None] = (None, None)
    for gold, silver in zip(
        report.gold_nisab_series, report.silver_nisab_series, strict=False
    ):
        current = (gold.nisab, silver.nisab)
        if current == previous:
            continue
        previous = current
        rows.append(
            (
                gold.when.isoformat(),
                gold.commodity or "",
                money(gold.price),
                _date(gold.price_date),
                str(gold.stale_days) if gold.stale_days is not None else "",
                money(gold.nisab),
                silver.commodity or "",
                money(silver.price),
                _date(silver.price_date),
                str(silver.stale_days) if silver.stale_days is not None else "",
                money(silver.nisab),
            )
        )
    return rows


def detail_rows(report: ZakatReport, basis: Basis) -> list[Sequence[str]]:
    """Every holding period for one basis, slice by slice."""
    from .hijri import hijri_year_of
    from .reporting import basis_period_rows

    rows: list[Sequence[str]] = []
    for row in basis_period_rows(report, basis):
        period = row.period
        rows.append(
            (
                basis.value,
                money(row.level),
                money(row.marginal),
                period.start.isoformat(),
                period.end.isoformat(),
                str(period.days),
                raw(period.lunar_years),
                str(hijri_year_of(period.start)),
                str(hijri_year_of(period.end)),
                money(row.nisab.low),
                money(row.nisab.high),
                _bool(period.at_level),
                _bool(period.above_nisab),
                period.hawl.value,
                _bool(period.qualifies),
                money(period.zakat_due),
                period.reason,
            )
        )
    return rows


def payment_rows(report: ZakatReport) -> list[Sequence[str]]:
    rows: list[Sequence[str]] = []
    running = Decimal("0")
    for payment in report.payments:
        running += payment.amount
        rows.append(
            (
                payment.when.isoformat(),
                payment.account,
                payment.payee or "",
                payment.narration or "",
                money(payment.original_amount),
                payment.original_currency,
                raw(payment.rate) if payment.rate is not None else "",
                money(payment.amount),
                _bool(payment.is_reversal),
                money(running),
            )
        )
    return rows


def _render(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    buffer = io.StringIO()
    writer = _writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return buffer.getvalue()


def render_all(report: ZakatReport) -> Iterator[tuple[str, str]]:
    """Yield ``(filename, content)`` for every CSV, in :data:`CSV_FILES` order."""
    yield "metadata.csv", _render(METADATA_COLUMNS, metadata_rows(report))
    yield "warnings.csv", _render(WARNING_COLUMNS, warning_rows(report))
    yield (
        "nisab_history.csv",
        _render(NISAB_HISTORY_COLUMNS, nisab_history_rows(report)),
    )
    yield "yearly_summary.csv", _render(YEARLY_COLUMNS, yearly_rows(report))
    yield "detail_gold.csv", _render(DETAIL_COLUMNS, detail_rows(report, Basis.GOLD))
    yield (
        "detail_silver.csv",
        _render(DETAIL_COLUMNS, detail_rows(report, Basis.SILVER)),
    )
    yield "payments.csv", _render(PAYMENT_COLUMNS, payment_rows(report))


def export(report: ZakatReport, destination: str | Path) -> list[Path]:
    """Write the CSV set to a directory, or to a ``.zip`` archive.

    Returns the paths written.
    """
    destination = Path(destination)
    if destination.suffix.lower() == ".zip":
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name, content in render_all(report):
                archive.writestr(name, content)
        return [destination]

    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in render_all(report):
        path = destination / name
        path.write_text(content, encoding="utf-8", newline="")
        written.append(path)
    return written
