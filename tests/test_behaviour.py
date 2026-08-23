"""Behaviour specifications for the parts of the model that are easy to get wrong.

Each of these pins a decision that has a plausible-looking alternative, so a
future change that quietly adopts the alternative fails here rather than in
someone's zakat figure.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from beancount_zakat.models import HawlStatus, Severity
from conftest import report_for


class TestLiabilitySign:
    """Net wealth is ``sum(assets) + sum(liabilities)``.

    A Beancount liability already carries a negative balance, so debt is
    subtracted by plain addition. Negating it a second time would make
    borrowing *increase* zakatable wealth.
    """

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2019-06-01 * "Borrow"
          Assets:Cash 300000.00 PKR
          Liabilities:Loan
        """

    def test_borrowing_leaves_zakatable_wealth_unchanged(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2019, 6, 1))
        final = report.wealth_series[-1]
        assert final.assets == Decimal("1300000.00")
        assert final.liabilities == Decimal("-300000.00")
        assert final.net == Decimal("1000000.00")

    def test_repayment_restores_the_previous_position(self, tmp_path):
        report = report_for(
            tmp_path,
            self.LEDGER
            + """
        2019-09-01 * "Repay"
          Liabilities:Loan 300000.00 PKR
          Assets:Cash
        """,
            as_of=date(2019, 9, 1),
        )
        assert report.wealth_series[-1].net == Decimal("1000000.00")


class TestAccrualToTheCutoff:
    """Open periods close at ``as_of``, not at the last ledger event."""

    QUIET = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        """

    def test_a_quiet_ledger_keeps_accruing(self, tmp_path):
        report = report_for(tmp_path, self.QUIET, as_of=date(2026, 8, 20))
        assert report.gold.cumulative_liability > 0
        assert report.silver.cumulative_liability > 0
        assert report.wealth_series[-1].when == date(2026, 8, 20)

    def test_the_period_runs_to_the_cutoff(self, tmp_path):
        report = report_for(tmp_path, self.QUIET, as_of=date(2026, 8, 20))
        period = report.gold.levels[0].periods[0]
        assert period.start == date(2019, 1, 1)
        assert period.end == date(2026, 8, 20)


class TestFutureEntriesAreExcluded:
    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2030-01-01 * "Far future"
          Assets:Cash 5000000.00 PKR
          Equity:Opening
        """

    def test_an_entry_after_the_cutoff_cannot_inflate_the_result(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2026, 1, 1))
        assert report.wealth_series[-1].net == Decimal("1000000.00")
        assert max(p.when for p in report.wealth_series) == date(2026, 1, 1)


class TestRevaluation:
    """A holding follows its price, whether or not you traded."""

    LEDGER = """
        2019-01-01 * "Buy bullion"
          Assets:Bullion 100 XAUGRAM @ 10000.00 PKR
          Equity:Opening
        2019-01-01 price XAUGRAM 10000.00 PKR
        2021-01-01 price XAUGRAM 30000.00 PKR
        """

    def test_a_price_move_alone_changes_net_wealth(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2022, 1, 1))
        by_date = {p.when: p.net for p in report.wealth_series}
        assert by_date[date(2019, 1, 1)] == Decimal("1000000.00")
        assert by_date[date(2021, 1, 1)] == Decimal("3000000.00")


