"""Packaging checks: what a consumer actually receives when they install this.

These do not need Fava, a network, or a built artifact --- they read the
declared metadata and the source tree, which is where packaging mistakes are
made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# `tomllib` arrived in 3.11 and this project still supports 3.10. Rather than
# take a TOML dependency for one test module, skip it on the old interpreter --
# packaging metadata does not vary by Python version, so checking it on the
# other three is enough.
tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src/beancount_zakat"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class TestVersionIsSingleSourced:
    """One version string. Two would drift, and the drift would ship."""

    def test_pyproject_reads_the_version_from_the_package(self, pyproject):
        assert "version" in pyproject["project"]["dynamic"]
        assert "version" not in pyproject["project"]
        assert pyproject["tool"]["hatch"]["version"]["path"] == (
            "src/beancount_zakat/__init__.py"
        )

    def test_the_cli_reports_that_same_version(self):
        from beancount_zakat import __version__

        assert (PKG / "__init__.py").read_text(encoding="utf-8").count(
            f'__version__ = "{__version__}"'
        ) == 1


class TestTheWheelCarriesEverythingTheDashboardNeeds:
    """Fava loads the template and the JS module off the installed package. If
    the build misses them the extension imports fine and then renders nothing.
    """

    @pytest.mark.parametrize(
        "relative",
        [
            "fava_extension/templates/ZakatDashboard.html",
            "fava_extension/templates/_zakat_about.html",
            "fava_extension/ZakatDashboard.js",
            "py.typed",
        ],
    )
    def test_non_python_file_is_inside_the_package_directory(self, relative):
        # hatchling's `packages = [...]` ships the whole directory, so being in
        # it is the whole requirement. Nothing here may live outside src/.
        assert (PKG / relative).is_file()

    def test_the_wheel_ships_the_package_and_nothing_else(self, pyproject):
        wheel = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
        assert wheel["packages"] == ["src/beancount_zakat"]


class TestDependenciesAreHonest:
    """Fava must stay optional: the engine and the CLI are the reason this can
    be installed on a machine that has never had Fava.
    """

    def test_fava_is_not_a_runtime_dependency(self, pyproject):
        runtime = " ".join(pyproject["project"]["dependencies"])
        assert "fava" not in runtime

    def test_fava_is_available_as_an_extra(self, pyproject):
        extras = pyproject["project"]["optional-dependencies"]
        assert any(spec.startswith("fava") for spec in extras["fava"])

    def test_every_runtime_dependency_is_bounded(self, pyproject):
        # An unbounded upper bound means a major release of Beancount silently
        # becomes this package's problem, in an environment we cannot test.
        for spec in pyproject["project"]["dependencies"]:
            assert "<" in spec, f"{spec} has no upper bound"

    def test_requirements_txt_matches_the_declared_runtime_dependencies(self):
        """`requirements.txt` is a convenience mirror, not a second source."""
        declared = {
            spec.replace(" ", "")
            for spec in tomllib.loads(
                (ROOT / "pyproject.toml").read_text(encoding="utf-8")
            )["project"]["dependencies"]
        }
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pinned = {
            line.split("#")[0].strip().replace(" ", "")
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        assert pinned == declared


class TestProjectMetadata:
    def test_the_urls_do_not_still_say_owner(self, pyproject):
        for url in pyproject["project"]["urls"].values():
            assert "OWNER" not in url

    def test_the_readme_and_licence_are_declared(self, pyproject):
        assert pyproject["project"]["readme"] == "README.md"
        assert pyproject["project"]["license"] == "MIT"
        assert (ROOT / "LICENSE").is_file()

    def test_the_sdist_leaves_out_the_screenshots(self, pyproject):
        # ~4.5 MB of evidence whose README links do not resolve on PyPI anyway.
        sdist = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]
        assert "/docs/screenshots" in sdist["exclude"]
