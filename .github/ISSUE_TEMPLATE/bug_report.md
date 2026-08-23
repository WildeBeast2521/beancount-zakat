---
name: Bug report
about: A figure is wrong, or something does not work
labels: bug
---

**Do not paste your real ledger.** Account names and balances are exactly the thing this project promises not to see. Reduce the problem to a synthetic ledger with made-up names and round numbers.

### What happened

### What you expected instead

### Smallest ledger that reproduces it

```beancount
2020-01-01 open Assets:Cash
  beancount_zakat: "asset"
...
```

### How you ran it

```
beancount-zakat ledger.beancount --as-of 2026-01-01
```

…or: Fava version, browser, and which tab.

### Versions

- `beancount-zakat --version`:
- Python:
- Beancount:
- Fava (if the dashboard is involved):
