"""The layered / marginal hawl engine.

This module holds the calculation itself and imports neither Beancount nor
Fava. The model:

1. Take a timeline of ``(date, net_zakatable_wealth)`` points.
2. Identify every distinct **positive** net-wealth level and sort them ascending.
3. Treat each level as a marginal slice: ``marginal = level - previous_level``.
4. Track a **separate** holding period (hawl) for each slice, because each
   portion of wealth was acquired at a different time.
5. A slice's period runs only while net wealth stays at or above that slice's
   level *and* total net wealth stays at or above the nisab for the basis.
6. Dropping below the level ends that slice's period; dropping below the nisab
   ends it and is recorded explicitly as a nisab break.
7. Every contiguous period is evaluated entirely on its own -- elapsed time is
   never carried across a reset.
8. Once a period reaches the one-lunar-year minimum::

       zakat_due = marginal x elapsed_lunar_years x zakat_rate

   The hawl is the *condition* that makes wealth zakatable, not a restriction
   limiting liability to whole years; past it, liability accrues in proportion
   to the time the wealth was held.
9. The whole process runs independently for gold and for silver.

Two conventions are load-bearing and easy to get subtly wrong:

* **``as_of``.** Open periods close at the report's cutoff date, not at the last
  ledger event, so wealth keeps accruing hawl through a quiet ledger. The caller
  is responsible for excluding entries dated after ``as_of``.
* **Inclusive day count.** A period covering ``start``..``end`` lasts
  ``(end - start).days + 1`` days. Counting exclusively at either end loses a
  day, which is enough to discard an entire slice sitting on the hawl boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from decimal import Decimal

from .constants import HIJRI_YEAR_DAYS, ZAKAT_RATE
from .formatting import quantize_money
from .models import Basis, HawlPeriod, LevelResult

#: Given a date, return the nisab in the operating currency, or ``None`` when
#: no price is available at or before that date.
NisabLookup = Callable[[date], "Decimal | None"]

ZERO = Decimal("0")


def identify_levels(timeline: Sequence[tuple[date, Decimal]]) -> list[Decimal]:
    """Distinct positive net-wealth levels, ascending.

    Every distinct positive value the timeline ever takes becomes a level, and
    the gap to the previous level is a marginal slice with its own independent
    hawl.
    """
    return sorted({value for _, value in timeline if value > ZERO})


def _period_days(start: date, end: date) -> int:
    """Inclusive day count for a period covering *start* through *end*."""
    return (end - start).days + 1


def _lunar_years(days: int) -> Decimal:
    return Decimal(days) / HIJRI_YEAR_DAYS


def _quantize(value: Decimal) -> Decimal:
    """Round to the money quantum, half away from zero."""
    return quantize_money(value, 2)


def _make_period(
    *,
    basis: Basis,
    level: Decimal,
    marginal: Decimal,
    start: date,
    end: date,
    rate: Decimal,
    reason: str,
) -> HawlPeriod:
    """Build a qualifying-or-not holding period for one slice."""
    days = _period_days(start, end)
    years = _lunar_years(days)
    qualifies = years >= Decimal("1")
    due = _quantize(marginal * years * rate) if qualifies else ZERO
    if qualifies:
        detail = (
            f"Held for {days} days ({years:.4f} lunar years), which completes "
            f"the one-year hawl. {reason}"
        )
    else:
        shortfall = HIJRI_YEAR_DAYS - Decimal(days)
        detail = (
            f"Held for {days} days ({years:.4f} lunar years) -- "
            f"{shortfall:.2f} days short of a complete hawl, so this period "
            f"contributes nothing. {reason}"
        )
    return HawlPeriod(
        basis=basis,
        level=level,
        marginal=marginal,
        start=start,
        end=end,
        days=days,
        lunar_years=years,
        at_level=True,
        above_nisab=True,
        qualifies=qualifies,
        zakat_due=due,
        reason=detail.strip(),
    )


def _make_nisab_break(
    *,
    basis: Basis,
    level: Decimal,
    marginal: Decimal,
    start: date,
    end: date,
) -> HawlPeriod:
    """Record a stretch during which total wealth sat below the nisab."""
    days = _period_days(start, end)
    return HawlPeriod(
        basis=basis,
        level=level,
        marginal=marginal,
        start=start,
        end=end,
        days=days,
        lunar_years=ZERO,
        at_level=True,
        above_nisab=False,
        qualifies=False,
        zakat_due=ZERO,
        reason=(
            f"Total net wealth fell below the {basis.value} nisab for {days} "
            "days, so the hawl was reset and this stretch earns no zakat."
        ),
    )


def _walk_level(
    resolved: Sequence[tuple[date, Decimal, Decimal | None, date]],
    *,
    basis: Basis,
    level: Decimal,
    marginal: Decimal,
    as_of: date,
    rate: Decimal,
) -> list[HawlPeriod]:
    """Cut the timeline into periods for one marginal slice.

    Kept as a module-level function taking everything it needs as arguments:
    closures over a loop variable are a classic source of silent breakage on
    refactor, and this is a financial calculation.
    """
    periods: list[HawlPeriod] = []
    hawl_start: date | None = None
    break_start: date | None = None

    for when, net, nisab, previous_day in resolved:
        above_nisab = nisab is not None and net >= nisab
        at_level = net >= level

        if at_level and above_nisab:
            if break_start is not None and previous_day >= break_start:
                periods.append(
                    _make_nisab_break(
                        basis=basis,
                        level=level,
                        marginal=marginal,
                        start=break_start,
                        end=previous_day,
                    )
                )
            break_start = None
            if hawl_start is None:
                hawl_start = when
            continue

        if hawl_start is not None and previous_day >= hawl_start:
            periods.append(
                _make_period(
                    basis=basis,
                    level=level,
                    marginal=marginal,
                    start=hawl_start,
                    end=previous_day,
                    rate=rate,
                    reason=(
                        "Ended because total net wealth fell below the nisab."
                        if at_level
                        else "Ended because net wealth fell below this level."
                    ),
                )
            )
        hawl_start = None

        if at_level:
            # Still at this level, but the nisab is no longer met: the hawl
            # resets and the stretch is recorded so the reason is visible.
            if break_start is None:
                break_start = when
        elif break_start is not None:
            if previous_day >= break_start:
                periods.append(
                    _make_nisab_break(
                        basis=basis,
                        level=level,
                        marginal=marginal,
                        start=break_start,
                        end=previous_day,
                    )
                )
            break_start = None

    # Anything still running closes at the report cutoff, not at the last
    # ledger event: a quiet ledger still accrues hawl.
    if hawl_start is not None and as_of >= hawl_start:
        periods.append(
            _make_period(
                basis=basis,
                level=level,
                marginal=marginal,
                start=hawl_start,
                end=as_of,
                rate=rate,
                reason="Still running at the report date.",
            )
        )
    if break_start is not None and as_of >= break_start:
        periods.append(
            _make_nisab_break(
                basis=basis,
                level=level,
                marginal=marginal,
                start=break_start,
                end=as_of,
            )
        )
    return periods


def compute_levels(
    timeline: Sequence[tuple[date, Decimal]],
    nisab_at: NisabLookup,
    *,
    basis: Basis,
    as_of: date,
    rate: Decimal = ZAKAT_RATE,
) -> tuple[LevelResult, ...]:
    """Run the layered/marginal model for one nisab basis.

    Args:
        timeline: ``(date, net_zakatable_wealth)`` points, ascending and
            deduplicated by date. Must not extend beyond *as_of*.
        nisab_at: Nisab lookup for this basis.
        basis: Which basis is being evaluated.
        as_of: Report cutoff. Open periods are closed here.
        rate: Zakat rate as a fraction.

    Returns:
        One :class:`~beancount_zakat.models.LevelResult` per marginal slice,
        ascending by level.
    """
    if not timeline:
        return ()

    levels = identify_levels(timeline)
    if not levels:
        return ()

    # Resolve the nisab -- and the preceding day, which is pure date
    # arithmetic -- once per timeline date rather than once per (level, date)
    # pair. The walk below runs once per level, so anything hoisted here is
    # saved a few thousand times over on a real ledger.
    one_day = timedelta(days=1)
    resolved = [(when, net, nisab_at(when), when - one_day) for when, net in timeline]

    results: list[LevelResult] = []
    previous_level = ZERO

    for level in levels:
        marginal = level - previous_level
        previous_level = level
        periods = _walk_level(
            resolved,
            basis=basis,
            level=level,
            marginal=marginal,
            as_of=as_of,
            rate=rate,
        )
        counted = tuple(period for period in periods if period.above_nisab)
        results.append(
            LevelResult(
                basis=basis,
                level=level,
                marginal=marginal,
                periods=tuple(periods),
                total_days=sum(period.days for period in counted),
                total_lunar_years=sum((period.lunar_years for period in counted), ZERO),
                hawl_complete=any(period.qualifies for period in counted),
                zakat_due=sum((period.zakat_due for period in counted), ZERO),
            )
        )

    return tuple(results)


def cumulative_liability(levels: Sequence[LevelResult]) -> Decimal:
    """Total zakat liability across every marginal slice."""
    return sum((level.zakat_due for level in levels), ZERO)
