"""The JSON handed to the browser so it can re-draw a chart interactively."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from beancount_zakat.chart import below_nisab_spans, chart_payload

WEALTH = [
    (date(2020, 1, 1), Decimal("100")),
    (date(2021, 1, 1), Decimal("40")),
    (date(2022, 1, 1), Decimal("300")),
]
NISAB = [(date(2020, 1, 1), Decimal("50"))]


class TestPayload:
    def test_it_is_json_serialisable(self):
        payload = chart_payload(
            series=[("wealth", "Net zakatable wealth", WEALTH)],
            start=date(2020, 1, 1),
            end=date(2022, 6, 1),
            currency="PKR",
        )
        assert json.loads(json.dumps(payload)) == payload

    def test_amounts_stay_exact_as_strings(self):
        payload = chart_payload(
            series=[("wealth", "w", [(date(2020, 1, 1), Decimal("0.1"))])],
            start=date(2020, 1, 1),
            end=date(2020, 1, 2),
            currency="PKR",
        )
        assert payload["series"][0]["points"] == [["2020-01-01", "0.1"]]

    def test_empty_series_are_dropped(self):
        payload = chart_payload(
            series=[("wealth", "w", WEALTH), ("gold", "g", [])],
            start=date(2020, 1, 1),
            end=date(2022, 6, 1),
            currency="PKR",
        )
        assert [s["key"] for s in payload["series"]] == ["wealth"]

    def test_bands_are_carried_as_iso_pairs(self):
        spans = below_nisab_spans(WEALTH, NISAB, date(2022, 6, 1))
        payload = chart_payload(
            series=[("wealth", "w", WEALTH)],
            start=date(2020, 1, 1),
            end=date(2022, 6, 1),
            currency="PKR",
            bands=spans,
        )
        assert payload["bands"] == [["2021-01-01", "2022-01-01"]]


class TestBelowNisabSpans:
    def test_it_finds_the_stretch_below_the_threshold(self):
        assert below_nisab_spans(WEALTH, NISAB, date(2022, 6, 1)) == [
            (date(2021, 1, 1), date(2022, 1, 1))
        ]

    def test_no_wealth_means_no_spans(self):
        assert below_nisab_spans([], NISAB, date(2022, 6, 1)) == []

    def test_a_span_still_open_at_the_cutoff_runs_to_it(self):
        wealth = [(date(2020, 1, 1), Decimal("10"))]
        assert below_nisab_spans(wealth, NISAB, date(2021, 1, 1)) == [
            (date(2020, 1, 1), date(2021, 1, 1))
        ]
