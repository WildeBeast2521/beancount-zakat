"""Architectural constraints, enforced rather than merely documented.

The calculation engine and the CLI must work on a machine that has never had
Fava installed, and the domain layer must not depend on Beancount. Both are easy
to break with a single convenience import, so they are checked rather than
merely written down.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "beancount_zakat"

#: Modules that must never import Fava, directly or transitively.
FAVA_FREE = [
    "beancount_zakat",
    "beancount_zakat.constants",
    "beancount_zakat.models",
    "beancount_zakat.engine",
    "beancount_zakat.hijri",
    "beancount_zakat.config",
    "beancount_zakat.prices",
    "beancount_zakat.adapter",
    "beancount_zakat.service",
    "beancount_zakat.reporting",
    "beancount_zakat.formatting",
    "beancount_zakat.tables",
    "beancount_zakat.chart",
    "beancount_zakat.csv_export",
    "beancount_zakat.cli",
]

#: Modules that must additionally never import Beancount: the pure domain.
PURE = [
    "beancount_zakat.constants",
    "beancount_zakat.models",
    "beancount_zakat.engine",
    "beancount_zakat.hijri",
    "beancount_zakat.reporting",
    "beancount_zakat.formatting",
    "beancount_zakat.tables",
    "beancount_zakat.chart",
]


def module_path(name: str) -> Path:
    tail = name.removeprefix("beancount_zakat")
    if not tail:
        return SRC / "__init__.py"
    return SRC / (tail.lstrip(".").replace(".", "/") + ".py")


def imported_names(path: Path) -> set[str]:
    """Top-level module names imported by a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


class TestSourceLevelImports:
    @pytest.mark.parametrize("name", FAVA_FREE)
    def test_no_fava_import_anywhere(self, name):
        assert "fava" not in imported_names(module_path(name))

    @pytest.mark.parametrize("name", PURE)
    def test_the_pure_layer_has_no_beancount_import(self, name):
        assert "beancount" not in imported_names(module_path(name))

    def test_the_fava_adapter_is_the_only_place_fava_appears(self):
        offenders = []
        for path in SRC.rglob("*.py"):
            if "fava_extension" in path.parts:
                continue
            if "fava" in imported_names(path):
                offenders.append(str(path.relative_to(SRC)))
        assert offenders == []


class TestRuntimeImports:
    def test_importing_the_package_does_not_pull_in_fava(self):
        """Run in a subprocess so an already-imported Fava cannot mask it."""
        code = (
            "import sys;"
            "import beancount_zakat, beancount_zakat.cli;"
            "assert 'fava' not in sys.modules, sorted("
            "m for m in sys.modules if m.startswith('fava'));"
            "print('ok')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_the_cli_works_with_fava_blocked(self, tmp_path):
        """Simulate a machine that has never had Fava installed."""
        ledger = tmp_path / "l.beancount"
        ledger.write_text(
            'option "operating_currency" "PKR"\n'
            "2019-01-01 open Assets:Cash PKR\n"
            '  beancount_zakat: "asset"\n'
            "2019-01-01 open Equity:Opening\n"
            "2019-01-01 price GLDTOLA 100000.00 PKR\n"
            "2019-01-01 price SLVTOLA 1200.00 PKR\n"
            '2019-01-01 * "Opening"\n'
            "  Assets:Cash 1000000.00 PKR\n"
            "  Equity:Opening\n",
            encoding="utf-8",
        )
        code = (
            "import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'fava' or name.startswith('fava.'):\n"
            "            raise ImportError('fava is not installed')\n"
            "sys.meta_path.insert(0, Block())\n"
            "from beancount_zakat.cli import main\n"
            f"sys.exit(main([{str(ledger)!r}, '--as-of', '2024-01-01',"
            " '--width', '100']))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "SUMMARY" in result.stdout
        assert "Cumulative liability" in result.stdout


class TestFavaAdapterIsThin:
    def test_the_adapter_does_not_reimplement_the_calculation(self):
        source = (
            module_path("beancount_zakat")
            .parent.joinpath("fava_extension/__init__.py")
            .read_text(encoding="utf-8")
        )
        for forbidden in ("HIJRI_YEAR_DAYS /", "* rate", "quantize("):
            assert forbidden not in source

    def test_the_adapter_delegates_to_build_report(self):
        from beancount_zakat.fava_extension import ZakatDashboard

        source = (
            module_path("beancount_zakat")
            .parent.joinpath("fava_extension/__init__.py")
            .read_text(encoding="utf-8")
        )
        assert "build_report(" in source
        assert ZakatDashboard.report_title == "Zakat"
