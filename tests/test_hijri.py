"""Hijri calendar handling, and its strict separation from the engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from beancount_zakat import hijri
from beancount_zakat.constants import HIJRI_YEAR_DAYS


class TestConversion:
    def test_a_known_date(self):
        assert hijri.to_hijri(date(2026, 8, 19)) == (1448, 3, 6)

    def test_year_of(self):
        assert hijri.hijri_year_of(date(2026, 8, 19)) == 1448

    def test_round_trip(self):
        for year in (1440, 1445, 1450):
            start = hijri.hijri_to_gregorian(year, 1, 1)
            assert hijri.hijri_year_of(start) == year


class TestRange:
    def test_the_supported_range_is_umm_al_qura(self):
        assert date(1924, 8, 1) == hijri.SUPPORTED_GREGORIAN_MIN
        assert date(2077, 11, 16) == hijri.SUPPORTED_GREGORIAN_MAX
        assert hijri.SUPPORTED_HIJRI_MIN_YEAR == 1343
        assert hijri.SUPPORTED_HIJRI_MAX_YEAR == 1500

    def test_a_date_before_the_range_is_refused(self):
        with pytest.raises(hijri.HijriRangeError):
            hijri.to_hijri(date(1900, 1, 1))

    def test_a_date_after_the_range_is_refused(self):
        with pytest.raises(hijri.HijriRangeError):
            hijri.to_hijri(date(2100, 1, 1))

    def test_a_hijri_year_outside_the_range_is_refused(self):
        with pytest.raises(hijri.HijriRangeError):
            hijri.hijri_to_gregorian(1600, 1, 1)

    def test_supported_predicate(self):
        assert hijri.supported(date(2026, 1, 1))
        assert not hijri.supported(date(1800, 1, 1))


class TestYearRanges:
    def test_a_year_runs_from_muharram_to_the_day_before_the_next(self):
        span = hijri.hijri_year_range(1447)
        assert span.start == hijri.hijri_to_gregorian(1447, 1, 1)
        next_start = hijri.hijri_to_gregorian(1448, 1, 1)
        assert (next_start - span.end).days == 1

    def test_consecutive_years_do_not_overlap_or_leave_gaps(self):
        for year in range(1440, 1450):
            this = hijri.hijri_year_range(year)
            following = hijri.hijri_year_range(year + 1)
            assert (following.start - this.end).days == 1

    def test_a_year_is_roughly_a_lunar_year_long(self):
        span = hijri.hijri_year_range(1445)
        length = (span.end - span.start).days + 1
        assert 353 <= length <= 356

    def test_years_between_covers_the_span(self):
        spans = hijri.hijri_years_between(date(2019, 1, 1), date(2026, 8, 19))
        assert [s.hijri_year for s in spans] == list(range(1440, 1449))
        assert spans[0].start <= date(2019, 1, 1)
        assert spans[-1].end >= date(2026, 8, 19)

    def test_years_between_is_empty_for_a_reversed_span(self):
        assert hijri.hijri_years_between(date(2020, 1, 1), date(2019, 1, 1)) == []

    def test_label(self):
        assert hijri.hijri_year_range(1447).label == "1447 AH"


class TestMeanLunarYear:
    """The engine's notion of a year is arithmetic, not calendrical."""

    def test_the_constant_is_preserved_exactly(self):
        assert Decimal("354.36708") == HIJRI_YEAR_DAYS

    def test_lunar_years_uses_only_the_constant(self):
        assert hijri.lunar_years(354) == Decimal(354) / HIJRI_YEAR_DAYS
        assert hijri.lunar_years(355) > 1
        assert hijri.lunar_years(354) < 1

    def test_the_engine_does_not_import_the_calendar_library(self):
        import inspect

        from beancount_zakat import engine

        source = inspect.getsource(engine)
        assert "hijridate" not in source
        assert "from .hijri import" not in source


class TestFullDateLabel:
    def test_it_names_the_day_month_and_year(self):
        assert hijri.format_hijri_date(date(2026, 8, 21)) == "8 Rabi' al-Awwal 1448 AH"

    def test_it_agrees_with_the_tuple_conversion(self):
        for day in (date(1990, 3, 1), date(2020, 1, 1), date(2050, 12, 31)):
            year, _, dom = hijri.to_hijri(day)
            label = hijri.format_hijri_date(day)
            assert label.startswith(f"{dom} ")
            assert label.endswith(f"{year} AH")

    def test_it_refuses_dates_outside_the_conversion_range(self):
        with pytest.raises(hijri.HijriRangeError):
            hijri.format_hijri_date(hijri.SUPPORTED_GREGORIAN_MIN - timedelta(days=1))
