"""The claims the README makes in public, enforced.

Code that reaches the network, generates itself at runtime, turns money into a
float, changes its answer between runs, or imports a package that was never
declared: none of these can be ruled out by reading the source, so each is
checked here.

If a test in this file fails, a sentence in the README has become false. Fix the
code, not the test.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from beancount_zakat import build_report
from beancount_zakat.cli import EXIT_OK, main

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "beancount_zakat"

#: Modules that can open a socket, directly or by delegation. `urllib.parse` is
#: deliberately absent: it manipulates URL strings and never opens anything.
NETWORKING = {
    "aiohttp",
    "asyncio",
    "ftplib",
    "http",
    "httpx",
    "imaplib",
    "poplib",
    "requests",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "telnetlib",
    "urllib.error",
    "urllib.request",
    "urllib3",
    "webbrowser",
    "xmlrpc",
}

#: Modules that execute code or data that was never reviewed.
UNSAFE = {
    "ctypes",
    "dill",
    "marshal",
    "multiprocessing",
    "pickle",
    "pty",
    "shelve",
    "subprocess",
}

#: A whole synthetic ledger, written out rather than imported, so the
#: subprocess tests below do not depend on anything in this repository.
LEDGER = """\
option "operating_currency" "PKR"

2019-01-01 open Assets:Cash        PKR
  beancount_zakat: "asset"
2019-01-01 open Liabilities:Loan   PKR
  beancount_zakat: "liability"
2019-01-01 open Expenses:Zakat     PKR
  beancount_zakat: "expense"
2019-01-01 open Equity:Opening

2019-01-01 price GLDTOLA 100000.00 PKR
2019-01-01 price SLVTOLA   1200.00 PKR

2019-01-01 * "Opening balance"
  Assets:Cash      1500000.00 PKR
  Equity:Opening

2020-06-01 * "A debt"
  Liabilities:Loan  -200000.00 PKR
  Assets:Cash

2022-03-01 * "Zakat paid"
  Expenses:Zakat      40000.00 PKR
  Assets:Cash
"""


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports, as full dotted names.

    Dotted rather than top-level, because `urllib.parse` and `urllib.request`
    are not remotely the same thing.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def run_python(code: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        **kwargs,
    )


@pytest.fixture(scope="module")
def ledger(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("guarantees") / "ledger.beancount"
    path.write_text(LEDGER, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def report(ledger: Path):
    entries, errors, options = loader.load_file(str(ledger))
    assert not errors, [e.message for e in errors]
    return build_report(entries, options, as_of=date(2026, 1, 1))


class TestNothingLeavesTheMachine:
    """The README promises no network access. This enforces it."""

    def test_no_networking_module_is_imported(self):
        offenders = {
            str(path.relative_to(SRC)): sorted(imported_modules(path) & NETWORKING)
            for path in source_files()
            if imported_modules(path) & NETWORKING
        }
        assert offenders == {}

    def test_a_report_still_builds_with_the_network_taken_away(self, ledger, tmp_path):
        """Not "no import of socket" — no socket, at all, for the whole run.

        Anything that tried to open one would raise instead of connecting, so a
        clean exit here means nothing tried.
        """
        code = f"""
import socket
def refuse(*a, **k):
    raise AssertionError("the network was used")
