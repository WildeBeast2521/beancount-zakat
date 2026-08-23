"""Property tests over randomly generated timelines.

The example-based tests pin known cases; these assert structural properties
that must hold for *any* wealth history, and so catch the shapes nobody thought
to write a fixture for.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from beancount_zakat.constants import ZAKAT_RATE
from beancount_zakat.engine import (
    compute_levels,
    cumulative_liability,
    identify_levels,
)
from beancount_zakat.models import Basis

NISAB = Decimal("1000")
TRIALS = 200


def run(timeline, as_of, nisab: Decimal = NISAB):
    return compute_levels(
        timeline,
        lambda _when: nisab,
        basis=Basis.GOLD,
        as_of=as_of,
        rate=ZAKAT_RATE,
    )


def random_timeline(rng: random.Random):
    """A plausible wealth history and a cutoff at or after its last event."""
    when = date(2019, 1, 1)
    timeline = []
    for _ in range(rng.randint(1, 8)):
        timeline.append((when, Decimal(rng.choice([0, 500, 1200, 3000, 5000, 9000]))))
        when += timedelta(days=rng.randint(1, 700))
    as_of = when + timedelta(days=rng.randint(0, 900))
    return timeline, as_of


@pytest.fixture(params=range(TRIALS))
def case(request):
    return random_timeline(random.Random(request.param))


class TestPeriodStructure:
    def test_periods_are_ordered_and_never_overlap(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for earlier, later in zip(level.periods, level.periods[1:], strict=False):
                assert earlier.end < later.start

    def test_no_period_is_reversed(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for period in level.periods:
                assert period.end >= period.start

    def test_day_counts_are_always_inclusive(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for period in level.periods:
                assert period.days == (period.end - period.start).days + 1

    def test_nothing_extends_past_the_cutoff(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for period in level.periods:
                assert period.start <= as_of
                assert period.end <= as_of

    def test_a_period_below_nisab_never_qualifies(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for period in level.periods:
                if not period.above_nisab:
                    assert not period.qualifies
                    assert period.zakat_due == 0

    def test_qualifying_means_at_least_one_lunar_year(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            for period in level.periods:
                assert period.qualifies == (
                    period.lunar_years >= 1 and period.above_nisab
                )


class TestLevelStructure:
    def test_marginals_partition_the_top_level(self, case):
        timeline, as_of = case
        levels = run(timeline, as_of)
        if not levels:
            return
        assert sum(level.marginal for level in levels) == levels[-1].level

    def test_levels_are_the_distinct_positive_values_ascending(self, case):
        timeline, as_of = case
        levels = run(timeline, as_of)
        assert [level.level for level in levels] == identify_levels(timeline)

    def test_every_marginal_is_positive(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            assert level.marginal > 0

    def test_level_totals_equal_their_periods(self, case):
        timeline, as_of = case
        for level in run(timeline, as_of):
            counted = [p for p in level.periods if p.above_nisab]
            assert level.zakat_due == sum((p.zakat_due for p in counted), Decimal("0"))


class TestMonotonicity:
    TIMELINE = [
        (date(2019, 1, 1), Decimal("5000")),
        (date(2021, 6, 1), Decimal("9000")),
        (date(2023, 1, 1), Decimal("500")),
        (date(2023, 6, 1), Decimal("9000")),
    ]

    def test_a_later_cutoff_never_lowers_the_liability(self):
        """Time only ever adds hawl; it can never take it away."""
        previous = None
        for year in range(2019, 2031):
            for month in (1, 7):
                total = cumulative_liability(run(self.TIMELINE, date(year, month, 1)))
                if previous is not None:
                    assert total >= previous
                previous = total
        assert previous > 0

    def test_a_lower_nisab_never_lowers_the_liability(self):
        as_of = date(2026, 1, 1)
        previous = None
        for nisab in (
            Decimal("20000"),
            Decimal("8000"),
            Decimal("4000"),
            Decimal("1000"),
            Decimal("1"),
        ):
            total = cumulative_liability(run(self.TIMELINE, as_of, nisab))
            if previous is not None:
                assert total >= previous
            previous = total


class TestScaling:
    def test_scaling_wealth_and_nisab_scales_the_liability(self):
        """Ten times the wealth against ten times the nisab owes ten times.

        Not exactly ten times: each period is rounded to the cent before being
        summed, so a little rounding noise is expected and correct.
        """
        timeline = [
            (date(2019, 1, 1), Decimal("5000")),
            (date(2021, 1, 1), Decimal("12000")),
        ]
        as_of = date(2026, 1, 1)
        single = cumulative_liability(run(timeline, as_of, Decimal("1000")))
        scaled = cumulative_liability(
            run([(d, v * 10) for d, v in timeline], as_of, Decimal("10000"))
        )
        assert single > 0
        assert abs(scaled - single * 10) <= Decimal("0.10")

    def test_liability_never_exceeds_the_naive_upper_bound(self, case):
        """Zakat cannot exceed rate x peak wealth x elapsed lunar years."""
        timeline, as_of = case
        levels = run(timeline, as_of)
        if not levels:
            return
        peak = max(value for _, value in timeline)
        span = (as_of - timeline[0][0]).days + 1
        bound = peak * (Decimal(span) / Decimal("354.36708")) * ZAKAT_RATE
        assert cumulative_liability(levels) <= bound + Decimal("1")

    def test_liability_is_never_negative(self, case):
        timeline, as_of = case
        assert cumulative_liability(run(timeline, as_of)) >= 0
