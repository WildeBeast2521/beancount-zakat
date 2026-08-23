### What this changes

### Why

### Checks

- [ ] `pytest`
- [ ] `ruff check src tests && ruff format --check src tests`
- [ ] `mypy src/beancount_zakat`
- [ ] No real ledger, account name, balance or file path in the diff
- [ ] `CHANGELOG.md` updated under `## Unreleased`, if this is user-visible

### If this touches the calculation

Which invariant in [`docs/architecture.md`](../docs/architecture.md) does it bear on, and why is the new behaviour the right one?
