"""Configuration model, parsing, validation and precedence.

There is no configuration file. Account roles come from ``Open``-directive
metadata in the ledger itself, and the handful of numeric settings that can
sensibly be overridden are read from the ``fava-extension`` directive or from
CLI flags. Keeping configuration inside the ledger means it travels with the
data it describes and cannot drift out of step with it.

Precedence, lowest to highest:

===  =============================  ==========================================
  1  Built-in defaults              :mod:`beancount_zakat.constants`
  2  Ledger ``Open`` metadata       ``beancount_zakat: "asset"`` etc.
  3  Extension / CLI options        ``fava-extension`` config, CLI flags
===  =============================  ==========================================

Account roles are merged as a union, **independently per role**, so declaring
one role never suppresses discovery of another. Anything unrecognised --- an
unknown option key, an invalid role, a nonsensical rate, an ambiguous commodity
unit --- is reported rather than silently ignored.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from .constants import (
    DEFAULT_METAL_COMMODITIES,
    DEFAULT_PRICE_STALENESS_DAYS,
    GOLD_NISAB_GRAMS,
    SILVER_NISAB_GRAMS,
    TOLA_GRAMS,
    UNIT_GRAMS,
    ZAKAT_RATE,
)
from .models import Finding, Role, Severity

_VALID_ROLES = {role.value for role in Role}
_ACCOUNT_RE = re.compile(
    r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][\w-]*)+$"
)


class ConfigError(ValueError):
    """Raised for configuration that cannot be used at all."""


def parse_rate(value: object) -> Decimal:
    """Parse a zakat rate.

    Accepted forms:

    * ``"2.5%"`` -> ``0.025`` (explicit percentage)
    * ``"0.025"`` / ``Decimal("0.025")`` -> ``0.025`` (fraction)

    A bare number greater than 1 is rejected rather than guessed at, so the
    same intent written two ways can never mean two different things.
    """
    text = str(value).strip()
    if not text:
        raise ConfigError("zakat_rate is empty")
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ArithmeticError) as exc:
        raise ConfigError(f"zakat_rate {value!r} is not a number") from exc
    rate = number / Decimal("100") if percent else number
    if rate < 0:
        raise ConfigError(f"zakat_rate {value!r} is negative")
    if rate > 1:
        raise ConfigError(
            f"zakat_rate {value!r} resolves to {rate * 100}%, which is above "
            '100%. Write a percentage explicitly, e.g. "2.5%", or a '
            "fraction, e.g. 0.025."
        )
    return rate


def _parse_decimal(name: str, value: object) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ArithmeticError) as exc:
        raise ConfigError(f"{name} {value!r} is not a number") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be positive, got {number}")
    return number


@dataclass(frozen=True, slots=True)
class ZakatConfig:
    """Fully resolved configuration for one report."""

    zakat_rate: Decimal = ZAKAT_RATE
    gold_nisab_grams: Decimal = GOLD_NISAB_GRAMS
    silver_nisab_grams: Decimal = SILVER_NISAB_GRAMS
    #: ``commodity -> (metal, weight-unit)``. ``GLDTOLA`` and ``SLVTOLA`` are
    #: understood out of the box as prices per tola.
    metal_commodities: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_METAL_COMMODITIES)
    )
    price_staleness_days: int = DEFAULT_PRICE_STALENESS_DAYS
    asset_accounts: tuple[str, ...] = ()
    liability_accounts: tuple[str, ...] = ()
    payment_accounts: tuple[str, ...] = ()

    @property
    def gold_nisab_tola(self) -> Decimal:
        return self.gold_nisab_grams / TOLA_GRAMS

    @property
    def silver_nisab_tola(self) -> Decimal:
        return self.silver_nisab_grams / TOLA_GRAMS

    @property
    def has_accounts(self) -> bool:
        return bool(
            self.asset_accounts or self.liability_accounts or self.payment_accounts
        )


# --------------------------------------------------------------------------
# Option parsing and merging
# --------------------------------------------------------------------------

_KNOWN_OPTION_KEYS = {
    "zakat_rate",
    "gold_nisab_grams",
    "silver_nisab_grams",
    "nisab_gold_tola",
    "nisab_silver_tola",
    "metal_commodities",
    "price_staleness_days",
}


def _parse_metal_commodities(
    value: object,
) -> tuple[dict[str, tuple[str, str]], list[Finding]]:
    """Parse ``{"XAUGRAM": ["gold", "gram"], ...}`` style commodity aliases."""
    warnings: list[Finding] = []
    if not isinstance(value, dict):
        raise ConfigError(
            "metal_commodities must be a mapping of "
            "commodity -> [metal, unit], e.g. {'XAUGRAM': ['gold', 'gram']}"
        )
    result: dict[str, tuple[str, str]] = {}
    for symbol, spec in value.items():
        if isinstance(spec, str):
            metal, unit = spec, "tola"
        else:
            try:
                metal, unit = spec
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"metal_commodities[{symbol!r}] must be [metal, unit]"
                ) from exc
        metal = str(metal).lower()
        unit = str(unit).lower()
        if metal not in {"gold", "silver"}:
            raise ConfigError(
                f"metal_commodities[{symbol!r}]: metal must be 'gold' or "
                f"'silver', got {metal!r}"
            )
        if unit not in UNIT_GRAMS:
            raise ConfigError(
                f"metal_commodities[{symbol!r}]: unit must be one of "
                f"{sorted(UNIT_GRAMS)}, got {unit!r}. Say explicitly whether "
                "the quoted price is per tola or per gram."
            )
        result[str(symbol)] = (metal, unit)
    if not result:
        raise ConfigError("metal_commodities is empty")
    return result, warnings


def config_from_options(
    options: dict | None,
    *,
    base: ZakatConfig | None = None,
) -> tuple[ZakatConfig, list[Finding]]:
    """Apply extension/CLI options on top of *base*."""
    config = base or ZakatConfig()
    warnings: list[Finding] = []
    if not options:
        return config, warnings

    for key in options:
        if key not in _KNOWN_OPTION_KEYS:
            warnings.append(
                Finding(
                    code="unknown-option",
                    severity=Severity.WARNING,
                    message=f"Unknown configuration option '{key}' was ignored.",
                    detail=f"Known options: {', '.join(sorted(_KNOWN_OPTION_KEYS))}",
                )
            )

    changes: dict[str, object] = {}
    if "zakat_rate" in options:
        changes["zakat_rate"] = parse_rate(options["zakat_rate"])
    if "gold_nisab_grams" in options:
        changes["gold_nisab_grams"] = _parse_decimal(
            "gold_nisab_grams", options["gold_nisab_grams"]
        )
    if "silver_nisab_grams" in options:
        changes["silver_nisab_grams"] = _parse_decimal(
            "silver_nisab_grams", options["silver_nisab_grams"]
        )
    if "nisab_gold_tola" in options:
        changes["gold_nisab_grams"] = (
            _parse_decimal("nisab_gold_tola", options["nisab_gold_tola"]) * TOLA_GRAMS
        )
    if "nisab_silver_tola" in options:
        changes["silver_nisab_grams"] = (
            _parse_decimal("nisab_silver_tola", options["nisab_silver_tola"])
            * TOLA_GRAMS
        )
    if "metal_commodities" in options:
        commodities, extra = _parse_metal_commodities(options["metal_commodities"])
        changes["metal_commodities"] = commodities
        warnings.extend(extra)
    if "price_staleness_days" in options:
        days = int(options["price_staleness_days"])
        if days < 0:
            raise ConfigError("price_staleness_days must not be negative")
        changes["price_staleness_days"] = days

    return replace(config, **changes), warnings  # type: ignore[arg-type]


def merge_accounts(
    config: ZakatConfig,
    *sources: dict[Role, Iterable[str]],
) -> tuple[ZakatConfig, list[Finding]]:
    """Union account roles across sources, **independently per role**.

    Declaring one role never suppresses another. Later sources take precedence
    when an account is claimed by two different roles, and the conflict is
    always reported.
    """
    warnings: list[Finding] = []
    owner: dict[str, Role] = {}
    buckets: dict[Role, list[str]] = {role: [] for role in Role}

    for source in sources:
        for role, accounts in source.items():
            for account in accounts:
                previous = owner.get(account)
                if previous is None:
                    owner[account] = role
                    buckets[role].append(account)
                elif previous is not role:
                    warnings.append(
                        Finding(
                            code="conflicting-role",
                            severity=Severity.WARNING,
                            message=(
                                f"{account} is declared as both "
                                f"'{previous.value}' and '{role.value}'. "
                                f"Using '{role.value}'."
                            ),
                            account=account,
                        )
                    )
                    buckets[previous].remove(account)
                    owner[account] = role
                    buckets[role].append(account)

    return (
        replace(
            config,
            asset_accounts=tuple(buckets[Role.ASSET]),
            liability_accounts=tuple(buckets[Role.LIABILITY]),
            payment_accounts=tuple(buckets[Role.PAYMENT]),
        ),
        warnings,
    )
