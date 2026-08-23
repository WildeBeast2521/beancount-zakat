"""The stacked composition chart.

Presentation only: these tests pin the picture to the numbers the engine
already produced. The load-bearing one is
:meth:`TestStackedChart.test_the_stack_height_is_net_wealth` --- if the drawn
stack could disagree with the calculated net, the chart would be lying.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from beancount_zakat.chart import (
    MAX_STACK_BANDS,
    build_stacked_chart,
    chart_payload,
    composition_series,
)

COMPOSITION = [
    (
        date(2020, 1, 1),
        {"Assets:Bank": Decimal("100"), "Assets:Gold": Decimal("40")},
    ),
    (
        date(2021, 1, 1),
        {
            "Assets:Bank": Decimal("120"),
            "Assets:Gold": Decimal("40"),
            "Liabilities:Card": Decimal("-30"),
        },
    ),
    (
        date(2022, 1, 1),
        {
            "Assets:Bank": Decimal("60"),
            "Assets:Gold": Decimal("90"),
            "Liabilities:Card": Decimal("-10"),
        },
    ),
]
DEBTS = ("Liabilities:Card",)


def _net(values: dict[str, Decimal]) -> Decimal:
    return sum(values.values(), Decimal("0"))


class TestCompositionSeries:
    def test_every_account_covers_every_date(self):
        series = composition_series(COMPOSITION, liability_accounts=DEBTS)
        assert {s.account for s in series} == {
            "Assets:Bank",
            "Assets:Gold",
            "Liabilities:Card",
        }
        for account in series:
            assert [when for when, _ in account.points] == [
                when for when, _ in COMPOSITION
            ]

    def test_an_account_absent_from_a_date_carries_zero_there(self):
        card = next(
            s
            for s in composition_series(COMPOSITION, liability_accounts=DEBTS)
            if s.account == "Liabilities:Card"
        )
        assert card.points[0] == (date(2020, 1, 1), Decimal("0"))

    def test_liabilities_are_tagged_and_come_last(self):
        series = composition_series(COMPOSITION, liability_accounts=DEBTS)
        roles = [s.role for s in series]
        assert roles == ["asset", "asset", "liability"]

    def test_the_largest_holding_sits_nearest_the_zero_line(self):
        series = composition_series(COMPOSITION, liability_accounts=DEBTS)
        assert series[0].account == "Assets:Bank"

    def test_palette_slots_are_unique(self):
        series = composition_series(COMPOSITION, liability_accounts=DEBTS)
        assert [s.index for s in series] == list(range(len(series)))

    def test_accounts_that_never_held_anything_are_dropped(self):
        composition = [
            (
                date(2020, 1, 1),
                {"Assets:Bank": Decimal("1"), "Assets:Old": Decimal("0")},
            )
        ]
        series = composition_series(composition)
        assert [s.account for s in series] == ["Assets:Bank"]

    def test_the_smallest_accounts_are_pooled_beyond_the_limit(self):
        values = {
            f"Assets:A{i}": Decimal(str(100 - i)) for i in range(MAX_STACK_BANDS + 5)
        }
        series = composition_series([(date(2020, 1, 1), values)])
        assert len(series) == MAX_STACK_BANDS + 1
        pooled = series[-1]
        assert pooled.key == "acct-other-asset"
        assert "5 accounts" in pooled.label
        # The pool carries the sum of what it replaced, so nothing is lost.
        assert pooled.points[0][1] == sum(
            sorted(values.values())[:5], start=Decimal("0")
        )

    def test_nothing_at_all_is_an_empty_result(self):
        assert composition_series([]) == ()


class TestStackedChart:
    def build(self, **kwargs):
        return build_stacked_chart(
            composition=COMPOSITION,
            liability_accounts=DEBTS,
            as_of=date(2022, 6, 1),
            currency="PKR",
            **kwargs,
        )

    def test_every_account_becomes_a_band(self):
        chart = self.build()
        assert [band.account for band in chart.stack] == [
            "Assets:Bank",
            "Assets:Gold",
            "Liabilities:Card",
        ]
        assert all(
            band.area.startswith("M ") and band.area.endswith("Z")
            for band in chart.stack
        )

    @staticmethod
    def _ys(band):
        """Every y coordinate in a band's path. SVG y grows *downwards*."""
        return [
            float(token)
            for token in band.area.replace("M", " ")
            .replace("L", " ")
            .replace("Z", " ")
            .split()
        ][1::2]

    def test_liabilities_never_stray_above_the_zero_line(self):
        chart = self.build()
        for band in chart.stack:
            if band.role != "liability":
                continue
            assert min(self._ys(band)) >= chart.zero_y - 0.01

    def test_bands_are_split_by_sign_so_none_overlaps_another(self):
        """An overdrawn asset belongs below the axis, not on top of its peers.

        Stacking a negative balance upwards would draw it back over whatever
        sits beneath it, which reads as a hole rather than as a debt.
        """
        overdrawn = [
            (
                date(2020, 1, 1),
                {"Assets:Gold": Decimal("100"), "Assets:Bank": Decimal("-30")},
            )
        ]
        chart = build_stacked_chart(
            composition=overdrawn, as_of=date(2020, 6, 1), currency="PKR"
        )
        bank = next(band for band in chart.stack if band.account == "Assets:Bank")
        gold = next(band for band in chart.stack if band.account == "Assets:Gold")
        assert min(self._ys(bank)) >= chart.zero_y - 0.01
        assert max(self._ys(gold)) <= chart.zero_y + 0.01

    def test_the_axis_goes_negative_only_when_something_is_owed(self):
        assert self.build().y_min < 0
        assets_only = [
            (when, {k: v for k, v in values.items() if not k.startswith("Liab")})
            for when, values in COMPOSITION
        ]
        chart = build_stacked_chart(
            composition=assets_only,
            as_of=date(2022, 6, 1),
            currency="PKR",
        )
        assert chart.y_min == 0

    def test_the_stack_height_is_net_wealth(self):
        """The drawn stack must reconcile with the calculated net, exactly.

        Assets stack up from zero and liabilities down from it, so the signed
        sum of every band at a date is the net figure the engine used. Anything
        else would be a chart that contradicts the number beside it.
        """
        series = composition_series(COMPOSITION, liability_accounts=DEBTS)
        for index, (_, values) in enumerate(COMPOSITION):
            drawn = sum(
                (account.points[index][1] for account in series), start=Decimal("0")
            )
            assert drawn == _net(values)

    def test_the_overlay_lines_are_passed_through_untouched(self):
        chart = self.build(
            overlays=[
                (
                    "wealth",
                    "Net zakatable wealth",
                    [(date(2020, 1, 1), Decimal("140"))],
                ),
                ("gold", "Gold nisab", [(date(2020, 1, 1), Decimal("90"))]),
            ]
        )
        assert [s.key for s in chart.series] == ["wealth", "gold"]
        assert chart.series[0].dashed is False
        assert chart.series[1].dashed is True

    def test_an_empty_series_is_not_drawn(self):
        chart = self.build(overlays=[("gold", "Gold nisab", [])])
        assert chart.series == ()

    def test_reset_spans_become_x_ranges(self):
        chart = self.build(reset_spans=[(date(2021, 1, 1), date(2021, 7, 1))])
        assert len(chart.reset_bands) == 1
        left, right = chart.reset_bands[0]
        assert chart.pad_left <= left < right <= chart.width - chart.pad_right
        assert "resetting the hawl" in chart.description

    def test_no_composition_is_an_empty_chart(self):
        chart = build_stacked_chart(
            composition=[], as_of=date(2022, 1, 1), currency="PKR"
        )
        assert chart.is_empty
        assert chart.stack == ()
        assert "No zakatable wealth" in chart.description

    def test_it_does_not_mutate_the_timeline_it_was_given(self):
        before = json.dumps(
            [
                [when.isoformat(), {k: str(v) for k, v in values.items()}]
                for when, values in COMPOSITION
            ]
        )
        self.build()
        after = json.dumps(
            [
                [when.isoformat(), {k: str(v) for k, v in values.items()}]
                for when, values in COMPOSITION
            ]
        )
        assert before == after

    def test_the_description_names_the_bands_for_a_screen_reader(self):
        description = self.build().description
        assert "2 asset band(s)" in description
        assert "1 liability band(s)" in description
        assert "Assets:Bank" in description


class TestStackPayload:
    def payload(self):
        return chart_payload(
            series=[],
            start=date(2020, 1, 1),
            end=date(2022, 6, 1),
            currency="PKR",
            stacks=composition_series(COMPOSITION, liability_accounts=DEBTS),
        )

    def test_it_is_json_serialisable(self):
        payload = self.payload()
        assert json.loads(json.dumps(payload)) == payload

    def test_bands_cross_the_wire_unstacked_and_exact(self):
        card = next(s for s in self.payload()["stacks"] if s["role"] == "liability")
        assert card["points"][1] == ["2021-01-01", "-30"]

    def test_each_band_carries_its_palette_slot(self):
        assert [s["index"] for s in self.payload()["stacks"]] == [0, 1, 2]

    def test_no_stacks_is_an_empty_list_not_a_missing_key(self):
        payload = chart_payload(
            series=[], start=date(2020, 1, 1), end=date(2020, 2, 1), currency="PKR"
        )
        assert payload["stacks"] == []
