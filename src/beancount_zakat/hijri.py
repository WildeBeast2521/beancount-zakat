"""Hijri (lunar) calendar helpers.

Two distinct notions are deliberately kept apart:

* **Mean lunar-year duration** (:data:`~beancount_zakat.constants.HIJRI_YEAR_DAYS`)
  -- used by the calculation engine to decide whether a hawl has completed and
  to convert an elapsed period into lunar years.  This is pure arithmetic and
  never touches a calendar library.

* **Exact calendar conversion** -- used only to *label* reporting years and to
  show Gregorian ranges next to Hijri years.  Backed by :mod:`hijridate`
  (Umm al-Qura), which is the maintained successor to the deprecated
  ``hijri-converter`` package.

Because the engine only uses the mean duration, the choice of calendar library
can never change a zakat amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache

from hijridate import Gregorian, Hijri
from hijridate.ummalqura import GREGORIAN_RANGE, HIJRI_RANGE

from .constants import HIJRI_YEAR_DAYS

#: Inclusive Gregorian dates for which exact conversion is supported.
SUPPORTED_GREGORIAN_MIN: date = date(*GREGORIAN_RANGE[0])
SUPPORTED_GREGORIAN_MAX: date = date(*GREGORIAN_RANGE[1])

#: Inclusive Hijri years for which exact conversion is supported.
SUPPORTED_HIJRI_MIN_YEAR: int = HIJRI_RANGE[0][0]
SUPPORTED_HIJRI_MAX_YEAR: int = HIJRI_RANGE[1][0]


class HijriRangeError(ValueError):
    """Raised when a date falls outside the Umm al-Qura conversion range."""


def supported(day: date) -> bool:
    """Whether *day* can be converted exactly."""
    return SUPPORTED_GREGORIAN_MIN <= day <= SUPPORTED_GREGORIAN_MAX


def to_hijri(day: date) -> tuple[int, int, int]:
    """Return ``(year, month, day)`` in the Hijri calendar for *day*."""
    if not supported(day):
        raise HijriRangeError(
            f"{day.isoformat()} is outside the supported Umm al-Qura range "
            f"({SUPPORTED_GREGORIAN_MIN.isoformat()} to "
            f"{SUPPORTED_GREGORIAN_MAX.isoformat()})"
        )
    return Gregorian(day.year, day.month, day.day).to_hijri().datetuple()


def hijri_year_of(day: date) -> int:
    """Return the Hijri year containing *day*."""
    return to_hijri(day)[0]


def hijri_to_gregorian(year: int, month: int, day: int) -> date:
    """Convert an exact Hijri date to Gregorian."""
    if not SUPPORTED_HIJRI_MIN_YEAR <= year <= SUPPORTED_HIJRI_MAX_YEAR:
        raise HijriRangeError(
            f"Hijri year {year} is outside the supported Umm al-Qura range "
            f"({SUPPORTED_HIJRI_MIN_YEAR}-{SUPPORTED_HIJRI_MAX_YEAR})"
        )
    greg = Hijri(year, month, day).to_gregorian()
    return date(greg.year, greg.month, greg.day)


@dataclass(frozen=True, slots=True)
class HijriYearRange:
    """One complete Hijri year, with its Gregorian bounds (both inclusive)."""

    hijri_year: int
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.hijri_year} AH"


@lru_cache(maxsize=512)
def hijri_year_range(hijri_year: int) -> HijriYearRange:
    """Gregorian bounds of the Hijri year *hijri_year*, both inclusive.

    Cached: the yearly allocation asks for the same handful of years once per
    qualifying period, and each miss costs two calendar conversions.

    Runs from 1 Muharram of *hijri_year* to the day before 1 Muharram of the
    following year.
    """
    start = hijri_to_gregorian(hijri_year, 1, 1)
    if hijri_year + 1 <= SUPPORTED_HIJRI_MAX_YEAR:
        end = hijri_to_gregorian(hijri_year + 1, 1, 1).toordinal() - 1
        return HijriYearRange(hijri_year, start, date.fromordinal(end))
    # Last supported year: fall back to its own final day.
    last = hijri_to_gregorian(hijri_year, 12, 30)
    return HijriYearRange(hijri_year, start, last)


def hijri_years_between(start: date, end: date) -> list[HijriYearRange]:
    """Every Hijri year overlapping the inclusive Gregorian span *start*..*end*."""
    if end < start:
        return []
    first = hijri_year_of(start)
    last = hijri_year_of(end)
    return [hijri_year_range(y) for y in range(first, last + 1)]


def lunar_years(days: int) -> Decimal:
    """Convert a whole number of days into mean lunar years.

    This is the engine's definition of elapsed hawl time and uses only
    :data:`~beancount_zakat.constants.HIJRI_YEAR_DAYS`.
    """
    return Decimal(days) / HIJRI_YEAR_DAYS


def format_hijri_date(day: date) -> str:
    """Render *day* as a full Hijri date, e.g. ``8 Rabi' al-Awwal 1448 AH``.

    Labelling only. Nothing here reaches the calculation.
    """
    if not supported(day):
        raise HijriRangeError(
            f"{day.isoformat()} is outside the supported Umm al-Qura range "
            f"({SUPPORTED_GREGORIAN_MIN.isoformat()} to "
            f"{SUPPORTED_GREGORIAN_MAX.isoformat()})"
        )
    hijri = Gregorian(day.year, day.month, day.day).to_hijri()
    year, _, dom = hijri.datetuple()
    return f"{dom} {hijri.month_name()} {year} AH"
