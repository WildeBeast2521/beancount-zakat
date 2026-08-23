"""CSV schema, determinism and round-tripping."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from beancount_zakat.csv_export import (
    CSV_FILES,
    DETAIL_COLUMNS,
    METADATA_COLUMNS,
    NISAB_HISTORY_COLUMNS,
    PAYMENT_COLUMNS,
    WARNING_COLUMNS,
    YEARLY_COLUMNS,
    export,
    render_all,
)
from conftest import report_for

LEDGER = """
2021-01-01 price GLDTOLA 150000.00 PKR
2021-01-01 price SLVTOLA 1800.00 PKR
2023-01-01 price GLDTOLA 220000.00 PKR
2023-01-01 price SLVTOLA 2600.00 PKR

2019-01-01 * "Opening"
  Assets:Cash 1000000.00 PKR
  Equity:Opening
2020-06-01 * "Spend"
  Assets:Cash -990000.00 PKR
  Expenses:Other
2021-01-01 * "Recover"
  Assets:Cash 990000.00 PKR
  Income:Salary
2022-01-01 * "Fund" "Payment"
  Expenses:Zakat 30000.00 PKR
  Assets:Cash
2022-06-01 * "Fund" "Refund, with a comma"
  Expenses:Zakat -5000.00 PKR
  Assets:Cash
