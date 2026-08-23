# CSV export schema

`beancount-zakat LEDGER --csv PATH` writes seven files into `PATH` (a
directory), or into a zip archive when `PATH` ends in `.zip`. The dashboard
serves the same files from its download links.

| File | Contents |
|---|---|
| `metadata.csv` | Report-level facts, both bases' headline figures, assumptions |
| `warnings.csv` | Every validation finding, with severity |
| `nisab_history.csv` | Every change to either threshold, with the price behind it |
| `yearly_summary.csv` | One row per Hijri reporting year |
| `detail_gold.csv` | Gold marginal slices and holding periods |
| `detail_silver.csv` | Silver marginal slices and holding periods |
| `payments.csv` | Signed payment detail with a running total |

Every field marked `*_at_as_of` describes the report date **only**. The nisab is
a moving threshold; nothing in this export presents a single figure as if it
applied throughout.

## Conventions

Every file follows the same rules, and they are asserted by
`tests/test_csv_export.py`:

| | |
|---|---|
| Encoding | UTF-8, no BOM |
| Line endings | `\r\n` (RFC 4180) |
| Quoting | minimal; fields containing `,` `"` or a newline are quoted |
| Column names | stable; new columns are only ever appended |
| Row order | deterministic, documented per file |
| Monetary values | **exact decimal strings** at 2 places — `285125.95`, never `285,125.95 PKR` |
| Ratios | full precision, ungrouped — e.g. `lunar_years` = `1.887872880291250530382223992` |
| Dates | ISO 8601 `YYYY-MM-DD` |
| Booleans | lowercase `true` / `false` |
| Empty values | empty string, never `None` or `NaN` |

There is no locale grouping and no currency symbol anywhere, so a spreadsheet
reads the numbers as numbers regardless of its locale. The operating currency is
stated once, in `metadata.csv`.

---

## `metadata.csv`

Two columns, `key` and `value`; one row per fact, in the fixed order below.

| key | meaning |
|---|---|
| `as_of` | Report cutoff |
| `as_of_hijri_year` | Hijri year containing `as_of` |
| `operating_currency` | Currency every monetary column is expressed in |
| `zakat_rate` | Rate as a fraction, e.g. `0.025` |
| `inception` | First date with zakatable activity; empty if none |
| `net_wealth_as_of` | Net zakatable wealth at `as_of` |
| `gold_nisab_at_as_of` / `silver_…` | Threshold **on the report date only**; see `nisab_history.csv` for the rest |
| `gold_nisab_commodity_at_as_of` / `silver_…` | Commodity the price came from |
| `gold_nisab_price_at_as_of` / `silver_…` | Price in force on the report date |
| `gold_nisab_price_date_at_as_of` / `silver_…` | When that price was quoted |
| `gold_qualifies` / `silver_qualifies` | Sahib-e-nisab at `as_of` |
| `gold_cumulative_liability` / `silver_…` | Total historical liability |
| `gold_payments` / `silver_payments` | Payments net of refunds |
| `gold_remaining_or_excess` / `silver_…` | **Signed**: positive owed, negative overpaid |
| `gold_status` / `silver_status` | `outstanding` \| `settled` \| `excess` |
| `asset_accounts` / `liability_accounts` / `payment_accounts` | `\|`-separated |
| `warning_count` | Number of rows in `warnings.csv` |
| `note` | Reminder that the two bases are alternatives |

## `warnings.csv`

One row per validation finding, in the order they were produced.

| column | meaning |
|---|---|
| `severity` | `error` \| `warning` \| `info` |
| `code` | Stable machine-readable code, e.g. `missing-nisab-price` |
| `message` | One-sentence description |
| `detail` | Actionable remedy; may contain newlines |
| `account` | Account concerned, if any |
| `commodity` | Commodity concerned, if any |
| `date` | Date concerned, if any |

Any row with `severity = error` means a displayed figure is not trustworthy;
the CLI exits `1` when one is present.

## `yearly_summary.csv`

One row per Hijri reporting year, ascending. No gaps: a year with no activity
still appears, with zeros.

| column | meaning |
|---|---|
| `hijri_year` | e.g. `1447` |
| `gregorian_start` / `gregorian_end` | Inclusive bounds; the last row's end is `as_of` |
| `gold_liability` / `silver_liability` | Liability allocated to this year |
| `payments` | Payments made in this year, net of refunds |
| `gold_balance` / `silver_balance` | **Running signed** balance at year end |
| `gold_balance_status` / `silver_balance_status` | `outstanding` \| `settled` \| `excess` |