class TestHawlBoundary:
    """The day count is inclusive of both endpoints.

    Counting exclusively at either end loses a day, which is enough to discard
    an entire slice sitting on the boundary.
    """

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2019-12-22 * "Spend most of it"
          Assets:Cash -990000.00 PKR
          Expenses:Other
        """

    def test_a_355_day_hold_completes_a_hawl(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2019, 12, 22))
        top = report.gold.levels[-1]
        period = top.periods[0]
        assert period.start == date(2019, 1, 1)
        assert period.end == date(2019, 12, 21)
        assert period.days == 355
        assert period.qualifies is True
        assert top.zakat_due > 0

    def test_a_354_day_hold_does_not(self, tmp_path):
        """The boundary is still a boundary; inclusivity is not a free pass."""
        start = date(2019, 1, 1)
        drop = start + timedelta(days=354)
        report = report_for(
            tmp_path,
            f"""
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            {drop.isoformat()} * "Spend most of it"
              Assets:Cash -990000.00 PKR
              Expenses:Other
            """,
            as_of=drop,
        )
        period = report.gold.levels[-1].periods[0]
        assert period.days == 354
        assert period.qualifies is False


class TestPaymentSigns:
    """A negative posting to a payment account is a refund, not a payment."""

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2021-01-01 * "Fund" "Payment"
          Expenses:Zakat 50000.00 PKR
          Assets:Cash
        2021-02-01 * "Fund" "Refund"
          Expenses:Zakat -20000.00 PKR
          Assets:Cash
        """

    def test_a_refund_reduces_the_total_paid(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2022, 1, 1))
        assert report.payments_total == Decimal("30000.00")
        assert report.payments[1].is_reversal is True


class TestSignedBalance:
    """The balance is signed and never clamped, so credit stays visible."""

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2021-01-01 * "Fund" "Very large payment"
          Expenses:Zakat 500000.00 PKR
          Assets:Cash
        """

    def test_overpayment_shows_as_a_negative_balance(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2021, 6, 1))
        assert report.gold.remaining_or_excess < 0
        assert report.gold.status == "excess"
        assert (
            report.gold.remaining_or_excess
            == report.gold.cumulative_liability - report.gold.payments_total
        )


class TestMissingPriceIsNeverASilentZero:
    PREAMBLE = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Equity:Opening
"""

    def test_a_ledger_with_no_metal_prices_reports_an_error(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2023, 1, 1),
            preamble=self.PREAMBLE,
        )
        errors = [f for f in report.warnings if f.code == "missing-nisab-price"]
        assert len(errors) == 2
        assert all(f.severity is Severity.ERROR for f in errors)
        assert report.has_errors
        # The liability is zero, but it is not *silently* zero.
        assert report.gold.cumulative_liability == 0
        assert report.gold.nisab is None


class TestRolesMergeIndependently:
    def test_declaring_one_role_does_not_suppress_another(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2022-01-01 * "Fund" "Payment"
              Expenses:Zakat 10000.00 PKR
              Assets:Cash
            """,
            as_of=date(2022, 6, 1),
        )
        assert "Assets:Cash" in report.asset_accounts
        assert "Liabilities:Loan" in report.liability_accounts
        assert "Expenses:Zakat" in report.payment_accounts
        assert report.payments_total == Decimal("10000.00")


class TestYearlyRowsReconcile:
    """A period spanning several years is split, not repeated in each of them."""

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2020-06-01 * "Spend"
          Assets:Cash -900000.00 PKR
          Expenses:Other
        2020-07-01 * "Recover"
          Assets:Cash 900000.00 PKR
          Income:Salary
        """

    def test_rows_sum_exactly_to_the_cumulative_total(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2023, 1, 1))
        for result, attribute in (
            (report.gold, "gold_liability"),
            (report.silver, "silver_liability"),
        ):
            rows = sum(getattr(row, attribute) for row in report.year_rows)
            assert rows == result.cumulative_liability

    def test_a_multi_year_period_contributes_to_several_rows(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2023, 1, 1))
        earning = [r for r in report.year_rows if r.silver_liability > 0]
        assert len(earning) > 1


class TestResetIsNotIncompleteness:
    """A stretch below the nisab stopped the clock; it did not fall short."""

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2020-06-01 * "Spend"
          Assets:Cash -990000.00 PKR
          Expenses:Other
        2022-01-01 * "Recover"
          Assets:Cash 990000.00 PKR
          Income:Salary
        """

    def test_a_long_below_nisab_stretch_is_not_running(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2026, 1, 1))
        resets = [
            period
            for level in report.gold.levels
            for period in level.periods
            if not period.above_nisab
        ]
        assert resets, "the fixture should contain a below-nisab stretch"
        for period in resets:
            assert period.hawl is HawlStatus.NOT_RUNNING
            assert period.days > 365, "long enough to be mistaken for a hawl"
            assert period.lunar_years == 0
            assert period.zakat_due == 0
