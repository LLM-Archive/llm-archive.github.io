# `core/schedule/`

## What you'll see here

| File | What it does |
|---|---|
| `schedule.py` | Reads `cadence.yaml`, compares against `state/last_run.json`, says what's due today. `due` jobs that are also a `core/budget/` category (`full_sweep`, `subject_fingerprint`) get a `budget.check()` decision attached. CLI: `python3 -m core.schedule.schedule due / mark-run`. |
| `run_due.py` | The caller `schedule.py` never had: dispatches each due, known cadence job to the runner that already exists for it (`full_sweep` → `core.measure.pilot`, `subject_fingerprint` → `core.plumbing.subject_fingerprint`, `runtime_fingerprint`/`guard_margin_rotation` → `core.plumbing.reference_model_runner`, one call covers both), then calls `schedule.record_run()` for whatever actually ran. Cadence entries with no runner (`human_review`, `checkpoint_sign`, …) are reported, never dispatched. CLI: `python3 -m core.schedule.run_due [--client fake\|anthropic] [--dry-run]` — defaults to `--client fake` (spends nothing) unless told otherwise. |
| `vectors.json` + `verify.py` | Hand-worked cases, including `run_due.plan()`'s pure routing logic. Run `python3 -m core.schedule.verify` after touching either file. |

## Why it needs to exist

`cadence.yaml` is just data — nothing reads it yet. This is the first thing that does: it turns
"subject_fingerprint: 1d" into an actual yes/no for today. It's deliberately dumb — it only knows
"has N days passed since `last_run.json` says this ran", nothing about what a job *is* or what to
do once it's due. Any `Nd`/`Nh` field in `cadence.yaml` counts, mechanically; non-interval fields
(`on_new_generation`, bare numbers, policy strings) are skipped because they don't parse as one,
not because of a hardcoded list of "real jobs".

## What this doesn't do (yet)

- **Doesn't run on a recurring, unattended schedule.** `run_due.py` is a caller you invoke by
  hand (or a workflow you trigger by hand) — it does not itself add a cron. Wiring an actual
  recurring trigger that runs `--client anthropic` and commits results back unattended is a
  separate, deliberate decision (STATUS.md) — two of the three runners spend real money, and
  none of them have a human present at run time the way every real run so far has.
- Doesn't know about `generation_bridge` (event-triggered by `bridge_trigger`, not a cadence
  field) — separate, later work.
- `last_run.json` only keeps the most recent date per job, not history — unlike `spend.json`,
  there's nothing here worth keeping an append-only log of.
