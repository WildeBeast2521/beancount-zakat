"""Integration: loading real ledger files, includes, and edge-case states."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount import loader

from beancount_zakat import build_report
from beancount_zakat.hijri import HijriRangeError
from beancount_zakat.models import Severity
from conftest import report_for


class TestIncludes:
    def test_metadata_and_prices_spread_across_included_files(self, tmp_path):
        (tmp_path / "accounts.beancount").write_text(
            "2019-01-01 open Assets:Cash PKR\n"
            '  beancount_zakat: "asset"\n'
            "2019-01-01 open Liabilities:Loan PKR\n"
            '  beancount_zakat: "liability"\n'
            "2019-01-01 open Expenses:Zakat PKR\n"
            '  beancount_zakat: "expense"\n'
            "2019-01-01 open Equity:Opening\n",
            encoding="utf-8",
        )
        (tmp_path / "prices.beancount").write_text(
            "2019-01-01 price GLDTOLA 100000.00 PKR\n"
            "2019-01-01 price SLVTOLA 1200.00 PKR\n",
            encoding="utf-8",
        )
        (tmp_path / "txns.beancount").write_text(
            '2019-01-01 * "Opening"\n'
            "  Assets:Cash 1000000.00 PKR\n"
            "  Equity:Opening\n"
            '2022-01-01 * "Fund" "Payment"\n'
            "  Expenses:Zakat 20000.00 PKR\n"
            "  Assets:Cash\n",
            encoding="utf-8",
        )
        root = tmp_path / "main.beancount"
        root.write_text(
            'option "operating_currency" "PKR"\n'
            'include "accounts.beancount"\n'
            'include "prices.beancount"\n'
            'include "txns.beancount"\n',
            encoding="utf-8",
        )
        entries, errors, options = loader.load_file(str(root))
        assert not errors
        report = build_report(entries, options, as_of=date(2023, 1, 1))
        assert report.asset_accounts == ("Assets:Cash",)
        assert report.liability_accounts == ("Liabilities:Loan",)
        assert report.payment_accounts == ("Expenses:Zakat",)
        assert report.gold.nisab == Decimal("750000")
        assert report.payments_total == Decimal("20000.00")
        assert report.gold.cumulative_liability > 0


class TestEmptyStates:
    def test_a_ledger_with_no_tagged_accounts(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Untagged only"
              Assets:Untagged 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2021, 1, 1),
            preamble=(
                'option "operating_currency" "PKR"\n'
                "2019-01-01 open Assets:Untagged PKR\n"
                "2019-01-01 open Equity:Opening\n"
                "2019-01-01 price GLDTOLA 100000.00 PKR\n"
                "2019-01-01 price SLVTOLA 1200.00 PKR\n"
            ),
        )
        assert report.is_empty
        assert report.has_errors
        codes = [w.code for w in report.warnings]
        assert "no-classified-accounts" in codes
        blocking = next(
            w for w in report.warnings if w.code == "no-classified-accounts"
        )
        assert "beancount_zakat" in blocking.detail

    def test_tagged_accounts_with_no_postings(self, tmp_path):
        report = report_for(tmp_path, "", as_of=date(2021, 1, 1))
        assert report.is_empty
        assert report.gold.cumulative_liability == 0
        assert report.year_rows == () or all(
            r.gold_liability == 0 for r in report.year_rows
        )
        assert not any(w.code == "no-classified-accounts" for w in report.warnings)

    def test_wealth_always_below_both_nisabs(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Small balance"
              Assets:Cash 1000.00 PKR
              Equity:Opening
            """,
            as_of=date(2024, 1, 1),
        )
        assert report.gold.cumulative_liability == 0
        assert report.silver.cumulative_liability == 0
        assert report.gold.qualifies_now is False
        assert report.silver.qualifies_now is False
        assert not report.has_errors