**Reconciliation guarantee.** `sum(gold_liability)` equals
`metadata.gold_cumulative_liability` exactly, and the last row's `gold_balance`
equals `metadata.gold_remaining_or_excess` exactly. Likewise for silver, and
`sum(payments)` equals the payment total. This is enforced by the invariant
tests, not merely intended.

## `nisab_history.csv`

One row per date on which **either** threshold changed, ascending. This is the
authoritative record of the thresholds the calculation actually used — the nisab
tracks the metal price, so it is different on almost every year of your history.

| column | meaning |
|---|---|
| `in_force_from` | The threshold below applies from this date until the next row |
| `gold_commodity` / `silver_commodity` | Price commodity used, e.g. `GLDTOLA` |
| `gold_price` / `silver_price` | Price in force |
| `gold_price_date` / `silver_price_date` | When that price was actually quoted — never after `in_force_from` |
| `gold_price_age_days` / `silver_price_age_days` | How stale the quote was |
| `gold_nisab` / `silver_nisab` | The threshold it implies |

A day with no price of its own reuses the last known price, so a row stands
until the next one replaces it.

## `detail_gold.csv` and `detail_silver.csv`

One row per holding period, grouped by marginal slice, slices ascending by
level. Identical schemas, one file per basis — the thresholds are far apart and
a reset under one basis says nothing about the other.

| column | meaning |
|---|---|
| `basis` | `gold` or `silver` |
| `level` | The net-wealth level this slice sits at |
| `marginal_amount` | `level − previous level`; the amount actually charged |
| `period_start` / `period_end` | Inclusive bounds of this period |
| `days` | `(period_end − period_start) + 1` |
| `lunar_years` | `days / 354.36708`, full precision — **`0` when the hawl was not running** |
| `hijri_year_start` / `hijri_year_end` | Hijri years the period touches |
| `nisab_low` / `nisab_high` | The threshold **range** in force during the period; they differ wherever the price moved inside it |
| `at_level` | Wealth was at or above `level` |
| `above_nisab` | Total wealth was at or above this basis's nisab |
| `hawl` | `complete` \| `incomplete` \| `not running` — see below |
| `qualifies` | Shorthand for `hawl == "complete"` |
| `zakat_due` | `marginal_amount × lunar_years × rate`, or `0.00` |
| `reason` | Plain-language explanation of the outcome |

**`hawl` has three states, because two would mislead:**

- `complete` — the period reached a full lunar year, so zakat is due on it.
- `incomplete` — the clock was running but has not yet reached a year.
- `not running` — wealth was below **this basis's** nisab, so the clock was
  reset. `lunar_years` is `0` on such a row regardless of how many days it
  spans: elapsed time during a reset counts for nothing, so reporting it as a
  fraction of a year would be misleading.

`sum(zakat_due)` equals that basis's `cumulative_liability` exactly.## `payments.csv`

One row per posting to a payment account, ascending by date then account.

| column | meaning |
|---|---|
| `date` | Posting date |
| `account` | Account tagged `beancount_zakat: "expense"` |
| `payee` / `narration` | From the transaction; may be empty |
| `original_amount` | Amount as posted, **signed** |
| `original_currency` | Currency as posted |
| `conversion_rate` | Rate applied; empty when already in the operating currency |
| `amount` | Amount in the operating currency, **signed** |
| `is_reversal` | `true` when `amount < 0` — a refund or correction |
| `running_total` | Cumulative `amount` through this row |

The final `running_total` equals `metadata.gold_payments` and
`metadata.silver_payments`.

---

## Reading it back

```python
import csv
from decimal import Decimal
from pathlib import Path

rows = list(csv.DictReader(Path("out/yearly_summary.csv").open(encoding="utf-8")))
total = sum(Decimal(row["gold_liability"]) for row in rows)
print(total)   # equals metadata.gold_cumulative_liability, exactly

detail = list(csv.DictReader(Path("out/detail_gold.csv").open(encoding="utf-8")))
print(sum(Decimal(r["zakat_due"]) for r in detail))   # the same figure
```

Use `Decimal`, not `float`: the values are exact and `float` would reintroduce
the error the calculation went to some trouble to avoid.
