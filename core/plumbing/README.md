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
  `guard`/`sealed` trials never reach this file), and `croissant.json` (describes the two CSVs for
  the MLCommons Croissant format — doesn't read `experiments/` at all, since it only describes
  the CSVs' shape, not their contents; its per-column data type comes from the same field-type
  labels the CSVs already use, not a fresh per-field decision). A field that isn't a plain scalar
  in `stability.csv` is written as one JSON cell — deciding how to split those into their own
  columns is left for later, not guessed here. Own build check:
  `python3 -m core.plumbing.render verify`. Doesn't yet build `coverage.csv`, describe
  `open-lane/` in `croissant.json` (needs a Croissant `fileSet`, not `fileObject`), or build the
  HTML pages — separate pieces.
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
