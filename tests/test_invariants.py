"""The invariants the whole project is required to hold.

These are asserted against several different ledgers, including the repository's
example, so they are not satisfied by one lucky fixture.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import pairwise

import pytest

from beancount_zakat.models import Basis
from conftest import report_for

LEDGERS = {
    "simple": """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        """,
    "with_debt_and_payments": """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2020-06-01 * "Borrow"
          Assets:Cash 300000.00 PKR
          Liabilities:Loan
        2021-03-01 * "Fund" "Payment"
          Expenses:Zakat 40000.00 PKR
          Assets:Cash
        2021-09-01 * "Fund" "Refund"
          Expenses:Zakat -5000.00 PKR
          Assets:Cash
        """,
    "nisab_break": """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2020-06-01 * "Spend"
          Assets:Cash -990000.00 PKR
          Expenses:Other
        2021-01-01 * "Recover"
          Assets:Cash 990000.00 PKR
          Income:Salary
        """,
    "commodity": """
        2019-01-01 * "Buy bullion"
          Assets:Bullion 100 XAUGRAM @ 10000.00 PKR
          Equity:Opening
        2019-01-01 price XAUGRAM 10000.00 PKR
        2021-01-01 price XAUGRAM 30000.00 PKR
        2023-01-01 price XAUGRAM 18000.00 PKR
        """,
    "overpaid": """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2021-01-01 * "Fund" "Huge payment"
          Expenses:Zakat 900000.00 PKR
          Assets:Cash
        """,
}

AS_OF = date(2026, 8, 19)


@pytest.fixture(params=sorted(LEDGERS))
def any_report(request, tmp_path):
    return report_for(tmp_path, LEDGERS[request.param], as_of=AS_OF)


@pytest.fixture(params=[Basis.GOLD, Basis.SILVER])
def basis(request):
    return request.param


class TestReconciliation:
    def test_overview_equals_the_sum_of_detailed_periods(self, any_report, basis):
        result = any_report.basis(basis)
        detail = sum((p.zakat_due for p in result.qualifying_periods), Decimal("0"))
        assert result.cumulative_liability == detail

    def test_level_totals_equal_the_sum_of_their_periods(self, any_report, basis):
        for level in any_report.basis(basis).levels:
            counted = [p for p in level.periods if p.above_nisab]
            assert level.zakat_due == sum((p.zakat_due for p in counted), Decimal("0"))

    def test_yearly_rows_sum_exactly_to_the_cumulative_liability(
        self, any_report, basis
    ):
        attribute = "gold_liability" if basis is Basis.GOLD else "silver_liability"
        rows = sum(
            (getattr(row, attribute) for row in any_report.year_rows),
            Decimal("0"),
        )
        assert rows == any_report.basis(basis).cumulative_liability

    def test_yearly_payment_rows_sum_to_the_payment_total(self, any_report):
        rows = sum((row.payments for row in any_report.year_rows), Decimal("0"))
        assert rows == any_report.payments_total

    def test_payment_detail_sums_to_the_payment_total(self, any_report):
        detail = sum((p.amount for p in any_report.payments), Decimal("0"))
        assert detail == any_report.payments_total
        assert detail == any_report.gold.payments_total
        assert detail == any_report.silver.payments_total

    def test_the_final_yearly_balance_equals_the_headline_balance(
        self, any_report, basis
    ):
        if not any_report.year_rows:
            pytest.skip("no reporting years")
        last = any_report.year_rows[-1]
        balance = last.gold_balance if basis is Basis.GOLD else last.silver_balance
        assert balance == any_report.basis(basis).remaining_or_excess


class TestSignedBalance:
    def test_balance_is_liability_minus_payments_and_is_never_clamped(
        self, any_report, basis
    ):
        result = any_report.basis(basis)
        assert (
            result.remaining_or_excess
            == result.cumulative_liability - result.payments_total
        )

    def test_status_matches_the_sign(self, any_report, basis):
        result = any_report.basis(basis)
        balance = result.remaining_or_excess
        expected = (
            "outstanding" if balance > 0 else "settled" if balance == 0 else "excess"
        )
        assert result.status == expected


class TestBasisIndependence:
    def test_both_bases_come_from_the_same_wealth_timeline(self, any_report):
        assert any_report.gold.net_wealth == any_report.silver.net_wealth

    def test_the_silver_nisab_is_never_the_gold_nisab_here(self, any_report):
        if any_report.gold.nisab is None:
            pytest.skip("no nisab available")
        assert any_report.gold.nisab != any_report.silver.nisab

    def test_a_lower_nisab_can_only_produce_more_liability(self, any_report):
        """Silver's threshold is lower here, so it can never owe less."""
        assert any_report.silver.nisab < any_report.gold.nisab
        assert (
            any_report.silver.cumulative_liability
            >= any_report.gold.cumulative_liability
        )


