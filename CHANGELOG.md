# Changelog

All notable changes to this project are documented here. This project adheres to [Semantic Versioning](https://semver.org/).

## Unreleased

## 1.0.2 — 2026-08-25

### Added

- `tests/test_guarantees.py`, enforcing the properties the README states publicly: no network access, no runtime code generation, no writes outside a requested export, identical output across hash seeds, no `float` in a finished report, no undeclared third-party import, and no private data in the tree.
- README section on how the project was built, including its use of AI assistance.


## 1.0.1 — 2026-08-23

Documentation only.

- README: scope split into In-Scope / Out-Scope, extra PyPI badges, dropped the privacy and testing sections.
- All Markdown unwrapped to one line per paragraph.


## 1.0.0 — 2026-08-23

First release.

### Calculation

- Layered / marginal hawl model: every distinct positive net-wealth level becomes a marginal slice with its own independent holding period.
- A slice's period runs while wealth stays at or above that slice's level **and** total wealth stays at or above the nisab. Dropping below either ends it; recovery starts a fresh period. Elapsed time is never carried across a reset.
- Once a period reaches one lunar year (`354.36708` days), `zakat_due = marginal × elapsed_lunar_years × 2.5%`.
- Gold and silver evaluated independently from the same wealth timeline, and never combined.
- Net wealth is `sum(assets) + sum(liabilities)`, with liabilities carrying their natural negative Beancount sign.
- Holdings in commodities other than the operating currency are re-valued whenever a relevant price moves.
- An explicit `as_of` cutoff, defaulting to today: balances are carried forward to it and keep accruing hawl, and nothing dated later — entry or price — can affect the result. In Fava, the time filter sets the cutoff.
- `Decimal` end to end; each period is quantized to 0.01 ROUND_HALF_UP before being summed. Floating point appears only in SVG chart geometry.
- Two calendar notions kept strictly apart: the engine uses only the mean lunar-year constant, while Umm al-Qura conversion (via `hijridate`) labels reporting years. The calendar library cannot move a zakat amount. Dates outside the supported range are refused rather than approximated.

### Scope

- Zakatable wealth is whatever you tag: cash, bank balances, holdings in other commodities, and stock bought for resale, less the debts you tag as deductible. Nothing is inferred from an account name.
- Long-term shareholdings are outside the model. They are commonly handled by looking through to the company's own zakatable assets, which a ledger does not record; a tagged holding is counted at market value, as trade goods.
- Zakat al-fitr, agricultural produce, livestock and *rikaz* are not modelled.


### Reporting

- Hijri yearly summary. A period spanning several years is split across them pro rata by days, with the rounding residual on the final year, so rows sum to the cumulative total exactly.
- Signed balances throughout: positive owed, zero settled, negative paid in excess. Never clamped.
- Payment signs preserved, so a refund or correcting reversal reduces the total paid rather than adding to it.
- Hawl reported in three states — `complete`, `incomplete` (running but short of a lunar year) and `not running` (below the nisab, so the clock was reset and no elapsed time counts).
- The nisab is presented as the moving threshold it is: a full history of every change with the price behind it, and the range in force during each period. No single figure is presented as if it applied throughout.
- Structured validation findings with severities. Missing valuation data raises a blocking error rather than silently producing a zero.

### Fava dashboard

- Six accessible tabs — `role="tab"` semantics, arrow/Home/End navigation, roving tabindex, visible focus, hash-linkable and back/forward aware — ending with About Zakat & Methodology.
- Calculation Detail gives each basis its own section: one chart of net wealth against that one threshold with below-nisab stretches shaded, a hawl timeline showing every slice as a colour-coded band, and a period table.
- Time-proportional charts, server-rendered as inline SVG with `<title>`/`<desc>` text alternatives and re-drawn in the browser for interaction: toggles that re-stack or rescale what is left, a date-range control, and a pointer readout of everything visible at the hovered date. Wealth & Nisab leads with a **stacked** chart of the distribution across accounts — one band each, held above the zero line and owed below it, so the two fronts meet at net wealth — followed by net wealth alone against both thresholds. Stacking is presentation only: the bands are the balances the calculation already used, and nothing drawn feeds back into a result. No CDN, no charting library, works offline and under a strict CSP.
- Styled entirely by Fava. Colours, fonts, table chrome, buttons, inputs and chart axes resolve through the CSS custom properties Fava declares on `:root`, and account bands use Fava's own categorical scale (`hcl_color_range(10)`), so the report picks up Fava's theme rather than approximating it. Light and dark follow Fava's colour-scheme switcher — including an explicit choice that contradicts the operating system, which a `prefers-color-scheme` media query would ignore.
- Mobile layouts; no status signalled by colour alone.
- CSV and ZIP downloads. Results cached per ledger revision and cutoff.
- Errors show a safe message; tracebacks go to the server log only.

### CLI

- `beancount-zakat` console command that does **not** require Fava.
- `--as-of`, `--csv` (directory or `.zip`), `--basis`, `--width`, `--quiet`.
- Width-adaptive tables that fall back to stacked label/value blocks on narrow terminals; verified from 40 to 220 columns.
- Meaningful exit codes and a reconciliation section.

### Configuration

- Account roles come from `beancount_zakat:` metadata on `Open` directives. There is no configuration file to keep in sync with the ledger.
- Optional overrides on the `fava-extension` directive: `zakat_rate`, nisab weights in grams or tola, commodity aliases with explicit units, and the price staleness threshold.
- Roles merged as a union, independently per role. Unknown keys, invalid roles, bad account names, absurd rates and ambiguous units are all reported.

### Packaging

- MIT licensed. `fava` is an optional extra, not a runtime dependency.
- Version single-sourced from `beancount_zakat.__version__`, so the wheel, the PyPI page and `beancount-zakat --version` cannot disagree.
- `py.typed`: the package ships its type information.
- Tested on Python 3.13.15, Beancount 3.2.3, Fava 1.30.16, hijridate 2.6.0, and on 3.10 through 3.13 in CI.
