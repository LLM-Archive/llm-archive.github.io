# LLM-Archive

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22881127.svg)](https://doi.org/10.5281/zenodo.22881127)

A long-running measurement project: does a commercial AI model's decision change when the
same question is asked in different words — and how would we know if our own way of checking that
had broken?

**Live site:** [llm-archive.github.io](https://llm-archive.github.io/) ·
**Guide:** [llm-archive.github.io/guide](https://llm-archive.github.io/guide/) ·
**Spec:** [`SPEC.md`](SPEC.md)

## What you'll find here

This repository *is* the public site and the public dataset — everything here ships as-is to
`https://llm-archive.github.io/`.

| Path | What it is |
|---|---|
| `index.html`, `website/` | The public site (`index.html` is generated; `website/` holds its source template) |
| `stability.csv` | One row per measurement — the headline `stability_pct`, its confidence interval, the noise floor, entropy, drop bounds, flags, and the full outcome distribution. Column-by-column reference: [`guide/data-dictionary.html`](https://llm-archive.github.io/guide/data-dictionary.html) |
| `outcomes.csv` | The same data, unpacked to one row per `(run_id, version, outcome)` — easier to group/plot |
| `open-lane/<year>.jsonl` | Full, word-for-word trial text and raw responses, but **only** for the two protocols whose wording is public (see "What you can and can't reproduce" below) |
| `croissant.json` | Machine-readable schema for `stability.csv`/`outcomes.csv` (MLCommons Croissant format) |
| `guide/` | Plain-English docs: for researchers, for developers, reproducing a result, a data dictionary, a glossary, an FAQ |
| `SPEC.md` | The frozen specification — exactly what's measured, how, and what every published field means |
| `core/measure/` | The estimator itself — frozen forever as of the first real measurement, so every number is reproducible against a fixed target |
| `core/schedule/`, `core/plumbing/`, `core/testdata/`, `core/instrument/` | Scheduling, rendering, test vectors, and the daily self-check that decides whether a given day's data can be trusted |
| `cadence.yaml`, `subject_models.yaml` | The measurement schedule and which model(s) are currently tracked |
| `CITATION.cff` | How to cite a specific measurement (preferred) or the project |
| `LICENSE.md` | Full license breakdown (see below) |

**What's actually measured:** 12 protocols — 3 scenario families (`risky_choice_framing`,
`sunk_cost_fallacy`, `base_rate_neglect`) × 4 rewording types (`wording`, `anchoring`, `order`,
`default`) — each a frozen, 15-scenario panel run at `n=30` per version. Coverage so far is
disclosed on the site's homepage (the "Currently measuring" bar) rather than assumed: a project
built around catching silent breakage doesn't get to silently imply more coverage than exists.

## What to check before trusting a number

Every `stability_pct` comes with the evidence needed to judge it, not just the headline:

1. **`flags`** — if it includes `below_surface_noise`, the "real" reword didn't move the answer
   any more than a punctuation-only null-change control did. If it includes `not_reading`, the
   model didn't respond to the positive control either, which means nothing else about that run is
   very informative.
2. **`on_curve`** — `false` means the measurement is still published in full, just not headlined,
   for one of the reasons in `flags`.
3. **`lane`** — `open` means the full scenario text and every raw response are in
   `open-lane/<year>.jsonl`, independently checkable end to end. `guard` means only outcome
   categories are public (the wording must stay unseen for a contamination check to keep working);
   `sealed` means existence and count only, by design, permanently.
4. **`panel_sha256` / `protocol_sha256`** — a fingerprint of the exact scenario content and full
   protocol used. If you re-derive a result from `open-lane/`, these should match.
5. **`scenario_gaps_pct`** — the 15 individual scenario-level gaps behind the one headline number.
   A `stability_pct` that rests on two scenarios out of fifteen is a different, weaker finding than
   one that holds evenly across the panel, even at the same headline value.

Full treatment, including what the metric deliberately does **not** claim (not a capability score,
not comparable across model families, not a human-likeness score):
[`guide/for-researchers.html`](https://llm-archive.github.io/guide/for-researchers.html).

## How to run it

```bash
git clone https://github.com/LLM-Archive/llm-archive.github.io.git
cd llm-archive.github.io
```

**Load the data** (no dependency beyond `pandas`, or none at all via `croissant.json`):

```python
import pandas as pd, json

stability = pd.read_csv("stability.csv")
stability["flags"] = stability["flags"].apply(json.loads)  # a few columns are JSON-in-a-cell

on_curve = stability[stability["on_curve"]]                # the headlined subset
```

**Check the pipeline itself** (pure standard library, no numerical dependency by design):

```bash
python3 -m core.measure.verify          # the frozen estimator against its golden test vectors
python3 -m core.plumbing.render verify  # the CSV/JSON/site-build logic
python3 -m core.instrument.verify       # the daily "can today's data be trusted" self-check
```

**Reproduce a published number**, for the two `open`-lane protocols whose full text is public
(`risky_choice_framing__wording__v0`, `base_rate_neglect__wording__v0`): pull that `run_id`'s
trials from `open-lane/<year>.jsonl`, re-run the same deterministic `DECISION: <token>` extraction
rule, and recompute `gap_pct`/`null_floor_pct`/the three gates from `SPEC.md` §6. Step by step:
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html).

**Run the same panel against your own model.** Because the panel, grammar, and extraction rule are
public, you can run the identical frozen panel against a model of your own choosing and have the
result published as a `comparison_point` — one model, one panel, one day, never merged into the
main chart or read as a ranking. Requirements and how to submit:
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html).

## How to get value out of the measurements

- **Cite a specific `run_id` and `protocol_id`**, not the project as a whole — see
  [`CITATION.cff`](CITATION.cff). A citation naming a specific, dated, hash-verified measurement is
  falsifiable in a way that "LLM-Archive found..." is not.
- **Build on the data directly** — `stability.csv`/`outcomes.csv`/`open-lane/` are CC-BY-4.0:
  reuse, merge with your own datasets, or build on them commercially, with attribution.
- **Watch a specific model or family over time** rather than a single snapshot — the point of the
  monthly cadence is the series, not any one measurement; a single point is one frame of a video.
- **Never merge across `model_family` boundaries or blend a `comparison_point` into the main
  series** — the project's own frozen code refuses to do either, for the same reason: it would
  present a trend that's really an artifact of which products happened to get measured when.
- **Audit or extend the pipeline itself** — `core/measure/` is frozen and checked against golden
  vectors specifically so it can be re-implemented from scratch, in any language, and checked
  bit-for-bit. Start at [`guide/for-developers.html`](https://llm-archive.github.io/guide/for-developers.html).

## License

This project doesn't use one single license — different parts of it are licensed differently on
purpose:

| What | License |
|---|---|
| Code (`core/`) | AGPL-3.0 |
| Data (the CSVs, `open-lane/`) | CC-BY-4.0 |
| Prose (the site, `guide/`, `SPEC.md`) | CC-BY-SA 4.0 |

See [`LICENSE.md`](LICENSE.md) for the reasoning behind each choice.

## How this repo is maintained

Nothing here is hand-edited. This repo is regenerated from a private working repository on every
release and pushed as a fresh commit — it shares no git history with that repo, and never receives
anything outside `SPEC.md` §10's published-file list (development notes, the private question bank,
and unpublished protocols all stay out). If something here looks wrong, it's a bug in the generator
or the underlying data, not a one-off edit worth patching directly.

## Questions, corrections, or reproducing a result

Start with [`guide/faq.html`](https://llm-archive.github.io/guide/faq.html) and
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html). Contact details
for anything not covered there are in `SPEC.md`.
