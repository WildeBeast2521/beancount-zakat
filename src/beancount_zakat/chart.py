"""Server-side chart geometry for a time-proportional line chart.

The chart is emitted as inline SVG rather than handed to a JavaScript charting
library. That means no third-party runtime dependency, so it works offline and
under a strict content-security policy; it is readable by screen readers through
``<title>``/``<desc>``; it needs no animation; and the x axis is a real time
scale, so a one-day gap and a three-year gap do not look alike.

``float`` appears here and only here: SVG coordinates are geometry, never
money.  Every label is still formatted from the original ``Decimal``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from .formatting import format_decimal


@dataclass(frozen=True, slots=True)
class Series:
    """One plotted line."""

    key: str
    label: str
    points: tuple[tuple[float, float], ...]
    #: ``stepped`` lines hold their value until the next point, which is how
    #: an account balance actually behaves.
    stepped: bool = True
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class StackBand:
    """One account's contribution to net wealth, as a filled band.

    Assets stack upwards from zero and liabilities downwards, so the height of
    the stack at any date *is* net zakatable wealth --- the same figure the
    engine computed, drawn rather than recomputed.
    """

    key: str
    account: str
    label: str
    #: ``"asset"`` or ``"liability"``.
    role: str
    #: Palette slot, so the same account keeps its colour across both charts.
    index: int
    #: SVG path data for the filled band.
    area: str


@dataclass(frozen=True, slots=True)
class AxisTick:
    position: float
    label: str


@dataclass(frozen=True, slots=True)
class ChartModel:
    """Everything the template needs to draw the chart."""

    width: int
    height: int
    pad_left: int
    pad_right: int
    pad_top: int
    pad_bottom: int
    series: tuple[Series, ...]
    x_ticks: tuple[AxisTick, ...]
    y_ticks: tuple[AxisTick, ...]
    start: date
    end: date
    y_max: Decimal
    description: str
    #: ``(x, iso-date, label-lines)`` for the hover readout.
    hover: tuple[tuple[float, str, tuple[str, ...]], ...] = field(default=())
    #: ``(x1, x2)`` spans during which wealth sat below the nisab, so the hawl
    #: was reset. Shaded on a basis chart to show *why* a slice earned nothing.
    reset_bands: tuple[tuple[float, float], ...] = field(default=())
    #: Stacked per-account bands drawn *behind* the lines. Presentation only:
    #: nothing here is read back by the engine, and the bands are derived from
    #: the same wealth timeline the calculation already used.
    stack: tuple[StackBand, ...] = field(default=())
    #: y of the zero line. Meaningful when liabilities push the axis negative.
    zero_y: float = 0.0
    #: Axis minimum. Zero unless a stacked chart drew liabilities below it.
    y_min: Decimal = field(default_factory=lambda: Decimal("0"))

    @property
    def plot_width(self) -> int:
        return self.width - self.pad_left - self.pad_right

    @property
    def plot_height(self) -> int:
        return self.height - self.pad_top - self.pad_bottom

    @property
    def is_empty(self) -> bool:
        return not self.series


def _nice_ceiling(value: Decimal) -> Decimal:
    """Round *value* up to a readable axis maximum."""
    if value <= 0:
        return Decimal("1")
    digits = len(format(int(value), "d"))
    magnitude = Decimal(10) ** (digits - 1)
    for step in (
        Decimal("1"),
        Decimal("1.25"),
        Decimal("1.5"),
        Decimal("2"),
        Decimal("2.5"),
        Decimal("3"),
        Decimal("4"),
        Decimal("5"),
        Decimal("6"),
        Decimal("8"),
        Decimal("10"),
    ):
        candidate = magnitude * step
        if candidate >= value:
            return candidate
    return magnitude * 10


def _year_ticks(start: date, end: date, span_days: int) -> list[date]:
    """Pick readable x-axis gridlines: Januarys, thinned out on long spans."""
    years = list(range(start.year, end.year + 1))
    stride = max(1, (len(years) + 7) // 8)
    ticks = [date(year, 1, 1) for year in years[::stride]]
    return [tick for tick in ticks if start <= tick <= end] or [start, end]


def build_chart(
    *,
    wealth: Sequence[tuple[date, Decimal]],
    gold_nisab: Sequence[tuple[date, Decimal]],
    silver_nisab: Sequence[tuple[date, Decimal]],
    as_of: date,
    currency: str,
    width: int = 960,
    height: int = 360,
    places: int = 0,
) -> ChartModel:
    """Project the wealth and nisab series onto a time-proportional canvas."""
    pad_left, pad_right, pad_top, pad_bottom = 76, 18, 16, 34

    if not wealth:
        return ChartModel(
            width,
            height,
            pad_left,
            pad_right,
            pad_top,
            pad_bottom,
            (),
            (),
            (),
            as_of,
            as_of,
            Decimal("0"),
            "No zakatable wealth has been recorded yet.",
        )

    start = min(point[0] for point in wealth)
    end = max(as_of, max(point[0] for point in wealth))
    span_days = max(1, (end - start).days)

    highest = max(
        [value for _, value in wealth]
        + [value for _, value in gold_nisab]
        + [value for _, value in silver_nisab]
        or [Decimal("1")]
    )
    lowest = min([value for _, value in wealth] or [Decimal("0")])
    y_max = _nice_ceiling(max(highest, Decimal("1")))
    y_min = min(Decimal("0"), lowest)
    y_range = y_max - y_min or Decimal("1")

    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    def x_of(when: date) -> float:
        return pad_left + plot_width * ((when - start).days / span_days)

    def y_of(value: Decimal) -> float:
        fraction = float((value - y_min) / y_range)
        return pad_top + plot_height * (1.0 - fraction)

    def project(
        data: Sequence[tuple[date, Decimal]],
    ) -> tuple[tuple[float, float], ...]:
        projected = [(x_of(when), y_of(value)) for when, value in data]
        if projected and data[-1][0] < end:
            projected.append((x_of(end), projected[-1][1]))
        return tuple(projected)

    series: list[Series] = [
        Series("wealth", "Net zakatable wealth", project(wealth)),
    ]
    if gold_nisab:
        series.append(Series("gold", "Gold nisab", project(gold_nisab), dashed=True))
    if silver_nisab:
        series.append(
            Series("silver", "Silver nisab", project(silver_nisab), dashed=True)
        )

    x_ticks = tuple(
        AxisTick(x_of(tick), str(tick.year))
        for tick in _year_ticks(start, end, span_days)
    )
    y_ticks = tuple(
        AxisTick(
            y_of(y_min + (y_range * Decimal(step) / Decimal(4))),
            format_decimal(y_min + (y_range * Decimal(step) / Decimal(4)), places),
        )
        for step in range(5)
    )

    wealth_map = dict(wealth)
    gold_map = dict(gold_nisab)
    silver_map = dict(silver_nisab)
    hover: list[tuple[float, str, tuple[str, ...]]] = []
    for when in sorted(wealth_map):
        lines = [f"Wealth {format_decimal(wealth_map[when], places)} {currency}"]
        if when in gold_map:
            lines.append(
                f"Gold nisab {format_decimal(gold_map[when], places)} {currency}"
            )
        if when in silver_map:
            lines.append(
                f"Silver nisab {format_decimal(silver_map[when], places)} {currency}"
            )
        hover.append((x_of(when), when.isoformat(), tuple(lines)))

    description = (
        f"Net zakatable wealth from {start.isoformat()} to {end.isoformat()}, "
        f"in {currency}, plotted against the gold and silver nisab thresholds. "
        f"The horizontal axis is proportional to elapsed time. "
        f"Wealth ends at {format_decimal(wealth[-1][1], places)} {currency}."
    )

    return ChartModel(
        width,
        height,
        pad_left,
        pad_right,
        pad_top,
        pad_bottom,
        tuple(series),
        x_ticks,
        y_ticks,
        start,
        end,
        y_max,
        description,
        tuple(hover),
    )


def path_data(series: Series) -> str:
    """SVG path ``d`` attribute for a series."""
    if not series.points:
        return ""
    commands = [f"M {series.points[0][0]:.2f} {series.points[0][1]:.2f}"]
    previous_y = series.points[0][1]
    for x, y in series.points[1:]:
        if series.stepped:
            commands.append(f"L {x:.2f} {previous_y:.2f}")
        commands.append(f"L {x:.2f} {y:.2f}")
        previous_y = y
    return " ".join(commands)


# ---------------------------------------------------------------------------
# Per-basis chart and hawl strip
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StripSegment:
    """One stretch of one marginal slice, on the hawl strip."""

    x1: float
    x2: float
    status: str
    label: str


@dataclass(frozen=True, slots=True)
class StripRow:
    """One marginal slice's band on the hawl strip."""

    level: Decimal
    label: str
    y: float
    height: float
    segments: tuple[StripSegment, ...]


