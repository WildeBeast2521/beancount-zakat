"""Template and static-asset checks that do not need a running Fava.

Fava serves extension templates as fragments and has no static-asset route for
extensions, so the shape of the shipped assets matters as much as their content.
These checks assert that shape directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[1] / "src/beancount_zakat/fava_extension"
TEMPLATE = EXT / "templates/ZakatDashboard.html"
ABOUT = EXT / "templates/_zakat_about.html"
JS = EXT / "ZakatDashboard.js"


def strip_jinja_comments(source: str) -> str:
    return re.sub(r"\{#.*?#\}", "", source, flags=re.S)


@pytest.fixture(scope="module")
def template() -> str:
    return strip_jinja_comments(TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def about() -> str:
    return strip_jinja_comments(ABOUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def about_text(about: str) -> str:
    """About content with markup and line breaks collapsed, for prose checks."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", about))


@pytest.fixture(scope="module")
def script() -> str:
    return JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style(template: str) -> str:
    """Just the inlined stylesheet."""
    return template.split("<style>")[1].split("</style>")[0]


@pytest.fixture(scope="module")
def rules(style: str) -> str:
    """The stylesheet with its commentary removed, so prose cannot satisfy a
    check that is meant to be about a declaration."""
    return re.sub(r"/\*.*?\*/", "", style, flags=re.S)


#: Fava declares these on :root in its app.css. The dashboard consumes them
#: rather than inventing a parallel palette, so that it follows Fava's theme --
#: including the user's explicit light/dark choice -- without any work of its
#: own. Each entry is (property, what the dashboard uses it for).
FAVA_TOKENS = [
    ("--text-color", "body text"),
    ("--text-color-lightest", "muted text and chart labels"),
    ("--background", "the page ground"),
    ("--background-darker", "panels and cards"),
    ("--border", "hairlines"),
    ("--link-color", "links and the accent"),
    ("--table-border", "table cell borders"),
    ("--table-header-background", "table header fill"),
    ("--table-header-text", "table header text"),
    ("--table-background-even", "zebra striping"),
    ("--button-background", "primary buttons"),
    ("--button-color", "primary button text"),
    ("--button-muted-background", "toggles and inactive switches"),
    ("--button-muted-color", "toggle text"),
    ("--font-family", "the UI face"),
    ("--font-family-monospaced", "figures"),
    ("--code-background", "inline code and pre blocks"),
    ("--box-shadow-button", "the raised state Fava gives buttons"),
    ("--chart-axis", "chart gridlines"),
    ("--chart-line-at-zero", "the stacked chart's zero rule"),
    ("--placeholder-background", "date inputs"),
    ("--green", "hawl-complete and excess-paid"),
    ("--warning", "hawl-incomplete and the disclaimer"),
    ("--error", "error callouts and chips"),
]


class TestAssetsExist:
    def test_the_template_is_named_after_the_class(self):
        from beancount_zakat.fava_extension import ZakatDashboard

        assert TEMPLATE.name == f"{ZakatDashboard.__name__}.html"
        assert JS.name == f"{ZakatDashboard.__name__}.js"

    def test_the_js_module_sits_beside_the_package(self):
        assert JS.parent == EXT
        assert (EXT / "__init__.py").exists()

    def test_the_about_include_exists(self):
        assert ABOUT.exists()

    def test_no_orphaned_static_directory(self):
        """Fava 1.30 cannot serve extension static files; nothing may rely on it."""
        assert not (EXT / "static").exists()


class TestTemplateShape:
    def test_it_is_a_fragment_not_a_full_document(self, template):
        assert "{% extends" not in template
        assert "{% block" not in template
        assert "<html" not in template
        assert "<head>" not in template
        assert "<body" not in template

    def test_styles_are_inlined(self, template):
        assert "<style>" in template
        assert "extension_static" not in template
        assert "<link" not in template

    def test_no_inline_event_handlers(self, template, about):
        for source in (template, about):
            assert not re.search(r"\son(error|click|load|change)\s*=", source)

    def test_no_remote_asset_urls(self, template, about):
        for source in (template, about):
            for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', source):
                assert not re.search(r"\.(js|css|woff2?|png|svg)\b", url), url

    def test_css_is_scoped_to_the_dashboard_root(self, template):
        block = re.search(r"<style>(.*?)</style>", template, re.S)
        assert block
        css = block.group(1)
        # Drop at-rule preludes and their braces; only rule selectors matter.
        css = re.sub(r"@media[^{]*\{", "", css)
        selectors = re.findall(r"(?:^|\})\s*([^{}@]+?)\s*\{", css, re.S)
        unscoped = [
            selector.strip().replace("\n", " ")
            for selector in selectors
            if ".bz-" not in selector and ":root" not in selector
        ]
        assert unscoped == [], unscoped

    def test_the_url_for_endpoint_collision_is_avoided(self, template):
        """`url_for(endpoint=...)` collides with Flask's own first parameter."""
        assert "url_for(" not in template
        assert "extension.csv_url(" in template

    def test_every_table_has_a_caption_and_scoped_headers(self, template):
        tables = re.findall(r"<table[^>]*>(.*?)</table>", template, re.S)
        assert len(tables) >= 5
        for body in tables:
            assert "<caption>" in body
            assert 'scope="col"' in body


