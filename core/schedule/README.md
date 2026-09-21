# `core/schedule/`

## What you'll see here

| File | What it does |
|---|---|
| `schedule.py` | Reads `cadence.yaml`, compares against `state/last_run.json`, says what's due today. `due` jobs that are also a `core/budget/` category (`full_sweep`, `subject_fingerprint`) get a `budget.check()` decision attached. CLI: `python3 -m core.schedule.schedule due / mark-run`. |
| `vectors.json` + `verify.py` | Hand-worked cases. Run `python3 -m core.schedule.verify` after touching `schedule.py`. |

## Why it needs to exist

`cadence.yaml` is just data — nothing reads it yet. This is the first thing that does: it turns
"subject_fingerprint: 1d" into an actual yes/no for today. It's deliberately dumb — it only knows
"has N days passed since `last_run.json` says this ran", nothing about what a job *is* or what to
do once it's due. Any `Nd`/`Nh` field in `cadence.yaml` counts, mechanically; non-interval fields
(`on_new_generation`, bare numbers, policy strings) are skipped because they don't parse as one,
not because of a hardcoded list of "real jobs".

## What this doesn't do (yet)

- Doesn't actually run anything — `mark-run` only records that a run happened; something else has
  to call it.
- Doesn't know about `generation_bridge` (event-triggered by `bridge_trigger`, not a cadence field)
  or the instrument (`runtime_fingerprint`/guard checks) — those are separate, later work.
- `last_run.json` only keeps the most recent date per job, not history — unlike `spend.json`,
  there's nothing here worth keeping an append-only log of.
