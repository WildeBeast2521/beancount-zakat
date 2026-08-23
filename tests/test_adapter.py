"""Beancount adapter: signs, valuation, revaluation, payments, classification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount_zakat.models import Severity
from conftest import GOLD_NISAB, report_for


class TestLiabilitySign:
    """Liabilities carry their natural negative Beancount sign."""

    def test_borrowing_does_not_increase_zakatable_wealth(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2019-06-01 * "Borrow"
              Assets:Cash 300000.00 PKR
              Liabilities:Loan
            """,
            as_of=date(2019, 6, 1),
        )
        final = report.wealth_series[-1]
        assert final.assets == Decimal("1300000.00")
        assert final.liabilities == Decimal("-300000.00")
        assert final.net == Decimal("1000000.00")

    def test_repaying_a_loan_restores_wealth(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2019-06-01 * "Borrow"
              Assets:Cash 300000.00 PKR
              Liabilities:Loan
            2019-09-01 * "Repay"
              Liabilities:Loan 300000.00 PKR
              Assets:Cash
            """,
            as_of=date(2019, 9, 1),
        )
        final = report.wealth_series[-1]
        assert final.liabilities == Decimal("0")
        assert final.net == Decimal("1000000.00")

    def test_a_liability_can_push_net_wealth_negative(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 100000.00 PKR
              Equity:Opening
            2019-06-01 * "Big debt"
              Expenses:Other 500000.00 PKR
              Liabilities:Loan
            """,
            as_of=date(2019, 6, 1),
        )
        assert report.wealth_series[-1].net == Decimal("-400000.00")
        assert report.gold.cumulative_liability == 0


class TestRevaluation:
    """Holdings in other commodities follow their prices."""

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
        assert report.wealth_series[-1].net == Decimal("3000000.00")

    def test_the_price_move_creates_a_price_driven_point(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2022, 1, 1))
        point = next(p for p in report.wealth_series if p.when == date(2021, 1, 1))
        assert point.price_driven is True

    def test_the_last_known_price_carries_forward(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2024, 6, 1))
        # No price after 2021; the 2024 cutoff still values at the 2021 price.
        assert report.wealth_series[-1].when == date(2024, 6, 1)
        assert report.wealth_series[-1].net == Decimal("3000000.00")

    def test_no_point_is_emitted_before_inception(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 price XAUGRAM 10000.00 PKR
            2020-01-01 price XAUGRAM 20000.00 PKR
            2021-06-01 * "Buy bullion"
              Assets:Bullion 100 XAUGRAM @ 20000.00 PKR
              Equity:Opening
            """,
            as_of=date(2022, 1, 1),
        )
        assert report.wealth_series[0].when == date(2021, 6, 1)

    def test_a_missing_holding_price_is_a_blocking_error(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Buy bullion"
              Assets:Bullion 100 XAUGRAM {5000.00 PKR}
              Equity:Opening
            """,
            as_of=date(2020, 1, 1),
        )
        codes = {w.code: w.severity for w in report.warnings}
        assert codes.get("missing-holding-price") is Severity.ERROR
        assert report.has_errors


class TestAsOfFiltering:
    """Nothing after the cutoff may influence the result."""

    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        2030-01-01 * "Far future"
          Assets:Cash 5000000.00 PKR
          Equity:Opening
        """

    def test_future_transactions_are_excluded(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2026, 1, 1))
        assert report.wealth_series[-1].net == Decimal("1000000.00")
        assert max(p.when for p in report.wealth_series) == date(2026, 1, 1)

    def test_future_prices_are_excluded(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2030-01-01 price GLDTOLA 999999.00 PKR
            """,
            as_of=date(2026, 1, 1),
        )
        assert report.gold.nisab == GOLD_NISAB
        assert report.gold.price.price_date == date(2019, 1, 1)

    def test_an_earlier_cutoff_is_reproducible(self, tmp_path):
        early = report_for(tmp_path, self.LEDGER, as_of=date(2020, 1, 1))
        again = report_for(tmp_path, self.LEDGER, as_of=date(2020, 1, 1))
        assert early.gold.cumulative_liability == again.gold.cumulative_liability


