# Architecture and calculation invariants

This is the document to read before changing anything in `src/`. It explains how
the package is arranged, and — more importantly — the decisions that look like
oversights until you know why they were made.

## The shape of the package

```
src/beancount_zakat/
  constants.py      domain constants; changing them changes results
  models.py         frozen dataclasses describing a report
  engine.py         the layered/marginal hawl model
  hijri.py          Umm al-Qura date labelling
  prices.py         price lookup and nisab valuation      \
  adapter.py        Beancount entries -> wealth timeline    } Beancount lives here
  config.py         configuration model and precedence     /
  service.py        build_report(): the single entry point
  reporting.py      yearly allocation, per-basis detail, reconciliation
  formatting.py     exact Decimal formatting
  tables.py         width-adaptive terminal tables
  chart.py          SVG geometry
  csv_export.py     deterministic CSV
  cli.py            the beancount-zakat command
  fava_extension/   the Fava adapter, template and JS module
```

## How a report is built

One pass, in this order, every time:

1. **`config`** reads the `fava-extension` options and the `beancount_zakat`
   metadata on every `Open` directive, and produces the set of tagged accounts
   with their roles. This is the only place scope is decided.
2. **`adapter`** replays the ledger's postings into a wealth timeline: one
   snapshot per posting date *and* per relevant price date, each carrying the
   per-account balances and the net figure. Payments are collected here too.
3. **`prices`** resolves the gold and silver nisab at every one of those dates,
   from the `price` directives the ledger already contains.
4. **`engine`** turns the timeline into marginal slices and holding periods, once
   per basis, and works out what is owed. It sees only `Decimal`s and dates —
   never a Beancount object.
5. **`reporting`** allocates that liability across Hijri years, builds the
   per-basis detail, and reconciles the two views against each other.
6. **`service`** assembles the frozen `ZakatReport` and hands it back.

Everything after that is rendering: `tables` and `cli` for the terminal,
`fava_extension` and `chart` for the dashboard, `csv_export` for files. None of
them can change a figure.

## Import boundaries

`tests/test_layering.py` fails if any of these is broken.

**Nothing outside `fava_extension/` may import Fava.** The engine and the CLI
have to work on a machine that has never had Fava installed. One test runs the
CLI in a subprocess with Fava blocked from `sys.meta_path` to prove it.

**The pure layer may not import Beancount either.** `constants`, `models`,
`engine`, `hijri`, `reporting`, `formatting`, `tables` and `chart` know nothing
about ledgers. Beancount enters through `adapter`, `prices`, `config`, `service`
and `cli`. This is what makes the calculation testable without constructing a
ledger, and reusable outside Beancount entirely.

**`service.build_report()` is the only entry point.** The CLI and the Fava
extension both call it and render the same frozen `ZakatReport`. Presentation
cannot alter a result, and the two front ends cannot drift apart.

**`chart.py` is the only module allowed to call `float()`.** SVG geometry is not
money. A test asserts that `formatting`, `csv_export`, `reporting` and `cli`
never call it.

## What decides scope

Nothing in the engine knows what a bank account is, or that gold is different
from a car. Scope is decided in exactly one place — the `beancount_zakat`
metadata on `Open` directives, read by `config` — and everything downstream just
works with the accounts it was handed.

Keep it that way. The moment the tool infers zakatability from an account name, a
commodity symbol or a payee, the user loses the ability to express their own
position, and a result stops being defensible. Widening what the tool covers
means widening what a user can *tag*, never widening what it guesses.

This is also why some things are out of scope structurally rather than by
omission: agricultural produce, livestock and *rikaz* need different rates and
thresholds, and a long-term shareholding needs to be looked through to the
company's own zakatable assets — information no ledger records.

## Calculation invariants

### The lunar year is a constant, not a calendar

`HIJRI_YEAR_DAYS = 354.36708` is the mean lunar year, and it is the *only* thing
the engine measures hawl with. The `hijridate` library labels reporting years and
does nothing else. Keep that separation: if the calendar library could influence
an amount, a library upgrade could silently change what someone owes.

### Liabilities are already negative

Net zakatable wealth is `sum(assets) + sum(liabilities)`. A Beancount liability
balance carries its own negative sign, so debt is subtracted by plain addition.
Negating it a second time is the easiest mistake to make here.

### Periods are inclusive at both ends

A period covering `start`..`end` lasts `(end - start).days + 1` days. 354 days
does not complete a hawl; 355 does.

### Liability accrues pro rata, not per anniversary

`zakat = marginal × elapsed_lunar_years × rate`, spread across the whole period.
Holding a slice for 3.34 lunar years costs 8.35% of it, not three charges of
2.5%.

`elapsed_lunar_years` is fractional, deliberately, and is never floored. The
position this tool takes is that the hawl *gates* liability — it decides whether
anything is owed at all — rather than quantising it into annual steps once the
gate is open. Institutions differ on this; the About tab says so plainly.

