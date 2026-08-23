"""Typed domain and view models.

Nothing in this module imports Beancount or Fava.  Every monetary quantity is a
:class:`~decimal.Decimal`; conversion to ``float`` happens only at the charting
boundary in the presentation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Basis(str, Enum):
    """A nisab basis.  Gold and silver are *alternative* scenarios."""

    GOLD = "gold"
    SILVER = "silver"

    @property
    def label(self) -> str:
        return "Gold" if self is Basis.GOLD else "Silver"


class Severity(str, Enum):
    """How badly a :class:`Finding` affects the trustworthiness of a result."""

    #: Informational; the result is sound.
    INFO = "info"
    #: The result is computed but rests on an assumption worth checking.
    WARNING = "warning"
    #: A required input is missing; the affected result is not trustworthy.
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    """A structured, machine-readable validation finding.

    Findings are data, never log strings, so the CLI, CSV export and dashboard
    all render the same set from the same source.
    """

    code: str
    severity: Severity
    message: str
    detail: str = ""
    account: str | None = None
    commodity: str | None = None
    when: date | None = None

    @property
    def is_blocking(self) -> bool:
        return self.severity is Severity.ERROR


class HawlStatus(str, Enum):
    """What the hawl clock was doing for one basis during a detail row.

    Three states, because two are not enough. "Incomplete" implies a clock that
    was running and fell short; a stretch spent below the nisab is different in
    kind -- the clock was stopped and reset, and its duration is irrelevant.
    Collapsing the two would read as though a 1.55-year stretch were somehow
    less than one year.
    """

    #: The enclosing period reached a full lunar year, so zakat is due on it.
    COMPLETE = "complete"
    #: The clock was running but the period has not yet reached a lunar year.
    INCOMPLETE = "incomplete"
    #: Wealth was below this basis's nisab: the hawl was reset and no time counts.
    NOT_RUNNING = "not running"

    @property
    def label(self) -> str:
        if self is HawlStatus.COMPLETE:
            return "complete"
        if self is HawlStatus.INCOMPLETE:
            return "incomplete"
        return "not running"

    @property
    def counts_time(self) -> bool:
        """Whether the row's elapsed span counts towards this basis's hawl."""
        return self is not HawlStatus.NOT_RUNNING


class Role(str, Enum):
    """The role an account plays in the calculation."""

    #: Counts positively towards zakatable wealth.
    ASSET = "asset"
    #: Counts towards zakatable wealth with its natural (negative) Beancount sign.
    LIABILITY = "liability"
    #: Postings here are zakat payments.
    PAYMENT = "expense"


@dataclass(frozen=True, slots=True)
class WealthPoint:
    """Net zakatable wealth at one instant of the timeline."""

    when: date
    assets: Decimal
    liabilities: Decimal
    net: Decimal
    #: Per-account value in the operating currency, for the composition view.
    by_account: dict[str, Decimal] = field(default_factory=dict)
    #: True when this point exists because a price moved rather than a posting.
    price_driven: bool = False


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A metal price and the nisab it implies, as at a valuation date."""

    when: date
    #: The date of the price actually used (<= ``when``); the last known price
    #: always carries forward.
    price_date: date | None
    price: Decimal | None
    commodity: str | None
    nisab: Decimal | None
    stale_days: int | None = None

    @property
    def missing(self) -> bool:
        return self.price is None


@dataclass(frozen=True, slots=True)
class HawlPeriod:
    """One contiguous period for one marginal slice.

    A period runs while net wealth stays at or above the slice's *level* and
    total net wealth stays at or above the nisab for this basis.  Each period
    is evaluated entirely on its own.
    """

    basis: Basis
    level: Decimal
    marginal: Decimal
    start: date
    end: date
    #: Inclusive day count: ``(end - start).days + 1``.
    days: int
    lunar_years: Decimal
    at_level: bool
    above_nisab: bool
    qualifies: bool
    zakat_due: Decimal
    #: Plain-language explanation of why the period ended and whether it counted.
    reason: str = ""

    @property
    def hawl(self) -> HawlStatus:
        """What the hawl clock was doing during this period.

        Three states, because two are not enough. "Incomplete" implies a clock
        that was running and fell short; a stretch spent below the nisab is
        different in kind -- the clock was stopped and reset, and its duration
        is irrelevant. Collapsing the two would show a 1.55-year stretch as
        though it were somehow less than one year.
        """
        if not self.above_nisab:
            return HawlStatus.NOT_RUNNING
        return HawlStatus.COMPLETE if self.qualifies else HawlStatus.INCOMPLETE