class TestAsOf:
    def test_no_event_after_the_cutoff_appears_anywhere(self, any_report):
        assert all(p.when <= AS_OF for p in any_report.wealth_series)
        assert all(p.when <= AS_OF for p in any_report.payments)
        for result in (any_report.gold, any_report.silver):
            for level in result.levels:
                for period in level.periods:
                    assert period.end <= AS_OF
                    assert period.start <= AS_OF

    def test_a_future_price_cannot_move_an_earlier_report(self, tmp_path):
        base = report_for(tmp_path, LEDGERS["simple"], as_of=date(2022, 1, 1))
        with_future = report_for(
            tmp_path,
            LEDGERS["simple"] + "\n        2029-01-01 price GLDTOLA 5000000.00 PKR\n",
            as_of=date(2022, 1, 1),
        )
        assert base.gold.nisab == with_future.gold.nisab
        assert base.gold.cumulative_liability == with_future.gold.cumulative_liability

    def test_the_report_is_deterministic(self, tmp_path):
        first = report_for(tmp_path, LEDGERS["with_debt_and_payments"], as_of=AS_OF)
        second = report_for(tmp_path, LEDGERS["with_debt_and_payments"], as_of=AS_OF)
        assert first.gold.cumulative_liability == second.gold.cumulative_liability
        assert [r.gold_liability for r in first.year_rows] == [
            r.gold_liability for r in second.year_rows
        ]


class TestValuationSafety:
    def test_missing_valuation_data_can_never_be_a_silent_zero(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2023, 1, 1),
            preamble=(
                'option "operating_currency" "PKR"\n'
                "2019-01-01 open Assets:Cash PKR\n"
                '  beancount_zakat: "asset"\n'
                "2019-01-01 open Equity:Opening\n"
            ),
        )
        assert report.gold.cumulative_liability == 0
        assert report.has_errors, "a zero from missing data must be flagged"

    def test_every_monetary_value_is_a_decimal(self, any_report):
        for result in (any_report.gold, any_report.silver):
            assert isinstance(result.cumulative_liability, Decimal)
            assert isinstance(result.remaining_or_excess, Decimal)
            for level in result.levels:
                assert isinstance(level.zakat_due, Decimal)
                for period in level.periods:
                    assert isinstance(period.zakat_due, Decimal)
                    assert isinstance(period.lunar_years, Decimal)
        for point in any_report.wealth_series:
            assert isinstance(point.net, Decimal)
        for payment in any_report.payments:
            assert isinstance(payment.amount, Decimal)


class TestPresentationCannotChangeDomain:
    def test_rendering_the_cli_leaves_the_report_untouched(self, any_report):
        from pathlib import Path

        from beancount_zakat.cli import render_report

        before = (
            any_report.gold.cumulative_liability,
            any_report.silver.cumulative_liability,
            any_report.payments_total,
            tuple(r.gold_liability for r in any_report.year_rows),
        )
        render_report(any_report, Path("x.beancount"), width=120)
        after = (
            any_report.gold.cumulative_liability,
            any_report.silver.cumulative_liability,
            any_report.payments_total,
            tuple(r.gold_liability for r in any_report.year_rows),
        )
        assert before == after

    def test_rendering_the_csv_leaves_the_report_untouched(self, any_report):
        from beancount_zakat.csv_export import render_all

        before = any_report.gold.cumulative_liability
        list(render_all(any_report))
        assert any_report.gold.cumulative_liability == before

    def test_the_report_is_frozen(self, any_report):
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            any_report.as_of = date(2000, 1, 1)


class TestExampleLedger:
    def test_the_shipped_example_reconciles(self, example_report):
        for basis in (Basis.GOLD, Basis.SILVER):
            result = example_report.basis(basis)
            detail = sum((p.zakat_due for p in result.qualifying_periods), Decimal("0"))
            attribute = "gold_liability" if basis is Basis.GOLD else "silver_liability"
            rows = sum(
                (getattr(r, attribute) for r in example_report.year_rows),
                Decimal("0"),
            )
            assert detail == rows == result.cumulative_liability

    def test_the_example_exercises_the_interesting_cases(self, example_report):
        assert example_report.gold.cumulative_liability > 0
        assert example_report.silver.cumulative_liability > 0
        assert (
            example_report.silver.cumulative_liability
            != example_report.gold.cumulative_liability
        )
        assert any(p.is_reversal for p in example_report.payments)
        assert any(p.price_driven for p in example_report.wealth_series)
        assert any(p.liabilities < 0 for p in example_report.wealth_series)
        assert any(
            not p.above_nisab
            for level in example_report.gold.levels
            for p in level.periods
        )


