# Security policy

## Supported versions

The latest release on PyPI is the supported one.

## Reporting a vulnerability

Please report privately through GitHub's [security advisories](https://github.com/WildeBeast2521/beancount-zakat/security/advisories/new) rather than opening a public issue. You should get an acknowledgement within a week.

## What is in scope

This tool reads a Beancount ledger on your own machine and renders a report. It makes no network requests, stores no credentials and has no server component of its own. The interesting risks are therefore about **your data leaving your machine**, and about the report page itself:

- Anything that would make the Fava dashboard execute attacker-controlled markup or script — for example a crafted account name, payee or metadata value that escapes the template's escaping and lands in the page.
- Anything that would cause ledger contents to be written somewhere unexpected, or transmitted anywhere at all.
- Anything that would let a crafted ledger read or write files outside the paths Beancount was asked to load.

## What is not in scope

- A calculation you disagree with on religious grounds. That is a [discussion](https://github.com/WildeBeast2521/beancount-zakat/issues), and a welcome one, but it is not a vulnerability.
- Denial of service from a pathological ledger. The engine is quadratic in the number of distinct wealth levels and that is documented; a slow report on a 20,000-level ledger is a performance issue.
- Vulnerabilities in Beancount or Fava themselves. Please report those upstream.