@dataclass(frozen=True, slots=True)
class LevelResult:
    """Every period belonging to one marginal slice."""

    basis: Basis
    level: Decimal
    marginal: Decimal
    periods: tuple[HawlPeriod, ...]
    total_days: int
    total_lunar_years: Decimal
    hawl_complete: bool
    zakat_due: Decimal


@dataclass(frozen=True, slots=True)
class Payment:
    """One zakat payment posting.

    The sign is preserved: a negative amount is a refund or reversal and
    reduces the total paid.
    """

    when: date
    account: str
    #: Amount in the operating currency.
    amount: Decimal
    #: Amount as posted, before any currency conversion.
    original_amount: Decimal
    original_currency: str
    #: Conversion rate applied, or ``None`` when already in the operating currency.
    rate: Decimal | None = None
    payee: str | None = None
    narration: str | None = None

    @property
    def is_reversal(self) -> bool:
        return self.amount < 0


@dataclass(frozen=True, slots=True)
class YearRow:
    """One Hijri reporting year.

    Liabilities are allocated across the Hijri years a qualifying period spans,
    pro rata by days, with the rounding residual placed on the final year so
    that the rows sum *exactly* to the cumulative liability.
    """

    hijri_year: int
    start: date
    end: date
    gold_liability: Decimal
    silver_liability: Decimal
    payments: Decimal
    #: Cumulative liability minus cumulative payments, at the end of this year.
    gold_balance: Decimal
    silver_balance: Decimal

    @property
    def label(self) -> str:
        return f"{self.hijri_year} AH"


@dataclass(frozen=True, slots=True)
class NisabSpan:
    """The nisab for one basis across a stretch of time.

    The nisab is not a single number: it moves with the metal price. Where a
    detail row spans more than one price, both ends are reported rather than
    quietly picking one.
    """

    low: Decimal | None
    high: Decimal | None

    @property
    def varies(self) -> bool:
        return self.low is not None and self.high is not None and self.low != self.high

    @property
    def known(self) -> bool:
        return self.low is not None


@dataclass(frozen=True, slots=True)
class BasisResult:
    """The complete result for one nisab basis."""

    basis: Basis
    #: Nisab in the operating currency as at the report's ``as_of`` date.
    nisab: Decimal | None
    #: The metal price behind that nisab.
    price: PricePoint
    #: Net zakatable wealth as at ``as_of``.
    net_wealth: Decimal
    #: Whether net wealth as at ``as_of`` meets or exceeds the nisab.
    qualifies_now: bool
    levels: tuple[LevelResult, ...]
    cumulative_liability: Decimal
    payments_total: Decimal

    @property
    def remaining_or_excess(self) -> Decimal:
        """Signed balance.

        Positive: still owed.  Zero: discharged.  Negative: paid in excess.
        """
        return self.cumulative_liability - self.payments_total

    @property
    def status(self) -> str:
        balance = self.remaining_or_excess
        if balance > 0:
            return "outstanding"
        if balance == 0:
            return "settled"
        return "excess"

    @property
    def qualifying_periods(self) -> tuple[HawlPeriod, ...]:
        return tuple(
            period
            for level in self.levels
            for period in level.periods
            if period.qualifies
        )


@dataclass(frozen=True, slots=True)
class ZakatReport:
    """Everything the dashboard, the CLI and the CSV export render."""

    as_of: date
    operating_currency: str
    zakat_rate: Decimal
    gold: BasisResult
    silver: BasisResult
    year_rows: tuple[YearRow, ...]
    payments: tuple[Payment, ...]
    wealth_series: tuple[WealthPoint, ...]
    gold_nisab_series: tuple[PricePoint, ...]
    silver_nisab_series: tuple[PricePoint, ...]
    warnings: tuple[Finding, ...]
    asset_accounts: tuple[str, ...] = ()
    liability_accounts: tuple[str, ...] = ()
    payment_accounts: tuple[str, ...] = ()
    #: First date with any zakatable activity, if any.
    inception: date | None = None

    def basis(self, basis: Basis) -> BasisResult:
        return self.gold if basis is Basis.GOLD else self.silver

    @property
    def payments_total(self) -> Decimal:
        return sum((p.amount for p in self.payments), Decimal("0"))

    @property
    def has_errors(self) -> bool:
        return any(w.is_blocking for w in self.warnings)

    @property
    def is_empty(self) -> bool:
        return not self.wealth_series