socket.socket = refuse
socket.create_connection = refuse
socket.socketpair = refuse
socket.getaddrinfo = refuse
from beancount_zakat.cli import main
raise SystemExit(main([{str(ledger)!r}, "--as-of", "2026-01-01", "--width", "100"]))
"""
        result = run_python(code, cwd=tmp_path)
        assert result.returncode == EXIT_OK, result.stderr
        assert "Zakat" in result.stdout

    def test_the_dashboard_javascript_never_requests_anything(self):
        js = (SRC / "fava_extension" / "ZakatDashboard.js").read_text(encoding="utf-8")
        # Strip comments first: a JSDoc type annotation mentioning import() is
        # documentation, not a call.
        code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        for token in (
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "EventSource",
            "sendBeacon",
            "importScripts",
        ):
            assert token not in code, f"{token} appears in the dashboard script"


class TestNoCodeIsBuiltAtRuntime:
    """Nothing in this package turns a string into code, or data into objects."""

    def test_nothing_is_evaluated_or_compiled(self):
        offenders = []
        for path in source_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"eval", "exec", "compile", "__import__"}
                ):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
        assert offenders == []

    def test_nothing_is_unpickled_and_no_process_is_spawned(self):
        offenders = {
            str(path.relative_to(SRC)): sorted(imported_modules(path) & UNSAFE)
            for path in source_files()
            if imported_modules(path) & UNSAFE
        }
        assert offenders == {}

    def test_the_os_module_is_never_used_to_run_a_command(self):
        for path in source_files():
            text = path.read_text(encoding="utf-8")
            for token in ("os.system", "os.popen", "os.exec", "os.spawn"):
                assert token not in text, f"{token} in {path.relative_to(SRC)}"


class TestNothingIsWrittenUnlessAsked:
    """Reading a ledger is a read. The only writes are exports you requested."""

    def test_a_plain_run_leaves_the_working_directory_alone(self, ledger, tmp_path):
        before = set(tmp_path.rglob("*"))
        code = (
            "from beancount_zakat.cli import main\n"
            f"raise SystemExit(main([{str(ledger)!r}, '--as-of', '2026-01-01']))\n"
        )
        result = run_python(code, cwd=tmp_path, env=_no_bytecode())
        assert result.returncode == EXIT_OK, result.stderr
        assert set(tmp_path.rglob("*")) == before

    def test_an_export_writes_only_where_it_was_pointed(self, ledger, tmp_path):
        out = tmp_path / "export"
        elsewhere = tmp_path / "untouched"
        elsewhere.mkdir()
        assert (
            main([str(ledger), "--as-of", "2026-01-01", "--csv", str(out)]) == EXIT_OK
        )
        assert out.is_dir() and any(out.iterdir())
        assert list(elsewhere.iterdir()) == []


def _no_bytecode() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class TestTheSameLedgerAlwaysGivesTheSameAnswer:
    """No clock, no randomness, no dictionary-ordering luck in the result."""

    def test_two_reports_from_one_ledger_are_equal(self, ledger):
        entries, errors, options = loader.load_file(str(ledger))
        assert not errors
        first = build_report(entries, options, as_of=date(2026, 1, 1))
        second = build_report(entries, options, as_of=date(2026, 1, 1))
        assert first == second

    def test_the_output_does_not_depend_on_the_hash_seed(self, ledger, tmp_path):
        """PYTHONHASHSEED changes set and dict iteration order between runs.

        If any figure, or the order of any row, were derived from one, the runs
        below would disagree.
        """
        code = (
            "from beancount_zakat.cli import main\n"
            f"raise SystemExit(main([{str(ledger)!r}, '--as-of', '2026-01-01',"
            " '--width', '100']))\n"
        )
        outputs = []
        for seed in ("0", "1", "424242"):
            env = _no_bytecode()
            env["PYTHONHASHSEED"] = seed
            result = run_python(code, cwd=tmp_path, env=env)
            assert result.returncode == EXIT_OK, result.stderr
            outputs.append(result.stdout)
        assert outputs[0] == outputs[1] == outputs[2]
        assert outputs[0].strip()


class TestMoneyNeverBecomesAFloat:
    """Source-level checks live in test_formatting; this checks the result."""

    def test_no_float_survives_anywhere_in_a_finished_report(self, report):
        found: list[str] = []

        def walk(value, path: str) -> None:
            if isinstance(value, float):
                found.append(f"{path} = {value!r}")
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                for field in dataclasses.fields(value):
                    walk(getattr(value, field.name), f"{path}.{field.name}")
            elif isinstance(value, (list, tuple, set, frozenset)):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")
            elif isinstance(value, dict):
                for key, item in value.items():
                    walk(key, f"{path}<key>")
                    walk(item, f"{path}[{key!r}]")

        walk(report, "report")
        assert found == []

    def test_exported_money_reads_back_as_an_exact_decimal(self, ledger, tmp_path):
        out = tmp_path / "csv"
        assert (
            main([str(ledger), "--as-of", "2026-01-01", "--csv", str(out)]) == EXIT_OK
        )
        import csv

        checked = 0
        for path in sorted(out.glob("*.csv")):
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    for cell in row.values():
                        if not cell or not re.fullmatch(r"-?\d+\.\d{2}", cell):
                            continue
                        # Round-trips exactly, and carries no separators or
                        # symbols a spreadsheet would read as text.
                        assert str(Decimal(cell)) == cell.lstrip("+")
                        checked += 1
        assert checked > 0, "no monetary cells were found to check"


class TestTheDependenciesAreTheDeclaredOnes:
    """An import that is never declared breaks on a clean install."""

    def test_every_third_party_import_is_declared(self):
        imported: set[str] = set()
        for path in source_files():
            imported |= {name.split(".")[0] for name in imported_modules(path)}
        third_party = {
            name
            for name in imported
            if name not in sys.stdlib_module_names
            and name != "beancount_zakat"
            and not name.startswith("_")
        }
        # beancount and hijridate are runtime dependencies; fava is the
        # optional extra; flask arrives with fava and is only ever touched
        # from inside the extension, which cannot run without it.
        assert third_party == {"beancount", "fava", "flask", "hijridate"}

    def test_fava_and_flask_stay_inside_the_extension(self):
        for path in source_files():
            if "fava_extension" in path.parts:
                continue
            tops = {name.split(".")[0] for name in imported_modules(path)}
            assert not tops & {"fava", "flask"}, path.relative_to(SRC)


class TestNothingPrivateIsInTheRepository:
    """Fixtures are synthetic; this is what enforces it."""

    @staticmethod
    def text_files() -> list[Path]:
        skip_dirs = {
            ".git",
            ".venv",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "dist",
            "build",
            "node_modules",
            "screenshots",
        }
        keep = {
            ".py",
            ".md",
            ".toml",
            ".yml",
            ".yaml",
            ".txt",
            ".html",
            ".js",
            ".css",
            ".beancount",
            ".cfg",
        }
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.suffix in keep
            and not skip_dirs & set(path.parts)
        ]

    def test_no_email_address_is_committed(self):
        pattern = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
        offenders = [
            f"{path.relative_to(ROOT)}: {match.group(0)}"
            for path in self.text_files()
            for match in [
                pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
            ]
            if match
        ]
        assert offenders == []

    def test_no_real_home_directory_path_is_committed(self):
        home = str(Path.home())
        pattern = re.compile(r"(/home/|/Users/|[A-Z]:\\Users\\)[A-Za-z0-9._-]+")
        offenders = []
        for path in self.text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            if home in text:
                offenders.append(f"{path.relative_to(ROOT)}: {home}")
            # Fixtures deliberately contain invented paths, to prove the
            # dashboard never echoes one back into the page.
            if "tests" in path.parts:
                continue
            match = pattern.search(text)
            if match:
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        assert offenders == []

    def test_the_only_ledgers_are_the_example_and_the_fixtures(self):
        allowed = {"examples", "tests"}
        strays = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*.beancount")
            if not allowed & set(path.relative_to(ROOT).parts)
            and ".venv" not in path.parts
        ]
        assert strays == []


class TestBadInputFailsLoudly:
    """No input may end in a traceback."""

    @pytest.mark.parametrize(
        ("name", "body"),
        [
            ("empty", ""),
            ("only comments", "; nothing to see\n"),
            ("no tagged account", 'option "operating_currency" "PKR"\n'),
            (
                "tagged account, no prices",
                'option "operating_currency" "PKR"\n'
                "2019-01-01 open Assets:Cash PKR\n"
                '  beancount_zakat: "asset"\n'
                "2019-01-01 open Equity:Opening\n"
                '2019-01-01 * "Opening"\n'
                "  Assets:Cash 900000.00 PKR\n"
                "  Equity:Opening\n",
            ),
        ],
    )
    def test_an_odd_ledger_is_reported_not_crashed(self, tmp_path, name, body):
        path = tmp_path / "odd.beancount"
        path.write_text(body, encoding="utf-8")
        code = (
            "from beancount_zakat.cli import main\n"
            f"raise SystemExit(main([{str(path)!r}, '--as-of', '2026-01-01',"
            " '--width', '100']))\n"
        )
        result = run_python(code, cwd=tmp_path, env=_no_bytecode())
        assert "Traceback" not in result.stderr, result.stderr
        assert result.returncode in {0, 1}, result.returncode


class TestAReportCannotBeChangedAfterItIsBuilt:
    """Presentation gets a frozen result, so no view can alter a figure."""

    def test_the_report_rejects_assignment(self, report):
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.zakat_rate = Decimal("0.05")

    def test_its_parts_reject_assignment_too(self, report):
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.gold.total_due = Decimal("0")