### The three states of a holding period

`HawlPeriod.hawl` is `complete`, `incomplete`, or `not running`. A stretch spent
below the nisab is `not running` — never `incomplete` — and its `lunar_years` is
`0` no matter how long it lasted. The clock was stopped, so its duration carries
no meaning.

A nisab-break period can only appear for a level *below* the nisab. Above that,
falling below the nisab also falls below the level, and the period simply ends.

### Rounding happens before summing

Each period is quantized to 0.01 `ROUND_HALF_UP` and only then added up. Sums of
rounded values, not rounded sums.

Yearly rows split a period's liability pro rata by days, with the residual placed
on the final year, so the rows sum to the cumulative total *exactly*. Do not
relax that to an approximate comparison — the whole point of the yearly view is
that it reconciles.

### Balances are signed

Positive is owed, negative is paid in excess. Never clamp to zero: someone who
has overpaid needs to see that, and the figure has to survive into the CSV.

### Missing data is an error, not a zero

Absent valuation data raises a `Severity.ERROR` finding. A zero liability caused
by a missing price must never look the same as a genuine zero.

### The nisab moves

It tracks the metal price, so it differs on almost every year of a history.
Never present a single figure as "the" nisab. Point-in-time values are named
`*_at_as_of` to make that explicit; the views that tell the truth are the nisab
history — every change, with the price behind it — and the `nisab_low` /
`nisab_high` range on each detail row.

### Gold and silver share slices but not conclusions

Marginal levels come from the wealth timeline, which does not depend on the
nisab, so both bases see the same slices. They are still reported entirely
separately: a reset under one basis says nothing about the other, and elapsed
time means a different thing for each.

## Charts

Charts are presentation. Nothing computed for a chart is ever read back by the
engine, and `tests/test_invariants.py` proves that drawing a report leaves it
untouched.

**Exactly one chart stacks accounts** — the first on Wealth & Nisab. Every other
chart is net wealth against a threshold.

That one stacked chart never plots the nisab. A stack front is a gross figure and
the threshold applies to the net, so drawing them together would invite a
comparison that means nothing.

**Bands stack by the sign of the balance, not by the account's role.** Debt is
always negative, so it lands below the axis either way; but an overdrawn asset is
negative too, and stacking it upwards would paint it back over the band beneath
it. Splitting on sign is what keeps bands from overlapping, and it is why
`composition_series` reconciles exactly with `WealthPoint.net` at every date.

A colour, once assigned to an account, is that account's colour everywhere it
appears.

## Working with Fava 1.30

**Extension templates are fragments.** Never `{% extends %}` — Fava renders the
template and injects the result into its own `_layout.html`, so extending would
produce a second nested HTML document.

**There is no `extension_static` route.** CSS is inlined in the template; the
JavaScript module is loaded through `has_js_module` and lives next to
`__init__.py`.

**`url_for('extension_endpoint', endpoint=...)` raises `TypeError`** — Flask's
own first parameter is also called `endpoint`. Use `extension.csv_url()`.

**`g.filtered.end_date` supplies the report cutoff** from Fava's time filter. It
sets the end only; the timeline always starts at inception, because hawl has to
be measured from when wealth was actually acquired.

**The report is cached on `(ledger.mtime, as_of)`**, so an unchanged ledger is
never recalculated.

**Never call `report()` more than once per render.** The calculation grows with
the square of the number of distinct wealth levels, so a second call is not a
rounding error in the page's cost.

### The dashboard has no palette of its own

Every colour, font and metric in the inlined CSS resolves through a CSS custom
property Fava declares on `:root` — `--text-color`, `--background-darker`, the
`--table-*` and `--button-*` families, `--font-family*`, `--chart-axis`, and the
rest. `tests/test_templates.py` pins the list and rejects a literal hex outside
the categorical palette. The only exceptions are gold and silver, which Fava has
no colour for.

Light and dark come from Fava's `--lightningcss-light` / `--lightningcss-dark`
pair, never from a `prefers-color-scheme` media query. Only the pair honours the
choice the user made in Fava's own colour-scheme switcher; a media query would
follow the operating system and contradict it.

The band palette is Fava's own `hcl_color_range(10)` — hue `270 + n*36` at chroma
45 and luminance 70 in CIE HCL — so the slots wrap at ten, matching Fava's
ordinal scale. If it ever needs extending, recompute it rather than picking
colours by eye.

## Where the words live

The dashboard's educational content — everything on the About tab — is in
`templates/_zakat_about.html`, never in a Python string or in JavaScript. That
keeps it diffable, reviewable and testable like any other text, and it is why a
test can assert things about the prose at all.

The wording rules that go with it — never invent a source, keep agreed facts
apart from this tool's own choices, spell it "hawl" — are in
[CONTRIBUTING.md](../CONTRIBUTING.md#religious-content), along with the rule that
every fixture in this repository is synthetic.
