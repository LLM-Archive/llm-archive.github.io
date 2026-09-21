# `core/testdata/vectors/`

## What you'll see here

Two JSON files of hand-worked examples, each with a known correct answer:

- **`grammar_vectors.json`** — 13 cases for `core/measure/grammar.py`'s decision extractor. Covers
  the normal case, markdown emphasis around the answer, trailing punctuation, empty/whitespace-only
  replies, a `None` response, and a case that proves Greek letters are never folded into Latin
  look-alikes (important because this project measures a model that answers in both scripts).
- **`stats_vectors.json`** — 7 categories for `core/measure/stats.py`: the gap measure (`tvd`), the
  worst-case-missing-data bound (`drop_bound_pct`), entropy, the noise floor, and the three pass/fail
  gates (`gate_ab`, `gate_aa`, `gate_ac`). Each value here was worked out by hand first, then checked
  against the code — not the other way around.

## Why it needs to exist

These are the ground truth. `core/measure/verify.py` loads both files, re-runs the real code on
each case, and fails loudly if even one output has drifted from the expected value. Without this,
a subtle bug in the statistics — the kind that doesn't crash, just quietly computes the wrong
number — could sit in `core/measure/` for months before anyone noticed. **Never edit an existing
expected value in either file to make a failing test pass** — work out why the code disagrees with
the hand-checked answer instead.