class TestTabDefinitions:
    def test_the_tab_list_is_declared_once_and_ends_with_about(self, template):
        block = re.search(r"\{% set tabs = \[(.*?)\] %\}", template, re.S)
        assert block
        keys = re.findall(r"\('([a-z]+)',", block.group(1))
        assert keys == [
            "overview",
            "yearly",
            "wealth",
            "detail",
            "payments",
            "about",
        ]
        assert keys[-1] == "about", "About must always be the last tab"

    def test_calculation_detail_is_split_per_basis(self, template):
        """A reset under one threshold says nothing about the other."""
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert 'data-bz-basis-panel="{{ sec.basis.value }}"' in panel
        assert 'data-bz-basis="gold"' in panel
        assert 'data-bz-basis="silver"' in panel
        assert "ctx.detail" in panel

    def test_each_basis_gets_a_chart_and_a_hawl_strip(self, template):
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert "bz-reset-band" in panel, "below-nisab shading explains the reset"
        assert "bz-strip" in panel
        assert "bz-seg bz-seg--" in panel
        assert "sec.chart" in panel and "sec.strip" in panel

    def test_the_hawl_column_has_three_states(self, template):
        """ "Incomplete" must not be used for a stretch below the nisab."""
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert "reset (below nisab)" in panel
        assert "p.hawl.value == 'complete'" in panel
        assert "p.hawl.value == 'incomplete'" in panel
        assert "Lunar years counted" in panel

    def test_the_detail_table_shows_a_nisab_range_not_a_figure(self, template):
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert "nisab in force" in panel
        assert "nisab_span" in panel

    def test_the_overview_quotes_no_point_in_time_threshold(self, template):
        panel = template.split('data-bz-panel="overview"')[1].split(
            'data-bz-panel="yearly"'
        )[0]
        for banned in ("Nisab threshold", "Price used", "Price date"):
            assert banned not in panel, banned
        # The moving threshold is documented where it is actually used: the
        # nisab history on Wealth & Nisab and the range column on the detail
        # table. The overview carries the settled position only.
        assert "Net Zakat Liability / Paid in Excess till date" in panel

    def test_wealth_tab_carries_the_nisab_history(self, template):
        panel = template.split('data-bz-panel="wealth"')[1].split(
            'data-bz-panel="detail"'
        )[0]
        assert "Nisab thresholds over time" in panel
        assert "nisab_history" in panel
        assert "In force from" in panel

    def test_every_tab_key_has_a_panel(self, template):
        block = re.search(r"\{% set tabs = \[(.*?)\] %\}", template, re.S)
        keys = re.findall(r"\('([a-z]+)',", block.group(1))
        for key in keys:
            assert f'data-bz-panel="{key}"' in template


