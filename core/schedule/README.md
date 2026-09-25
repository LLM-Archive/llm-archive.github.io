# `core/schedule/`

## What you'll see here

| File | What it does |
|---|---|
| `schedule.py` | Reads `cadence.yaml`, compares against `state/last_run.json`, says what's due today. `due` jobs that are also a `core/budget/` category (`full_sweep`, `subject_fingerprint`) get a `budget.check()` decision attached. CLI: `python3 -m core.schedule.schedule due / mark-run`. |
| `run_due.py` | The caller `schedule.py` never had: dispatches each due, known cadence job to the runner that already exists for it (`full_sweep` → `core.measure.pilot`, `subject_fingerprint` → `core.plumbing.subject_fingerprint`, `runtime_fingerprint`/`guard_margin_rotation` → `core.plumbing.reference_model_runner`, one call covers both), then calls `schedule.record_run()` and `coverage.record_observation()` for whatever actually ran (or didn't, with a named cause). Cadence entries with no runner (`human_review`, `checkpoint_sign`, …) are reported, never dispatched. CLI: `python3 -m core.schedule.run_due [--client fake\|anthropic] [--dry-run]` — defaults to `--client fake` (spends nothing) unless told otherwise. |
| `coverage.py` | For the 4 jobs `run_due.py` knows how to run: whether a due one ran, and if not, why (one of `core.measure.schema.GAP_CAUSES`, never guessed). Appends to `state/coverage_log.jsonl` — the only source `core.plumbing.render`'s `build-coverage` ever builds the public `coverage.csv` from. See its own docstring for scope (which of cadence.yaml's ~20 jobs this covers) and which gap causes are currently reachable. |
| `vectors.json` + `verify.py` | Hand-worked cases, including `run_due.plan()`'s pure routing logic and `coverage.py`'s `gap_cause_for`/`record_observation`. Run `python3 -m core.schedule.verify` after touching any of these files. |

## Why it needs to exist

`cadence.yaml` is just data — nothing reads it yet. This is the first thing that does: it turns
"subject_fingerprint: 1d" into an actual yes/no for today. It's deliberately dumb — it only knows
"has N days passed since `last_run.json` says this ran", nothing about what a job *is* or what to
do once it's due. Any `Nd`/`Nh` field in `cadence.yaml` counts, mechanically; non-interval fields
(`on_new_generation`, bare numbers, policy strings) are skipped because they don't parse as one,
not because of a hardcoded list of "real jobs".

## What this doesn't do (yet)

- **Doesn't schedule itself.** `run_due.py` is a caller; the recurring triggers are GitHub
  Actions workflows in `.github/workflows/`, one per kind of job, all calling it with `--only`:
  `daily-reference-check.yml` (`--only reference_model`, free), `subject-fingerprint.yml`
  (`--only subject_fingerprint`, ~EUR 0.003/day) and `paid-sweep.yml` (`--only full_sweep`, real
  money, days 1-5 of the month; `run_due` only runs it when 30 days have passed). `--only` is what
  keeps the cheap jobs structurally unable to reach the expensive one.
- **Paid runs are unattended, so they carry their own limits** (added 2026-09-25):
  - *Measured spend.* After each paid protocol, `run_due` writes tokens x list price into
    `state/spend.json` (`core.plumbing.anthropic_client` counts the API's own `usage`; a model with
    no listed price is not auto-logged, and the manual "log the real bill" reminder prints as
    before). The entry's note says it is not yet reconciled against the Console bill: reconcile
    monthly, record any difference by hand.
  - *Would it fit?* Before each protocol, `spent + the protocol's pessimistic estimate` must be
    within `monthly_ceiling_eur`, on top of the rung check (which only reads what is already spent).
  - *Circuit breaker.* `CircuitOpen` (a 401/403/404, or 5 failed calls in a row) stops the whole
    sweep; the calls already billed are logged as an `...__aborted` ledger entry and the job stays
    due (`circuit_open` is in `_STILL_DUE_SKIPS`).
- Doesn't know about `generation_bridge` (event-triggered by `bridge_trigger`, not a cadence
  field) — separate, later work.
- `last_run.json` only keeps the most recent date per job, not history — that's exactly why
  `coverage.py` keeps its own separate, append-only log (`state/coverage_log.jsonl`) instead of
  trying to derive coverage history from `last_run.json`.
- `coverage.py` only covers the 4 jobs above — the rest of `cadence.yaml`'s human/lifecycle
  cadences (`human_review`, `release_publish`, `dormant_after`, …) aren't in `coverage.csv` yet;
  see that module's docstring.
