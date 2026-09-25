# `core/plumbing/`

## What you'll see here

- `fake_client.py` — a synthetic stand-in for a real language model. It answers questions
  instantly, for free, by following a simple made-up rule based on which version and scenario
  number it was asked (it never actually reads the question text).
- `anthropic_client.py` — the real adapter for the commercial subject model (spec.md §5): Sonnet-
  class tier, thinking disabled, no system instruction, sent through Anthropic's Messages API.
  `temperature`/`top_p`/`top_k` are not sent — the current API no longer accepts them; confirmed
  live across 4 protocol families that repeated identical prompts still come back deterministic
  without them, so nothing about this project's measurement depends on that knob existing.
  Since 2026-09-25 it also counts the tokens the API returns per call (`input_tokens`,
  `output_tokens`) and prices them (`cost_usd()`, Sonnet 5 = $2 / $10 per 1M; an unlisted model has
  no cost), and it has a circuit breaker: `CircuitOpen` on the first 401/403/404 or after 5 failed
  calls in a row. `core/schedule/run_due.py` uses both; see `core/schedule/README.md`. Checked by
  `python3 -m core.schedule.verify` (`anthropic_client_metering`, against a stand-in `anthropic`).
  `model_id`/`model_family` are constructor arguments, never hardcoded, because unlike the
  reference model this one changes across generations (spec.md §4.3). Needs `pip install anthropic`
  and `ANTHROPIC_API_KEY` set in the environment to actually run; importing the module doesn't
  (same lazy-import pattern as `reference_client.py`). No automated `verify()` for the same reason
  `reference_client.py` has none — the one thing worth checking can't be checked without spending a
  real, billed call.
- `reference_client.py` — the real adapter for the frozen open-weights reference model
  (`core/instrument/`, spec.md §7): `Qwen/Qwen2.5-1.5B-Instruct-GGUF`, pinned by revision and
  file sha256, run through `llama-cpp-python`. Greedy (temperature 0), fixed thread count, fixed
  seed, explicit ChatML template — every knob spec.md §5 asks a client to send explicitly.
  Needs `llama-cpp-python` installed and a real `.gguf` file on disk to actually run; importing the
  module doesn't (the import is lazy, inside `__init__`).
- `runtime_fingerprint.py` — produces the other half of spec.md §7's daily check: runs 12 fixed
  prompts through the reference model, greedy, and hashes each one's top-5 logprobs per token
  (rounded to 4 decimals) rather than just its output text, so a distribution that's already
  drifting is caught even on a day its greedy output still happens to tie out the same. Shares
  `reference_client.py`'s pinned load parameters via `load_llama()` rather than duplicating them.
- `reference_model_runner.py` — the actual daily entry point: runs `runtime_fingerprint.py`, runs
  ONE guard-lane protocol's full `core/measure/pilot.py` pass against `reference_client.py`
  (rotating through all 10 on a 10-day cycle, not all 10 every day — see its docstring for the
  real CI-cost numbers behind that), and feeds both into `core.instrument.instrument.status()`.
  Bootstraps its own frozen comparison points (`state/instrument/`) the first time each one is
  needed, rather than requiring them to be seeded by hand.
