"""Price lookup and nisab valuation.

Policy, stated explicitly because every displayed total depends on it:

* **Direction.**  Prices are looked up as ``commodity -> operating_currency``.
  Beancount's price map is consulted for the direct pair and, failing that, the
  inverse pair is inverted.
* **Carry-forward.**  The price used on a given day is the most recent price
  dated *at or before* that day.  A day with no price of its own therefore
  reuses the last known price; this is what the user asked for and matches
  Beancount's own ``get_price`` semantics.
* **No future prices.**  A price dated after the valuation date is never used,
  so a report for an earlier ``as_of`` can never be moved by a later price.
* **Staleness.**  A carried-forward price older than
  :data:`~beancount_zakat.constants.DEFAULT_PRICE_STALENESS_DAYS` is still used
  but is reported as stale.
* **Missing.**  When no price exists at or before the date, the value is
  ``None`` and the caller raises a validation finding.  "Unknown" is never
  silently rendered as "nothing owed".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core import data as bdata
from beancount.core.prices import build_price_map, get_price

from .constants import (
    DEFAULT_METAL_COMMODITIES,
    DEFAULT_PRICE_STALENESS_DAYS,
    GOLD_NISAB_GRAMS,
    SILVER_NISAB_GRAMS,
    UNIT_GRAMS,
)
from .models import Basis, PricePoint

#: Derived nisab thresholds are rounded to this precision.
NISAB_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Quote:
    """A resolved price and the date it was actually quoted on."""

    rate: Decimal
    price_date: date

    def stale_days(self, when: date) -> int:
        return (when - self.price_date).days


class PriceService:
    """Resolves commodity prices into the operating currency."""

    def __init__(
        self,
        entries: Iterable[bdata.Directive],
        operating_currency: str,
        *,
        as_of: date,
        staleness_days: int = DEFAULT_PRICE_STALENESS_DAYS,
    ) -> None:
        self.operating_currency = operating_currency
        self.as_of = as_of
        self.staleness_days = staleness_days
        # A price dated after the cutoff must not influence the report.
        self._price_entries = [
            entry
            for entry in entries
            if isinstance(entry, bdata.Price) and entry.date <= as_of
        ]
        self._price_map = build_price_map(self._price_entries)
        self._cache: dict[tuple[str, date], Quote | None] = {}

    @property
    def price_dates(self) -> set[date]:
        """Every date on which any price was quoted, up to ``as_of``."""
        return {entry.date for entry in self._price_entries}

    def price_dates_for(self, commodities: Iterable[str]) -> set[date]:
        """Price dates for the given commodities only."""
        wanted = set(commodities)
        return {entry.date for entry in self._price_entries if entry.currency in wanted}

    def quote(self, commodity: str, when: date) -> Quote | None:
        """Most recent price of *commodity* in the operating currency at *when*."""
        if commodity == self.operating_currency:
            return Quote(Decimal("1"), when)
        key = (commodity, when)
        if key in self._cache:
            return self._cache[key]
        result = self._lookup(commodity, when)
        self._cache[key] = result
        return result

    def _lookup(self, commodity: str, when: date) -> Quote | None:
        pair = (commodity, self.operating_currency)
        price_date, rate = get_price(self._price_map, pair, when)
        if rate is not None and price_date is not None:
            return Quote(Decimal(rate), price_date)
        inverse_date, inverse_rate = get_price(
            self._price_map, (self.operating_currency, commodity), when
        )
        if inverse_rate and inverse_date is not None:
            return Quote(Decimal(1) / Decimal(inverse_rate), inverse_date)
        return None

    def convert(self, amount: Decimal, commodity: str, when: date) -> Quote | None:
        """Convenience wrapper returning the quote used for *amount*."""
        return self.quote(commodity, when)


NISAB_GRAMS: dict[Basis, Decimal] = {
    Basis.GOLD: GOLD_NISAB_GRAMS,
    Basis.SILVER: SILVER_NISAB_GRAMS,
}


class NisabService:
    """Turns metal prices into a nisab threshold in the operating currency."""

    def __init__(
        self,
        prices: PriceService,
        *,
        metal_commodities: dict[str, tuple[str, str]] | None = None,
        nisab_grams: dict[Basis, Decimal] | None = None,
    ) -> None:
        self.prices = prices
        self.metal_commodities = dict(metal_commodities or DEFAULT_METAL_COMMODITIES)
        self.nisab_grams = dict(nisab_grams or NISAB_GRAMS)
        self._cache: dict[tuple[Basis, date], PricePoint] = {}

    def commodities_for(self, basis: Basis) -> list[str]:
        """Configured price commodities for *basis*, in declaration order."""
        return [
            symbol
            for symbol, (metal, _unit) in self.metal_commodities.items()
            if metal == basis.value
        ]

    def point(self, basis: Basis, when: date) -> PricePoint:
        """Nisab for *basis* as at *when*, with provenance."""
        key = (basis, when)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        point = self._compute(basis, when)
        self._cache[key] = point
        return point

    def _compute(self, basis: Basis, when: date) -> PricePoint:
        grams_required = self.nisab_grams[basis]
        best: tuple[str, Quote, str] | None = None
        for symbol in self.commodities_for(basis):
            quote = self.prices.quote(symbol, when)
            if quote is None:
                continue
            unit = self.metal_commodities[symbol][1]
            if best is None or quote.price_date > best[1].price_date:
                best = (symbol, quote, unit)
        if best is None:
            return PricePoint(
                when=when,
                price_date=None,
                price=None,
                commodity=None,
                nisab=None,
            )
        symbol, quote, unit = best
        # Quantize the derived threshold: dividing by the grams-per-unit
        # otherwise leaves 20+ artefact digits that mean nothing and read
        # badly in exports.
        per_gram = quote.rate / UNIT_GRAMS[unit]
        nisab = (per_gram * grams_required).quantize(NISAB_QUANTUM)
        return PricePoint(
            when=when,
            price_date=quote.price_date,
            price=quote.rate,
            commodity=symbol,
            nisab=nisab,
            stale_days=quote.stale_days(when),
        )

    def nisab(self, basis: Basis, when: date) -> Decimal | None:
        return self.point(basis, when).nisab

    def lookup(self, basis: Basis):
        """A ``date -> Decimal | None`` callable for the engine."""

        def _lookup(when: date) -> Decimal | None:
            return self.nisab(basis, when)

        return _lookup

    def relevant_price_dates(self) -> set[date]:
        """Dates on which any configured metal price moved."""
        return self.prices.price_dates_for(self.metal_commodities)