class TestAboutContent:
    """Educational content lives in a template so it stays diffable."""

    def test_the_disclaimer_is_verbatim(self, about_text):
        for phrase in (
            "informational and record-keeping purposes only",
            "vary by school of jurisprudence, asset type, debt treatment",
            "personal circumstances, locality, and scholar",
            "may contain errors",
            "does not constitute religious, legal, tax, accounting, or "
            "financial advice",
            "consult a qualified Islamic scholar",
        ):
            assert phrase in about_text, phrase

    def test_it_explains_the_three_core_ideas(self, about):
        for term in ("Nisab", "Sahib-e-nisab", "Hawl"):
            assert term in about

    def test_it_states_the_constants_in_grams_and_tola(self, about):
        assert "gold_grams" in about and "gold_tola" in about
        assert "silver_grams" in about and "silver_tola" in about
        assert "lunar_year_days" in about

    def test_it_covers_every_methodology_step(self, about):
        for topic in (
            "Select accounts",
            "Build a wealth timeline",
            "Value everything",
            "Compute the nisab over time",
            "Slice the wealth into levels",
            "Time each slice",
            "Handle resets",
            "Charge each qualifying period",
            "Apply payments last",
            "Rounding",
            "Allocation to reporting years",
            "Calendar handling",
        ):
            assert topic in about, topic

    def test_it_states_the_fractional_year_position_explicitly(self, about_text):
        """The tool's interpretive choice must be stated, not implied."""
        assert "stated position of this tool" in about_text
        assert "not a restriction limiting liability to whole years" in about_text
        assert "deliberate interpretive choice" in about_text
        assert "Raise the method itself with your scholar" in about_text

    def test_it_explains_that_the_nisab_moves(self, about_text):
        assert "moving threshold, not a fixed sum" in about_text
        assert "No single figure is quoted" in about_text

    def test_it_explains_the_as_of_behaviour(self, about_text):
        assert "time filter" in about_text
        assert "quiet ledger is not a zakat-free ledger" in about_text
        assert "nothing dated after the cutoff" in about_text.lower()

    def test_it_distinguishes_agreed_facts_from_this_tools_choices(self, about_text):
        assert "Universally agreed" in about_text
        assert "not a standard scholarly formula" in about_text
        assert "is a choice, not a consensus" in about_text

    def test_it_lists_limitations(self, about_text):
        assert "Limitations and assumptions" in about_text
        assert "Gram equivalents vary" in about_text
        assert "85 g" in about_text and "595 g" in about_text

    def test_sources_are_cited_with_a_retrieval_date(self, about):
        assert "<h3>Sources</h3>" in about
        assert "retrieved 2026-08-19" in about
        for source in ("Qur", "Bukhari", "AAOIFI", "Human Appeal"):
            assert source in about, source

    def test_no_source_is_asserted_without_attribution(self, about):
        block = about[about.index("<h3>Sources</h3>") :]
        assert block.count("<li>") >= 5

    def test_configuration_examples_are_present(self, about):
        assert 'beancount_zakat: "asset"' in about
        assert 'beancount_zakat: "liability"' in about
        assert 'beancount_zakat: "expense"' in about
        assert "metal_commodities" in about
        assert "fava-extension" in about


class TestJavaScript:
    def test_it_uses_favas_documented_lifecycle(self, script):
        assert "export default" in script
        assert "onExtensionPageLoad" in script

    def test_it_is_progressive_enhancement(self, script):
        assert "progressive enhancement" in script

    def test_it_implements_keyboard_navigation(self, script):
        for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Home", "End"):
            assert f'"{key}"' in script, key

    def test_it_manages_aria_state(self, script):
        assert "aria-selected" in script
        assert "aria-pressed" in script
        assert "aria-sort" in script

    def test_it_persists_the_selected_tab(self, script):
        assert "location.hash" in script
        assert "sessionStorage" in script

    def test_storage_failures_cannot_break_the_page(self, script):
        assert script.count("catch") >= 2

    def test_no_remote_fetches(self, script):
        assert "fetch(" not in script
        assert "cdn." not in script
        # The SVG namespace URI is an identifier, not an address anything is
        # ever retrieved from; nothing else may carry a URL.
        remote = [
            line
            for line in script.splitlines()
            if ("http://" in line or "https://" in line)
            and "www.w3.org/2000/svg" not in line
        ]
        assert remote == []


class TestFullWidthLayout:
    def test_the_dashboard_is_not_capped_to_a_fixed_column(self, template):
        assert "max-width: 1200px" not in template
        assert "max-width: none" in template

    def test_prose_is_still_capped_to_a_readable_measure(self, template):
        assert ".bz-root p { margin: 0 0 1rem; max-width: 70ch; }" in template


@pytest.fixture(scope="module")
def overview(template: str) -> str:
    """Just the Overview panel."""
    return template.split('data-bz-panel="overview"')[1].split(
        'data-bz-panel="yearly"'
    )[0]


@pytest.fixture(scope="module")
def figures(template: str) -> list[str]:
    """One entry per interactive chart figure, cut at its closing tag."""
    return [
        chunk.split("</figure>")[0] for chunk in template.split("data-bz-chart>")[1:]
    ]


@pytest.fixture(scope="module")
def stack_figure(figures: list[str]) -> str:
    """The one figure that stacks accounts: Wealth & Nisab, first chart."""
    return next(figure for figure in figures if "bz-band bz-band--" in figure)