@dataclass(frozen=True, slots=True)
class HawlStrip:
    """A Gantt-style view of every slice's hawl, on a shared time axis.

    Answers "why did this slice earn nothing?" at a glance: the band turns grey
    exactly where wealth fell below the nisab, lining up with the shaded reset
    span on the chart above it.
    """

    width: int
    height: int
    pad_left: int
    pad_right: int
    pad_top: int
    pad_bottom: int
    rows: tuple[StripRow, ...]
    x_ticks: tuple[AxisTick, ...]
    start: date
    end: date
    description: str
    truncated: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.rows


def build_basis_chart(
    *,
    wealth: Sequence[tuple[date, Decimal]],
    nisab: Sequence[tuple[date, Decimal]],
    basis: str,
    as_of: date,
    currency: str,
    width: int = 960,
    height: int = 300,
    places: int = 0,
) -> ChartModel:
    """Wealth against **one** nisab, with hawl resets shaded.

    A single threshold tells the story of a single basis; the two-threshold
    chart cannot show which one caused a given reset.
    """
    model = build_chart(
        wealth=wealth,
        gold_nisab=nisab if basis == "gold" else [],
        silver_nisab=nisab if basis == "silver" else [],
        as_of=as_of,
        currency=currency,
        width=width,
        height=height,
        places=places,
    )
    if model.is_empty or not nisab:
        return model

    start = model.start
    span_days = max(1, (model.end - start).days)
    plot_width = model.width - model.pad_left - model.pad_right

    def x_of(when: date) -> float:
        return model.pad_left + plot_width * ((when - start).days / span_days)

    # Walk the wealth series against the carried-forward nisab and record the
    # stretches spent below it.
    thresholds = list(nisab)
    bands: list[tuple[float, float]] = []
    open_from: date | None = None
    current = None
    index = 0
    for when, value in [*wealth, (model.end, wealth[-1][1])]:
        while index < len(thresholds) and thresholds[index][0] <= when:
            current = thresholds[index][1]
            index += 1
        below = current is None or value < current
        if below and open_from is None:
            open_from = when
        elif not below and open_from is not None:
            bands.append((x_of(open_from), x_of(when)))
            open_from = None
    if open_from is not None:
        bands.append((x_of(open_from), x_of(model.end)))

    if bands:
        spans = "; ".join(
            f"{a.isoformat()} to {b.isoformat()}"
            for a, b in _band_dates(wealth, thresholds, model.end)[:4]
        )
        reset_note = (
            f" Shaded: {len(bands)} stretch(es) spent below the {basis} nisab, "
            f"each resetting the hawl ({spans})."
        )
    else:
        reset_note = (
            f" Wealth never fell below the {basis} nisab, so no hawl was reset."
        )
    description = model.description + reset_note
    return replace(model, reset_bands=tuple(bands), description=description)


