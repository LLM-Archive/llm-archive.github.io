# `core/measure/`

This folder is the part of the project meant to stop changing. It has no opinion about diseases,
sunk costs, or spam filters — it only knows how to take raw model responses and turn them into one
number that says "is this result real, or could it be noise/missing data/the model not reading the
question."

## What you'll see here

| File | What it does |
|---|---|
| `rng.py` | A small, hand-written random number generator (SplitMix64). Used instead of Python's built-in `random` so results can be reproduced exactly, in any language, by anyone who has the same inputs. |
| `chain.py` | Hashing helpers. Turns a protocol or a panel of scenarios into a short fingerprint (`sha256`), so it's provable later that nothing was quietly edited after the fact. |
| `grammar.py` | Reads a model's raw text reply and pulls out its decision (`A` or `B`) using a fixed, dumb string rule — no AI involved in reading the answer, on purpose. |
| `schema.py` | The closed lists: every outcome, flag, and field name this project is allowed to produce. Nothing gets invented on the spot. |
| `stats.py` | The actual statistics: how big the gap is between two conditions, how much of that gap could be explained by missing responses, confidence intervals, entropy. |
| `invariants.py` | Checks a protocol's design *before* any model ever sees it — e.g. "do the two options really have equal expected value?" Catches design bugs for free, before they'd cost an API call. |
| `client.py` | The shape every model client must have (`complete()` in, a classified outcome out), and the fixed rule for what counts as a valid answer vs. a refusal vs. junk. |
| `measurement.py` | The orchestrator for the statistics: takes all the trials from one run and produces the one `measurement` record — the three validity checks, any red flags, and whether the result is clean enough to publish. |
| `pilot.py` | Runs one protocol end to end: sends every prompt, collects every response, calls `measurement.py`, writes the result to `experiments/`. |
| `verify.py` | The build check. Re-runs known, hand-checked examples and confirms this code still gets the same answer. Run this after touching anything in this folder. |
| `promote.py` | Flips a protocol's `status` from `candidate` to `admitted`, assigns it a lane, and optionally pairs it with its twin (`--twin-of`). Refuses to run without `--human-reviewed` and without an explicit `--lane` — a human, not this script, decides that a protocol is ready, which lane it belongs to, and what it pairs with. |

## Why it needs to exist, as its own folder

Everything above decides whether a number is trustworthy — not whether a scenario is well written
(that's `mutable/`) and not how an answer got fetched (that's `plumbing/`). Keeping that logic in
one place, separate from anything that changes often, means a bug fix here is something you can
reason about in isolation, and a change here is something that should be rare and deliberate.

**Before committing any change in this folder:** run `python3 -m core.measure.verify` from the
project root. It must print `11/11 [PASS]`.