class TestOverviewTrimming:
    def test_the_as_of_date_carries_a_full_hijri_date(self, template):
        head = template.split('class="bz-head"')[1].split("</div>")[0]
        assert "{{ ctx.as_of_hijri }}" in head
        # A bare year would be followed by a literal " AH" in the template;
        # the full date brings its own era marker.
        assert "AH)" not in head

    def test_it_does_not_call_out_being_below_the_nisab_today(self, overview):
        assert "Below nisab today" not in overview
        assert "Sahib-e-nisab today" in overview

    def test_accounts_are_listed_one_per_line(self, overview):
        assert "bz-acctlist" in overview
        assert "<li>{{ account }}</li>" in overview
        assert "|join(', ')" not in overview

    def test_the_liability_block_is_a_heading(self, overview):
        assert "<h3>Net Zakat Liability / Paid in Excess till date</h3>" in overview
        assert "alternative</strong> bases" not in overview


class TestInteractiveCharts:
    def test_every_chart_is_an_interactive_figure(self, figures):
        # Wealth & Nisab carries two: the account stack, then net against both
        # thresholds. Calculation Detail carries one per basis section.
        assert len(figures) == 3

    def test_each_figure_carries_its_own_data(self, template):
        assert "{{ ctx.stack_data|tojson }}" in template
        assert "{{ ctx.chart_data|tojson }}" in template
        assert "{{ sec.chart_data|tojson }}" in template
        assert template.count("data-bz-chart-data") == 3

    def test_each_figure_has_series_toggles_and_a_date_filter(self, figures):
        for figure in figures:
            assert "data-bz-toggles" in figure
            assert "data-bz-range" in figure

    def test_the_static_svg_survives_for_readers_without_javascript(self, figures):
        for figure in figures:
            assert "<svg" in figure

    def test_the_wealth_tab_drops_the_thresholds_actually_used_aside(self, template):
        panel = template.split('data-bz-panel="wealth"')[1].split(
            'data-bz-panel="detail"'
        )[0]
        assert "the calculation actually used" not in panel

    def test_the_detail_tab_states_only_what_the_chart_shows(self, template):
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert "<p>The two bases are shown separately.</p>" in panel
        assert "says nothing about the other" not in panel
        assert "why each hawl ran or reset" not in panel
        assert "The chart plots net zakatable wealth over time" in panel

    def test_the_detail_chart_carries_no_bands(self, template):
        # Composition is the Wealth & Nisab tab's business; this section is
        # about one threshold and the hawl resets under it.
        panel = template.split('data-bz-panel="detail"')[1].split("</section>")[0]
        assert "bz-band bz-band--" not in panel
        assert "bz-zero" not in panel


class TestStackedComposition:
    """Exactly one chart stacks accounts, and it leaves the nisab alone."""

    def test_only_the_first_wealth_chart_stacks(self, figures):
        assert sum("bz-band bz-band--" in figure for figure in figures) == 1

    def test_a_band_names_its_account_for_hover_and_screen_readers(self, stack_figure):
        assert "<title>{{ band.account }}</title>" in stack_figure

    def test_the_zero_line_is_drawn_because_the_axis_goes_negative(self, stack_figure):
        assert 'class="bz-zero"' in stack_figure
        assert "stack.zero_y" in stack_figure

    def test_the_static_legend_lists_every_band(self, stack_figure):
        assert "bz-swatch--block" in stack_figure
        assert "{{ band.label }}" in stack_figure
        assert "(liability)" in stack_figure

    def test_the_stack_does_not_chart_the_nisab(self, stack_figure):
        # A stack front is a gross figure; the threshold applies to the net.
        assert "nisab" not in stack_figure.lower()

    def test_the_palette_wraps_rather_than_running_out(self, template):
        assert template.count("band.index % 10") == 2  # the band, and its legend

    def test_every_palette_slot_the_bands_can_reach_is_defined(self, template):
        for slot in range(10):
            assert f"--bz-cat-{slot}:" in template

    def test_the_wealth_tab_explains_the_stack(self, template):
        panel = template.split('data-bz-panel="wealth"')[1].split(
            'data-bz-panel="detail"'
        )[0]
        assert "Anything held stacks upwards from the zero line" in panel
        assert "anything owed hangs below it" in panel

    def test_the_second_wealth_chart_carries_the_thresholds(self, figures):
        threshold_charts = [f for f in figures if "Silver nisab" in f]
        assert len(threshold_charts) == 1
        assert "bz-band bz-band--" not in threshold_charts[0]

    def test_the_wealth_area_fill_gave_way_to_the_bands(self, template):
        # The stack *is* the fill now; a translucent wash under the net line on
        # top of it would only muddy the bands.
        assert "bz-area-wealth" not in template


