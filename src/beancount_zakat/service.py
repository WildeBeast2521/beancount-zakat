"""The one entry point that turns loaded Beancount entries into a report.

Both the CLI and the Fava extension call :func:`build_report` and render the
same :class:`~beancount_zakat.models.ZakatReport`.  Presentation code cannot
change a domain result because it never computes one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from beancount.core import data as bdata

from .adapter import (
    build_payments,
    build_timeline,
    classify_accounts,
    operating_currency,
)
from .config import ZakatConfig, config_from_options, merge_accounts
from .engine import compute_levels, cumulative_liability
from .hijri import (
    SUPPORTED_GREGORIAN_MAX,
    SUPPORTED_GREGORIAN_MIN,
    HijriRangeError,
)
from .models import (
    Basis,
    BasisResult,
    Finding,
    Severity,
    ZakatReport,
)
from .prices import NisabService, PriceService
from .reporting import build_year_rows, wealth_at

ZERO = Decimal("0")


def resolve_config(
    entries: Sequence[bdata.Directive],
    *,
    options: dict | None = None,
) -> tuple[ZakatConfig, list[Finding]]:
    """Build the effective configuration.

    Defaults, then account roles read from ledger metadata, then any overrides
    from the ``fava-extension`` directive or the CLI. See
    :mod:`beancount_zakat.config` for the precedence table.
    """
    config = ZakatConfig()
    findings: list[Finding] = []

    metadata_roles, metadata_findings = classify_accounts(entries)
    findings.extend(metadata_findings)

    config, merge_findings = merge_accounts(config, dict(metadata_roles))
    findings.extend(merge_findings)

    config, option_findings = config_from_options(options, base=config)
    findings.extend(option_findings)

    return config, findings


def build_report(
    entries: Sequence[bdata.Directive],
    options: dict,
    *,
    as_of: date | None = None,
    config: ZakatConfig | None = None,
    extension_options: dict | None = None,
) -> ZakatReport:
    """Calculate zakat from loaded Beancount entries.

    Args:
        entries: Every directive from the root ledger, includes and all.
        options: The Beancount options map.
        as_of: Report cutoff.  Defaults to today.  Nothing dated after this is
            allowed to influence the result, and holdings keep accruing hawl
            up to it even when the ledger has been quiet.
        config: A fully resolved configuration; when omitted one is built from
            ledger metadata and *extension_options*.
        extension_options: Options from the ``fava-extension`` directive or CLI.
    """
    as_of = as_of or date.today()
    warnings: list[Finding] = []

    if config is None:
        config, config_findings = resolve_config(entries, options=extension_options)
        warnings.extend(config_findings)

    currency = operating_currency(options)
    if not options.get("operating_currency"):
        warnings.append(
            Finding(
                code="no-operating-currency",
                severity=Severity.WARNING,
                message=(
                    f"The ledger declares no operating currency; assuming {currency}."
                ),
                detail='Add: option "operating_currency" "PKR"',
            )
        )

    if not (SUPPORTED_GREGORIAN_MIN <= as_of <= SUPPORTED_GREGORIAN_MAX):
        raise HijriRangeError(
            f"as-of date {as_of.isoformat()} is outside the supported "
            f"Umm al-Qura conversion range "
            f"({SUPPORTED_GREGORIAN_MIN.isoformat()} to "
            f"{SUPPORTED_GREGORIAN_MAX.isoformat()})."
        )

    if not config.has_accounts:
        warnings.append(
            Finding(
                code="no-classified-accounts",
                severity=Severity.ERROR,
                message=(
                    "No accounts are marked for zakat, so there is nothing to "
                    "calculate."
                ),
                detail=(
                    "Add metadata to the Open directives you want included, "
                    "e.g.\n  2020-01-01 open Assets:Cash\n    "
                    'beancount_zakat: "asset"'
                ),
            )
        )

    prices = PriceService(
        entries,
        currency,
        as_of=as_of,
        staleness_days=config.price_staleness_days,
    )
    nisab_service = NisabService(
        prices,
        metal_commodities=config.metal_commodities,
        nisab_grams={
            Basis.GOLD: config.gold_nisab_grams,
            Basis.SILVER: config.silver_nisab_grams,
        },
    )

    timeline = build_timeline(
        entries,
        asset_accounts=config.asset_accounts,
        liability_accounts=config.liability_accounts,
        prices=prices,
        as_of=as_of,
        extra_dates=nisab_service.relevant_price_dates(),
    )
    warnings.extend(timeline.warnings)

    payments, payment_warnings = build_payments(
        entries,
        payment_accounts=config.payment_accounts,
        prices=prices,
        as_of=as_of,
    )
    warnings.extend(payment_warnings)

    series = timeline.series
    payments_total = sum((p.amount for p in payments), ZERO)
    net_now = wealth_at(timeline.points, as_of)

    results: dict[Basis, BasisResult] = {}
    for basis in (Basis.GOLD, Basis.SILVER):
        point = nisab_service.point(basis, as_of)
        warnings.extend(_price_warnings(basis, point, config, prices))
        levels = compute_levels(
            series,
            nisab_service.lookup(basis),
            basis=basis,
            as_of=as_of,
            rate=config.zakat_rate,
        )
        results[basis] = BasisResult(
            basis=basis,
            nisab=point.nisab,
            price=point,
            net_wealth=net_now,
            qualifies_now=point.nisab is not None and net_now >= point.nisab,
            levels=levels,
            cumulative_liability=cumulative_liability(levels),
            payments_total=payments_total,
        )

    gold = results[Basis.GOLD]
    silver = results[Basis.SILVER]

    year_rows = build_year_rows(
        gold_periods=gold.qualifying_periods,
        silver_periods=silver.qualifying_periods,
        payments=payments,
        as_of=as_of,
        inception=timeline.inception,
    )

    nisab_dates = [point.when for point in timeline.points]
    return ZakatReport(
        as_of=as_of,
        operating_currency=currency,
        zakat_rate=config.zakat_rate,
        gold=gold,
        silver=silver,
        year_rows=year_rows,
        payments=payments,
        wealth_series=timeline.points,
        gold_nisab_series=tuple(
            nisab_service.point(Basis.GOLD, when) for when in nisab_dates
        ),
        silver_nisab_series=tuple(
            nisab_service.point(Basis.SILVER, when) for when in nisab_dates
        ),
        warnings=tuple(warnings),
        asset_accounts=config.asset_accounts,
        liability_accounts=config.liability_accounts,
        payment_accounts=config.payment_accounts,
        inception=timeline.inception,
    )


def _price_warnings(
    basis: Basis,
    point,
    config: ZakatConfig,
    prices: PriceService,
) -> list[Finding]:
    """Turn a missing or stale nisab price into an actionable finding."""
    symbols = [
        symbol
        for symbol, (metal, _unit) in config.metal_commodities.items()
        if metal == basis.value
    ]
    if point.nisab is None:
        example = symbols[0] if symbols else basis.value.upper()
        return [
            Finding(
                code="missing-nisab-price",
                severity=Severity.ERROR,
                message=(
                    f"No {basis.value} price is available, so the "
                    f"{basis.label} nisab could not be determined and the "
                    f"{basis.label} result is not trustworthy."
                ),
                detail=(
                    "Add a price directive to your ledger, e.g.\n"
                    f"  {point.when.isoformat()} price {example} "
                    f"<amount> {prices.operating_currency}\n"
                    f"Expected commodities for {basis.value}: "
                    f"{', '.join(symbols) or '(none configured)'}"
                ),
                commodity=example,
                when=point.when,
            )
        ]
    if point.stale_days is not None and point.stale_days > config.price_staleness_days:
        return [
            Finding(
                code="stale-nisab-price",
                severity=Severity.WARNING,
                message=(
                    f"The {basis.label} nisab uses a {point.commodity} price "
                    f"from {point.price_date.isoformat()}, "
                    f"{point.stale_days} days before the report date."
                ),
                detail=(
                    "The last known price always carries forward, but a fresher "
                    "price directive would make the threshold more accurate."
                ),
                commodity=point.commodity,
                when=point.price_date,
            )
        ]
    return []
