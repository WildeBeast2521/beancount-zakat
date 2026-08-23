# Contributing

Thanks for looking. This is a tool people use to work out money they intend to
give away, so the bar for a change that touches the calculation is high, and the
bar for a change that touches presentation is ordinary.

## Getting set up

```bash
git clone https://github.com/WildeBeast2521/beancount-zakat
cd beancount-zakat
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt      # editable install + fava + dev tools
```

Run the tool against the synthetic example ledger to see what you are changing:

```bash
beancount-zakat examples/ledger/main.beancount --as-of 2026-08-20 --width 120
fava examples/ledger/main.beancount       # then open the "Zakat" report
```

That ledger is built to exercise the awkward cases — a nisab break and recovery,
a refund, a holding in another commodity, included files, and a quiet tail with
no recent transactions.

## Before you open a pull request

```bash
pytest
ruff check src tests && ruff format --check src tests
mypy src/beancount_zakat
```

CI runs the same three on Python 3.10 through 3.13 on Linux, and on macOS and
Windows on 3.13, then builds the distributions and installs the wheel into a
clean environment.

The browser checks are separate. They drive a real Fava server and assert that
the dashboard has no console errors, no layout overflow, working keyboard
navigation and correct chart geometry; they also produce `docs/screenshots/`.
They need Chromium, so they are not wired into `pytest` — but if you change the
template, the stylesheet or `ZakatDashboard.js`, run them and regenerate the
screenshots.

Add a `CHANGELOG.md` entry under `## Unreleased` for anything a user would
notice.

## Never commit a real ledger

`.gitignore` blocks `*.beancount` outside `examples/` and `tests/`, blocks CSV
exports at the repository root, and blocks Fava's cache. That is a safety net,
not a licence to be careless: **every fixture in this repository is synthetic**,
and a pull request carrying a real account name, balance or file path will be
closed rather than amended. Build a fixture that reproduces the problem instead
— `tests/conftest.py` shows how the existing ones are written.

## What the tests are for

The suite is not a coverage exercise. Most of it pins decisions that are easy to
undo by accident:

- `tests/test_invariants.py` — the reconciliation guarantees. Yearly rows sum to
  the cumulative total *exactly*; the chart's bands reconcile with net wealth at
  every date; drawing never mutates a report. Do not relax an exact comparison
  to an approximate one.
- `tests/test_layering.py` — nothing outside `fava_extension/` may import Fava,
  and the pure layer may not import Beancount either. One test runs the CLI in a
  subprocess with Fava blocked from `sys.meta_path`.
- `tests/test_properties.py` — the invariants again, against randomly generated
  wealth timelines rather than hand-written ones.
- `tests/test_templates.py` — the shape of the shipped dashboard assets, and the
  Fava design tokens the stylesheet is required to consume.
- `tests/test_packaging.py` — what a consumer actually receives on install.

If your change makes one of these fail, the interesting question is which of the
two is wrong. Sometimes it is the test. Say so in the pull request and explain
why.

## Architecture in one paragraph

`service.build_report()` is the single entry point; the CLI and the Fava
extension both call it and render the same frozen `ZakatReport`, so presentation
cannot alter a result. The pure layer (`constants`, `models`, `engine`, `hijri`,
`reporting`, `formatting`, `tables`, `chart`) knows nothing about Beancount;
Beancount lives in `adapter`, `prices`, `config`, `service` and `cli`. `Decimal`
is used end to end, and `chart.py` is the only module allowed to call `float()`,
because SVG geometry is not money.

[`docs/architecture.md`](docs/architecture.md) has the rest, including the
calculation invariants that look like oversights until you know why they are
there — the fractional `elapsed_lunar_years`, the inclusive period length, the
sign convention on liabilities. Read it before changing the engine.

## Proposing a feature

Check [the Scope section of the README](README.md#scope) first. Some things are
absent on purpose rather than by omission — zakat on agricultural produce,
livestock and *rikaz* follow different rules entirely, and long-term
shareholdings need company-level information a ledger does not carry. A proposal
to add one of those is really a proposal to widen the tool's remit, so make that
case explicitly rather than filing it as a gap.

Anything that asks the tool to *infer* zakatability — from an account name, a
commodity symbol, a payee — is a hard no. Which accounts count is the user's
judgement, expressed by tagging, and keeping it that way is what makes the
result defensible.

## Religious content

Never invent a source. Keep three things distinct, as the About page does:
facts that are universally agreed (the 2.5% rate, the one-lunar-year hawl), this
tool's own modelling choices (the layered pro-rata treatment), and figures that
genuinely vary between institutions (gram equivalents of the nisab). If you are
adding an opinion, label it as one.

Educational content belongs in `templates/_zakat_about.html`, never in a Python
string or in JavaScript, so that it stays diffable and reviewable like any other
text. And spell it **hawl**, not "haul" — the About page notes the alternative
spelling exactly once, and a test keeps it at once.

## Reporting a bug

Open an issue with the smallest **synthetic** ledger that reproduces it, the
command you ran, and what you expected instead. Please do not paste your real
ledger, even partially.
