"""Beancount adapter: entries in, timelines and payments out.

This is the only module that understands Beancount directives.  It is where the
two most consequential approved corrections live:

* **Liability sign.** A normal Beancount liability already carries a *negative*
  balance, so net zakatable wealth is simply ``sum(assets) + sum(liabilities)``.
  Debt is subtracted by plain addition; nothing is negated a second time.
* **Revaluation.** Holdings denominated in a non-operating commodity are
  re-valued on every date any relevant price moves, rather than being frozen at
  the value they had on the last date a posting happened to touch the account.

Whether a given liability is *jurisprudentially* deductible is a separate
question, decided entirely by which accounts the user tags
``beancount_zakat: "liability"``.  This module only gets the accounting sign
right.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from beancount.core import data as bdata

from .models import Finding, Payment, Role, Severity, WealthPoint
from .prices import PriceService

ZERO = Decimal("0")

#: Metadata key on ``Open`` directives that assigns an account its role.
METADATA_KEY = "beancount_zakat"


def classify_accounts(
    entries: Iterable[bdata.Directive],
) -> tuple[dict[Role, list[str]], list[Finding]]:
    """Read account roles from ``Open``-directive metadata.

    Recognised values are ``"asset"``, ``"liability"`` and ``"expense"``
    (zakat payments).  Anything else is reported rather than ignored.
    """
    roles: dict[Role, list[str]] = {role: [] for role in Role}
    warnings: list[Finding] = []
    seen: dict[str, Role] = {}

    for entry in entries:
        if not isinstance(entry, bdata.Open):
            continue
        raw = (entry.meta or {}).get(METADATA_KEY)
        if raw is None:
            continue
        value = str(raw).strip().lower()
        try:
            role = Role(value)
        except ValueError:
            warnings.append(
                Finding(
                    code="invalid-role",
                    severity=Severity.WARNING,
                    message=(
                        f"{entry.account}: {METADATA_KEY} value {raw!r} is not "
                        "recognised and the account was skipped."
                    ),
                    detail="Valid values are: asset, liability, expense.",
                    account=entry.account,
                )
            )
            continue
        if entry.account in seen:
            warnings.append(
                Finding(
                    code="duplicate-open",
                    severity=Severity.WARNING,
                    message=(
                        f"{entry.account} is opened more than once with a "
                        f"{METADATA_KEY} role; the first was kept."
                    ),
                    account=entry.account,
                )
            )
            continue
        seen[entry.account] = role
        roles[role].append(entry.account)

    return roles, warnings


@dataclass(frozen=True, slots=True)
class TimelineResult:
    """Everything the engine needs about wealth over time."""

    points: tuple[WealthPoint, ...]
    warnings: tuple[Finding, ...]
    inception: date | None

    @property
    def series(self) -> list[tuple[date, Decimal]]:
        return [(point.when, point.net) for point in self.points]


def build_timeline(
    entries: Sequence[bdata.Directive],
    *,
    asset_accounts: Iterable[str],
    liability_accounts: Iterable[str],
    prices: PriceService,
    as_of: date,
    extra_dates: Iterable[date] = (),
) -> TimelineResult:
    """Replay postings into a net-zakatable-wealth timeline up to *as_of*.

    A point is emitted for every date on which either a posting touched a
    tracked account or a price relevant to a tracked holding moved, plus
    *as_of* itself so the series always reaches the report date.
    """
    assets = set(asset_accounts)
    liabilities = set(liability_accounts)
    tracked = assets | liabilities
    warnings: list[Finding] = []

    if not tracked:
        return TimelineResult((), (), None)

    # 1. Collect per-date balance deltas, per account and commodity.
    deltas: dict[date, dict[tuple[str, str], Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    commodities: set[str] = set()
    inception: date | None = None

    for entry in entries:
        if not isinstance(entry, bdata.Transaction):
            continue
        # Entries after the cutoff must not influence the report.
        if entry.date > as_of:
            continue
        for posting in entry.postings:
            if posting.account not in tracked:
                continue
            units = posting.units
            if units is None or units.number is None:
                warnings.append(
                    Finding(
                        code="incomplete-posting",
                        severity=Severity.WARNING,
                        message=(
                            f"{posting.account}: a posting on "
                            f"{entry.date.isoformat()} has no amount and was "
                            "skipped."
                        ),
                        account=posting.account,
                        when=entry.date,
                    )
                )
                continue
            deltas[entry.date][(posting.account, units.currency)] += Decimal(
                units.number
            )
            commodities.add(units.currency)
            if inception is None or entry.date < inception:
                inception = entry.date

    if not deltas:
        return TimelineResult((), tuple(warnings), None)

    # 2. Dates worth a snapshot: postings, relevant price moves, and as_of.
    #    A price move alone is enough to change net wealth.
    revaluation_dates = {
        moment
        for moment in prices.price_dates_for(commodities - {prices.operating_currency})
        if moment <= as_of
    }
    # Nothing before the first zakatable posting can have a balance, so a
    # price move before inception must not create a spurious zero point.
    first_posting = min(deltas)
    event_dates = sorted(
        {d for d in deltas if d <= as_of}
        | {d for d in revaluation_dates if d >= first_posting}
        | {d for d in extra_dates if first_posting <= d <= as_of}
        | {as_of}
    )
    posting_dates = set(deltas)

    # 3. Walk forward, applying deltas then valuing everything at that date.
    balances: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    missing_prices: set[tuple[str, str]] = set()
    points: list[WealthPoint] = []
    pending = sorted(deltas)
    cursor = 0

    for moment in event_dates:
        while cursor < len(pending) and pending[cursor] <= moment:
            for key, amount in deltas[pending[cursor]].items():
                balances[key] += amount
            cursor += 1

        by_account: dict[str, Decimal] = defaultdict(Decimal)
        total_assets = ZERO
        total_liabilities = ZERO

        for (account, currency), amount in balances.items():
            if amount == ZERO:
                by_account.setdefault(account, ZERO)
                continue
            if currency == prices.operating_currency:
                value = amount
            else:
                quote = prices.quote(currency, moment)
                if quote is None:
                    key = (account, currency)
                    if key not in missing_prices:
                        missing_prices.add(key)
                        warnings.append(
                            Finding(
                                code="missing-holding-price",
                                severity=Severity.ERROR,
                                message=(
                                    f"No price for {currency} in "
                                    f"{prices.operating_currency} on or before "
                                    f"{moment.isoformat()}, so the {account} "
                                    "balance could not be valued."
                                ),
                                detail=(
                                    "Add a price directive, e.g. "
                                    f"{moment.isoformat()} price {currency} "
                                    f"<amount> {prices.operating_currency}"
                                ),
                                account=account,
                                commodity=currency,
                                when=moment,
                            )
                        )
                    continue
                value = amount * quote.rate
            # Liabilities keep their natural (negative) Beancount sign, so
            # debt is subtracted by plain addition.
            by_account[account] += value
            if account in liabilities:
                total_liabilities += value
            else:
                total_assets += value

        points.append(
            WealthPoint(
                when=moment,
                assets=total_assets,
                liabilities=total_liabilities,
                net=total_assets + total_liabilities,
                by_account=dict(by_account),
                price_driven=moment not in posting_dates,
            )
        )

    return TimelineResult(tuple(points), tuple(warnings), inception)


def build_payments(
    entries: Sequence[bdata.Directive],
    *,
    payment_accounts: Iterable[str],
    prices: PriceService,
    as_of: date,
) -> tuple[tuple[Payment, ...], tuple[Finding, ...]]:
    """Collect zakat payments, **preserving sign**.

    Signs are preserved rather than taken as absolute: a negative posting to a
    payment account is a refund or a correcting reversal, and reduces the total
    paid.
    """
    accounts = set(payment_accounts)
    payments: list[Payment] = []
    warnings: list[Finding] = []
    if not accounts:
        return (), ()

    for entry in entries:
        if not isinstance(entry, bdata.Transaction):
            continue
        if entry.date > as_of:
            continue
        for posting in entry.postings:
            if posting.account not in accounts:
                continue
            units = posting.units
            if units is None or units.number is None:
                continue
            amount = Decimal(units.number)
            rate: Decimal | None = None
            if units.currency != prices.operating_currency:
                quote = prices.quote(units.currency, entry.date)
                if quote is None:
                    warnings.append(
                        Finding(
                            code="missing-payment-price",
                            severity=Severity.ERROR,
                            message=(
                                f"Zakat payment of {amount} {units.currency} on "
                                f"{entry.date.isoformat()} could not be "
                                f"converted to {prices.operating_currency} and "
                                "was excluded."
                            ),
                            account=posting.account,
                            commodity=units.currency,
                            when=entry.date,
                        )
                    )
                    continue
                rate = quote.rate
                converted = amount * quote.rate
            else:
                converted = amount
            payments.append(
                Payment(
                    when=entry.date,
                    account=posting.account,
                    amount=converted,
                    original_amount=amount,
                    original_currency=units.currency,
                    rate=rate,
                    payee=entry.payee,
                    narration=entry.narration,
                )
            )

    payments.sort(key=lambda p: (p.when, p.account))
    return tuple(payments), tuple(warnings)


def operating_currency(options: dict) -> str:
    """The ledger's primary operating currency."""
    value = options.get("operating_currency")
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return "USD"


def decimal_places_for(
    entries: Iterable[bdata.Directive], currency: str, default: int = 2
) -> int:
    """Read ``precision:`` metadata from a ``commodity`` directive, if any.

    Lives here rather than in the formatting module because reading Beancount
    directives is an adapter concern.
    """
    for entry in entries:
        if isinstance(entry, bdata.Commodity) and entry.currency == currency:
            raw = (entry.meta or {}).get("precision")
            if raw is not None:
                try:
                    return max(0, int(raw))
                except (TypeError, ValueError):
                    return default
    return default
