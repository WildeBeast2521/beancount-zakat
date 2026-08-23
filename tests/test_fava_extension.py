"""Smoke and integration tests against a real, running Fava.

These exercise the actual Flask app rather than a mock, because the failure
modes that matter here -- a template that renders a nested second document, a
stylesheet Fava never serves, a route that does not exist -- are invisible to
anything that stops short of a real request.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

fava = pytest.importorskip("fava", reason="Fava is an optional dependency")

from fava.application import create_app  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/ledger/main.beancount"


@pytest.fixture(scope="module")
def client():
    if not EXAMPLE.exists():  # pragma: no cover
        pytest.skip("example ledger missing")
    app = create_app([str(EXAMPLE)], load=True)
    return app.test_client()


@pytest.fixture(scope="module")
def slug(client) -> str:
    return client.get("/").headers["Location"].strip("/").split("/")[0]


@pytest.fixture(scope="module")
def base(slug) -> str:
    return f"/{slug}/extension/ZakatDashboard/"


@pytest.fixture(scope="module")
def page(client, base) -> str:
    """The whole document Fava serves, extension chrome included."""
    response = client.get(base)
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def fragment(client, base) -> str:
    """Only the extension's own fragment, as the SPA router requests it."""
    response = client.get(base, query_string={"partial": "1"})
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def markup(fragment: str) -> str:
    """The fragment with its inline stylesheet removed.

    Structural counts must not be confused by CSS selectors such as
    ``.bz-tab[aria-selected="true"]``.
    """
    return re.sub(r"<style>.*?</style>", "", fragment, flags=re.S)


class TestRegistration:
    def test_the_report_is_reachable(self, client, base):
        assert client.get(base).status_code == 200

    def test_the_js_module_is_served(self, client, slug):
        response = client.get(f"/{slug}/extension_js_module/ZakatDashboard.js")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "onExtensionPageLoad" in body
        assert "export default" in body

    def test_the_extension_declares_a_js_module(self):
        from beancount_zakat.fava_extension import ZakatDashboard

        assert ZakatDashboard.has_js_module is True
        assert ZakatDashboard.report_title == "Zakat"


class TestDocumentStructure:
    """Fava injects the fragment into its own layout: exactly one document."""

    def test_exactly_one_html_document(self, page):
        assert page.lower().count("<!doctype html>") == 1
        assert page.count("<html") == 1
        assert page.count("<body") == 1

    def test_favas_own_json_blobs_are_not_duplicated(self, page):
        assert page.count('id="ledger-data"') == 1
        assert page.count('id="page-title"') == 1

    def test_the_partial_route_returns_a_fragment(self, client, base):
        body = client.get(base + "?partial=1").get_data(as_text=True)
        assert "<!doctype" not in body.lower()
        assert "<html" not in body
        assert body.lstrip().startswith("<style>")
        assert "data-zakat-dashboard" in body

    def test_styles_are_inlined_not_linked(self, page):
        # Fava 1.30 has no extension_static route, so a <link> would 404.
        assert "<style>" in page
        assert ".bz-root" in page
        assert "extension_static" not in page


class TestNoRemoteDependencies:
    def test_no_script_or_style_is_loaded_from_a_third_party(self, page):
        external = set(re.findall(r'https?://[^\s"\'<>]+', page))
        assets = {
            url for url in external if re.search(r"\.(js|css|woff2?|png|svg)\b", url)
        }
        assert assets == set()

    def test_only_documentation_links_leave_the_machine(self, page):
        external = set(re.findall(r'https?://[^\s"\'<>]+', page))
        assert all(
            url.startswith(("https://humanappealusa.org", "https://www.zakat.org"))
            for url in external
        ), external

    def test_no_inline_event_handlers(self, page):
        """Behaviour belongs in the JS module, not in HTML attributes."""
        assert not re.search(r'\son[a-z]+\s*=\s*"', page)

    def test_the_chart_is_inline_svg(self, page):
        assert "<svg" in page
        assert "chart.js" not in page.lower()
        assert "cdn." not in page


