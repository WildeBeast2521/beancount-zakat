"""CLI behaviour, output shape and exit codes."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from beancount_zakat.cli import (
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VALIDATION,
    main,
    render_report,
)
from conftest import write_ledger

LEDGER = """
2019-01-01 * "Opening"
  Assets:Cash 1000000.00 PKR
  Equity:Opening
2022-01-01 * "Fund" "Payment"
  Expenses:Zakat 30000.00 PKR
  Assets:Cash
"""


@pytest.fixture
def ledger(tmp_path) -> Path:
    return write_ledger(tmp_path, LEDGER)


class TestExitCodes:
    def test_success(self, ledger, capsys):
        assert main([str(ledger), "--as-of", "2024-01-01"]) == EXIT_OK

    def test_missing_ledger(self, tmp_path, capsys):
        code = main([str(tmp_path / "nope.beancount")])
        assert code == EXIT_USAGE
        assert "ledger not found" in capsys.readouterr().err

    def test_a_validation_error_returns_one(self, tmp_path, capsys):
        path = write_ledger(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            preamble=(
                'option "operating_currency" "PKR"\n'
                "2019-01-01 open Assets:Cash PKR\n"
                '  beancount_zakat: "asset"\n'
                "2019-01-01 open Equity:Opening\n"
            ),
        )
        assert main([str(path), "--as-of", "2024-01-01"]) == EXIT_VALIDATION
        out = capsys.readouterr().out
        assert "[ERROR]" in out
        assert "no gold price is available" in out.lower()

    def test_a_bad_as_of_is_a_usage_error(self, ledger):
        with pytest.raises(SystemExit) as exc:
            main([str(ledger), "--as-of", "not-a-date"])
        assert exc.value.code == 2


class TestOutput:
    def _run(self, ledger, *args, capsys, **kwargs):
        main([str(ledger), "--as-of", "2024-01-01", "--width", "140", *args])
        return capsys.readouterr().out

    def test_every_section_is_present(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        for heading in (
            "REPORT",
            "DATA QUALITY",
            "SUMMARY",
            "YEARLY SUMMARY",
            "RECONCILIATION",
            "NISAB HISTORY (",
            "GOLD NISAB -- CALCULATION DETAIL",
            "SILVER NISAB -- CALCULATION DETAIL",
            "PAYMENTS",
        ):
            assert heading in out, heading

    def test_the_as_of_date_is_stated(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        assert re.search(r"As of\s+: 2024-01-01", out)

    def test_gold_and_silver_appear_side_by_side(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        assert "Gold basis" in out and "Silver basis" in out
        assert "ALTERNATIVE bases" in out

    def test_the_reconciliation_agrees(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        assert re.search(r"Reconciles\s+: yes", out)
        assert not re.search(r"Reconciles\s+: NO", out)

    def test_the_disclaimer_is_printed(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        assert "informational only" in out
        assert "qualified scholar" in out

    def test_quiet_omits_the_detail_tables(self, ledger, capsys):
        out = self._run(ledger, "--quiet", capsys=capsys)
        assert "SUMMARY" in out
        assert "CALCULATION DETAIL" not in out
        assert "NISAB HISTORY (" not in out

    @pytest.mark.parametrize("basis", ["gold", "silver"])
    def test_basis_selection(self, ledger, capsys, basis):
        out = self._run(ledger, "--basis", basis, capsys=capsys)
        other = "silver" if basis == "gold" else "gold"
        assert f"{basis.upper()} NISAB -- CALCULATION DETAIL" in out
        assert f"{other.upper()} NISAB -- CALCULATION DETAIL" not in out

    def test_each_basis_gets_its_own_detail_table(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        assert "GOLD NISAB -- CALCULATION DETAIL" in out
        assert "SILVER NISAB -- CALCULATION DETAIL" in out
        assert out.count("Nisab in force") == 2

    def test_the_summary_quotes_no_single_nisab(self, ledger, capsys):
        """The threshold moves, so a summary figure would misrepresent history."""
        out = self._run(ledger, capsys=capsys)
        summary = out.split("SUMMARY")[1].split("YEARLY SUMMARY")[0]
        assert "Nisab threshold" not in summary
        assert "Price date" not in summary
        assert "NISAB HISTORY (" in out

    def test_a_below_nisab_stretch_reads_as_not_running(self, ledger, capsys):
        """Never "incomplete": the clock was reset, not merely short."""
        out = self._run(ledger, capsys=capsys)
        detail = out.split("GOLD NISAB -- CALCULATION DETAIL")[1]
        assert "not running" in detail

    def test_nisab_history_shows_each_change_with_its_price(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        history = out.split("NISAB HISTORY (")[1].split("GOLD NISAB --")[0]
        assert "In force from" in history
        assert "Gold price" in history and "Gold nisab" in history
        assert "Quoted" in history

    def test_hawl_not_haul(self, ledger, capsys):
        out = self._run(ledger, capsys=capsys)
        # The ledger path is echoed verbatim and pytest names tmp dirs after
        # the test, so exclude it from the spelling check.
        prose = "\n".join(line for line in out.splitlines() if "Ledger" not in line)
        assert "haul" not in prose.lower()
        assert "hawl" in prose.lower()


class TestWidth:
    @pytest.mark.parametrize("width", [40, 60, 80, 100, 140, 220])
    def test_output_never_exceeds_the_requested_width(self, ledger, capsys, width):
        main([str(ledger), "--as-of", "2024-01-01", "--width", str(width)])
        out = capsys.readouterr().out
        too_long = [line for line in out.splitlines() if len(line) > width]
        assert not too_long, (
            f"{len(too_long)} line(s) exceed width {width}; "
            f"longest is {max(len(x) for x in too_long)}"
        )

    def test_narrow_output_falls_back_to_stacked_blocks(self, ledger, capsys):
        main([str(ledger), "--as-of", "2024-01-01", "--width", "50"])
        out = capsys.readouterr().out
        assert "Gold basis   :" in out
        assert "-- TOTAL --" in out


class TestLocaleIndependence:
    def test_numbers_do_not_depend_on_the_process_locale(
        self, ledger, capsys, monkeypatch
    ):
        import locale

        main([str(ledger), "--as-of", "2024-01-01", "--width", "140"])
        baseline = capsys.readouterr().out
        try:
            locale.setlocale(locale.LC_ALL, "de_DE.UTF-8")
        except locale.Error:
            pytest.skip("de_DE.UTF-8 not available")
        try:
            main([str(ledger), "--as-of", "2024-01-01", "--width", "140"])
            other = capsys.readouterr().out
        finally:
            locale.setlocale(locale.LC_ALL, "C")
        assert baseline == other

    def test_thousands_and_decimal_separators_are_fixed(self, ledger, capsys):
        main([str(ledger), "--as-of", "2024-01-01", "--width", "140"])
        out = capsys.readouterr().out
        assert re.search(r"\d{1,3}(,\d{3})+\.\d{2}", out)


class TestCsvOption:
    def test_csv_directory_is_written(self, ledger, tmp_path, capsys):
        target = tmp_path / "out"
        assert main([str(ledger), "--as-of", "2024-01-01", "--csv", str(target)]) == 0
        names = sorted(p.name for p in target.iterdir())
        assert names == [
            "detail_gold.csv",
            "detail_silver.csv",
            "metadata.csv",
            "nisab_history.csv",
            "payments.csv",
            "warnings.csv",
            "yearly_summary.csv",
        ]

    def test_csv_zip_is_written(self, ledger, tmp_path):
        import zipfile

        target = tmp_path / "out.zip"
        assert main([str(ledger), "--as-of", "2024-01-01", "--csv", str(target)]) == 0
        with zipfile.ZipFile(target) as archive:
            assert "yearly_summary.csv" in archive.namelist()


class TestRenderIsPure:
    def test_render_report_returns_a_string_and_prints_nothing(self, ledger, capsys):
        from beancount import loader

        from beancount_zakat import build_report

        entries, _, options = loader.load_file(str(ledger))
        report = build_report(entries, options, as_of=date(2024, 1, 1))
        text = render_report(report, ledger, width=120)
        assert isinstance(text, str)
        assert capsys.readouterr().out == ""
        assert text.endswith("\n")