class TestPerBasisDetail:
    """The per-basis detail tables must reconcile to their own headline."""

    def test_rows_sum_exactly_to_the_cumulative_liability(self, any_report, basis):
        from beancount_zakat.reporting import basis_period_rows

        total = sum(
            (row.period.zakat_due for row in basis_period_rows(any_report, basis)),
            Decimal("0"),
        )
        assert total == any_report.basis(basis).cumulative_liability

    def test_rows_never_overlap_within_a_level(self, any_report, basis):
        from beancount_zakat.reporting import basis_period_rows

        by_level: dict[Decimal, list] = {}
        for row in basis_period_rows(any_report, basis):
            by_level.setdefault(row.level, []).append(row.period)
        for periods in by_level.values():
            for earlier, later in pairwise(periods):
                assert earlier.end < later.start

    def test_both_bases_share_the_same_levels(self, any_report):
        """Levels come from the wealth timeline, which is nisab-independent."""
        gold = {level.level for level in any_report.gold.levels}
        silver = {level.level for level in any_report.silver.levels}
        assert gold == silver

    def test_hawl_status_is_not_running_exactly_when_below_the_nisab(
        self, any_report, basis
    ):
        from beancount_zakat.models import HawlStatus
        from beancount_zakat.reporting import basis_period_rows

        for row in basis_period_rows(any_report, basis):
            period = row.period
            assert (period.hawl is HawlStatus.NOT_RUNNING) == (not period.above_nisab)

    def test_a_reset_period_counts_no_elapsed_time(self, any_report, basis):
        """The clock was stopped, so its duration is irrelevant."""
        from beancount_zakat.reporting import basis_period_rows

        for row in basis_period_rows(any_report, basis):
            if not row.period.above_nisab:
                assert row.period.lunar_years == 0
                assert row.period.zakat_due == 0
                assert row.period.days > 0

    def test_only_a_complete_hawl_earns_zakat(self, any_report, basis):
        from beancount_zakat.models import HawlStatus
        from beancount_zakat.reporting import basis_period_rows

        for row in basis_period_rows(any_report, basis):
            if row.period.zakat_due > 0:
                assert row.period.hawl is HawlStatus.COMPLETE

    def test_the_nisab_range_brackets_the_period(self, any_report, basis):
        from beancount_zakat.reporting import basis_period_rows

        for row in basis_period_rows(any_report, basis):
            if row.nisab.known:
                assert row.nisab.low <= row.nisab.high


class TestHawlStrip:
    """The strip must depict exactly what the table says."""

    def test_segments_match_the_periods(self, any_report, basis):
        from beancount_zakat.chart import build_hawl_strip

        result = any_report.basis(basis)
        if not result.levels:
            pytest.skip("no levels")
        strip = build_hawl_strip(
            result.levels,
            start=any_report.wealth_series[0].when,
            end=any_report.as_of,
            currency=any_report.operating_currency,
            basis=basis.value,
        )
        drawn = {row.level for row in strip.rows}
        expected = {level.level for level in result.levels if level.periods}
        if not strip.truncated:
            assert drawn == expected
        else:
            assert drawn <= expected

    def test_segment_states_are_the_period_states(self, any_report, basis):
        from beancount_zakat.chart import build_hawl_strip

        result = any_report.basis(basis)
        if not result.levels:
            pytest.skip("no levels")
        strip = build_hawl_strip(
            result.levels,
            start=any_report.wealth_series[0].when,
            end=any_report.as_of,
            currency=any_report.operating_currency,
            basis=basis.value,
        )
        by_level = {level.level: level for level in result.levels}
        for row in strip.rows:
            states = [segment.status for segment in row.segments]
            expected = [
                period.hawl.value.replace(" ", "-")
                for period in by_level[row.level].periods
            ]
            assert states == expected

    def test_segments_stay_inside_the_plot(self, any_report, basis):
        from beancount_zakat.chart import build_hawl_strip

        result = any_report.basis(basis)
        if not result.levels:
            pytest.skip("no levels")
        strip = build_hawl_strip(
            result.levels,
            start=any_report.wealth_series[0].when,
            end=any_report.as_of,
            currency=any_report.operating_currency,
            basis=basis.value,
        )
        right = strip.width - strip.pad_right
        for row in strip.rows:
            for segment in row.segments:
                assert segment.x1 >= strip.pad_left - 0.01
                assert segment.x2 <= right + 2
                assert segment.x2 > segment.x1


