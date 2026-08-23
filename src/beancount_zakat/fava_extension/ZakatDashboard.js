// @ts-check
//
// Behaviour for the Zakat Dashboard.
//
// Loaded by Fava through `has_js_module = True` and served from
// /<bfile>/extension_js_module/ZakatDashboard.js. Fava injects the report into
// the page via innerHTML, so ordinary inline <script> would never run; the
// `onExtensionPageLoad` lifecycle hook below is the documented way in.
//
// Everything below is progressive enhancement: with JavaScript disabled the
// first panel is visible and every other panel is still in the DOM.

/** Tab keys, in DOM order. The last one is always "about". */
const STORAGE_PREFIX = "bz-tab:";

/**
 * Wire up one tablist: roving tabindex, arrow-key navigation, and the URL
 * hash so a tab can be linked to and survives a reload.
 * @param {HTMLElement} root
 */
function initTabs(root) {
  const tablist = root.querySelector('[role="tablist"]');
  if (!(tablist instanceof HTMLElement)) return;

  /** @type {HTMLButtonElement[]} */
  const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
  if (tabs.length === 0) return;

  /** @param {string} key */
  const panelFor = (key) => root.querySelector(`[data-bz-panel="${key}"]`);

  /**
   * @param {string} key
   * @param {{focus?: boolean, updateHash?: boolean}} [opts]
   */
  function select(key, opts = {}) {
    const { focus = false, updateHash = true } = opts;
    let matched = false;
    tabs.forEach((tab) => {
      const isTarget = tab.dataset.bzTab === key;
      matched = matched || isTarget;
      tab.setAttribute("aria-selected", isTarget ? "true" : "false");
      tab.tabIndex = isTarget ? 0 : -1;
      const panel = panelFor(tab.dataset.bzTab ?? "");
      if (panel instanceof HTMLElement) panel.hidden = !isTarget;
    });
    if (!matched) return false;
    if (focus) {
      const active = tabs.find((tab) => tab.dataset.bzTab === key);
      active?.focus();
    }
    if (updateHash) {
      try {
        const url = new URL(window.location.href);
        url.hash = `zakat-${key}`;
        window.history.replaceState(null, "", url);
        window.sessionStorage?.setItem(
          STORAGE_PREFIX + window.location.pathname,
          key,
        );
      } catch {
        /* hash and storage are conveniences; never break the page for them */
      }
    }
    return true;
  }

  tablist.addEventListener("click", (event) => {
    const tab = /** @type {HTMLElement} */ (event.target)?.closest?.(
      '[role="tab"]',
    );
    if (tab instanceof HTMLElement && tab.dataset.bzTab) {
      select(tab.dataset.bzTab, { focus: true });
    }
  });

  tablist.addEventListener("keydown", (event) => {
    const current = tabs.findIndex(
      (tab) => tab.getAttribute("aria-selected") === "true",
    );
    if (current < 0) return;
    let next = -1;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        next = (current + 1) % tabs.length;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = (current - 1 + tabs.length) % tabs.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const key = tabs[next]?.dataset.bzTab;
    if (key) select(key, { focus: true });
  });

  /** The tab named by the current URL hash, if any. */
  const tabFromHash = () =>
    window.location.hash.startsWith("#zakat-")
      ? window.location.hash.slice("#zakat-".length)
      : "";

  // Browser back/forward between tabs changes the hash without reloading the
  // document, so onExtensionPageLoad does not fire again. Listen for it.
  // Guarded so repeated SPA navigations do not stack listeners.
  if (!window.__bzHashListener) {
    window.__bzHashListener = true;
    window.addEventListener("hashchange", () => {
      const root = document.querySelector("[data-zakat-dashboard]");
      if (!root) return;
      const key = tabFromHash();
      if (key) selectIn(root, key);
    });
  }

  // Restore from the URL hash first, then from this session, else leave the
  // server-rendered default (the first tab) alone.
  const fromHash = tabFromHash();
  let restored = fromHash ? select(fromHash, { updateHash: false }) : false;
  if (!restored) {
    try {
      const stored = window.sessionStorage?.getItem(
        STORAGE_PREFIX + window.location.pathname,
      );
      if (stored) restored = select(stored, { updateHash: false });
    } catch {
      /* storage may be unavailable; the default tab is already correct */
    }
  }
}