"""


@pytest.fixture
def report(tmp_path):
    return report_for(tmp_path, LEDGER, as_of=date(2026, 8, 19))


@pytest.fixture
def files(report) -> dict[str, str]:
    return dict(render_all(report))


class TestStructure:
    def test_all_files_are_produced_in_order(self, files):
        assert tuple(files) == CSV_FILES

    @pytest.mark.parametrize(
        ("name", "columns"),
        [
            ("metadata.csv", METADATA_COLUMNS),
            ("warnings.csv", WARNING_COLUMNS),
            ("nisab_history.csv", NISAB_HISTORY_COLUMNS),
            ("yearly_summary.csv", YEARLY_COLUMNS),
            ("detail_gold.csv", DETAIL_COLUMNS),
            ("detail_silver.csv", DETAIL_COLUMNS),
            ("payments.csv", PAYMENT_COLUMNS),
        ],
    )
    def test_headers_match_the_declared_schema(self, files, name, columns):
        header = next(csv.reader(io.StringIO(files[name])))
        assert tuple(header) == tuple(columns)

    def test_rfc_line_endings(self, files):
        for name, content in files.items():
            assert "\r\n" in content, name
            assert not content.replace("\r\n", "").count("\n"), name

    def test_utf8_round_trip(self, files):
        for content in files.values():
            assert content.encode("utf-8").decode("utf-8") == content


class TestRoundTrip:
    def test_every_file_parses_back(self, files):
        for name, content in files.items():
            rows = list(csv.DictReader(io.StringIO(content)))
            assert isinstance(rows, list), name

    def test_monetary_columns_parse_as_decimals(self, files):
        rows = list(csv.DictReader(io.StringIO(files["yearly_summary.csv"])))
        for row in rows:
            for column in (
                "gold_liability",
                "silver_liability",
                "payments",
                "gold_balance",
                "silver_balance",
            ):
                Decimal(row[column])

    def test_yearly_rows_still_sum_to_the_total_after_a_round_trip(self, files, report):
        rows = list(csv.DictReader(io.StringIO(files["yearly_summary.csv"])))
        total = sum(Decimal(row["gold_liability"]) for row in rows)
        assert total == report.gold.cumulative_liability

    def test_payment_running_total_reconciles_after_a_round_trip(self, files, report):
        rows = list(csv.DictReader(io.StringIO(files["payments.csv"])))
        assert Decimal(rows[-1]["running_total"]) == report.payments_total
        assert sum(Decimal(r["amount"]) for r in rows) == report.payments_total

    def test_a_comma_in_a_narration_is_quoted_and_survives(self, files):
        rows = list(csv.DictReader(io.StringIO(files["payments.csv"])))
        narrations = [r["narration"] for r in rows]
        assert "Refund, with a comma" in narrations

    def test_detail_rows_sum_to_each_cumulative_liability(self, files, report):
        for name, result in (
            ("detail_gold.csv", report.gold),
            ("detail_silver.csv", report.silver),
        ):
            rows = list(csv.DictReader(io.StringIO(files[name])))
            total = sum(Decimal(r["zakat_due"]) for r in rows)
            assert total == result.cumulative_liability


class TestContent:
    def test_metadata_carries_the_essentials(self, files, report):
        data = dict(csv.reader(io.StringIO(files["metadata.csv"])))
        assert data["as_of"] == report.as_of.isoformat()
        assert data["operating_currency"] == report.operating_currency
        assert Decimal(data["zakat_rate"]) == report.zakat_rate
        assert Decimal(data["gold_cumulative_liability"]) == (
            report.gold.cumulative_liability
        )
        assert data["gold_status"] == report.gold.status
        assert "alternative bases" in data["note"]

    def test_no_locale_grouping_or_currency_symbols(self, files):
        for name in ("yearly_summary.csv", "detail_gold.csv", "payments.csv"):
            rows = list(csv.reader(io.StringIO(files[name])))
            for row in rows[1:]:
                for cell in row:
                    if cell and cell.replace(".", "").replace("-", "").isdigit():
                        assert "," not in cell

    def test_signed_balances_and_statuses_are_exported(self, files):
        rows = list(csv.DictReader(io.StringIO(files["yearly_summary.csv"])))
        assert all(
            row["gold_balance_status"] in {"outstanding", "settled", "excess"}
            for row in rows
        )

    def test_reversals_are_flagged(self, files):
        rows = list(csv.DictReader(io.StringIO(files["payments.csv"])))
        assert any(row["is_reversal"] == "true" for row in rows)
        assert any(row["is_reversal"] == "false" for row in rows)

    def test_both_hijri_and_gregorian_fields_are_present(self, files):
        yearly = list(csv.DictReader(io.StringIO(files["yearly_summary.csv"])))
        assert yearly[0]["hijri_year"].isdigit()
        date.fromisoformat(yearly[0]["gregorian_start"])
        detail = list(csv.DictReader(io.StringIO(files["detail_gold.csv"])))
        assert detail[0]["hijri_year_start"].isdigit()
        date.fromisoformat(detail[0]["period_start"])

    def test_each_file_carries_only_its_own_basis(self, files):
        for name, basis in (
            ("detail_gold.csv", "gold"),
            ("detail_silver.csv", "silver"),
        ):
            rows = list(csv.DictReader(io.StringIO(files[name])))
            assert {row["basis"] for row in rows} == {basis}

    def test_the_nisab_is_a_range_not_a_single_figure(self, files):
        rows = list(csv.DictReader(io.StringIO(files["detail_gold.csv"])))
        varying = [r for r in rows if r["nisab_low"] != r["nisab_high"]]
        assert varying, "expected at least one period spanning a price change"
        for row in rows:
            if row["nisab_low"]:
                assert Decimal(row["nisab_low"]) <= Decimal(row["nisab_high"])

    def test_hawl_has_three_states_and_agrees_with_the_nisab_flag(self, files):
        """A stretch below the nisab is "not running", never "incomplete"."""
        valid = {"complete", "incomplete", "not running"}
        for name in ("detail_gold.csv", "detail_silver.csv"):
            rows = list(csv.DictReader(io.StringIO(files[name])))
            for row in rows:
                assert row["hawl"] in valid
                if row["above_nisab"] == "false":
                    assert row["hawl"] == "not running"
                    assert Decimal(row["zakat_due"]) == 0
                    assert Decimal(row["lunar_years"]) == 0
                if Decimal(row["zakat_due"]) > 0:
                    assert row["hawl"] == "complete"

    def test_a_reset_period_reports_zero_elapsed_years(self, files):
        """A reset stopped the clock, so no part of the span counts."""
        rows = list(csv.DictReader(io.StringIO(files["detail_gold.csv"])))
        reset = [r for r in rows if r["hawl"] == "not running"]
        assert reset, "fixture should contain a below-nisab stretch"
        for row in reset:
            assert Decimal(row["lunar_years"]) == 0
            assert int(row["days"]) > 0

    def test_every_period_carries_its_explanation(self, files):
        rows = list(csv.DictReader(io.StringIO(files["detail_gold.csv"])))
        assert all(row["reason"].strip() for row in rows)

    def test_nisab_history_records_each_change_with_its_price(self, files):
        rows = list(csv.DictReader(io.StringIO(files["nisab_history.csv"])))
        assert len(rows) >= 2, "the example ledger moves the threshold"
        for row in rows:
            date.fromisoformat(row["in_force_from"])
            date.fromisoformat(row["gold_price_date"])
            assert Decimal(row["gold_nisab"]) > 0
            assert Decimal(row["silver_nisab"]) > 0
            # the price used is never dated after the date it applies from
            assert date.fromisoformat(row["gold_price_date"]) <= (
                date.fromisoformat(row["in_force_from"])
            )

    def test_nisab_history_thresholds_are_distinct_between_rows(self, files):
        rows = list(csv.DictReader(io.StringIO(files["nisab_history.csv"])))
        pairs = [(r["gold_nisab"], r["silver_nisab"]) for r in rows]
        assert len(pairs) == len(set(pairs)), "each row must be a real change"

    def test_metadata_marks_point_in_time_fields_explicitly(self, files):
        data = dict(csv.reader(io.StringIO(files["metadata.csv"])))
        assert "gold_nisab_at_as_of" in data
        assert "gold_nisab" not in data
        assert "nisab_history.csv" in data["note"]


class TestDeterminism:
    def test_two_renders_are_byte_identical(self, report):
        assert dict(render_all(report)) == dict(render_all(report))

    def test_two_reports_of_the_same_ledger_agree(self, tmp_path):
        first = report_for(tmp_path, LEDGER, as_of=date(2026, 8, 19))
        second = report_for(tmp_path, LEDGER, as_of=date(2026, 8, 19))
        assert dict(render_all(first)) == dict(render_all(second))


class TestExport:
    def test_directory_export(self, report, tmp_path):
        target = tmp_path / "csvout"
        written = export(report, target)
        assert [p.name for p in written] == list(CSV_FILES)
        assert all(p.exists() for p in written)
        text = (target / "yearly_summary.csv").read_bytes().decode("utf-8")
        assert text.startswith("hijri_year,")
        assert b"\r\n" in (target / "yearly_summary.csv").read_bytes()

    def test_zip_export(self, report, tmp_path):
        target = tmp_path / "zakat.zip"
        written = export(report, target)
        assert written == [target]
        with zipfile.ZipFile(target) as archive:
            assert archive.namelist() == list(CSV_FILES)
            content = archive.read("metadata.csv").decode("utf-8")
            assert content.startswith("key,value")

    def test_export_creates_missing_directories(self, report, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        export(report, target)
        assert (target / "metadata.csv").exists()


class TestEmptyReport:
    def test_an_empty_report_still_produces_valid_files(self, tmp_path):
        report = report_for(tmp_path, "", as_of=date(2021, 1, 1))
        files = dict(render_all(report))
        assert tuple(files) == CSV_FILES
        for name, content in files.items():
            rows = list(csv.DictReader(io.StringIO(content)))
            assert isinstance(rows, list), name
        data = dict(csv.reader(io.StringIO(files["metadata.csv"])))
        assert data["as_of"] == "2021-01-01"