class TestTabs:
    def test_six_tabs_with_about_last(self, page):
        keys = re.findall(r'data-bz-tab="([a-z]+)"', page)
        assert keys == [
            "overview",
            "yearly",
            "wealth",
            "detail",
            "payments",
            "about",
        ]
        assert keys[-1] == "about"

    def test_tab_semantics(self, markup):
        assert markup.count('role="tablist"') == 1
        assert markup.count('role="tab"') == 6
        assert markup.count('role="tabpanel"') == 6
        assert markup.count('aria-selected="true"') == 1
        assert markup.count('aria-selected="false"') == 5

    def test_each_tab_controls_a_panel_that_exists(self, page):
        for key in re.findall(r'data-bz-tab="([a-z]+)"', page):
            assert f'id="bz-panel-{key}"' in page
            assert f'aria-controls="bz-panel-{key}"' in page
            assert f'aria-labelledby="bz-tab-{key}"' in page

    def test_roving_tabindex(self, markup):
        tabs = re.findall(r'<button[^>]*role="tab"[^>]*>', markup)
        assert sum('tabindex="0"' in tab for tab in tabs) == 1
        assert sum('tabindex="-1"' in tab for tab in tabs) == 5

    def test_every_panel_but_the_first_is_hidden_server_side(self, page):
        panels = re.findall(r'<section class="bz-panel"[^>]*>', page)
        assert len(panels) == 6
        assert sum("hidden" in p for p in panels) == 5


class TestContent:
    def test_both_bases_are_shown_side_by_side(self, page):
        assert "Gold basis" in page
        assert "Silver basis" in page
        assert "bz-card--gold" in page
        assert "bz-card--silver" in page

    def test_the_alternatives_are_explained(self, page):
        assert "alternative" in page.lower()
        assert "never" in page.lower()

    def test_the_as_of_date_is_stated(self, page):
        assert re.search(r"As of <b>\d{4}-\d{2}-\d{2}</b>", page)

    def test_the_disclaimer_is_present_and_complete(self, page):
        for phrase in (
            "informational and record-keeping purposes only",
            "school of jurisprudence",
            "does not constitute religious, legal, tax, accounting, or "
            "financial advice",
            "qualified Islamic scholar",
        ):
            assert phrase in page, phrase

    def test_the_methodology_names_the_constants(self, page):
        assert "87.48" in page and "7.5 tola" in page
        assert "612.36" in page and "52.5 tola" in page
        assert "2.5%" in page
        assert "354.36708" in page

    def test_the_methodology_flags_itself_as_a_choice(self, page):
        assert "not a standard scholarly formula" in page
        assert "not a consensus" in page

    def test_sources_are_cited_with_a_retrieval_date(self, page):
        assert "Sources" in page
        assert "retrieved 2026-08-19" in page
        assert "AAOIFI" in page

    def test_spelling_is_hawl_not_haul(self, page):
        # One deliberate mention, in the note explaining the alternative form.
        assert page.count("haul") == 1
        assert "hawl" in page

    def test_no_server_paths_leak_from_the_extension(self, fragment):
        """Fava's own ledger-data blob carries filenames; ours must not."""
        assert not re.search(r"(/home/|/mnt/|[A-Z]:\\\\Users)", fragment)
        assert "site-packages" not in fragment
        assert "Traceback" not in fragment


class TestAccessibility:
    def test_tables_use_scoped_headers(self, page):
        assert page.count('scope="col"') > 20
        assert page.count('scope="row"') > 5

    def test_tables_have_captions(self, page):
        assert page.count("<caption>") >= 5

    def test_the_chart_has_a_text_alternative(self, page):
        assert 'role="img"' in page
        assert 'aria-labelledby="bz-chart-title bz-chart-desc"' in page
        assert '<title id="bz-chart-title">' in page
        assert '<desc id="bz-chart-desc">' in page

    def test_status_is_never_signalled_by_colour_alone(self, page):
        for chip in re.findall(r'<span class="bz-chip[^"]*">([^<]*)</span>', page):
            text = re.sub(r"&#\d+;", "", chip).strip()
            assert text, "a status chip carried no text"

    def test_dark_mode_and_reduced_motion_are_handled(self, page):
        # Dark mode is not the extension's own media query: it inherits Fava's
        # tokens, so it follows the colour scheme the user picked in Fava even
        # when that contradicts the operating system.
        assert "var(--lightningcss-dark," in page
        assert "var(--text-color)" in page
        assert "prefers-reduced-motion: reduce" in page

    def test_focus_is_visible(self, page):
        assert ":focus-visible" in page

    def test_wide_tables_scroll_inside_their_own_container(self, page):
        assert "overflow-x: auto" in page
        assert page.count('class="bz-scroll"') >= 5


