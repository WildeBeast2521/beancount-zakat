"""``beancount-zakat`` -- terminal zakat report and CSV export.

Imports Beancount but never Fava, so it runs on a machine that has never had
Fava installed.

Exit codes:

===  ==========================================================
  0  success (warnings may still have been printed)
  1  a validation error makes a result untrustworthy
  2  usage error, or the ledger could not be loaded
===  ==========================================================
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import __version__
from .adapter import decimal_places_for
from .config import ConfigError
from .constants import (
    GOLD_NISAB_GRAMS,
    GOLD_NISAB_TOLA,
    SILVER_NISAB_GRAMS,
    SILVER_NISAB_TOLA,
)
from .csv_export import export
from .formatting import (
    format_decimal,
    format_money,
    format_rate,
    format_signed_balance,
    format_years,
)
from .hijri import HijriRangeError, hijri_year_of
from .models import Basis, Severity, ZakatReport
from .reporting import basis_period_rows
from .service import build_report
from .tables import Column, elide, field, heading, render_table, terminal_width, wrap

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2

_SEVERITY_LABEL = {
    Severity.ERROR: "ERROR",
    Severity.WARNING: "WARN",
    Severity.INFO: "INFO",
}


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date in YYYY-MM-DD form"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beancount-zakat",
        description=(
            "Calculate zakat from a Beancount ledger on independent gold and "
            "silver nisab bases."
        ),
        epilog=(
            'Accounts are selected by beancount_zakat: "asset" / "liability" '
            '/ "expense" metadata on their Open directives. '
            "This tool is informational only and is not religious, legal, tax, "
            "accounting or financial advice."
        ),
    )
    parser.add_argument(
        "ledger", type=Path, help="root Beancount file (includes are followed)"
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "report cutoff (default: today). Nothing dated later affects the "
            "result, and holdings keep accruing hawl up to this date."
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "also write the CSV set to this directory, or to a .zip archive "
            "when PATH ends in .zip"
        ),
    )
    parser.add_argument(
        "--basis",
        choices=("both", "gold", "silver"),
        default="both",
        help="which detail tables to print (default: both)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        metavar="N",
        help="force an output width instead of detecting the terminal size",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the summary and the reconciliation",
    )
    parser.add_argument(
        "--version", action="version", version=f"beancount-zakat {__version__}"
    )
    return parser


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def section_metadata(report: ZakatReport, ledger: Path, places: int, width: int) -> str:
    def row(label: str, value: str) -> list[str]:
        return field(label, value, width)

    lines = [heading("REPORT", width)]
    lines += row("Ledger", elide(str(ledger), max(20, width - 25)))
    lines += row(
        "As of",
        f"{report.as_of.isoformat()} ({hijri_year_of(report.as_of)} AH)",
    )
    lines += row("Operating currency", report.operating_currency)
    lines += row("Zakat rate", format_rate(report.zakat_rate))
    lines += row(
        "Gold nisab weight",
        f"{format_decimal(GOLD_NISAB_GRAMS, 2)} g "
        f"({format_decimal(GOLD_NISAB_TOLA, 1)} tola)",
    )
    lines += row(
        "Silver nisab weight",
        f"{format_decimal(SILVER_NISAB_GRAMS, 2)} g "
        f"({format_decimal(SILVER_NISAB_TOLA, 1)} tola)",
    )
    if report.inception:
        lines += row("First activity", report.inception.isoformat())
    lines += row(
        "Accounts",
        f"{len(report.asset_accounts)} asset, "
        f"{len(report.liability_accounts)} liability, "
        f"{len(report.payment_accounts)} payment",
    )
    return "\n".join(lines)


def section_warnings(report: ZakatReport, width: int) -> str:
    if not report.warnings:
        return f"{heading('DATA QUALITY', width)}\n  No issues found."
    lines = [heading("DATA QUALITY", width)]
    for warning in report.warnings:
        label = _SEVERITY_LABEL[warning.severity]
        head = f"  [{label}] "
        wrapped = wrap(warning.message, width - len(head))
        lines.append(f"{head}{wrapped[0]}")
        lines.extend(" " * len(head) + line for line in wrapped[1:])
        if warning.detail:
            lines.extend(wrap(warning.detail, width, indent="         "))
    return "\n".join(lines)


def section_summary(report: ZakatReport, places: int, width: int) -> str:
    """Lifetime liability, payments and signed balance -- no point-in-time figures.

    The nisab moves with the metal price, so quoting one threshold here would
    misrepresent every historical period. The thresholds actually used are in
    the nisab history and the calculation detail.
    """
    currency = report.operating_currency

    def money(value: Decimal | None) -> str:
        return "unavailable" if value is None else format_money(value, currency, places)

    columns = (
        Column(""),
        Column("Gold basis", "right"),
        Column("Silver basis", "right"),
    )
    gold, silver = report.gold, report.silver
    rows = [
        (
            "Sahib-e-nisab today",
            "yes" if gold.qualifies_now else "no",
            "yes" if silver.qualifies_now else "no",
        ),
        (
            "Cumulative liability (lifetime)",
            money(gold.cumulative_liability),
            money(silver.cumulative_liability),
        ),
        (
            "Zakat paid (net of refunds)",
            money(gold.payments_total),
            money(silver.payments_total),
        ),
    ]
    footer = (
        "Remaining / excess",
        format_signed_balance(gold.remaining_or_excess, currency, places),
        format_signed_balance(silver.remaining_or_excess, currency, places),
    )
    return "\n".join(
        [
            heading("SUMMARY", width),
            "\n".join(
                wrap(
                    "Gold and silver are ALTERNATIVE bases. Pick one; do not add "
                    "them. The nisab moves with the metal price, so no single "
                    "threshold is quoted here; every one the calculation used is "
                    "listed below.",
                    width,
                    indent="  ",
                )
            ),
            "",
            render_table(columns, rows, width=width, footer=footer),
        ]
    )


def section_nisab_history(report: ZakatReport, places: int, width: int) -> str:
    """Every date on which either threshold moved, and the price behind it."""
    rows: list[tuple[str, ...]] = []
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
                format_decimal(gold.price, places) if gold.price else "n/a",
                gold.price_date.isoformat() if gold.price_date else "-",
                format_decimal(gold.nisab, places) if gold.nisab else "n/a",
                format_decimal(silver.price, places) if silver.price else "n/a",
                silver.price_date.isoformat() if silver.price_date else "-",
                format_decimal(silver.nisab, places) if silver.nisab else "n/a",
            )
        )
    columns = (
        Column("In force from"),
        Column("Gold price", "right"),
        Column("Quoted"),
        Column("Gold nisab", "right"),
        Column("Silver price", "right"),
        Column("Quoted"),
        Column("Silver nisab", "right"),
    )
    return "\n".join(
        [
            heading(f"NISAB HISTORY ({report.operating_currency})", width),
            "\n".join(
                wrap(
                    "The threshold in force on each date. A day with no price of "
                    "its own reuses the last known price, so a row stands until "
                    "the next replaces it.",
                    width,
                    indent="  ",
                )
            ),
            "",
            render_table(columns, rows, width=width, empty="No metal prices found."),
        ]
    )


def _price_label(basis_result, places: int) -> str:
    point = basis_result.price
    if point.price is None:
        return "missing"
    stale = f" +{point.stale_days}d" if point.stale_days else ""
    return (
        f"{format_decimal(point.price, places)} {point.commodity} "
        f"{point.price_date.isoformat()}{stale}"
    )


def section_yearly(report: ZakatReport, places: int, width: int) -> str:
    currency = report.operating_currency
    columns = (
        Column("Hijri year"),
        Column("Gregorian range"),
        Column("Gold liability", "right"),
        Column("Silver liability", "right"),
        Column("Payments", "right"),
        Column("Gold balance", "right"),
        Column("Silver balance", "right"),
    )
    rows = [
        (
            row.label,
            f"{row.start.isoformat()} to {row.end.isoformat()}",
            format_decimal(row.gold_liability, places),
            format_decimal(row.silver_liability, places),
            format_decimal(row.payments, places),
            format_decimal(row.gold_balance, places, signed=False),
            format_decimal(row.silver_balance, places, signed=False),
        )
        for row in report.year_rows
    ]
    footer = (
        "TOTAL",
        "",
        format_decimal(report.gold.cumulative_liability, places),
        format_decimal(report.silver.cumulative_liability, places),
        format_decimal(report.payments_total, places),
        format_decimal(report.gold.remaining_or_excess, places),
        format_decimal(report.silver.remaining_or_excess, places),
    )
    return "\n".join(
        [
            heading(f"YEARLY SUMMARY ({currency})", width),
            "\n".join(
                wrap(
                    "Balance columns are signed: negative means paid in excess.",
                    width,
                    indent="  ",
                )
            ),
            "",
            render_table(columns, rows, width=width, footer=footer),
        ]
    )


def section_reconciliation(report: ZakatReport, places: int, width: int) -> str:
    currency = report.operating_currency
    lines = [heading("RECONCILIATION", width)]
    for result in (report.gold, report.silver):
        rows_total = sum(
            (
                row.gold_liability
                if result.basis is Basis.GOLD
                else row.silver_liability
                for row in report.year_rows
            ),
            Decimal("0"),
        )
        detail_total = sum(
            (period.zakat_due for period in result.qualifying_periods),
            Decimal("0"),
        )
        agrees = rows_total == detail_total == result.cumulative_liability
        lines.append(f"  {result.basis.label} basis")
        for label, value in (
            ("Sum of holding periods", format_money(detail_total, currency, places)),
            ("Sum of yearly rows", format_money(rows_total, currency, places)),
            (
                "Cumulative liability",
                format_money(result.cumulative_liability, currency, places),
            ),
            (
                "Less payments",
                format_money(result.payments_total, currency, places),
            ),
            (
                "Remaining / excess",
                format_signed_balance(result.remaining_or_excess, currency, places),
            ),
            ("Reconciles", "yes" if agrees else "NO"),
        ):
            lines.extend(field(label, value, width, indent="    ", label_width=22))
    return "\n".join(lines)


def section_detail(report: ZakatReport, basis: Basis, places: int, width: int) -> str:
    """One basis's marginal slices and holding periods.

    Kept separate per basis: the thresholds are far apart, a reset under one
    says nothing about the other, and elapsed time means something different
    for each.
    """
    rows_data = basis_period_rows(report, basis)
    result = report.basis(basis)

    def span(value) -> str:
        """Thresholds at whole units here; exact figures are in NISAB HISTORY."""
        if not value.known:
            return "n/a"
        low = format_decimal(value.low, 0)
        if not value.varies:
            return low
        return f"{low}-{format_decimal(value.high, 0)}"

    columns = (
        Column("Level", "right"),
        Column("Marginal", "right"),
        Column("Start"),
        Column("End"),
        Column("Days", "right"),
        Column("Lunar yrs", "right"),
        Column("Nisab in force", "right"),
        Column("Above nisab"),
        Column("Hawl"),
        Column("Zakat due", "right"),
    )
    rows: list[tuple[str, ...]] = []
    for row in rows_data:
        period = row.period
        rows.append(
            (
                format_decimal(row.level, places) if row.first_of_level else "",
                format_decimal(row.marginal, places) if row.first_of_level else "",
                period.start.isoformat(),
                period.end.isoformat(),
                str(period.days),
                format_years(period.lunar_years),
                span(row.nisab),
                "yes" if period.above_nisab else "no",
                period.hawl.label,
                format_decimal(period.zakat_due, places),
            )
        )
    footer = (
        "TOTAL",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        format_decimal(result.cumulative_liability, places),
    )
    return "\n".join(
        [
            heading(
                f"{basis.label.upper()} NISAB -- CALCULATION DETAIL "
                f"({report.operating_currency})",
                width,
            ),
            "\n".join(
                wrap(
                    "The nisab column shows the RANGE in force during the "
                    "period, because the threshold moves with the metal price. "
                    "Hawl is one of: complete (a full lunar year was reached, "
                    "so zakat is due), incomplete (running but short of a "
                    "year), or 'not running' (wealth was below this nisab, so "
                    "the clock was reset and no elapsed time counts).",
                    width,
                    indent="  ",
                )
            ),
            "",
            render_table(
                columns,
                rows,
                width=width,
                footer=footer,
                empty="No qualifying wealth levels.",
            ),
        ]
    )


def section_payments(report: ZakatReport, places: int, width: int) -> str:
    columns = (
        Column("Date"),
        Column("Account"),
        Column("Payee / narration"),
        Column("Posted", "right"),
        Column("Ccy"),
        Column("Amount", "right"),
        Column("Kind"),
        Column("Running total", "right"),
    )
    rows: list[tuple[str, ...]] = []
    running = Decimal("0")
    for payment in report.payments:
        running += payment.amount
        description = " / ".join(
            part for part in (payment.payee, payment.narration) if part
        )
        rows.append(
            (
                payment.when.isoformat(),
                payment.account,
                description,
                format_decimal(payment.original_amount, places),
                payment.original_currency,
                format_decimal(payment.amount, places),
                "refund" if payment.is_reversal else "payment",
                format_decimal(running, places),
            )
        )
    footer = (
        "TOTAL",
        "",
        "",
        "",
        "",
        format_decimal(report.payments_total, places),
        "",
        format_decimal(report.payments_total, places),
    )
    return "\n".join(
        [
            heading(f"PAYMENTS ({report.operating_currency})", width),
            "\n".join(
                wrap(
                    "Negative amounts are refunds or reversals and reduce the total.",
                    width,
                    indent="  ",
                )
            ),
            "",
            render_table(
                columns,
                rows,
                width=width,
                footer=footer,
                empty="No zakat payments recorded.",
            ),
        ]
    )


def render_report(
    report: ZakatReport,
    ledger: Path,
    *,
    places: int = 2,
    width: int | None = None,
    basis: str = "both",
    quiet: bool = False,
) -> str:
    available = width or terminal_width()
    parts = [
        section_metadata(report, ledger, places, available),
        section_warnings(report, available),
        section_summary(report, places, available),
        section_yearly(report, places, available),
        section_reconciliation(report, places, available),
    ]
    if not quiet:
        parts.append(section_nisab_history(report, places, available))
        if basis in ("both", "gold"):
            parts.append(section_detail(report, Basis.GOLD, places, available))
        if basis in ("both", "silver"):
            parts.append(section_detail(report, Basis.SILVER, places, available))
        parts.append(section_payments(report, places, available))
    parts.append(
        "\n".join(
            wrap(
                "This report is informational only. Zakat rulings vary by "
                "school of jurisprudence, asset type, debt treatment and "
                "circumstance. Verify the inputs and consult a qualified "
                "scholar before acting.",
                available,
            )
        )
    )
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.ledger.exists():
        print(f"error: ledger not found: {args.ledger}", file=sys.stderr)
        return EXIT_USAGE

    from beancount import loader

    try:
        entries, load_errors, options = loader.load_file(str(args.ledger))
    except Exception as exc:
        print(f"error: could not load ledger: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if load_errors:
        print(
            f"Beancount reported {len(load_errors)} problem(s) loading the ledger:",
            file=sys.stderr,
        )
        for error in load_errors[:10]:
            print(f"  {error.message}", file=sys.stderr)
        if len(load_errors) > 10:
            print(f"  ... and {len(load_errors) - 10} more", file=sys.stderr)

    try:
        report = build_report(entries, options, as_of=args.as_of)
    except (ConfigError, HijriRangeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:
        print(f"error: calculation failed: {exc}", file=sys.stderr)
        return EXIT_USAGE

    places = decimal_places_for(entries, report.operating_currency)
    sys.stdout.write(
        render_report(
            report,
            args.ledger,
            places=places,
            width=args.width,
            basis=args.basis,
            quiet=args.quiet,
        )
    )

    if args.csv is not None:
        try:
            written = export(report, args.csv)
        except OSError as exc:
            print(f"error: could not write CSV: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"\nCSV written to {args.csv}:", file=sys.stderr)
        for path in written:
            print(f"  {path}", file=sys.stderr)

    return EXIT_VALIDATION if report.has_errors else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
