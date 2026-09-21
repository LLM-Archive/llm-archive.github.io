# For researchers

This page is about what LLM-Archive's numbers mean, what they don't mean, and how to use them
responsibly in your own work. For column-level detail see [`data-dictionary.md`](data-dictionary.md);
for the full formal treatment, [`../docs/spec.md`](../docs/spec.md) §6 is the governing document —
if anything here seems to disagree with it, the spec is right.

## What the metric is, precisely

`stability_pct` answers one question: *on this frozen 15-scenario panel, did this model's decision
distribution shift when the same question was reworded in a way that shouldn't have changed the
answer?* It is a distance between two independent response distributions (total variation
distance), not a per-question agreement rate — the two wordings of a question are never shown to
the same conversation, so there is no way to say "in 9 out of 10 matched pairs it answered the
same way," and no such claim is ever published.

## What it does not claim

- **Not a measure of capability.** A very capable model can be highly unstable, and vice versa.
- **Not a measure of correctness**, for protocols with no ground truth (framing, preference-style
  decisions). Stability there is a measure of *consistency*, not of being right.
- **Not a claim that the wording change was psychologically inert.** That the change "shouldn't"
  matter is the protocol's own design claim, argued in its Method & Limits documentation — not
  something this statistic measures directly.
- **Not comparable across model families.** The frozen code refuses to draw one line across
  `model_family` boundaries. A Sonnet-series curve and a hypothetical Opus-series curve are never
  merged, because doing so would present a trend that's really an artifact of which products
  happened to get measured when.
- **Not a human-likeness score.** Any comparison to a published human result (`human_model_gap`) is
  secondary, reported only where a multi-lab or meta-analytic human finding exists for direct
  comparison, and is never aggregated or treated as a headline. Published research on this
  question has found that models can fail to reproduce human response biases at all — so a
  human-model gap and a model's self-stability are not guaranteed to be measuring the same
  underlying thing, and this project does not claim they are.

## The scope every published number carries

Every measurement's claim is exactly: *"on this frozen panel, on this model, on that day."* Never
"the model is X% stable under wording" as a general property — always "on panel `p-004`," naming
the specific panel. This isn't a hedge added after the fact; it follows from a specific, disclosed
design decision: the confidence interval is a bootstrap over responses, **conditional on the
panel**, and no generalization interval is published (`docs/spec.md` §6). A 15-scenario cluster
bootstrap would understate uncertainty, so rather than publish a falsely narrow interval, this
project publishes the raw per-scenario heterogeneity instead (`scenario_gaps_pct`,
`scenario_concentration_pct`) and lets you see directly whether a finding rides on the whole panel
or on two scenarios.

The nearest thing to a generalization signal is architectural, not statistical: **each rewording
type runs as three independent scenario-family panels**, and if all three move together, that's
much stronger evidence than any interval over 15 items could provide.

## Reading the checks, not just the headline

Before treating any `stability_pct` as meaningful, check three things the record publishes
alongside it:

1. **Did it beat the null-change control?** (`flags` doesn't include `below_surface_noise`.) If a
   punctuation-only rewording moved the answer almost as much as the real one, the "real" gap
   isn't distinguishable from surface noise.
2. **Was the model actually reading the question?** (`flags` doesn't include `not_reading`.) The
   positive control (version C) is built so the correct answer flips — if the model didn't move
   there either, nothing else about that run means much.
3. **Is `on_curve` true?** If not, read `flags` for why — the measurement is still published in
   full, just not headlined.

A model that answers randomly with a fixed probability, or that always gives the same answer
regardless of the question, would otherwise score very well on `stability_pct` alone — the entropy
series and the positive control exist specifically to catch each of those two failure modes.

## Citing this project

See [`../CITATION.cff`](../CITATION.cff). Where possible, cite a specific `run_id` and
`protocol_id`, not the project as a whole — a citation naming a specific, dated, hash-verified
measurement is falsifiable in a way that "LLM-Archive found..." is not.

## Using the data

```python
import pandas as pd

stability = pd.read_csv("stability.csv")
# JSON-typed columns (outcomes, decisions, flags, scenario_gaps_pct, scenario_spread_pct, gates)
# need one extra step:
import json
stability["flags"] = stability["flags"].apply(json.loads)

# Only look at measurements that made the main curve:
on_curve = stability[stability["on_curve"]]

# Never mix series or model families in one comparison — the project itself won't:
sonnet_wording = on_curve[
    (on_curve["model_family"] == "claude-sonnet") & (on_curve["rewording_type"] == "wording")
]
```

Or read `croissant.json` with any [MLCommons Croissant](https://mlcommons.org/croissant/)-aware
tool for schema-driven loading, including automatic typing of every column.

## Reporting a factual issue

If a human-comparison figure misquotes a study you're associated with, or you've found an actual
defect in the frozen estimator, see [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — there's a
specific process for each, and a specific, honest statement of what's built and what's still
manual today.

## Running your own comparison

You can run the identical public panel against a model of your own and have the result published
as a `comparison_point`, shown on its own line, on its own date, never merged into the main chart
or read as a ranking. See [`reproducing.md`](reproducing.md) for exactly what's required.