class TestTimeFilter:
    """Fava's time filter sets the cutoff; it never truncates the start."""

    def _as_of(self, body: str) -> str:
        match = re.search(r"As of <b>(\d{4}-\d{2}-\d{2})</b>", body)
        assert match
        return match.group(1)

    def _header(self, body: str) -> str:
        match = re.search(r'<div class="bz-meta">(.*?)</div>', body, re.S)
        assert match
        return match.group(1)

    def test_no_filter_uses_today(self, client, base):
        body = client.get(base).get_data(as_text=True)
        assert self._as_of(body) == date.today().isoformat()
        assert "from Fava" not in self._header(body)

    @pytest.mark.parametrize(
        ("time_filter", "expected"),
        [
            ("2024", "2024-12-31"),
            ("2026-10-01", "2026-10-01"),
            ("2022-01-01 - 2023-06-30", "2023-06-30"),
        ],
    )
    def test_the_filter_sets_the_cutoff(self, client, base, time_filter, expected):
        body = client.get(base, query_string={"time": time_filter}).get_data(
            as_text=True
        )
        assert self._as_of(body) == expected
        assert "from Fava" in self._header(body)

    def test_the_timeline_still_starts_at_inception(self, client, base):
        body = client.get(base, query_string={"time": "2024"}).get_data(as_text=True)
        dates = re.findall(r'<th scope="row">(\d{4}-\d{2}-\d{2})</th>', body)
        assert dates[0] == "2019-02-01"

    def test_a_narrower_filter_never_increases_the_liability(self, client, base):
        def liability(query):
            body = client.get(base, query_string=query).get_data(as_text=True)
            match = re.search(r"<dt>Cumulative liability</dt>\s*<dd>([\d,.]+)", body)
            assert match
            return float(match.group(1).replace(",", ""))

        assert liability({"time": "2022"}) < liability({})


class TestCsvEndpoints:
    def test_the_zip_endpoint(self, client, base):
        import io
        import zipfile

        response = client.get(base + "download_csv")
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert "attachment" in response.headers["Content-Disposition"]
        with zipfile.ZipFile(io.BytesIO(response.get_data())) as archive:
            assert "yearly_summary.csv" in archive.namelist()

    @pytest.mark.parametrize(
        "name",
        [
            "yearly_summary.csv",
            "detail_gold.csv",
            "detail_silver.csv",
            "nisab_history.csv",
            "payments.csv",
        ],
    )
    def test_single_file_endpoints(self, client, base, name):
        response = client.get(base + "download_csv", query_string={"file": name})
        assert response.status_code == 200
        assert response.mimetype == "text/csv"
        assert f"zakat_{name}" in response.headers["Content-Disposition"]

    def test_every_link_on_the_page_resolves(self, client, page):
        for url in sorted(set(re.findall(r'href="(/[^"]*)"', page))):
            assert client.get(url).status_code == 200, url


class TestErrorHandling:
    def test_a_failure_shows_a_safe_message_not_a_traceback(self, monkeypatch):
        from beancount_zakat import fava_extension

        def boom(*args, **kwargs):
            raise RuntimeError("secret path /home/someone/private.beancount")

        monkeypatch.setattr(fava_extension, "build_report", boom)
        app = create_app([str(EXAMPLE)], load=True)
        client = app.test_client()
        slug = client.get("/").headers["Location"].strip("/").split("/")[0]
        body = client.get(f"/{slug}/extension/ZakatDashboard/").get_data(as_text=True)
        assert "secret path" not in body
        assert "Traceback" not in body
        assert "could not be calculated" in body
        assert 'role="alert"' in body


class TestCaching:
    def test_the_report_is_computed_once_per_revision(self, monkeypatch):
        from beancount_zakat import fava_extension

        calls = []
        original = fava_extension.build_report

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(fava_extension, "build_report", counting)
        app = create_app([str(EXAMPLE)], load=True)
        client = app.test_client()
        slug = client.get("/").headers["Location"].strip("/").split("/")[0]
        url = f"/{slug}/extension/ZakatDashboard/"
        client.get(url)
        first = len(calls)
        client.get(url)
        client.get(url)
        assert len(calls) == first, "the report should be cached per revision"
