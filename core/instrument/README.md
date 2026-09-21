# `core/instrument/`

## What you'll see here

| File | What it does |
|---|---|
| `instrument.py` | Two checks: `check_runtime_fingerprint(...)` — exact equality against the reference model's fixed prompts — and `check_guard_margin(...)` — today's guard-protocol measurement on the reference model vs. a frozen baseline, reusing `core.measure.stats.compare`. `status(...)` combines them into one `instrument_suspect` yes/no. CLI: `python3 -m core.instrument.instrument check ...`. |
| `vectors.json` | Hand-worked cases for both checks and for `status()`. |
| `verify.py` | This folder's own build check. Run `python3 -m core.instrument.verify` after touching `instrument.py`. |

## Why it needs to exist, and why it's separate from `core/measure/`

`core/measure/` is frozen (`spec.md` §13). This folder isn't that — it's the guardrail that
decides whether *today's* measurements can be trusted at all, per `spec.md` §7: if the
reference model's fixed prompts don't come back byte-identical, or its guard-protocol result moves
beyond the same composite margin used everywhere else (`core.measure.stats.compare`), the day is
`instrument_suspect` and publication stops — the cycle keeps running, but nothing goes on a curve.

It doesn't run anything against a real model. Like `core/budget/` and `core/schedule/`, this is
the decision logic, built before the reference-model runner existed. It takes already-computed
observations (a runtime-fingerprint file, a today's-measurement dict)
and says pass/fail — the same shape as `budget.check()` taking an already-billed ledger and
`schedule.due_jobs()` taking an already-recorded `last_run.json`.

## What this folder must never contain

Anything that produces a fingerprint or runs a guard protocol — that's the reference-model runner,
which doesn't exist yet. This module only ever compares numbers it's handed; it never calls a
model, real or reference.
