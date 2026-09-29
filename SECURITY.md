# Security Policy

This file is
for actual security vulnerabilities. A methodology disagreement, a bug in `core/measure/`, or a
correction to a human-literature citation is not a security report — those go through
[`CONTRIBUTING.md`](CONTRIBUTING.md) instead.

## Scope

**In scope:**

- The public site ([`llm-archive.github.io`](https://llm-archive.github.io/)) — e.g. XSS, a
  vulnerable dependency in the site build, or anything that could let an attacker alter published
  data as it's shown to a visitor.
- A way to forge or tamper with a measurement without breaking the hash chain that
  `core/measure/verify.py` checks — i.e. anything that defeats the chain-of-custody the project's
  comparability depends on.
- Exposure of secrets (API keys, never committed — see `docs/spec.md` §10) or of the private,
  contamination-sensitive contents of `protocols/` or `mutable/` (see `CONTRIBUTING.md`'s boundary
  table) through this repo's tooling, CI, or the release process.

**Out of scope** (handled elsewhere, not as a vulnerability):

- Disagreement with the measurement methodology — that's a discussion, not a report.
- Corrections to human-literature citations — see `CONTRIBUTING.md`.
- A suspected defect in `core/measure/` with no security impact — see "Reporting a bug in frozen
  code" in `CONTRIBUTING.md`; it's fixed by a disclosed `core_change`, not a silent patch.

## Reporting

Email **mkalognomos@gmail.com** directly, from an address you can be identified by. Include:

1. What you found and where (file, URL, or endpoint).
2. Steps to reproduce.
3. What you think the impact is.

Please don't open a public GitHub issue for a security report until it's been addressed. There's
no bug bounty — this is an unfunded research project — but every report gets read and a real
reply.

## Response

Single operator, so no formal SLA. A confirmed issue gets fixed and, where it affects published
data or the record's integrity, disclosed the same way any other change is — see
[`CHANGELOG.md`](CHANGELOG.md).
