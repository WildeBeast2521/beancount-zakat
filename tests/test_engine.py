"""Unit tests for the pure layered/marginal hawl engine.

These use the engine directly with a hand-built timeline, so they exercise the
model without Beancount in the way.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from beancount_zakat.constants import HIJRI_YEAR_DAYS, ZAKAT_RATE
from beancount_zakat.engine import (
    compute_levels,
    cumulative_liability,
    identify_levels,
)
from beancount_zakat.models import Basis

NISAB = Decimal("63000")


def flat_nisab(value=NISAB):
    return lambda _when: value


def run(timeline, as_of, *, nisab=NISAB, basis=Basis.GOLD, rate=ZAKAT_RATE):
    return compute_levels(
        timeline, flat_nisab(nisab), basis=basis, as_of=as_of, rate=rate
    )


class TestIdentifyLevels:
    def test_distinct_positive_values_ascending(self):
        timeline = [
            (date(2020, 1, 1), Decimal("100")),
            (date(2020, 2, 1), Decimal("300")),
            (date(2020, 3, 1), Decimal("100")),
            (date(2020, 4, 1), Decimal("200")),
        ]
        assert identify_levels(timeline) == [
            Decimal("100"),
            Decimal("200"),
            Decimal("300"),
        ]

    def test_zero_and_negative_values_are_not_levels(self):
        timeline = [
            (date(2020, 1, 1), Decimal("-500")),
            (date(2020, 2, 1), Decimal("0")),
            (date(2020, 3, 1), Decimal("400")),
        ]
        assert identify_levels(timeline) == [Decimal("400")]

    def test_empty_timeline(self):
        assert identify_levels([]) == []


class TestMarginalSlices:
    def test_marginals_are_the_gaps_between_levels(self):
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2019, 6, 1), Decimal("250000")),
        ]
        levels = run(timeline, date(2022, 1, 1))
        assert [lvl.level for lvl in levels] == [
            Decimal("100000"),
            Decimal("250000"),
        ]
        assert [lvl.marginal for lvl in levels] == [
            Decimal("100000"),
            Decimal("150000"),
        ]

    def test_each_slice_has_its_own_start_date(self):
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2020, 6, 1), Decimal("250000")),
        ]
        levels = run(timeline, date(2023, 1, 1))
        assert levels[0].periods[0].start == date(2019, 1, 1)
        assert levels[1].periods[0].start == date(2020, 6, 1)

    def test_marginals_sum_to_the_top_level(self):
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2019, 6, 1), Decimal("250000")),
            (date(2020, 1, 1), Decimal("900000")),
        ]
        levels = run(timeline, date(2021, 1, 1))
        assert sum(lvl.marginal for lvl in levels) == Decimal("900000")


class TestHawlBoundary:
    """The day count is inclusive of both endpoints."""

    START = date(2019, 1, 1)

    def _held_for(self, days: int):
        """Hold wealth for exactly *days* days, then drop below the nisab."""
        drop = self.START + timedelta(days=days)
        timeline = [
            (self.START, Decimal("100000")),
            (drop, Decimal("1000")),
        ]
        levels = run(timeline, drop + timedelta(days=1))
        return levels[-1].periods[0]

    def test_one_day_short_of_a_hawl_does_not_qualify(self):
        period = self._held_for(354)
        assert period.days == 354
        assert period.lunar_years < 1
        assert period.qualifies is False
        assert period.zakat_due == 0

    def test_exactly_at_the_hawl_boundary_qualifies(self):
        # 355 days is the first inclusive count that exceeds 354.36708.
        period = self._held_for(355)
        assert period.days == 355
        assert period.lunar_years > 1
        assert period.qualifies is True
        assert period.zakat_due > 0

    def test_past_the_boundary_qualifies(self):
        period = self._held_for(400)
        assert period.days == 400
        assert period.qualifies is True

    def test_period_end_is_the_day_before_the_change(self):
        period = self._held_for(355)
        assert period.start == self.START
        assert period.end == self.START + timedelta(days=354)
        assert (period.end - period.start).days + 1 == period.days


class TestFormula:
    def test_zakat_is_marginal_times_years_times_rate(self):
        start, end = date(2019, 1, 1), date(2021, 1, 1)
        timeline = [(start, Decimal("100000"))]
        levels = run(timeline, end)
        period = levels[0].periods[0]
        expected = (
            Decimal("100000") * (Decimal(period.days) / HIJRI_YEAR_DAYS) * ZAKAT_RATE
        ).quantize(Decimal("0.01"))
        assert period.zakat_due == expected

    def test_pro_rata_not_per_anniversary(self):
        """3.34 lunar years is charged 8.35% of the slice, not 7.5%."""
        start = date(2019, 1, 1)
        days = 1184
        timeline = [(start, Decimal("1000000"))]
        levels = run(timeline, start + timedelta(days=days - 1))
        period = levels[0].periods[0]
        assert period.days == days
        assert period.lunar_years > 3 and period.lunar_years < 4
        # Strictly more than three completed years' worth.
        assert period.zakat_due > Decimal("1000000") * 3 * ZAKAT_RATE

    def test_custom_rate_is_honoured(self):
        timeline = [(date(2019, 1, 1), Decimal("100000"))]
        default = run(timeline, date(2021, 1, 1))
        doubled = run(timeline, date(2021, 1, 1), rate=ZAKAT_RATE * 2)
        # Each period is rounded to the cent before being summed, so doubling
        # the rate can differ from doubling the result by up to one cent.
        assert abs(doubled[0].zakat_due - default[0].zakat_due * 2) <= Decimal("0.01")


class TestLevelDrop:
    def test_dropping_below_a_level_ends_only_that_slice(self):
        timeline = [
            (date(2019, 1, 1), Decimal("500000")),
            (date(2021, 1, 1), Decimal("200000")),
        ]
        levels = run(timeline, date(2023, 1, 1))
        low, high = levels[0], levels[1]
        assert low.level == Decimal("200000")
        assert high.level == Decimal("500000")
        # The lower slice never stopped running.
        assert len(low.periods) == 1
        assert low.periods[0].end == date(2023, 1, 1)
        # The upper slice closed the day before the drop.
        assert high.periods[0].end == date(2020, 12, 31)

    def test_a_sub_hawl_period_contributes_nothing(self):
        timeline = [
            (date(2019, 1, 1), Decimal("500000")),
            (date(2019, 3, 1), Decimal("100000")),
        ]
        levels = run(timeline, date(2019, 4, 1))
        high = levels[-1]
        assert high.periods[0].qualifies is False
        assert high.zakat_due == 0


class TestNisabReset:
    def test_falling_below_nisab_ends_the_period_and_is_recorded(self):
        """A slice below the nisab records the break explicitly.

        A nisab-break row can only appear for a level that is itself below the
        nisab -- above that, dropping below the nisab also drops below the
        level, and the period simply ends.
        """
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2020, 6, 1), Decimal("1000")),
            (date(2020, 7, 1), Decimal("100000")),
        ]
        levels = run(timeline, date(2023, 1, 1))
        low = levels[0]
        assert low.level == Decimal("1000")
        kinds = [(p.above_nisab, p.qualifies) for p in low.periods]
        assert kinds == [(True, True), (False, False), (True, True)]
        assert low.periods[1].zakat_due == 0
        assert "below the gold nisab" in low.periods[1].reason

    def test_dropping_below_nisab_above_the_level_just_ends_the_period(self):
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2020, 6, 1), Decimal("1000")),
            (date(2020, 7, 1), Decimal("100000")),
        ]
        levels = run(timeline, date(2023, 1, 1))
        top = levels[-1]
        assert top.level == Decimal("100000")
        assert [(p.above_nisab, p.qualifies) for p in top.periods] == [
            (True, True),
            (True, True),
        ]
        assert top.periods[0].end == date(2020, 5, 31)
        assert top.periods[1].start == date(2020, 7, 1)

    def test_elapsed_time_is_not_carried_across_a_reset(self):
        """Two 200-day stretches around a break do not add up to a hawl."""
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2019, 7, 20), Decimal("1000")),
            (date(2019, 7, 21), Decimal("100000")),
        ]
        levels = run(timeline, date(2020, 2, 5))
        top = levels[-1]
        qualifying = [p for p in top.periods if p.qualifies]
        assert qualifying == []
        assert top.zakat_due == 0

    def test_each_period_is_evaluated_independently(self):
        timeline = [
            (date(2019, 1, 1), Decimal("100000")),
            (date(2021, 1, 1), Decimal("1000")),
            (date(2021, 2, 1), Decimal("100000")),
        ]
        levels = run(timeline, date(2023, 6, 1))
        top = levels[-1]
        qualifying = [p for p in top.periods if p.qualifies]
        assert len(qualifying) == 2
        assert top.zakat_due == sum(p.zakat_due for p in qualifying)

    def test_no_nisab_available_means_nothing_qualifies(self):
        timeline = [(date(2019, 1, 1), Decimal("1000000"))]
        levels = compute_levels(
            timeline, lambda _d: None, basis=Basis.GOLD, as_of=date(2023, 1, 1)
        )
        assert cumulative_liability(levels) == 0
        assert all(not lvl.hawl_complete for lvl in levels)


class TestAsOf:
    """Open periods close at the report cutoff, not the last event."""

    def test_a_quiet_ledger_keeps_accruing(self):
        timeline = [(date(2019, 1, 1), Decimal("1000000"))]
        levels = run(timeline, date(2026, 8, 19))
        period = levels[0].periods[0]
        assert period.end == date(2026, 8, 19)
        assert period.days == (date(2026, 8, 19) - date(2019, 1, 1)).days + 1
        assert period.qualifies is True
        assert levels[0].zakat_due > 0

    def test_a_later_cutoff_never_reduces_the_liability(self):
        timeline = [(date(2019, 1, 1), Decimal("1000000"))]
        earlier = cumulative_liability(run(timeline, date(2022, 1, 1)))
        later = cumulative_liability(run(timeline, date(2026, 1, 1)))
        assert later > earlier

    def test_single_point_timeline_still_produces_a_period(self):
        timeline = [(date(2019, 1, 1), Decimal("1000000"))]
        levels = run(timeline, date(2019, 1, 1))
        assert levels[0].periods[0].days == 1
        assert levels[0].periods[0].qualifies is False


class TestBasisIndependence:
    def test_gold_and_silver_diverge_from_the_same_timeline(self):
        timeline = [(date(2019, 1, 1), Decimal("100000"))]
        as_of = date(2023, 1, 1)
        gold = compute_levels(
            timeline,
            flat_nisab(Decimal("750000")),
            basis=Basis.GOLD,
            as_of=as_of,
        )
        silver = compute_levels(
            timeline,
            flat_nisab(Decimal("63000")),
            basis=Basis.SILVER,
            as_of=as_of,
        )
        assert cumulative_liability(gold) == 0
        assert cumulative_liability(silver) > 0

    def test_basis_is_recorded_on_every_period(self):
        timeline = [(date(2019, 1, 1), Decimal("100000"))]
        levels = run(timeline, date(2023, 1, 1), basis=Basis.SILVER)
        assert all(p.basis is Basis.SILVER for lvl in levels for p in lvl.periods)


class TestEdgeCases:
    def test_empty_timeline_returns_nothing(self):
        assert run([], date(2020, 1, 1)) == ()

    def test_all_negative_timeline_returns_nothing(self):
        timeline = [(date(2019, 1, 1), Decimal("-5000"))]
        assert run(timeline, date(2021, 1, 1)) == ()

    @pytest.mark.parametrize("days", [0, 1, 353, 354, 355, 356, 700])
    def test_day_counts_are_always_inclusive(self, days):
        start = date(2019, 1, 1)
        timeline = [(start, Decimal("100000"))]
        levels = run(timeline, start + timedelta(days=days))
        assert levels[0].periods[0].days == days + 1