/**
 * Select a tab from outside `initTabs`, used by the hashchange listener.
 * Deliberately DOM-driven rather than closing over `initTabs` state, so it
 * still works after Fava replaces the page content.
 * @param {Element} root
 * @param {string} key
 */
function selectIn(root, key) {
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  if (!tabs.some((tab) => tab.getAttribute("data-bz-tab") === key)) return;
  tabs.forEach((tab) => {
    const isTarget = tab.getAttribute("data-bz-tab") === key;
    tab.setAttribute("aria-selected", isTarget ? "true" : "false");
    if (tab instanceof HTMLElement) tab.tabIndex = isTarget ? 0 : -1;
    const panel = root.querySelector(
      `[data-bz-panel="${tab.getAttribute("data-bz-tab")}"]`,
    );
    if (panel instanceof HTMLElement) panel.hidden = !isTarget;
  });
}

/**
 * The gold/silver switch on the Calculation Detail tab.
 *
 * Each basis carries its own chart, hawl strip and table, so the switch moves
 * a whole self-contained section rather than just a table.
 * @param {HTMLElement} root
 */
function initBasisSwitch(root) {
  const buttons = Array.from(root.querySelectorAll("[data-bz-basis]")).filter(
    (el) => el instanceof HTMLButtonElement,
  );
  if (buttons.length === 0) return;

  /** @param {string} basis */
  function show(basis) {
    buttons.forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        button.dataset.bzBasis === basis ? "true" : "false",
      );
    });
    root.querySelectorAll("[data-bz-basis-panel]").forEach((panel) => {
      if (panel instanceof HTMLElement) {
        panel.hidden = panel.dataset.bzBasisPanel !== basis;
      }
    });
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.bzBasis) show(button.dataset.bzBasis);
    });
  });
}

/**
 * Click-to-sort on tables marked `data-bz-sortable`. Numeric columns sort
 * numerically; everything else sorts as text. Sort state is announced through
 * `aria-sort`, and `tfoot` totals never move.
 * @param {HTMLElement} root
 */
function initSortableTables(root) {
  root.querySelectorAll("table[data-bz-sortable]").forEach((table) => {
    if (!(table instanceof HTMLTableElement)) return;
    const headers = Array.from(table.tHead?.rows[0]?.cells ?? []);
    headers.forEach((header, index) => {
      header.tabIndex = 0;
      header.setAttribute("role", "columnheader");
      header.setAttribute("aria-sort", "none");
      header.style.cursor = "pointer";
      header.title = "Sort by this column";

      const sort = () => {
        const body = table.tBodies[0];
        if (!body) return;
        const ascending = header.getAttribute("aria-sort") !== "ascending";
        const numeric = header.classList.contains("num");
        const rows = Array.from(body.rows).filter((row) => row.cells.length > 1);
        rows.sort((a, b) => {
          const left = a.cells[index]?.textContent?.trim() ?? "";
          const right = b.cells[index]?.textContent?.trim() ?? "";
          if (numeric) {
            const lv = Number.parseFloat(left.replace(/[^0-9.-]/g, "")) || 0;
            const rv = Number.parseFloat(right.replace(/[^0-9.-]/g, "")) || 0;
            return ascending ? lv - rv : rv - lv;
          }
          return ascending
            ? left.localeCompare(right)
            : right.localeCompare(left);
        });
        rows.forEach((row) => body.appendChild(row));
        headers.forEach((other) => other.setAttribute("aria-sort", "none"));
        header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
      };

      header.addEventListener("click", sort);
      header.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sort();
        }
      });
    });
  });
}

/* ==========================================================================
 * Interactive charts
 *
 * The server renders a complete, static SVG for every chart, so the page is
 * readable with JavaScript switched off. When this module runs it takes the
 * same data over as JSON and re-draws the chart in the browser, which buys
 * three things the static image cannot give: series you can switch on and off,
 * a date window you can narrow, and a readout of every visible series at
 * whatever date the pointer (or a finger) is on.
 *
 * Drawing is done at the container's real pixel size rather than by scaling a
 * fixed viewBox, so labels stay the same size on a phone and on a 4K display
 * and the plot uses all the width that is going.
 * ========================================================================== */

const SVG_NS = "http://www.w3.org/2000/svg";