class TestResetBands:
    def test_bands_mark_exactly_the_below_nisab_stretches(self, any_report, basis):
        """The shading must line up with what the table calls "not running"."""
        from beancount_zakat.chart import build_basis_chart

        series = (
            any_report.gold_nisab_series
            if basis is Basis.GOLD
            else any_report.silver_nisab_series
        )
        nisab = [(p.when, p.nisab) for p in series if p.nisab is not None]
        if not nisab or not any_report.wealth_series:
            pytest.skip("nothing to chart")
        chart = build_basis_chart(
            wealth=[(p.when, p.net) for p in any_report.wealth_series],
            nisab=nisab,
            basis=basis.value,
            as_of=any_report.as_of,
            currency=any_report.operating_currency,
        )
        ever_below = any(
            not period.above_nisab
            for level in any_report.basis(basis).levels
            for period in level.periods
        )
        assert bool(chart.reset_bands) == ever_below
        for left, right in chart.reset_bands:
            assert right > left


class TestNoSingleNisabIsPresentedAsAuthoritative:
    """The threshold moves, so no summary may imply one figure applied throughout."""

    def test_the_report_exposes_the_full_nisab_series(self, any_report):
        assert len(any_report.gold_nisab_series) == len(any_report.wealth_series)
        assert len(any_report.silver_nisab_series) == len(any_report.wealth_series)

    def test_the_as_of_nisab_is_only_one_point_of_the_series(self, any_report):
        if any_report.gold.nisab is None:
            pytest.skip("no nisab available")
        last = any_report.gold_nisab_series[-1]
        assert any_report.gold.nisab == last.nisab

    def test_a_moving_price_produces_a_moving_nisab(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 price GLDTOLA 200000.00 PKR
            2023-01-01 price GLDTOLA 300000.00 PKR
            """,
            as_of=AS_OF,
        )
        thresholds = {point.nisab for point in report.gold_nisab_series if point.nisab}
        assert len(thresholds) == 3
        assert thresholds == {
            Decimal("750000"),
            Decimal("1500000"),
            Decimal("2250000"),
        }


class TestStackedComposition:
    """The chart is a picture of the result, never a second opinion about it."""

    def test_the_stack_reconciles_with_net_wealth_at_every_date(self, any_report):
        from beancount_zakat.chart import composition_series

        if not any_report.wealth_series:
            pytest.skip("nothing to chart")
        composition = [(p.when, p.by_account) for p in any_report.wealth_series]
        bands = composition_series(
            composition, liability_accounts=any_report.liability_accounts
        )
        for index, point in enumerate(any_report.wealth_series):
            drawn = sum((band.points[index][1] for band in bands), start=Decimal("0"))
            assert drawn == point.net

    def test_liability_bands_are_never_negated_a_second_time(self, any_report):
        """Beancount already signs debt negative; the chart must not flip it."""
        from beancount_zakat.chart import composition_series

        if not any_report.liability_accounts:
            pytest.skip("no debt in this ledger")
        composition = [(p.when, p.by_account) for p in any_report.wealth_series]
        bands = composition_series(
            composition, liability_accounts=any_report.liability_accounts
        )
        for band in bands:
            if band.role != "liability":
                continue
            assert all(value <= 0 for _, value in band.points)

    def test_drawing_the_chart_leaves_the_report_untouched(self, any_report):
        from beancount_zakat.chart import build_stacked_chart

        before = [(p.when, p.net, dict(p.by_account)) for p in any_report.wealth_series]
        build_stacked_chart(
            composition=[(p.when, p.by_account) for p in any_report.wealth_series],
            liability_accounts=any_report.liability_accounts,
            overlays=[
                (
                    "wealth",
                    "Net zakatable wealth",
                    [(p.when, p.net) for p in any_report.wealth_series],
                )
            ],
            as_of=any_report.as_of,
            currency=any_report.operating_currency,
        )
        after = [(p.when, p.net, dict(p.by_account)) for p in any_report.wealth_series]
        assert before == after
