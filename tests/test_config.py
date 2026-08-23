"""Configuration parsing, validation and precedence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from beancount_zakat.config import (
    ConfigError,
    ZakatConfig,
    config_from_options,
    merge_accounts,
    parse_rate,
)
from beancount_zakat.constants import TOLA_GRAMS
from beancount_zakat.models import Role, Severity
from conftest import report_for


class TestParseRate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2.5%", Decimal("0.025")),
            ("2.500%", Decimal("0.025")),
            ("0.025", Decimal("0.025")),
            (Decimal("0.025"), Decimal("0.025")),
            ("1%", Decimal("0.01")),
            ("100%", Decimal("1")),
            ("0", Decimal("0")),
        ],
    )
    def test_accepted_forms(self, value, expected):
        assert parse_rate(value) == expected

    @pytest.mark.parametrize("value", ["2.5", 2.5, "150%", "-0.01", "abc", ""])
    def test_rejected_forms(self, value):
        with pytest.raises(ConfigError):
            parse_rate(value)

    def test_string_and_numeric_agree(self):
        """The same intent written two ways must not mean two different things."""
        assert parse_rate("0.025") == parse_rate(Decimal("0.025"))
        with pytest.raises(ConfigError):
            parse_rate("2.5")
        with pytest.raises(ConfigError):
            parse_rate(2.5)

    def test_the_error_says_how_to_fix_it(self):
        with pytest.raises(ConfigError, match=r'"2\.5%"'):
            parse_rate("2.5")


class TestOptions:
    def test_defaults(self):
        config = ZakatConfig()
        assert config.zakat_rate == Decimal("0.025")
        assert config.gold_nisab_grams == Decimal("87.48")
        assert config.silver_nisab_grams == Decimal("612.36")
        assert config.gold_nisab_tola == Decimal("7.5")
        assert config.silver_nisab_tola == Decimal("52.5")

    def test_rate_override(self):
        config, warnings = config_from_options({"zakat_rate": "2%"})
        assert config.zakat_rate == Decimal("0.02")
        assert warnings == []

    def test_tola_override_converts_to_grams(self):
        config, _ = config_from_options({"nisab_gold_tola": "8"})
        assert config.gold_nisab_grams == Decimal("8") * TOLA_GRAMS

    def test_unknown_keys_are_reported(self):
        config, warnings = config_from_options({"nisab_gold_tolla": "8"})
        assert [w.code for w in warnings] == ["unknown-option"]
        assert warnings[0].severity is Severity.WARNING
        assert config.gold_nisab_grams == Decimal("87.48")

    def test_metal_commodities_alias(self):
        config, _ = config_from_options(
            {"metal_commodities": {"XAUG": ["gold", "gram"]}}
        )
        assert config.metal_commodities == {"XAUG": ("gold", "gram")}

    def test_ambiguous_unit_is_rejected(self):
        with pytest.raises(ConfigError, match="per tola or per gram"):
            config_from_options({"metal_commodities": {"XAUG": ["gold", "ounce"]}})

    def test_unknown_metal_is_rejected(self):
        with pytest.raises(ConfigError, match="gold' or 'silver"):
            config_from_options({"metal_commodities": {"XPT": ["platinum", "gram"]}})

    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_invalid_weights_are_rejected(self, value):
        with pytest.raises(ConfigError):
            config_from_options({"gold_nisab_grams": value})

    def test_negative_staleness_is_rejected(self):
        with pytest.raises(ConfigError):
            config_from_options({"price_staleness_days": -1})


class TestAccountMerging:
    def test_roles_merge_independently(self):
        config, warnings = merge_accounts(
            ZakatConfig(),
            {Role.ASSET: ["Assets:Cash"]},
            {
                Role.LIABILITY: ["Liabilities:Loan"],
                Role.PAYMENT: ["Expenses:Zakat"],
            },
        )
        assert config.asset_accounts == ("Assets:Cash",)
        assert config.liability_accounts == ("Liabilities:Loan",)
        assert config.payment_accounts == ("Expenses:Zakat",)
        assert warnings == []

    def test_duplicates_across_sources_are_not_doubled(self):
        config, warnings = merge_accounts(
            ZakatConfig(),
            {Role.ASSET: ["Assets:Cash"]},
            {Role.ASSET: ["Assets:Cash", "Assets:Bank"]},
        )
        assert config.asset_accounts == ("Assets:Cash", "Assets:Bank")
        assert warnings == []

    def test_a_conflicting_role_warns_and_the_later_source_wins(self):
        config, warnings = merge_accounts(
            ZakatConfig(),
            {Role.ASSET: ["Assets:Thing"]},
            {Role.LIABILITY: ["Assets:Thing"]},
        )
        assert config.asset_accounts == ()
        assert config.liability_accounts == ("Assets:Thing",)
        assert [w.code for w in warnings] == ["conflicting-role"]
        assert "Using 'liability'" in warnings[0].message


class TestPrecedence:
    LEDGER = """
        2019-01-01 * "Opening"
          Assets:Cash 1000000.00 PKR
          Equity:Opening
        """

    def test_options_beat_the_defaults(self, tmp_path):
        report = report_for(
            tmp_path,
            self.LEDGER,
            as_of=date(2021, 1, 1),
            extension_options={"zakat_rate": "3%"},
        )
        assert report.zakat_rate == Decimal("0.03")

    def test_defaults_apply_when_nothing_is_set(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2021, 1, 1))
        assert report.zakat_rate == Decimal("0.025")

    def test_account_roles_all_come_from_metadata(self, tmp_path):
        report = report_for(tmp_path, self.LEDGER, as_of=date(2021, 1, 1))
        assert "Assets:Cash" in report.asset_accounts
        assert "Liabilities:Loan" in report.liability_accounts
        assert "Expenses:Zakat" in report.payment_accounts


class TestCommodityAliases:
    def test_a_per_gram_alias_gives_the_same_nisab_as_per_tola(self, tmp_path):
        preamble = """\
option "operating_currency" "PKR"
2019-01-01 open Assets:Cash PKR
  beancount_zakat: "asset"
2019-01-01 open Equity:Opening
2019-01-01 price XAUGRAM 10000.00 PKR
2019-01-01 price XAGGRAM 100.00 PKR
"""
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
            preamble=preamble,
            extension_options={
                "metal_commodities": {
                    "XAUGRAM": ["gold", "gram"],
                    "XAGGRAM": ["silver", "gram"],
                }
            },
        )
        assert report.gold.nisab == Decimal("87.48") * Decimal("10000")
        assert report.silver.nisab == Decimal("612.36") * Decimal("100")

    def test_the_default_symbols_work_without_configuration(self, tmp_path):
        report = report_for(
            tmp_path,
            """
            2019-01-01 * "Opening"
              Assets:Cash 1000000.00 PKR
              Equity:Opening
            """,
            as_of=date(2019, 6, 1),
        )
        assert report.gold.price.commodity == "GLDTOLA"
        assert report.silver.price.commodity == "SLVTOLA"
        assert report.gold.nisab == Decimal("750000")