/** CSS custom property carrying each series' colour. */
const SERIES_COLOUR = {
  wealth: "var(--bz-accent)",
  gold: "var(--bz-gold)",
  silver: "var(--bz-silver)",
};

const DAY_MS = 86400000;

/**
 * @param {string} name
 * @param {Record<string, string|number>} [attrs]
 * @param {string} [text]
 */
function svg(name, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, String(value));
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * @param {string} name
 * @param {Record<string, string>} [attrs]
 * @param {string} [text]
 */
function html(name, attrs = {}, text) {
  const node = document.createElement(name);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Parse an ISO date as a UTC day number, so no time zone can shift it. */
function dayOf(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / DAY_MS;
}

/** Inverse of {@link dayOf}. */
function isoOf(day) {
  return new Date(Math.round(day) * DAY_MS).toISOString().slice(0, 10);
}

/** Round up to a readable axis maximum. Mirrors the server's `_nice_ceiling`. */
function niceCeiling(value) {
  if (!(value > 0)) return 1;
  const magnitude = 10 ** (Math.floor(Math.log10(value)));
  for (const step of [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (magnitude * step >= value) return magnitude * step;
  }
  return magnitude * 10;
}

/**
 * The value of a stepped series on day *t*: the last point at or before it.
 * Returns null before the series starts.
 * @param {{d: number, v: number}[]} points
 * @param {number} t
 */
function valueAt(points, t) {
  let low = 0;
  let high = points.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (points[mid].d <= t) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found < 0 ? null : points[found].v;
}

/**
 * Readable x-axis ticks for a span, aiming for six to nine labels.
 * @param {number} from day number
 * @param {number} to day number
 */
function timeTicks(from, to) {
  const span = Math.max(1, to - from);
  const out = [];
  const push = (dayNumber, label) => {
    if (dayNumber >= from && dayNumber <= to) out.push({ d: dayNumber, label });
  };
  const first = new Date(from * DAY_MS);
  if (span > 366 * 2) {
    const years = Math.ceil(span / 365.25);
    const stride = Math.max(1, Math.ceil(years / 8));
    for (
      let y = first.getUTCFullYear();
      y <= new Date(to * DAY_MS).getUTCFullYear();
      y += stride
    ) {
      push(Date.UTC(y, 0, 1) / DAY_MS, String(y));
    }
  } else if (span > 80) {
    const stride = span > 550 ? 3 : span > 250 ? 2 : 1;
    let y = first.getUTCFullYear();
    let m = first.getUTCMonth();
    for (let i = 0; i < 40; i += 1) {
      const dayNumber = Date.UTC(y, m, 1) / DAY_MS;
      if (dayNumber > to) break;
      push(
        dayNumber,
        new Date(dayNumber * DAY_MS).toLocaleDateString(undefined, {
          month: "short",
          year: "2-digit",
          timeZone: "UTC",
        }),
      );
      m += stride;
      while (m > 11) {
        m -= 12;
        y += 1;
      }
    }
  } else {
    const stride = Math.max(1, Math.ceil(span / 7));
    for (let d = from; d <= to; d += stride) {
      push(
        d,
        new Date(d * DAY_MS).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          timeZone: "UTC",
        }),
      );
    }
  }
  if (out.length === 0) out.push({ d: from, label: isoOf(from) });
  return out;
}

/** Round down to a readable axis minimum; zero unless the value is negative. */
function niceFloor(value) {
  if (!(value < 0)) return 0;
  return -niceCeiling(-value);
}

/**
 * A stepped band: forward along the top, back along the bottom, closed.
 * Mirrors the server's `_stack_path` so both renderings agree.
 */
function stackPath(xs, lo, hi, xEnd) {
  if (xs.length === 0) return "";
  const parts = [`M ${xs[0].toFixed(2)} ${hi[0].toFixed(2)}`];
  for (let i = 1; i < xs.length; i += 1) {
    parts.push(`L ${xs[i].toFixed(2)} ${hi[i - 1].toFixed(2)}`);
    parts.push(`L ${xs[i].toFixed(2)} ${hi[i].toFixed(2)}`);
  }
  parts.push(`L ${xEnd.toFixed(2)} ${hi[hi.length - 1].toFixed(2)}`);
  parts.push(`L ${xEnd.toFixed(2)} ${lo[lo.length - 1].toFixed(2)}`);
  for (let i = xs.length - 1; i > 0; i -= 1) {
    parts.push(`L ${xs[i].toFixed(2)} ${lo[i].toFixed(2)}`);
    parts.push(`L ${xs[i].toFixed(2)} ${lo[i - 1].toFixed(2)}`);
  }
  parts.push(`L ${xs[0].toFixed(2)} ${lo[0].toFixed(2)} Z`);
  return parts.join(" ");
}