- `render.py` — the first slices of the public-site renderer (spec.md §10/§11): reads
  `experiments/*/measurement.json` and `.../trials.jsonl` and writes `stability.csv` (one row per
  measurement, one column per `core.measure.schema.MEASUREMENT_FIELDS` key — that closed list, not
  a copy of it, so the two can't drift apart), `outcomes.csv` (the same `outcomes` field, unpacked
  to one row per `(run_id, version, outcome, count)`), `open-lane/<year>.jsonl` (every
  `trials.jsonl` record from a `lane: "open"` run, copied through unchanged and grouped by year —
  `guard`/`sealed` trials never reach this file), `croissant.json` (describes the two CSVs for
  the MLCommons Croissant format — doesn't read `experiments/` at all, since it only describes
  the CSVs' shape, not their contents; its per-column data type comes from the same field-type
  labels the CSVs already use, not a fresh per-field decision), and `coverage.csv` (one row per
  `core.schedule.coverage` observation — this module only formats that log, it doesn't decide
  whether a job ran or why not; see that module's docstring). A field that isn't a plain scalar
  in `stability.csv` is written as one JSON cell — deciding how to split those into their own
  columns is left for later, not guessed here. Own build check:
  `python3 -m core.plumbing.render verify`. Doesn't yet describe `open-lane/` in `croissant.json`
  (needs a Croissant `fileSet`, not `fileObject`), or build the HTML pages — separate pieces.
- `subject_fingerprint.py` — the daily check against the REAL commercial model (spec.md §7): 24
  fixed, short yes/no fact checks (12 yes / 12 no, in same-topic pairs so an "always yes" strategy
  scores only 50%), asked with the exact same frozen `DECISION: X` grammar every protocol prompt
  uses (`core.measure.grammar`/`client.classify`) rather than a second, bespoke parser. Unlike
  `runtime_fingerprint.py` against the pinned local reference model, the remote commercial API
  exposes no logprobs to hash for exact-equality drift detection, so the observable here is
  coarser: how many of the 24 came back valid-and-correct today. Writes to
  `experiments/subject_fingerprint/<date>__<subject_model_id>.json`, append-only. `record_type:
  "subject_fingerprint"` follows `core/instrument/instrument.py`'s `"instrument_alarm"` precedent
  rather than touching `core/measure/schema.py`'s frozen `RECORD_TYPES`. Own build check:
  `python3 -m core.plumbing.subject_fingerprint verify`.
- `model_watch.py` — notices a new model id in the commercial family (`GET /v1/models`, free), the
  trigger spec.md §4.3 calls "automatic, immediate" for the generation bridge and that nothing else
  in the code detected. Detects and prints the bridge plan + budget verdict; never spends money and
  never switches the active model (a new id is `needs_review`). First run only records a baseline.
  Append-only log: `state/model_registry.jsonl`. Exit status 10 = action needed. Runs daily in
  `.github/workflows/model-watch.yml` (06:30 UTC; opens a GitHub issue with the bridge plan on exit 10;
  needs the `ANTHROPIC_API_KEY` repo secret, a workspace-scoped key). Own build check:
  `python3 -m core.plumbing.model_watch verify`.
- `budget_watch.py` — two readers of `state/spend.json` + `budget.json` that tell a person what the ledger already
  knows: `rung` (exit 10 as soon as the month leaves the `full` rung, naming the rung and the percentage --
  the ladder cuts the sweep silently otherwise) and `reconcile` (the previous month's ledger split into entries
  computed from tokens vs. entries logged by hand, to compare with the Console bill, plus the command to record
  a difference). Never records or spends. Daily in `.github/workflows/budget-watch.yml`: one issue per
  (month, rung), and on the 6th one reconciliation issue per month. Own build check:
  `python3 -m core.plumbing.budget_watch verify`.
- `deadman.py` — cadence.yaml's `deadman_alert: 36h`, which nothing read before: exit 10 when the newest
  commit by `github-actions[bot]` is older than that threshold (a person's commit doesn't count -- it says
  nothing about whether the automation is alive). Catches the one failure no workflow reports itself: a
  workflow that never starts. Detects only; runs every 6 hours in `.github/workflows/deadman.yml`, which
  opens one issue and not another while it is open. Own build check: `python3 -m core.plumbing.deadman verify`.
- `ots_upgrade.py` — turns a pending OpenTimestamps proof into a Bitcoin proof (the by-hand `ots upgrade` of
  `state/timestamps/README.md`). Decides "already has a Bitcoin attestation" from the file's bytes, so
  `status` needs no `ots` install, and never touches a file that has one. Weekly in
  `.github/workflows/ots-upgrade.yml`; with nothing pending (the normal state) it does and commits nothing.
  Own build check: `python3 -m core.plumbing.ots_upgrade verify`.
- `alarm_log.py` — a durable trace of the reference-model self-check. `run_due.py` used to print the
  `instrument_alarm` verdict and forget it; now each run appends one line to `state/instrument/alarm_log.jsonl`
  (date, verdict, which half moved), which the daily workflow already commits. `recent` exits 10 when an
  alarm falls in the last N days; `tools/auto_merge_gate.py` reads it. Records only, changes no exit status.
  Own build check: `python3 -m core.plumbing.alarm_log verify`.
- `prereg.py` — the pre-registration guard: a protocol counts as pre-registered only if a manifest in
  `state/timestamps/` lists its current `panel_sha256`/`protocol_sha256` and that manifest has a matching
  OpenTimestamps `.ots` (checked by comparing the `.ots` header digest to the manifest's sha256, stdlib
  only). Enforced in `core/schedule/run_due.run_full_sweep` (paid runs are skipped as
  `not_preregistered`); NOT enforced on a hand-typed `core.measure.pilot` run (frozen). `python3 -m
  core.plumbing.prereg check` lists every protocol; own build check `... prereg verify`. Plain-words
  explanation: `guide/timestamps.md`.
- `revalidate.py` — the `revalidation_daily` job (cadence.yaml: "10 historical records, local, zero cost"),
  which had a schedule and no code. Re-derives a fixed daily selection of old runs from their raw
  `trials.jsonl` and checks four things: protocol hash, prompts, classifications, and the whole
  measurement. An `analysis_code_sha`-only difference is a NOTE (the code legitimately evolved), any
  other difference a failure. Appends to `state/revalidation_log.jsonl`. `python3 -m
  core.plumbing.revalidate run|verify`. Not on any schedule yet.
- `render_guide.py` — turns `guide/*.md` into `guide/*.html`, a small standalone docs site sitting
  next to (not inside) the closed 8-page site spec.md §11 describes, per `guide/README.md`'s own
  plan to eventually lift it "alongside the main website." A hand-rolled markdown→HTML converter,
  on purpose — same reasoning as "why numpy is banned" in `guide/for-developers.md`: pure stdlib,
  no CDN, nothing here should ever fail to build because some other library's future didn't arrive.
  Only understands the markdown constructs `guide/*.md` actually use. Own build check:
  `python3 -m core.plumbing.render_guide verify`.

## Why it needs to exist

Three reasons:

1. **Testing without spending money.** Everything in `core/measure/` needs something that
   implements the `Client` contract (defined in `core/measure/client.py`) to run against. Before a
   real, paid API client exists, `fake_client.py` lets the whole pipeline — prompt building,
   response classification, statistics, gates — be exercised end to end, thousands of times, at
   zero cost.

2. **A real Anthropic client will live here too, later**, sitting right next to `fake_client.py`.
   Both are "adapters": interchangeable implementations of the same contract. That's why this code
   is not inside `core/measure/` (which is meant to stay frozen) — an adapter is expected to
   change (new provider, new API version, new retry logic) without that being a change to the
   measurement logic itself.

3. **`reference_client.py` is the same kind of adapter, for the instrument's own control.** It
   feeds `core/measure/pilot.py` exactly like any other client, which is what makes the guard-lane
   protocols runnable against the reference model with zero new logic anywhere else — the
   `measurement.json` it produces is already the shape `core.instrument.check_guard_margin` and
   `core.measure.stats.compare` expect.

**What this folder must never contain:** anything that decides whether a result is valid, computes
a statistic, or touches a threshold. If it looks like it's making a judgment call about the data
rather than just fetching or formatting it, it belongs in `core/measure/` instead.