class TestPayments:
    """Payment signs are preserved."""

    def test_a_refund_reduces_the_total_paid(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 * "Fund" "Zakat payment"
              Expenses:Zakat 50000.00 PKR
              Assets:Cash
            2021-02-01 * "Fund" "Refund"
              Expenses:Zakat -20000.00 PKR
              Assets:Cash
            """,
            as_of=date(2022, 1, 1),
        )
        assert [p.amount for p in report.payments] == [
            Decimal("50000.00"),
            Decimal("-20000.00"),
        ]
        assert report.payments_total == Decimal("30000.00")
        assert report.payments[1].is_reversal is True
        assert report.payments[0].is_reversal is False

    def test_payee_and_narration_are_carried_through(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 * "Local fund" "Annual zakat"
              Expenses:Zakat 50000.00 PKR
              Assets:Cash
            """,
            as_of=date(2022, 1, 1),
        )
        assert report.payments[0].payee == "Local fund"
        assert report.payments[0].narration == "Annual zakat"

    def test_no_payment_accounts_means_no_payments(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2022, 1, 1),
        )
        assert report.payments == ()
        assert report.payments_total == 0


class TestExcessPayment:
    """The balance is signed and never clamped."""

    def test_overpayment_shows_as_a_negative_balance(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 * "Fund" "Huge payment"
              Expenses:Zakat 500000.00 PKR
              Assets:Cash
            """,
            as_of=date(2021, 6, 1),
        )
        assert report.gold.remaining_or_excess < 0
        assert report.gold.status == "excess"
        assert (
            report.gold.remaining_or_excess
            == report.gold.cumulative_liability - report.gold.payments_total
        )


class TestClassification:
    def test_untagged_accounts_are_ignored(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 100000.00 PKR
              Equity:Opening
            2019-01-02 * "Untagged asset"
              Assets:Untagged 900000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
        )
        assert "Assets:Untagged" not in report.asset_accounts
        assert report.wealth_series[-1].net == Decimal("100000.00")

    def test_roles_are_read_from_metadata(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 100000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
        )
        assert set(report.asset_accounts) == {"Assets:Cash", "Assets:Bullion"}
        assert report.liability_accounts == ("Liabilities:Loan",)
        assert report.payment_accounts == ("Expenses:Zakat",)

    def test_an_invalid_role_is_reported(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Assets:Weird PKR
  beancount_zakat: "assset"
2019-01-01 open Equity:Opening
2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA 1200.00 PKR
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 100000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
            preamble=preamble,
        )
        invalid = [w for w in report.warnings if w.code == "invalid-role"]
        assert len(invalid) == 1
        assert invalid[0].account == "Assets:Weird"
        assert "asset, liability, expense" in invalid[0].detail

    def test_classification_is_exact_not_by_prefix(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Bank PKR
  beancount_zakat: "asset"
2019-01-01 open Assets:Bank:Sub PKR
2019-01-01 open Equity:Opening
2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA 1200.00 PKR
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Parent"
              Assets:Bank 100000.00 PKR
              Equity:Opening
            2019-01-02 * "Child"
              Assets:Bank:Sub 900000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
            preamble=preamble,
        )
        assert report.asset_accounts == ("Assets:Bank",)
        assert report.wealth_series[-1].net == Decimal("100000.00")


class TestPriceQuality:
    def test_a_missing_nisab_price_is_a_blocking_error(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Equity:Opening
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2023, 1, 1),
            preamble=preamble,
        )
        codes = [w.code for w in report.warnings]
        assert codes.count("missing-nisab-price") == 2
        assert report.has_errors
        assert report.gold.nisab is None
        assert report.gold.cumulative_liability == 0

    def test_a_stale_price_warns_but_is_still_used(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2023, 1, 1),
        )
        stale = [w for w in report.warnings if w.code == "stale-nisab-price"]
        assert len(stale) == 2
        assert all(w.severity is Severity.WARNING for w in stale)
        assert report.gold.nisab == GOLD_NISAB
        assert not report.has_errors

    def test_a_missing_day_reuses_the_last_known_price(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2020-06-15 price GLDTOLA 200000.00 PKR
            """,
            as_of=date(2020, 6, 20),
        )
        assert report.gold.nisab == Decimal("1500000")
        assert report.gold.price.price_date == date(2020, 6, 15)
        assert report.gold.price.stale_days == 5