/** The palette slot an account band paints with. */
function catVar(index) {
  return `var(--bz-cat-${((index % 10) + 10) % 10})`;
}

/**
 * Turn one `[data-bz-chart]` figure into a live chart.
 *
 * The stack is drawn from *unstacked* per-account balances and added up here,
 * so switching an account off re-stacks the rest instead of leaving a hole.
 * None of this arithmetic goes anywhere: it is geometry for a picture of a
 * result the server already computed.
 *
 * @param {HTMLElement} figure
 */
function setupChart(figure) {
  const payloadNode = figure.querySelector("[data-bz-chart-data]");
  const plot = figure.querySelector("[data-bz-plot]");
  const bar = figure.querySelector("[data-bz-toggles]");
  const rangeBox = figure.querySelector("[data-bz-range]");
  if (
    !payloadNode ||
    !(plot instanceof HTMLElement) ||
    !(bar instanceof HTMLElement) ||
    !(rangeBox instanceof HTMLElement)
  ) {
    return;
  }

  /** @type {any} */
  let data;
  try {
    data = JSON.parse(payloadNode.textContent ?? "");
  } catch {
    return; // leave the server-rendered SVG in place
  }
  if (!data || !Array.isArray(data.series)) return;

  const toPoints = (raw) =>
    raw.map(([iso, value]) => ({ d: dayOf(iso), v: Number.parseFloat(value) }));

  const series = data.series.map((s) => ({
    key: s.key,
    label: s.label,
    points: toPoints(s.points),
  }));
  const stacks = (data.stacks ?? []).map((s) => ({
    key: s.key,
    label: s.label,
    role: s.role === "liability" ? "liability" : "asset",
    index: Number(s.index) || 0,
    points: toPoints(s.points),
  }));
  if (series.length === 0 && stacks.length === 0) return;

  const bands = (data.bands ?? []).map(([a, b]) => [dayOf(a), dayOf(b)]);
  const fullFrom = dayOf(data.start);
  const fullTo = Math.max(dayOf(data.end), fullFrom + 1);
  const places = Number(data.places ?? 0);
  const currency = String(data.currency ?? "");
  const money = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });

  const hidden = new Set();
  const visibleCount = () => series.length + stacks.length - hidden.size;
  let from = fullFrom;
  let to = fullTo;

  // ---- controls --------------------------------------------------------
  bar.replaceChildren();

  /** One toggle chip. `paint` decorates the swatch for lines vs bands. */
  const addToggle = (item, paint) => {
    const button = html("button", {
      type: "button",
      class: "bz-toggle",
      "aria-pressed": "true",
      title: item.label,
    });
    const swatch = html("i", { class: "bz-swatch" });
    paint(swatch);
    button.append(swatch, document.createTextNode(item.label));
    button.addEventListener("click", () => {
      if (hidden.has(item.key)) hidden.delete(item.key);
      else if (visibleCount() > 1) hidden.add(item.key);
      else return; // never leave the chart with nothing on it
      button.setAttribute("aria-pressed", hidden.has(item.key) ? "false" : "true");
      render();
    });
    bar.append(button);
  };

  stacks.forEach((s) =>
    addToggle(s, (swatch) => {
      swatch.classList.add("bz-swatch--block");
      swatch.style.setProperty("background", catVar(s.index));
    }),
  );
  series.forEach((s) =>
    addToggle(s, (swatch) => {
      swatch.style.setProperty(
        "border-top-color",
        SERIES_COLOUR[s.key] ?? "var(--bz-fg)",
      );
      if (s.key !== "wealth") swatch.style.setProperty("border-top-style", "dashed");
    }),
  );
  if (bands.length) {
    const key = html("span", { class: "bz-legend-key" });
    key.append(
      html("i", { class: "bz-band-key" }),
      document.createTextNode("Below nisab — hawl reset"),
    );
    bar.append(key);
  }

  rangeBox.replaceChildren();
  const preset = html("select", { class: "bz-input", "aria-label": "Date range" });
  const PRESETS = [
    ["all", "All time"],
    ["1", "Last 1 year"],
    ["3", "Last 3 years"],
    ["5", "Last 5 years"],
    ["10", "Last 10 years"],
    ["ytd", "Year to date"],
    ["custom", "Custom…"],
  ];
  PRESETS.forEach(([value, label]) => preset.append(new Option(label, value)));
  const fromInput = html("input", {
    type: "date",
    class: "bz-input",
    "aria-label": "From date",
    min: data.start,
    max: data.end,
  });
  const toInput = html("input", {
    type: "date",
    class: "bz-input",
    "aria-label": "To date",
    min: data.start,
    max: data.end,
  });
  const syncInputs = () => {
    if (fromInput instanceof HTMLInputElement) fromInput.value = isoOf(from);
    if (toInput instanceof HTMLInputElement) toInput.value = isoOf(to);
  };
  preset.addEventListener("change", () => {
    const value = /** @type {HTMLSelectElement} */ (preset).value;
    if (value === "all") {
      from = fullFrom;
      to = fullTo;
    } else if (value === "ytd") {
      to = fullTo;
      from = Math.max(
        fullFrom,
        Date.UTC(new Date(fullTo * DAY_MS).getUTCFullYear(), 0, 1) / DAY_MS,
      );
    } else if (value !== "custom") {
      to = fullTo;
      from = Math.max(fullFrom, fullTo - Number(value) * 365.25);
    }
    syncInputs();
    render();
  });
  const onManual = () => {
    const a = dayOf(/** @type {HTMLInputElement} */ (fromInput).value || data.start);
    const b = dayOf(/** @type {HTMLInputElement} */ (toInput).value || data.end);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return;
    from = Math.max(fullFrom, a);
    to = Math.min(fullTo, b);
    /** @type {HTMLSelectElement} */ (preset).value = "custom";
    render();
  };
  fromInput.addEventListener("change", onManual);
  toInput.addEventListener("change", onManual);
  rangeBox.append(
    preset,
    html("span", { class: "bz-range-sep" }, "from"),
    fromInput,
    html("span", { class: "bz-range-sep" }, "to"),
    toInput,
  );
  syncInputs();

  // ---- readout ---------------------------------------------------------
  const tip = html("div", { class: "bz-tip", hidden: "hidden" });
  plot.append(tip);

  // ---- drawing ---------------------------------------------------------
  let frame = 0;
  function render() {
    const width = Math.max(320, Math.round(plot.clientWidth || 960));
    const padLeft = 86;
    const padRight = 18;
    const padTop = 14;
    const padBottom = 32;
    const height = Math.max(260, Math.min(480, Math.round(width * 0.36)));
    const plotWidth = width - padLeft - padRight;
    const plotHeight = height - padTop - padBottom;
    const span = Math.max(1, to - from);

    // Stack the visible accounts on a shared set of change dates, splitting on
    // the *sign* of each balance rather than on its role. Liabilities are
    // always negative so they land below the axis either way; an overdrawn
    // asset is negative too, and stacking that upwards would paint it back
    // over the band beneath it. The two fronts still meet at net wealth.
    const shownStacks = stacks.filter((s) => !hidden.has(s.key));
    const days = new Set([from]);
    for (const s of shownStacks) {
      for (const point of s.points) {
        if (point.d > from && point.d < to) days.add(point.d);
      }
    }
    const stackDays = [...days].sort((a, b) => a - b);
    const rising = new Array(stackDays.length).fill(0);
    const falling = new Array(stackDays.length).fill(0);
    const stacked = shownStacks.map((s) => {
      const values = [];
      const lo = [];
      const hi = [];
      stackDays.forEach((day, i) => {
        const value = valueAt(s.points, day) ?? 0;
        values.push(value);
        if (value === 0) {
          // A dormant account rests on the axis rather than riding on top of
          // the stack as an invisible sliver in the wrong place.
          lo.push(0);
          hi.push(0);
          return;
        }
        const base = value < 0 ? falling : rising;
        const bottom = base[i];
        const top = bottom + value;
        base[i] = top;
        lo.push(Math.min(bottom, top));
        hi.push(Math.max(bottom, top));
      });
      return { ...s, values, lo, hi };
    });

    const shownSeries = series.filter((s) => !hidden.has(s.key));
    const clipped = shownSeries.map((s) => {
      const inner = s.points.filter((p) => p.d > from && p.d < to);
      const head = valueAt(s.points, from);
      const tail = valueAt(s.points, to);
      const drawn = [];
      if (head !== null) drawn.push({ d: from, v: head });
      else if (inner.length) drawn.push({ d: inner[0].d, v: inner[0].v });
      drawn.push(...inner);
      if (tail !== null) drawn.push({ d: to, v: tail });
      return { ...s, drawn };
    });

    const lineValues = clipped.flatMap((s) => s.drawn.map((p) => p.v));
    const yMax = niceCeiling(Math.max(1, ...rising, ...lineValues));
    const yMin = niceFloor(Math.min(0, ...falling, ...lineValues));
    const yRange = yMax - yMin || 1;

    const xOf = (d) => padLeft + (plotWidth * (d - from)) / span;
    const yOf = (v) => padTop + plotHeight * (1 - (v - yMin) / yRange);

    const root = svg("svg", {
      class: "bz-chart",
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": data.title || "chart",
    });
    root.append(svg("title", {}, data.title || "Chart"));

    for (const [a, b] of bands) {
      const x1 = Math.max(from, a);
      const x2 = Math.min(to, b);
      if (x2 <= x1) continue;
      root.append(
        svg("rect", {
          class: "bz-reset-band",
          x: xOf(x1).toFixed(2),
          y: padTop,
          width: Math.max(1, xOf(x2) - xOf(x1)).toFixed(2),
          height: plotHeight,
        }),
      );
    }

    for (let i = 0; i <= 4; i += 1) {
      const value = yMin + (yRange * i) / 4;
      const y = yOf(value);
      root.append(
        svg("line", {
          class: "bz-grid",
          x1: padLeft,
          y1: y.toFixed(2),
          x2: width - padRight,
          y2: y.toFixed(2),
        }),
        svg(
          "text",
          {
            class: "bz-axis-label",
            x: padLeft - 8,
            y: (y + 4).toFixed(2),
            "text-anchor": "end",
          },
          money.format(value),
        ),
      );
    }

    for (const tick of timeTicks(from, to)) {
      const x = xOf(tick.d);
      root.append(
        svg("line", {
          class: "bz-grid",
          x1: x.toFixed(2),
          y1: padTop,
          x2: x.toFixed(2),
          y2: height - padBottom,
        }),
        svg(
          "text",
          {
            class: "bz-axis-label",
            x: x.toFixed(2),
            y: height - padBottom + 18,
            "text-anchor": "middle",
          },
          tick.label,
        ),
      );
    }

    const xs = stackDays.map(xOf);
    const xEnd = xOf(to);
    for (const s of stacked) {
      const path = svg("path", {
        class: `bz-band bz-band--${s.role} bz-band--i${((s.index % 10) + 10) % 10}`,
        d: stackPath(xs, s.lo.map(yOf), s.hi.map(yOf), xEnd),
      });
      path.append(svg("title", {}, s.label));
      root.append(path);
    }

    if (stacked.length) {
      root.append(
        svg("line", {
          class: "bz-zero",
          x1: padLeft,
          y1: yOf(0).toFixed(2),
          x2: width - padRight,
          y2: yOf(0).toFixed(2),
        }),
      );
    }

    for (const s of clipped) {
      if (s.drawn.length === 0) continue;
      const parts = [
        `M ${xOf(s.drawn[0].d).toFixed(2)} ${yOf(s.drawn[0].v).toFixed(2)}`,
      ];
      let previous = s.drawn[0].v;
      for (const point of s.drawn.slice(1)) {
        parts.push(`L ${xOf(point.d).toFixed(2)} ${yOf(previous).toFixed(2)}`);
        parts.push(`L ${xOf(point.d).toFixed(2)} ${yOf(point.v).toFixed(2)}`);
        previous = point.v;
      }
      root.append(svg("path", { class: `bz-line-${s.key}`, d: parts.join(" ") }));
    }

    const crosshair = svg("line", {
      class: "bz-crosshair",
      x1: 0,
      y1: padTop,
      x2: 0,
      y2: height - padBottom,
      hidden: "hidden",
    });
    const markers = svg("g", { class: "bz-markers" });
    root.append(crosshair, markers);

    const surface = svg("rect", {
      x: padLeft,
      y: padTop,
      width: plotWidth,
      height: plotHeight,
      fill: "transparent",
      class: "bz-surface",
    });
    root.append(surface);

    const hide = () => {
      crosshair.setAttribute("hidden", "hidden");
      markers.replaceChildren();
      tip.hidden = true;
    };

    const addTipRow = (paint, label, value, extra) => {
      const row = html("div", { class: `bz-tip-row${extra ? ` ${extra}` : ""}` });
      const swatch = html("i", { class: "bz-swatch" });
      paint(swatch);
      row.append(
        swatch,
        html("span", { class: "bz-tip-label" }, label),
        html(
          "span",
          { class: "bz-tip-value" },
          `${money.format(value)} ${currency}`.trim(),
        ),
      );
      tip.append(row);
    };

    const move = (event) => {
      const box = root.getBoundingClientRect();
      const scale = box.width / width || 1;
      const px = (event.clientX - box.left) / scale;
      if (px < padLeft || px > width - padRight) return hide();
      const day = Math.round(from + ((px - padLeft) / plotWidth) * span);
      const x = xOf(day);
      crosshair.removeAttribute("hidden");
      crosshair.setAttribute("x1", x.toFixed(2));
      crosshair.setAttribute("x2", x.toFixed(2));
      markers.replaceChildren();
      tip.replaceChildren();
      tip.append(html("div", { class: "bz-tip-date" }, isoOf(day)));

      // Accounts, in stack order, then the running total of what is shown.
      let rows = 0;
      let assets = 0;
      let debts = 0;
      for (const s of stacked) {
        const value = valueAt(s.points, day) ?? 0;
        if (s.role === "liability") debts += value;
        else assets += value;
        if (value === 0) continue;
        addTipRow(
          (swatch) => {
            swatch.classList.add("bz-swatch--block");
            swatch.style.setProperty("background", catVar(s.index));
          },
          s.label,
          value,
        );
        rows += 1;
      }
      if (stacked.length) {
        addTipRow(
          (swatch) => swatch.style.setProperty("border-top-color", "transparent"),
          stacked.length === stacks.length ? "Stack total" : "Shown accounts",
          assets + debts,
          "bz-tip-row--total",
        );
        rows += 1;
      }

      for (const s of clipped) {
        const value = valueAt(s.points, day);
        if (value === null) continue;
        markers.append(
          svg("circle", {
            class: `bz-dot bz-dot-${s.key}`,
            cx: x.toFixed(2),
            cy: yOf(value).toFixed(2),
            r: 3.5,
          }),
        );
        addTipRow(
          (swatch) =>
            swatch.style.setProperty(
              "border-top-color",
              SERIES_COLOUR[s.key] ?? "var(--bz-fg)",
            ),
          s.label,
          value,
        );
        rows += 1;
      }

      tip.hidden = rows === 0;
      const left = Math.min(
        Math.max(0, x * scale + 14),
        Math.max(0, box.width - tip.offsetWidth - 6),
      );
      tip.style.left = `${left}px`;
      tip.style.top = `${padTop * scale + 6}px`;
    };

    root.addEventListener("pointermove", move);
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointerleave", hide);

    const old = plot.querySelector("svg");
    if (old) old.remove();
    plot.prepend(root);
    figure.dataset.bzRendered = "1";
  }

  const schedule = () => {
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(render);
  };
  if (typeof ResizeObserver === "function") {
    new ResizeObserver(schedule).observe(plot);
  } else {
    window.addEventListener("resize", schedule);
  }
  render();
}

/** @param {HTMLElement} root */
function initCharts(root) {
  root.querySelectorAll("[data-bz-chart]").forEach((figure) => {
    if (figure instanceof HTMLElement) setupChart(figure);
  });
}

/** @type import("../../frontend/src/extension-api").ExtensionModule */
export default {
  onExtensionPageLoad() {
    const root = document.querySelector("[data-zakat-dashboard]");
    if (!(root instanceof HTMLElement)) return;
    initTabs(root);
    initBasisSwitch(root);
    initSortableTables(root);
    initCharts(root);
  },
};