def _band_dates(wealth, thresholds, end: date) -> list[tuple[date, date]]:
    """The below-nisab stretches as dates, for the chart's text alternative."""
    spans: list[tuple[date, date]] = []
    open_from: date | None = None
    current = None
    index = 0
    for when, value in [*wealth, (end, wealth[-1][1])]:
        while index < len(thresholds) and thresholds[index][0] <= when:
            current = thresholds[index][1]
            index += 1
        below = current is None or value < current
        if below and open_from is None:
            open_from = when
        elif not below and open_from is not None:
            spans.append((open_from, when))
            open_from = None
    if open_from is not None:
        spans.append((open_from, end))
    return spans


#: Never draw more bands than this; the tallest ledgers have hundreds of levels.
MAX_STRIP_ROWS = 24


def build_hawl_strip(
    levels: Sequence,
    *,
    start: date,
    end: date,
    currency: str,
    basis: str,
    width: int = 960,
    row_height: int = 16,
    places: int = 0,
) -> HawlStrip:
    """Lay every marginal slice's periods onto a shared time axis."""
    pad_left, pad_right, pad_top, pad_bottom = 118, 18, 10, 26
    shown = [level for level in levels if level.periods]
    truncated = max(0, len(shown) - MAX_STRIP_ROWS)
    if truncated:
        # Keep the largest slices: they carry the most zakat.
        shown = sorted(shown, key=lambda level: level.marginal, reverse=True)[
            :MAX_STRIP_ROWS
        ]
        shown = sorted(shown, key=lambda level: level.level)

    height = pad_top + pad_bottom + max(1, len(shown)) * (row_height + 4)
    plot_width = width - pad_left - pad_right
    span_days = max(1, (end - start).days)

    def x_of(when: date) -> float:
        clamped = min(max(when, start), end)
        return pad_left + plot_width * ((clamped - start).days / span_days)

    rows: list[StripRow] = []
    for index, level in enumerate(shown):
        segments = tuple(
            StripSegment(
                x1=x_of(period.start),
                x2=max(x_of(period.end) + 1.0, x_of(period.start) + 1.5),
                status=period.hawl.value.replace(" ", "-"),
                label=(
                    f"{period.start.isoformat()} to {period.end.isoformat()}: "
                    f"{period.hawl.label}"
                    + (
                        f", {format_decimal(period.zakat_due, 2)} {currency}"
                        if period.zakat_due
                        else ""
                    )
                ),
            )
            for period in level.periods
        )
        rows.append(
            StripRow(
                level=level.level,
                label=format_decimal(level.marginal, places),
                y=pad_top + index * (row_height + 4),
                height=row_height,
                segments=segments,
            )
        )

    ticks = tuple(
        AxisTick(x_of(tick), str(tick.year))
        for tick in _year_ticks(start, end, span_days)
    )
    complete = sum(
        1 for row in rows for seg in row.segments if seg.status == "complete"
    )
    reset = sum(
        1 for row in rows for seg in row.segments if seg.status == "not-running"
    )
    description = (
        f"Hawl timeline for the {basis} basis: {len(rows)} marginal slices "
        f"from {start.isoformat()} to {end.isoformat()}. "
        f"{complete} periods completed a hawl and {reset} were reset by wealth "
        f"falling below the {basis} nisab."
    )
    return HawlStrip(
        width=width,
        height=height,
        pad_left=pad_left,
        pad_right=pad_right,
        pad_top=pad_top,
        pad_bottom=pad_bottom,
        rows=tuple(rows),
        x_ticks=ticks,
        start=start,
        end=end,
        description=description,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Stacked composition chart
#
# Presentation only. The engine works from net wealth and never sees a band;
# this section only re-expresses the per-account balances the timeline already
# carries so that a reader can see *what* the net figure is made of. Assets
# stack up from zero, liabilities stack down from it, and the distance between
# the two stack fronts is the net wealth line drawn on top of them.
# ---------------------------------------------------------------------------


#: Beyond this many accounts on one side, the smallest are pooled into a single
#: "Other" band. A stack of forty accounts is a colour wheel, not a chart; the
#: per-account tables remain complete either way.
MAX_STACK_BANDS = 8


@dataclass(frozen=True, slots=True)
class AccountSeries:
    """One account's balance through time, ready to be stacked."""

    key: str
    account: str
    label: str
    role: str
    index: int
    points: tuple[tuple[date, Decimal], ...]

    @property
    def peak(self) -> Decimal:
        return max((abs(value) for _, value in self.points), default=Decimal("0"))


def _slug(text: str) -> str:
    """A DOM/CSS-safe token for an account name."""
    out = [character.lower() if character.isalnum() else "-" for character in text]
    return "".join(out).strip("-") or "account"


def composition_series(
    composition: Sequence[tuple[date, dict[str, Decimal]]],
    *,
    liability_accounts: Sequence[str] = (),
    limit: int = MAX_STACK_BANDS,
) -> tuple[AccountSeries, ...]:
    """Per-account balance series, ordered for stacking.

    Every series covers *every* date in ``composition``, carrying zero where an
    account had no balance yet, so the bands can be stacked index-by-index
    without any date alignment at draw time. Largest holdings come first, so the
    biggest band sits nearest the zero line and the small ones ride on top of a
    stable base instead of jittering along the axis.
    """
    if not composition:
        return ()

    dates = [when for when, _ in composition]
    debts = set(liability_accounts)
    names = sorted({account for _, values in composition for account in values})

    raw: list[tuple[str, str, list[tuple[date, Decimal]]]] = []
    for account in names:
        points = [
            (when, values.get(account, Decimal("0"))) for when, values in composition
        ]
        if all(value == 0 for _, value in points):
            continue
        raw.append((account, "liability" if account in debts else "asset", points))

    ordered: list[AccountSeries] = []
    index = 0
    for role in ("asset", "liability"):
        side = [entry for entry in raw if entry[1] == role]
        side.sort(
            key=lambda entry: max(
                (abs(value) for _, value in entry[2]), default=Decimal("0")
            ),
            reverse=True,
        )
        kept, pooled = side[:limit], side[limit:]
        for account, _, points in kept:
            ordered.append(
                AccountSeries(
                    key=f"acct-{_slug(account)}",
                    account=account,
                    label=account,
                    role=role,
                    index=index,
                    points=tuple(points),
                )
            )
            index += 1
        if pooled:
            totals = [
                (
                    when,
                    sum(
                        (entry[2][position][1] for entry in pooled), start=Decimal("0")
                    ),
                )
                for position, when in enumerate(dates)
            ]
            label = f"Other {role}s ({len(pooled)} accounts)"
            ordered.append(
                AccountSeries(
                    key=f"acct-other-{role}",
                    account=label,
                    label=label,
                    role=role,
                    index=index,
                    points=tuple(totals),
                )
            )
            index += 1
    return tuple(ordered)


def _stack_path(
    xs: Sequence[float],
    lows: Sequence[float],
    highs: Sequence[float],
    x_end: float,
) -> str:
    """A stepped band: forward along the top, back along the bottom, closed.

    ``xs[i]`` is where the ``i``-th value comes into force; it holds until
    ``xs[i + 1]`` --- or until ``x_end`` for the last one --- because a balance
    is a step function, not a ramp.
    """
    if not xs:
        return ""
    parts = [f"M {xs[0]:.2f} {highs[0]:.2f}"]
    for i in range(1, len(xs)):
        parts.append(f"L {xs[i]:.2f} {highs[i - 1]:.2f}")
        parts.append(f"L {xs[i]:.2f} {highs[i]:.2f}")
    parts.append(f"L {x_end:.2f} {highs[-1]:.2f}")
    parts.append(f"L {x_end:.2f} {lows[-1]:.2f}")
    for i in range(len(xs) - 1, 0, -1):
        parts.append(f"L {xs[i]:.2f} {lows[i]:.2f}")
        parts.append(f"L {xs[i]:.2f} {lows[i - 1]:.2f}")
    parts.append(f"L {xs[0]:.2f} {lows[0]:.2f} Z")
    return " ".join(parts)


def _nice_floor(value: Decimal) -> Decimal:
    """Round *value* down to a readable axis minimum; zero when non-negative."""
    if value >= 0:
        return Decimal("0")
    return -_nice_ceiling(-value)


def build_stacked_chart(
    *,
    composition: Sequence[tuple[date, dict[str, Decimal]]],
    liability_accounts: Sequence[str] = (),
    overlays: Sequence[tuple[str, str, Sequence[tuple[date, Decimal]]]] = (),
    as_of: date,
    currency: str,
    width: int = 960,
    height: int = 400,
    places: int = 0,
    reset_spans: Sequence[tuple[date, date]] = (),
    title: str = "",
) -> ChartModel:
    """Net wealth as a stack of its accounts, with threshold lines on top.

    *overlays* are ordinary stepped lines --- net wealth and the nisab
    thresholds --- drawn over the bands. They are passed in rather than derived
    so that this function never has an opinion about what the numbers mean.
    """
    pad_left, pad_right, pad_top, pad_bottom = 86, 18, 16, 34
    accounts = composition_series(composition, liability_accounts=liability_accounts)
    if not composition or not accounts:
        return ChartModel(
            width,
            height,
            pad_left,
            pad_right,
            pad_top,
            pad_bottom,
            (),
            (),
            (),
            as_of,
            as_of,
            Decimal("0"),
            "No zakatable wealth has been recorded yet.",
        )

    dates = [when for when, _ in composition]
    start = dates[0]
    end = max(as_of, dates[-1])
    span_days = max(1, (end - start).days)

    count = len(dates)
    asset_bands = [a for a in accounts if a.role == "asset"]
    liability_bands = [a for a in accounts if a.role == "liability"]

    # Stack by *sign*, not by role. Liabilities are always negative, so they
    # land below the axis either way; but an overdrawn asset is negative too,
    # and stacking it upwards would paint it back over whatever sits beneath
    # it. Splitting on the sign means no band ever overlaps another, and the
    # two fronts still meet at the net figure.
    rising = [Decimal("0")] * count
    falling = [Decimal("0")] * count
    bounds: list[tuple[list[Decimal], list[Decimal]]] = []
    for account in accounts:
        lows: list[Decimal] = []
        highs: list[Decimal] = []
        for i in range(count):
            value = account.points[i][1]
            if value == 0:
                # A dormant account rests on the axis rather than riding on
                # top of the stack as an invisible sliver in the wrong place.
                lows.append(Decimal("0"))
                highs.append(Decimal("0"))
                continue
            base = falling if value < 0 else rising
            bottom = base[i]
            top = bottom + value
            base[i] = top
            lows.append(min(bottom, top))
            highs.append(max(bottom, top))
        bounds.append((lows, highs))

    overlay_values = [value for _, _, points in overlays for _, value in points]
    y_max = _nice_ceiling(max([*rising, *overlay_values, Decimal("1")]))
    y_min = _nice_floor(min([*falling, *overlay_values, Decimal("0")]))
    y_range = y_max - y_min or Decimal("1")

    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom

    def x_of(when: date) -> float:
        clamped = min(max(when, start), end)
        return pad_left + plot_width * ((clamped - start).days / span_days)

    def y_of(value: Decimal) -> float:
        fraction = float((value - y_min) / y_range)
        return pad_top + plot_height * (1.0 - fraction)

    xs = [x_of(when) for when in dates]
    x_end = x_of(end)

    stack = [
        StackBand(
            key=account.key,
            account=account.account,
            label=account.label,
            role=account.role,
            index=account.index,
            area=_stack_path(
                xs, [y_of(v) for v in lows], [y_of(v) for v in highs], x_end
            ),
        )
        for account, (lows, highs) in zip(accounts, bounds, strict=True)
    ]

    def project(
        data: Sequence[tuple[date, Decimal]],
    ) -> tuple[tuple[float, float], ...]:
        projected = [(x_of(when), y_of(value)) for when, value in data]
        if projected and data[-1][0] < end:
            projected.append((x_end, projected[-1][1]))
        return tuple(projected)

    series = tuple(
        Series(key, label, project(points), dashed=key != "wealth")
        for key, label, points in overlays
        if points
    )

    x_ticks = tuple(
        AxisTick(x_of(tick), str(tick.year))
        for tick in _year_ticks(start, end, span_days)
    )
    y_ticks = tuple(
        AxisTick(
            y_of(y_min + (y_range * Decimal(step) / Decimal(4))),
            format_decimal(y_min + (y_range * Decimal(step) / Decimal(4)), places),
        )
        for step in range(5)
    )

    largest = ", ".join(
        f"{band.account} peaking at {format_decimal(band.peak, places)} {currency}"
        for band in accounts[:3]
    )
    description = (
        f"{title or 'Account composition of net zakatable wealth'} from "
        f"{start.isoformat()} to {end.isoformat()}, in {currency}. "
        f"{len(asset_bands)} asset band(s) and {len(liability_bands)} liability "
        f"band(s); anything owed sits below the zero line and anything held "
        f"above it, so the two stack fronts meet at net zakatable wealth. "
        f"Largest contributors: {largest}. "
        f"The horizontal axis is proportional to elapsed time and every band is "
        f"stepped, because a balance holds until something changes it."
    )
    if reset_spans:
        description += (
            f" Shaded: {len(reset_spans)} stretch(es) spent below the nisab, "
            "each resetting the hawl."
        )

    return ChartModel(
        width,
        height,
        pad_left,
        pad_right,
        pad_top,
        pad_bottom,
        series,
        x_ticks,
        y_ticks,
        start,
        end,
        y_max,
        description,
        (),
        tuple((x_of(a), x_of(b)) for a, b in reset_spans),
        tuple(stack),
        y_of(Decimal("0")),
        y_min,
    )


# ---------------------------------------------------------------------------
# Client payload
# ---------------------------------------------------------------------------


def below_nisab_spans(
    wealth: Sequence[tuple[date, Decimal]],
    nisab: Sequence[tuple[date, Decimal]],
    end: date,
) -> list[tuple[date, date]]:
    """Inclusive-start, exclusive-end stretches spent below the nisab."""
    if not wealth:
        return []
    return _band_dates(wealth, list(nisab), end)


def chart_payload(
    *,
    series: Sequence[tuple[str, str, Sequence[tuple[date, Decimal]]]],
    start: date,
    end: date,
    currency: str,
    places: int = 0,
    bands: Sequence[tuple[date, date]] = (),
    stacks: Sequence[AccountSeries] = (),
    title: str = "",
) -> dict:
    """A JSON-serialisable description of a chart, for the browser to draw.

    The server still renders a complete static SVG; this payload is what the
    progressive enhancement re-draws from when the reader toggles a series or
    narrows the date window. Amounts stay as decimal *strings*: the browser
    needs them for geometry and for a readout, never for arithmetic that
    anything else depends on.

    ``stacks`` carries the per-account bands unstacked --- each account's own
    balance, not a running total. The browser adds them up itself, which is what
    lets a reader switch one account off and see the rest re-stack instead of
    leaving a hole in the middle of the chart.
    """
    return {
        "title": title,
        "currency": currency,
        "places": places,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bands": [[a.isoformat(), b.isoformat()] for a, b in bands],
        "stacks": [
            {
                "key": account.key,
                "label": account.label,
                "role": account.role,
                "index": account.index,
                "points": [
                    [when.isoformat(), str(value)] for when, value in account.points
                ],
            }
            for account in stacks
        ],
        "series": [
            {
                "key": key,
                "label": label,
                "points": [[when.isoformat(), str(value)] for when, value in points],
            }
            for key, label, points in series
            if points
        ],
    }
