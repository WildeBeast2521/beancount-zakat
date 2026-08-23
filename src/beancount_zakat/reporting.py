"""Reporting-layer view models: yearly allocation and reconciliation.

Each qualifying period's liability is split across the Hijri years it spans
**pro rata by overlapping days**, with the rounding residual placed on the
period's final year. A period that runs for three years therefore contributes a
share to each of them rather than its whole amount to all of them, and the rows
sum to the cumulative liability *exactly* -- asserted as a test invariant, not
merely intended.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from .formatting import quantize_money
from .hijri import HijriYearRange, hijri_year_of, hijri_year_range
from .models import (
    HawlPeriod,
    NisabSpan,
    Payment,
    WealthPoint,
    YearRow,
)

if TYPE_CHECKING:  # pragma: no cover
    from .models import Basis, PricePoint, ZakatReport

ZERO = Decimal("0")


def _overlap_days(period: HawlPeriod, span: HijriYearRange) -> int:
    start = max(period.start, span.start)
    end = min(period.end, span.end)
    return (end - start).days + 1 if end >= start else 0


def allocate_period(
    period: HawlPeriod, spans: Sequence[HijriYearRange]
) -> dict[int, Decimal]:
    """Split one qualifying period's liability across Hijri years.

    Pro rata by overlapping days; the residual left by rounding each share to
    the money quantum is added to the final year, so the shares sum to
    ``period.zakat_due`` exactly.
    """
    if not period.qualifies or period.zakat_due == ZERO:
        return {}
    overlaps = [(span.hijri_year, _overlap_days(period, span)) for span in spans]
    overlaps = [(year, days) for year, days in overlaps if days > 0]
    if not overlaps:
        return {}
    total_days = sum(days for _, days in overlaps)
    shares: dict[int, Decimal] = {}
    running = ZERO
    for index, (year, days) in enumerate(overlaps):
        if index == len(overlaps) - 1:
            share = period.zakat_due - running
        else:
            share = quantize_money(
                period.zakat_due * Decimal(days) / Decimal(total_days), 2
            )
            running += share
        shares[year] = shares.get(year, ZERO) + share
    return shares


def build_year_rows(
    *,
    gold_periods: Sequence[HawlPeriod],
    silver_periods: Sequence[HawlPeriod],
    payments: Sequence[Payment],
    as_of: date,
    inception: date | None,
) -> tuple[YearRow, ...]:
    """One row per Hijri reporting year, reconciling exactly to the totals.

    A reporting year runs from 1 Muharram to the day before the next
    1 Muharram -- an actual Hijri calendar year, which is what the "Hijri year"
    column names.
    """
    gold_by_year: dict[int, Decimal] = defaultdict(Decimal)
    silver_by_year: dict[int, Decimal] = defaultdict(Decimal)

    def accumulate(periods: Sequence[HawlPeriod], bucket: dict[int, Decimal]) -> None:
        for period in periods:
            if not period.qualifies:
                continue
            first = hijri_year_of(period.start)
            last = hijri_year_of(period.end)
            spans = [hijri_year_range(y) for y in range(first, last + 1)]
            for year, share in allocate_period(period, spans).items():
                bucket[year] += share

    accumulate(gold_periods, gold_by_year)
    accumulate(silver_periods, silver_by_year)

    payments_by_year: dict[int, Decimal] = defaultdict(Decimal)
    for payment in payments:
        payments_by_year[hijri_year_of(payment.when)] += payment.amount

    years = set(gold_by_year) | set(silver_by_year) | set(payments_by_year)
    if inception is not None:
        years.add(hijri_year_of(inception))
    years.add(hijri_year_of(as_of))
    if not years:
        return ()

    rows: list[YearRow] = []
    gold_running = ZERO
    silver_running = ZERO
    for year in range(min(years), max(years) + 1):
        span = hijri_year_range(year)
        gold = gold_by_year.get(year, ZERO)
        silver = silver_by_year.get(year, ZERO)
        paid = payments_by_year.get(year, ZERO)
        gold_running += gold - paid
        silver_running += silver - paid
        rows.append(
            YearRow(
                hijri_year=year,
                start=span.start,
                end=min(span.end, as_of),
                gold_liability=gold,
                silver_liability=silver,
                payments=paid,
                gold_balance=gold_running,
                silver_balance=silver_running,
            )
        )
    return tuple(rows)


def wealth_at(points: Sequence[WealthPoint], when: date) -> Decimal:
    """Net wealth as at *when*, carrying the last known value forward."""
    result = ZERO
    for point in points:
        if point.when > when:
            break
        result = point.net
    return result


# ---------------------------------------------------------------------------
# Per-basis calculation detail
# ---------------------------------------------------------------------------


def _nisab_lookup(series: Sequence[PricePoint]):
    """Carry-forward nisab lookup over a price-point series."""
    known = [(point.when, point.nisab) for point in series]

    def at(when: date) -> Decimal | None:
        value = None
        for moment, nisab in known:
            if moment > when:
                break
            if nisab is not None:
                value = nisab
        return value

    return at


def nisab_change_dates(report: ZakatReport) -> list[date]:
    """Dates on which either basis's nisab value changed."""
    changes: list[date] = []
    previous: tuple[Decimal | None, Decimal | None] = (None, None)
    for gold, silver in zip(
        report.gold_nisab_series, report.silver_nisab_series, strict=False
    ):
        current = (gold.nisab, silver.nisab)
        if current != previous:
            changes.append(gold.when)
            previous = current
    return changes


def _nisab_span(at, start: date, end: date, changes: Sequence[date]) -> NisabSpan:
    """The range the nisab takes over the inclusive span *start*..*end*."""
    values = [at(start)]
    values.extend(at(moment) for moment in changes if start < moment <= end)
    present = [value for value in values if value is not None]
    if not present:
        return NisabSpan(None, None)
    return NisabSpan(min(present), max(present))


@dataclass(frozen=True, slots=True)
class PeriodRow:
    """One holding period of one marginal slice, for one basis.

    Carries the nisab **range** in force during the period: the threshold moves
    with the metal price, so a single figure would be wrong wherever it moved.
    """

    level: Decimal
    marginal: Decimal
    period: HawlPeriod
    nisab: NisabSpan
    #: True on the first row of a slice, for display grouping.
    first_of_level: bool
    #: Rows in this slice, so a template can span the level cell.
    rows_in_level: int


def basis_period_rows(report: ZakatReport, basis: Basis) -> tuple[PeriodRow, ...]:
    """Every holding period for *basis*, slice by slice, ascending by level."""
    result = report.basis(basis)
    series = (
        report.gold_nisab_series
        if basis.value == "gold"
        else report.silver_nisab_series
    )
    at = _nisab_lookup(series)
    changes = nisab_change_dates(report)

    rows: list[PeriodRow] = []
    for level in result.levels:
        for index, period in enumerate(level.periods):
            rows.append(
                PeriodRow(
                    level=level.level,
                    marginal=level.marginal,
                    period=period,
                    nisab=_nisab_span(at, period.start, period.end, changes),
                    first_of_level=index == 0,
                    rows_in_level=len(level.periods),
                )
            )
    return tuple(rows)