class TestStackedChartScript:
    def test_the_browser_stacks_the_bands_itself(self, script):
        # Unstacked data plus client-side stacking is what lets a reader switch
        # one account off and see the rest re-stack.
        assert "function stackPath(" in script
        assert "data.stacks" in script

    def test_anything_negative_stacks_downwards(self, script):
        # By sign, not by role: an overdrawn asset must not be painted back
        # over the band beneath it.
        assert "const base = value < 0 ? falling : rising;" in script

    def test_the_axis_can_go_negative(self, script):
        assert "function niceFloor(" in script
        assert "const yMin = niceFloor(" in script

    def test_the_readout_totals_what_is_shown(self, script):
        assert '"Stack total"' in script
        assert '"Shown accounts"' in script

    def test_accounts_and_lines_share_one_toggle_mechanism(self, script):
        assert "const addToggle = (item, paint)" in script
        assert "visibleCount() > 1" in script


class TestFavaThemeConsistency:
    """The dashboard must look like part of Fava, not like a guest in it.

    Fava publishes its whole theme as custom properties on ``:root``. Consuming
    those is the only way the extension can track a Fava restyle, or the user's
    light/dark choice, without shipping a second copy of the palette that drifts.
    """

    @pytest.mark.parametrize(("token", "used_for"), FAVA_TOKENS)
    def test_fava_token_is_consumed(self, rules, token, used_for):
        assert f"var({token}" in rules, f"{token} ({used_for}) is not used"

    def test_no_hardcoded_light_theme_colours_outside_the_palette(self, rules):
        """Literal hex only in the categorical palette and in alpha keywords.

        Anything else with a fixed hue would stay put when Fava's theme moved.
        A hex inside ``var(--token, #fallback)`` does not count: it is reached
        only when Fava is not the one rendering the page.
        """
        stripped = re.sub(r"--bz-cat-\d+:\s*#[0-9a-f]{3,8}", "", rules)
        stripped = re.sub(
            r"var\((?!--lightningcss)--[a-z0-9-]+,\s*#[0-9a-f]{3,8}\)", "", stripped
        )
        stray = {
            match
            for match in re.findall(r"#[0-9a-f]{3,8}", stripped)
            if match != "#0000"  # transparent, exactly as Fava writes it
        }
        # The metals are the one exception: Fava has no gold or silver of its
        # own, so the dashboard supplies both, one value per theme.
        assert stray == {"#8a6100", "#d9a94a", "#566572", "#a8b6c4"}, stray

    def test_light_and_dark_follow_favas_own_switch(self, rules):
        """Not ``prefers-color-scheme``.

        Fava lets the user override the system preference. A media query would
        ignore that override; Fava's ``--lightningcss-*`` pair does not.
        """
        assert "var(--lightningcss-light," in rules
        assert "var(--lightningcss-dark," in rules
        assert "prefers-color-scheme" not in rules

    def test_headings_use_favas_type_scale(self, rules):
        assert "font-size: 1.2857em" in rules  # h2, as in Fava's base.css
        assert "font-size: 1.1429em" in rules  # h3
        assert "font-weight: 500" in rules

    def test_tables_copy_favas_cell_metrics(self, rules):
        assert ".bz-table th, .bz-table td { padding: 2px 5px" in rules
        assert "width: 7em" in rules  # td.num, as in Fava

    def test_the_categorical_palette_is_favas_own_scale(self, rules):
        """Fava's ``hcl_color_range(10)``: hue 270 + n*36, chroma 45, luminance 70.

        Recomputed here rather than trusted, so a typo in a hex digit fails.
        """
        expected = [
            "#7eaefd",
            "#c29bee",
            "#eb8cc6",
            "#f78a94",
            "#e5986a",
            "#beaa57",
            "#8bb866",
            "#4bbf90",
            "#00c0c3",
            "#17bbec",
        ]
        for slot, colour in enumerate(expected):
            assert f"--bz-cat-{slot}:{colour}" in rules

    def test_the_net_wealth_line_stays_legible_over_the_bands(self, rules):
        """Same colour on every chart; a halo only where bands sit behind it."""
        assert ".bz-chart:has(.bz-band) .bz-line-wealth { filter: drop-shadow" in rules
        assert rules.count(".bz-line-wealth { stroke: var(--bz-accent)") == 1

    def test_the_sort_indicator_matches_favas_triangles(self, rules):
        """Fava hangs them off ``data-order``; ours off ``aria-sort``."""
        assert 'th[aria-sort="ascending"]::after' in rules
        assert 'th[aria-sort="descending"]::after' in rules
        assert "border-top: 5px solid var(--text-color-lightest)" in rules
