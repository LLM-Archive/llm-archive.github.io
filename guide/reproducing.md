# Reproducing a result / submitting a comparison point

LLM-Archive's panel, decision grammar, and extraction rule are all public by design — the point of
the project isn't "trust us," it's "here is exactly what would have to be true for this number to
be wrong, go check." This page covers the two things an outside party can actually do with that:
reproduce an `open`-lane result, and run the same panel against a different model.

## Reproducing an `open`-lane result

Two of the twelve v0 protocols publish their complete, word-for-word trial text:
`risky_choice_framing__wording__v0` and `base_rate_neglect__wording__v0` (see
[`glossary.md`](glossary.md) for what `lane: open` means and why only these two).

**Where the text actually lives:** not as a standalone protocol file — `protocols/*.json` stays in
the private repo even for `open` protocols (`../docs/spec.md` §10). The exact wording reaches the
public only through `open-lane/<year>.jsonl`: every trial from an `open` run, copied through
unchanged. To reconstruct the four versions (A / A′ / B / C) of a given scenario, filter that file
by `protocol_id` and read the `version` and prompt-text fields directly off the trial records — see
[`data-dictionary.md`](data-dictionary.md) for the exact fields.

To check a specific published number:

1. Pull every trial for the `run_id` you're checking from `open-lane/<year>.jsonl`.
2. Re-run the same deterministic extraction rule described in `../docs/spec.md` §5 (`DECISION:
   <token>` on its own line, `token` from the protocol's closed option set) against each raw
   response.
3. Recompute `gap_pct`, `null_floor_pct`, and the three gates using the formulas in
   `../docs/spec.md` §6 — pure standard-library arithmetic, no numerical library required.
4. Compare against the published `stability_pct`, `gap_null_pct`, `gap_positive_pct`, and `flags`
   for that `run_id` in `stability.csv`.

If your recomputation disagrees with the published record, that's exactly the kind of finding this
project wants reported — see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Running the panel against your own model — a `comparison_point`

Because the panel, grammar, and extraction rule are public, anyone can run the *same* frozen
15-scenario panel against a model of their own choosing. If you do, the result can be published as
a `comparison_point` — but only if it's a genuinely like-for-like run. To be accepted, a submission
needs **all** of the following, matching exactly:

- The same `panel_sha256` as the protocol you're comparing against (i.e., you used the real
  scenario content, not a paraphrase of it — for a `guard`-lane protocol this means you cannot
  submit a comparison point at all, since its wording isn't public).
- The same `grammar_version` — the identical `DECISION: <token>` extraction rule, applied the same
  way.
- The same `n` per version (30 in v0).
- **All four versions** (A, A′, B, C) — not just the headline A/B contrast. A submission missing
  the null-change or positive control can't be checked for the same validity issues every project
  measurement is checked for.
- The full outcome distribution per version (valid / unparseable / refused / etc.), not just a
  final score — so `u`, the drop bound, and the gates can be computed for your run exactly as they
  are for the project's own.
- The exact model id you ran, verifiably.

## What a `comparison_point` is not

This is stated plainly because it's the most likely thing to be misused: a `comparison_point` is
**one model, on one frozen 15-scenario panel, on one day.** It is never merged into the main chart,
never averaged with anything, and never treated as a ranking. It appears in its own table, with its
own date, next to — never blended with — the project's own multi-year series. **No one is entitled
to conclude "my model beat every AI"** from a single comparison point, and any presentation of one
that implies that is a misuse of the data, not something the project's own methodology supports.

It is also strictly read-only from the project's perspective: submitting one never triggers a
project-run measurement and never changes what's on the regular monthly rotation.

## Submitting one

Today (pre-launch), send the run's full data (matching every requirement above) to
mkalognomos@gmail.com. Once the project is public, this is expected to move to a structured
submission path against the public repository — check back here, since this page will be updated
rather than left describing a process that no longer matches reality.