class TestNisabThresholds:
    LEDGER_TEMPLATE = """
        2019-01-01 * "Opening"
          Assets:Cash {amount} PKR
          Equity:Opening
        """

    @pytest.mark.parametrize(
        ("amount", "gold_qualifies"),
        [
            ("749999.99", False),
            ("750000.00", True),
            ("750000.01", True),
        ],
    )
    def test_gold_nisab_boundary_is_inclusive(self, tmp_path, amount, gold_qualifies):
        report = report_for(
            tmp_path,
            self.LEDGER_TEMPLATE.format(amount=amount),
            as_of=date(2021, 1, 1),
        )
        assert report.gold.qualifies_now is gold_qualifies
        assert (report.gold.cumulative_liability > 0) is gold_qualifies

    @pytest.mark.parametrize(
        ("amount", "silver_qualifies"),
        [("62999.99", False), ("63000.00", True), ("63000.01", True)],
    )
    def test_silver_nisab_boundary_is_inclusive(
        self, tmp_path, amount, silver_qualifies
    ):
        report = report_for(
            tmp_path,
            self.LEDGER_TEMPLATE.format(amount=amount),
            as_of=date(2021, 1, 1),
        )
        assert report.silver.qualifies_now is silver_qualifies

    def test_a_nisab_price_change_alone_can_end_a_hawl(self, tmp_path):
        """No transaction, but the threshold rises above the wealth."""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 800000.00 PKR
              Equity:Opening
            2019-06-01 price GLDTOLA 200000.00 PKR
            """,
            as_of=date(2021, 1, 1),
        )
        # Gold nisab jumps 750,000 -> 1,500,000 with no posting at all.
        dates = [p.when for p in report.wealth_series]
        assert date(2019, 6, 1) in dates
        assert report.gold.cumulative_liability == 0
        assert report.silver.cumulative_liability > 0

    def test_a_nisab_price_fall_can_start_a_hawl(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 400000.00 PKR
              Equity:Opening
            2019-06-01 price GLDTOLA 40000.00 PKR
            """,
            as_of=date(2022, 1, 1),
        )
        assert report.gold.nisab == Decimal("300000")
        assert report.gold.cumulative_liability > 0
        period = report.gold.levels[-1].periods[-1]
        assert period.start == date(2019, 6, 1)


class TestOperatingCurrency:
    def test_a_missing_operating_currency_warns(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 USD
              Equity:Opening
            """,
            as_of=date(2021, 1, 1),
            preamble=(
                "2019-01-01 open Assets:Cash USD\n"
                '  beancount_zakat: "asset"\n'
                "2019-01-01 open Equity:Opening\n"
                "2019-01-01 price GLDTOLA 1000.00 USD\n"
                "2019-01-01 price SLVTOLA 12.00 USD\n"
            ),
        )
        assert report.operating_currency == "USD"
        assert any(w.code == "no-operating-currency" for w in report.warnings)


class TestRangeGuards:
    def test_an_as_of_outside_the_calendar_range_is_refused(self, tmp_path):
        with pytest.raises(HijriRangeError, match="Umm al-Qura"):
            report_for(
                tmp_path,
                """
                2019-01-01 * "Opening"
                  Assets:Cash 1000000.00 PKR
                  Equity:Opening
                """,
                as_of=date(2200, 1, 1),
            )


class TestForeignCurrencyPayments:
    def test_a_payment_in_another_currency_is_converted_and_recorded(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Assets:USD USD
2019-01-01 open Expenses:Zakat USD
  beancount_zakat: "expense"
2019-01-01 open Equity:Opening
2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA 1200.00 PKR
2021-01-01 price USD 280.00 PKR
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 * "Fund" "Payment abroad"
              Expenses:Zakat 100.00 USD
              Assets:USD
            """,
            as_of=date(2022, 1, 1),
            preamble=preamble,
        )
        payment = report.payments[0]
        assert payment.original_amount == Decimal("100.00")
        assert payment.original_currency == "USD"
        assert payment.rate == Decimal("280.00")
        assert payment.amount == Decimal("28000.00")
        assert report.payments_total == Decimal("28000.00")

    def test_an_unconvertible_payment_is_reported_not_dropped_silently(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Assets:USD USD
2019-01-01 open Expenses:Zakat USD
  beancount_zakat: "expense"
2019-01-01 open Equity:Opening
2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA 1200.00 PKR
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            2021-01-01 * "Fund" "Payment abroad"
              Expenses:Zakat 100.00 USD
              Assets:USD
            """,
            as_of=date(2022, 1, 1),
            preamble=preamble,
        )
        assert report.payments == ()
        errors = [w for w in report.warnings if w.code == "missing-payment-price"]
        assert len(errors) == 1
        assert errors[0].severity is Severity.ERROR
