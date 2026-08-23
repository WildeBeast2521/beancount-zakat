"""Fava extension: the Zakat Dashboard.

Register it in your ledger with::

    2020-01-01 custom "fava-extension" "beancount_zakat.fava_extension" "{}"

Written against the Fava 1.30 extension API:

* the report template is a **fragment** --- Fava renders it and injects the
  result into its own ``_layout.html``, so it must not ``{% extends %}``
  anything, or the page ends up containing a second nested HTML document;
* Fava exposes no static-asset route for extensions, so CSS is inlined into the
  template and behaviour is loaded through ``has_js_module``;
* ``ZakatDashboard.js`` is served from ``extension_js_module`` and hooks the
  documented ``onExtensionPageLoad`` lifecycle.

Fava's **time filter** sets the report cutoff: with a filter ending 2026-10-01
the report covers inception through 2026-10-01 inclusive. With no time filter
the cutoff is today. The filter never truncates the *start* of the timeline ---
hawl has to be measured from when wealth was actually acquired.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, cast

from fava.ext import FavaExtensionBase, extension_endpoint

from ..adapter import decimal_places_for
from ..chart import (
    below_nisab_spans,
    build_basis_chart,
    build_chart,
    build_hawl_strip,
    build_stacked_chart,
    chart_payload,
    composition_series,
    path_data,
)
from ..config import ConfigError
from ..constants import (
    GOLD_NISAB_GRAMS,
    GOLD_NISAB_TOLA,
    HIJRI_YEAR_DAYS,
    SILVER_NISAB_GRAMS,
    SILVER_NISAB_TOLA,
    TOLA_GRAMS,
)
from ..formatting import (
    format_decimal,
    format_money,
    format_rate,
    format_signed_balance,
    format_years,
)
from ..hijri import HijriRangeError, format_hijri_date, hijri_year_of
from ..models import Basis, NisabSpan, Severity, ZakatReport
from ..reporting import basis_period_rows
from ..service import build_report

log = logging.getLogger(__name__)

#: Shown to the user when the calculation raises. The real traceback goes to the
#: server log: rendering it into the page would leak absolute filesystem paths
#: to anyone who can reach the Fava instance.
GENERIC_ERROR = (
    "The zakat report could not be calculated. The details have been written "
    "to the Fava server log. Check that your ledger loads without errors and "
    "that the beancount_zakat metadata and price directives are valid."
)


class ZakatDashboard(FavaExtensionBase):
    """Zakat calculation dashboard for Fava."""

    report_title = "Zakat"
    has_js_module = True

    # ------------------------------------------------------------------
    # Cutoff
    # ------------------------------------------------------------------

    def as_of(self) -> date:
        """Report cutoff: the end of Fava's time filter, else today."""
        try:
            from flask import g

            filtered = getattr(g, "filtered", None)
            end = getattr(filtered, "end_date", None)
            if isinstance(end, date):
                return end
        except (ImportError, RuntimeError):  # pragma: no cover - no request ctx
            pass
        return date.today()

    def filter_is_active(self) -> bool:
        try:
            from flask import g

            filtered = getattr(g, "filtered", None)
            return getattr(filtered, "date_range", None) is not None
        except (ImportError, RuntimeError):  # pragma: no cover
            return False

    # ------------------------------------------------------------------
    # Calculation (cached per ledger revision and cutoff)
    # ------------------------------------------------------------------

    def _compute(self, as_of: date) -> ZakatReport:
        # Fava types entries against its own `fava.beans.abc.Directive`
        # protocol while the domain is typed against Beancount's own union.
        # They are the same objects at runtime; matching the two impedances is
        # exactly what an adapter is for.
        return build_report(
            cast("Any", self.ledger.all_entries),
            cast("dict[Any, Any]", self.ledger.options),
            as_of=as_of,
            extension_options=self.config if isinstance(self.config, dict) else None,
        )

    def report(self) -> dict[str, Any]:
        """Build the whole view model the template renders.

        Cached on ``(ledger mtime, cutoff)``. The template calls this while
        rendering, and the calculation is quadratic in the number of distinct
        wealth levels, so it must not run more than once per page.
        """
        as_of = self.as_of()
        key = (getattr(self.ledger, "mtime", None), as_of)
        cached = getattr(self, "_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]

        try:
            context = self._build_context(as_of)
        except (ConfigError, HijriRangeError) as exc:
            log.warning("beancount_zakat: %s", exc)
            context = self._error_context(as_of, str(exc))
        except Exception:
            log.exception("beancount_zakat: zakat report failed")
            context = self._error_context(as_of, GENERIC_ERROR)

        self._cache = (key, context)
        return context

    def _error_context(self, as_of: date, message: str) -> dict[str, Any]:
        return {
            "error": message,
            "report": None,
            "as_of": as_of,
            "currency": "",
            "places": 2,
        }

    def _build_context(self, as_of: date) -> dict[str, Any]:
        report = self._compute(as_of)
        entries = self.ledger.all_entries
        places = decimal_places_for(cast("Any", entries), report.operating_currency)

        wealth = [(point.when, point.net) for point in report.wealth_series]
        gold_nisab = [
            (point.when, point.nisab)
            for point in report.gold_nisab_series
            if point.nisab is not None
        ]
        silver_nisab = [
            (point.when, point.nisab)
            for point in report.silver_nisab_series
            if point.nisab is not None
        ]
        # Two charts, because they answer two different questions and mixing
        # them makes both harder to read. The first is a *presentation* of the
        # timeline the engine already produced --- the same per-account
        # balances, stacked, so the reader can see what the net figure is made
        # of. Nothing computed for it is ever read back.
        composition = [(point.when, point.by_account) for point in report.wealth_series]
        stacks = composition_series(
            composition, liability_accounts=report.liability_accounts
        )
        stack_overlay = [("wealth", "Net zakatable wealth", wealth)]
        stack = build_stacked_chart(
            composition=composition,
            liability_accounts=report.liability_accounts,
            overlays=stack_overlay,
            as_of=as_of,
            currency=report.operating_currency,
            places=0,
            title="Distribution of net zakatable wealth by account",
        )
        # The second asks the only question the thresholds are relevant to: was
        # net wealth above the nisab, and when. No bands: a stack front is a
        # gross figure, and comparing a gross figure to the nisab would be wrong.
        line_series = [
            ("wealth", "Net zakatable wealth", wealth),
            ("gold", "Gold nisab", gold_nisab),
            ("silver", "Silver nisab", silver_nisab),
        ]
        chart = build_chart(
            wealth=wealth,
            gold_nisab=gold_nisab,
            silver_nisab=silver_nisab,
            as_of=as_of,
            currency=report.operating_currency,
            places=0,
        )

        return {
            "error": None,
            "report": report,
            "nisab_history": _nisab_history(report),
            "detail": self._detail_sections(report, chart),
            "as_of": as_of,
            "as_of_hijri": format_hijri_date(as_of),
            "filter_active": self.filter_is_active(),
            "currency": report.operating_currency,
            "places": places,
            "stack": stack,
            "stack_data": chart_payload(
                series=stack_overlay,
                start=stack.start,
                end=stack.end,
                currency=report.operating_currency,
                places=0,
                stacks=stacks,
                title="Distribution of net zakatable wealth by account",
            ),
            "stack_paths": [(series, path_data(series)) for series in stack.series],
            "chart": chart,
            "chart_data": chart_payload(
                series=line_series,
                start=chart.start,
                end=chart.end,
                currency=report.operating_currency,
                places=0,
                title="Net zakatable wealth against the nisab thresholds",
            ),
            "chart_paths": [(series, path_data(series)) for series in chart.series],
            "bases": (report.gold, report.silver),
            "errors": [w for w in report.warnings if w.severity is Severity.ERROR],
            "warnings": [w for w in report.warnings if w.severity is Severity.WARNING],
            "notes": [w for w in report.warnings if w.severity is Severity.INFO],
            "constants": {
                "rate": format_rate(report.zakat_rate),
                "gold_grams": format_decimal(GOLD_NISAB_GRAMS, 2),
                "gold_tola": format_decimal(GOLD_NISAB_TOLA, 1),
                "silver_grams": format_decimal(SILVER_NISAB_GRAMS, 2),
                "silver_tola": format_decimal(SILVER_NISAB_TOLA, 1),
                "tola_grams": format_decimal(TOLA_GRAMS, 3),
                "lunar_year_days": format_decimal(HIJRI_YEAR_DAYS, 5),
            },
        }

    def _detail_sections(self, report: ZakatReport, overview_chart) -> list[dict]:
        """One self-contained bundle per basis: chart, hawl strip, table.

        Separate rather than merged, because a single chart cannot show which
        of the two thresholds caused a given reset, and the elapsed-time column
        means something different for each basis.
        """
        wealth = [(point.when, point.net) for point in report.wealth_series]
        sections = []
        for basis in (Basis.GOLD, Basis.SILVER):
            series = (
                report.gold_nisab_series
                if basis is Basis.GOLD
                else report.silver_nisab_series
            )
            nisab = [
                (point.when, point.nisab) for point in series if point.nisab is not None
            ]
            spans = below_nisab_spans(wealth, nisab, report.as_of)
            overlays = [
                ("wealth", "Net zakatable wealth", wealth),
                (basis.value, f"{basis.label} nisab", nisab),
            ]
            # One chart per basis, and no bands on it: this section is about a
            # threshold, and the composition of the wealth under it is the
            # Wealth & Nisab tab's business.
            chart = build_basis_chart(
                wealth=wealth,
                nisab=nisab,
                basis=basis.value,
                as_of=report.as_of,
                currency=report.operating_currency,
                places=0,
            )
            strip = build_hawl_strip(
                report.basis(basis).levels,
                start=chart.start,
                end=chart.end,
                currency=report.operating_currency,
                basis=basis.value,
                places=0,
            )
            sections.append(
                {
                    "basis": basis,
                    "result": report.basis(basis),
                    "rows": basis_period_rows(report, basis),
                    "chart": chart,
                    "chart_data": chart_payload(
                        series=overlays,
                        start=chart.start,
                        end=chart.end,
                        currency=report.operating_currency,
                        places=0,
                        bands=spans,
                        title=f"Net zakatable wealth against the {basis.value} nisab",
                    ),
                    "strip": strip,
                    "paths": [(line, path_data(line)) for line in chart.series],
                }
            )
        return sections

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def csv_url(self, filename: str | None = None) -> str:
        """URL of the CSV endpoint for this ledger.

        Built from the extension report URL rather than by calling
        ``url_for('extension_endpoint', endpoint=...)``: Flask's own first
        parameter is also called ``endpoint``, so passing the route's
        ``endpoint`` view argument by keyword raises TypeError.
        """
        from urllib.parse import urlencode

        from flask import url_for

        base = url_for("extension_report", extension_name=self.name)
        url = f"{base.rstrip('/')}/download_csv"
        if filename:
            url = f"{url}?{urlencode({'file': filename})}"
        return url

    # mypy cannot follow the decorator's overloads; the runtime contract is
    # checked by tests/test_fava_extension.py::TestCsvEndpoints.
    @extension_endpoint("download_csv")  # type: ignore[arg-type]
    def download_csv(self):  # type: ignore[no-untyped-def]
        """Serve the whole CSV set as a zip archive."""
        import io
        import zipfile

        from flask import Response, request

        from ..csv_export import render_all

        context = self.report()
        report = context.get("report")
        if report is None:
            return Response(context.get("error", "unavailable"), status=503)

        wanted = request.args.get("file")
        for name, content in render_all(report):
            if wanted and name == wanted:
                return Response(
                    content,
                    mimetype="text/csv",
                    headers={
                        "Content-Disposition": (f'attachment; filename="zakat_{name}"')
                    },
                )

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in render_all(report):
                archive.writestr(name, content)
        stamp = report.as_of.isoformat()
        return Response(
            buffer.getvalue(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": (f'attachment; filename="zakat_{stamp}.zip"')
            },
        )

    # ------------------------------------------------------------------
    # Formatting helpers exposed to the template
    # ------------------------------------------------------------------

    @staticmethod
    def money(value: Decimal | None, currency: str = "", places: int = 2) -> str:
        if value is None:
            return "n/a"
        return format_money(value, currency, places)

    @staticmethod
    def number(value: Decimal | None, places: int = 2) -> str:
        if value is None:
            return "n/a"
        return format_decimal(value, places)

    @staticmethod
    def balance(value: Decimal, currency: str, places: int = 2) -> str:
        return format_signed_balance(value, currency, places)

    @staticmethod
    def years(value: Decimal) -> str:
        return format_years(value)

    @staticmethod
    def nisab_span(span: NisabSpan, currency: str = "", places: int = 2) -> str:
        """Render a nisab that may have moved during the row's span."""
        if span.low is None:
            return "unavailable"
        low = format_decimal(span.low, places)
        if not span.varies or span.high is None:
            return f"{low} {currency}".strip()
        high = format_decimal(span.high, places)
        return f"{low} \u2013 {high} {currency}".strip()

    @staticmethod
    def hijri_year(when: date) -> int:
        return hijri_year_of(when)


def _nisab_history(report: ZakatReport) -> list[dict[str, Any]]:
    """One row per date on which either nisab changed.

    The nisab is a moving threshold, so a single figure on a summary card would
    misrepresent every historical period. This is the authoritative view: which
    price was in force, when it was quoted, and what threshold it implied.
    """
    rows: list[dict[str, Any]] = []
    previous: tuple[Decimal | None, Decimal | None] = (None, None)
    for gold, silver in zip(
        report.gold_nisab_series, report.silver_nisab_series, strict=False
    ):
        current = (gold.nisab, silver.nisab)
        if current == previous:
            continue
        previous = current
        rows.append(
            {
                "when": gold.when,
                "gold": gold,
                "silver": silver,
                "gold_stale": bool(gold.stale_days and gold.stale_days > 90),
                "silver_stale": bool(silver.stale_days and silver.stale_days > 90),
            }
        )
    return rows
