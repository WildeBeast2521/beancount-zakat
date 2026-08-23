"""Shared fixtures.

Every ledger used by the tests is written inline and is entirely synthetic.
No real account name, balance or path appears anywhere in this directory.
"""

from __future__ import annotations

import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from beancount_zakat import build_report
from beancount_zakat.models import ZakatReport

#: A minimal ledger preamble: one asset, one liability, one payment account,
#: and metal prices that put the gold nisab at 750,000 and silver at 63,000.
PREAMBLE = """\
option "operating_currency" "PKR"

2019-01-01 open Assets:Cash              PKR
  beancount_zakat: "asset"
2019-01-01 open Assets:Bullion           XAUGRAM
  beancount_zakat: "asset"
2019-01-01 open Liabilities:Loan         PKR
  beancount_zakat: "liability"
2019-01-01 open Expenses:Zakat           PKR
  beancount_zakat: "expense"
2019-01-01 open Assets:Untagged          PKR
2019-01-01 open Equity:Opening
2019-01-01 open Income:Salary
2019-01-01 open Expenses:Other

2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA   1200.00 PKR
"""

GOLD_NISAB = Decimal("750000")
SILVER_NISAB = Decimal("63000")


def write_ledger(tmp_path: Path, body: str, *, preamble: str = PREAMBLE) -> Path:
    """Write a ledger file and return its path."""
    path = tmp_path / "ledger.beancount"
    path.write_text(preamble + textwrap.dedent(body), encoding="utf-8")
    return path


def load(path: Path):
    entries, errors, options = loader.load_file(str(path))
    assert not errors, [e.message for e in errors]
    return entries, options


def report_for(
    tmp_path: Path,
    body: str,
    *,
    as_of: date,
    preamble: str = PREAMBLE,
    **kwargs,
) -> ZakatReport:
    """Build a report from an inline ledger body."""
    path = write_ledger(tmp_path, body, preamble=preamble)
    entries, options = load(path)
    return build_report(entries, options, as_of=as_of, **kwargs)


@pytest.fixture
def example_ledger() -> Path:
    """The repository's sanitized example ledger."""
    path = Path(__file__).resolve().parents[1] / "examples/ledger/main.beancount"
    if not path.exists():  # pragma: no cover
        pytest.skip("example ledger missing")
    return path


@pytest.fixture
def example_report(example_ledger: Path) -> ZakatReport:
    entries, errors, options = loader.load_file(str(example_ledger))
    assert not errors, [e.message for e in errors]
    return build_report(entries, options, as_of=date(2026, 8, 19))
