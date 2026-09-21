# For developers

This page is for anyone reading or extending the codebase — auditing the estimator, building a
new source adapter, or just checking that the published numbers really come from the code they
claim to. For what each published field means, see [`data-dictionary.md`](data-dictionary.md); for
the authoritative behavior, [`../docs/spec.md`](../docs/spec.md) always wins over this page.

## The trust boundary, in code terms

The single most important fact about this codebase: **`core/measure/` is frozen forever**, as of
commit #1. It contains the estimator, the two statistical checks (null-change and positive
control), the noise floor, the entropy series, the bootstrap confidence interval, the deterministic
decision-extraction grammar, the structural admission gate, and the hash chain. Every value in it
is checked against hand-derived golden test vectors (`core/testdata/vectors/`) so it can be
re-implemented from scratch, in any language, and checked bit-for-bit against this project's own
numbers — that reproducibility is the entire point of freezing it.

| Folder | Can change | How |
|---|---|---|
| `core/measure/` | Never (after commit #1) | A confirmed bug is fixed by declaring a `core_change` and a disclosed discontinuity — never a silent patch, because that would make earlier measurements incomparable without saying so. |
| `core/plumbing/` | Yes, reviewed | Renderer, source adapters (any client implementing `core/measure/client.py`'s contract), templates, formatting. Never a number or a key. |
| `core/budget/`, `core/schedule/`, `core/instrument/` | Yes, reviewed | Guardrails around the frozen core: spending ceiling, "is this due today," and the daily reference-model self-check. Each reads already-computed observations — none of them runs a model itself. |
| `mutable/`, `protocols/` | Owner only, never external | Scenario content and protocol generation. Kept private for a structural reason (contamination), not a preference — see `../CONTRIBUTING.md`. |

## Architecture in one pass

```text
mutable/build_protocol_<family>__<type>.py
    → writes protocols/<family>__<type>__v0.json   (frozen text + declared thresholds)

core/measure/pilot.py
    → runs a protocol end to end against a Client (fake or real)
    → core/measure/grammar.py extracts a DECISION: token deterministically
    → core/measure/measurement.py turns 120 trials into one measurement record:
        - core/measure/stats.py computes gap_pct, the noise floor, entropy, drop bounds,
          the three gates, and the bootstrap CI
        - core/measure/invariants.py has already gate-1-checked the protocol at admission time
    → writes experiments/<run_id>/{trials.jsonl, run.json, measurement.json}   (append-only)

core/plumbing/render.py
    → reads experiments/ → writes stability.csv, outcomes.csv, open-lane/<year>.jsonl,
      croissant.json, and (build-site) website/index.html from website/template.html
```

`core/budget/`, `core/schedule/`, and `core/instrument/` sit alongside this pipeline rather than
inside it: `schedule.py` decides *what's due*, `budget.py` decides *whether it's allowed to
spend*, and `instrument.py` decides *whether today's pipeline run can be trusted at all* — none of
the three runs a model or touches `core/measure/`'s own logic.

## The `Client` contract

Every source of model responses — the synthetic `fake_client.py` used for tests, and the real
subject-model / reference-model clients — implements the same interface defined in
`core/measure/client.py`. This is what lets a protocol run identically against a zero-cost
synthetic model during development and a real, billed API in production, with zero changes to
`core/measure/` itself. If you're adding a new subject model, this is the one file whose contract
you need to satisfy; everything downstream (grammar, statistics, rendering) is already generic
over it.

## Build checks

Run the relevant one after any change in that area — each is independent and each must fully pass:

```bash
python3 -m core.measure.verify          # 11/11 — golden vectors, gate 1 on every protocol,
                                         # twin-pairing well-formedness, the _pct naming rule
python3 -m core.budget.verify           # 3/3  — only after touching core/budget/ or budget.json
python3 -m core.schedule.verify         # 2/2  — only after touching core/schedule/
python3 -m core.instrument.verify       # 3/3  — only after touching core/instrument/
python3 -m core.plumbing.render verify  # renderer's CSV/JSON/site-build output

# End-to-end, zero cost, zero network:
python3 -m core.measure.pilot protocols/<file>.json --client fake --out <scratch dir>

# End-to-end against the real commercial model (spends real, billed API calls):
python3 -m core.measure.pilot protocols/<file>.json --client anthropic --model-id <id> --model-family <family> --out <scratch dir>
```

`core/measure/verify.py` does not exercise a real client and never spends money — it replays fixed
vectors. The synthetic `FakeClient` exercises the full pipeline shape but picks answers by version
and scenario number only, never by reading the actual scenario text, so it can confirm the plumbing
works without ever telling you whether a panel's *design* actually works. Only a real model run —
or a `comparison_point` submitted by someone else, see [`reproducing.md`](reproducing.md) — can
answer that.

## Why `numpy` is banned in `core/measure/`

Enforced by a CI assertion, not just convention: `core/measure/` is pure standard library so the
estimator can be re-read and re-implemented decades from now without depending on a numerical
library whose own future version might not build. If you're extending statistics logic, this
constraint doesn't relax just because a numpy one-liner would be shorter.

## Extending the schema

`core/measure/schema.py` holds every closed list this project uses (outcome types, flags, record
types, lanes, gap causes, and every published field's name and type). These are closed on purpose:
a coverage gap or a flag can only ever take a value from a pre-declared list, never something
invented on the spot after the fact. Adding a value to any list here is a real, visible schema
change — it changes `schema_sha256`, and per `../docs/spec.md` §13, additive evolution follows
three rules: names are never reused, fields are never deleted (only dated as superseded), and a new
required field ships with a declared default so old records remain valid.

## Reporting a bug

See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — the process differs depending on whether the bug
is in frozen code (`core/measure/`) or reviewable code (`core/plumbing/` and the guardrail
modules).
