"""Zakat calculation for Beancount ledgers.

Importing this package pulls in the calculation engine only.  It deliberately
does **not** import Fava, so the CLI works on a machine that has never had Fava
installed.  The Fava extension lives in :mod:`beancount_zakat.fava_extension`.
"""

from __future__ import annotations

from .config import ZakatConfig
from .constants import (
    GOLD_NISAB_GRAMS,
    GOLD_NISAB_TOLA,
    HIJRI_YEAR_DAYS,
    SILVER_NISAB_GRAMS,
    SILVER_NISAB_TOLA,
    ZAKAT_RATE,
)
from .models import (
    Basis,
    BasisResult,
    Finding,
    HawlPeriod,
    LevelResult,
    Payment,
    Role,
    Severity,
    YearRow,
    ZakatReport,
)
from .service import build_report

__version__ = "1.0.0"

__all__ = [
    "GOLD_NISAB_GRAMS",
    "GOLD_NISAB_TOLA",
    "HIJRI_YEAR_DAYS",
    "SILVER_NISAB_GRAMS",
    "SILVER_NISAB_TOLA",
    "ZAKAT_RATE",
    "Basis",
    "BasisResult",
    "Finding",
    "HawlPeriod",
    "LevelResult",
    "Payment",
    "Role",
    "Severity",
    "YearRow",
    "ZakatConfig",
    "ZakatReport",
    "__version__",
    "build_report",
]
