"""Domain constants for zakat calculation.

These values are load-bearing: they define the calculation and are referenced
directly by the About/Methodology page and by the test suite.  Change nothing
here without a corresponding before/after test.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: The zakat rate applied to qualifying wealth: 2.5%.
ZAKAT_RATE: Final[Decimal] = Decimal("0.025")

#: Grams in one tola (as used for the classical nisab weights below).
#: 87.48 / 7.5 == 612.36 / 52.5 == 11.664, so both nisab weights are
#: internally consistent under this definition.
TOLA_GRAMS: Final[Decimal] = Decimal("11.664")

#: Gold nisab: 87.48 grams == 7.5 tola.
GOLD_NISAB_GRAMS: Final[Decimal] = Decimal("87.48")
GOLD_NISAB_TOLA: Final[Decimal] = Decimal("7.5")

#: Silver nisab: 612.36 grams == 52.5 tola.
SILVER_NISAB_GRAMS: Final[Decimal] = Decimal("612.36")
SILVER_NISAB_TOLA: Final[Decimal] = Decimal("52.5")

#: Mean length of a lunar (Hijri) year in days.
#:
#: This is the constant the *engine* uses to decide whether a holding period
#: has completed one hawl, and to convert an elapsed period into lunar years.
#: It is deliberately the engine's only notion of a year: the Hijri calendar
#: library labels reporting years and nothing else, so the choice of library
#: can never move a zakat amount.
HIJRI_YEAR_DAYS: Final[Decimal] = Decimal("354.36708")

#: Quantum that monetary results are rounded to (ROUND_HALF_UP).
MONEY_QUANTUM: Final[Decimal] = Decimal("0.01")

#: Default commodity symbols understood as metal prices, mapped to
#: (metal, unit). ``unit`` is the weight one unit of the commodity represents.
#: Any other symbol can be declared through the ``metal_commodities`` option.
DEFAULT_METAL_COMMODITIES: Final[dict[str, tuple[str, str]]] = {
    "GLDTOLA": ("gold", "tola"),
    "SLVTOLA": ("silver", "tola"),
}

#: Recognised weight units for metal price commodities.
UNIT_GRAMS: Final[dict[str, Decimal]] = {
    "tola": TOLA_GRAMS,
    "gram": Decimal("1"),
}

#: A price older than this many days (relative to the valuation date) is
#: reported as stale.  It is still used -- the last known price always
#: carries forward -- but the report says so.
DEFAULT_PRICE_STALENESS_DAYS: Final[int] = 90
