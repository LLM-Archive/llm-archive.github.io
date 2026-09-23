# Reproducing a result / submitting a comparison point

LLM-Archive's panel, decision grammar, and extraction rule are all public by design — the point of
the project isn't "trust us," it's "here is exactly what would have to be true for this number to
be wrong, go check." This page covers the two things an outside party can actually do with that:
reproduce an `open`-lane result, and run the same panel against a different model.

## In plain words: how to download our results and compare with your own model

**1. Get our published numbers.** On the site, go to **Downloads** and get `stability.csv`. It's a
spreadsheet of every test we've run: which question, which model, how stable the answer was (0 to
100), and whether it passed our quality checks.

**2. Get the real questions and answers — for 2 of the 12 tests.** Only two tests publish their
actual wording: `risky_choice_framing__wording` and `base_rate_neglect__wording` (marked
`lane: open` — see [`glossary.md`](glossary.md)). For those, download `open-lane/<year>.jsonl` from
Downloads: every real question the model was asked, its exact response, and which option it
picked.

**3. What you can do with that today: check our math, for your own sake.** Take the real responses
from step 2 and recompute the published stability score yourself — the formula is public
(`../docs/spec.md` §6, plain arithmetic, no special software needed). This is the difference
between citing a number because a website says so and knowing it's right because you re-derived it
yourself. If your number matches ours, you can now cite it with that confidence. If it doesn't,
you've caught something worth not trusting yet — worth telling us too, since a wrong published
number helps no one, but either way you now know before you relied on it (see
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) if you want to send it our way).

**4. What you can't do yet: run your own model on our exact questions.** To fairly compare a model
of your own, you'd need the wording of all 15 questions in a test. Today, only the **first**
question's wording is public (shown on the Results page when you open that row) — the other 14
aren't published anywhere yet. This is a known, disclosed gap, not an oversight — see
[`../docs/spec.md`](../docs/spec.md) §1.1. Once it's closed, this page will say so, with
step-by-step instructions for submitting your own comparison.

**5. What a result would tell you, once this opens up.** Not "is my model smarter." Only: does it
change its answer when asked the exact same question worded differently, on this one day, on this
one frozen set of 15 questions. It's never merged into the project's own chart or averaged with
anything — see "What a `comparison_point` is not" below.

## Reproducing an `open`-lane result

Two of the twelve v0 protocols publish their real trial-level data (every response, in full):
`risky_choice_framing__wording__v0` and `base_rate_neglect__wording__v0` (see
[`glossary.md`](glossary.md) for what `lane: open` means and why only these two). **This is not the
same as the full question wording** — see "In plain words," step 4 above, for what's actually
public today (14 of each test's 15 questions aren't yet).

**Where the real trial data lives:** not in a standalone protocol file — `protocols/*.json` stays in
the private repo even for `open` protocols (`../docs/spec.md` §10). Every trial from an `open` run
reaches the public only through `open-lane/<year>.jsonl`, copied through unchanged. Each line has
`run_id` (the protocol id is embedded in it, as a substring — there is no separate `protocol_id`
field), `scenario_id`, `version`, the model's real `response_text`, the extracted `token`, and
`prompt_sha256` (a one-way hash — lets you confirm a prompt you already hold is the exact one used,
but doesn't reveal it). See [`data-dictionary.md`](data-dictionary.md) for the exact field list.

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

Today, since the structured submission path isn't built yet, send the run's full data (matching
every requirement above) to <mkalognomos@gmail.com>. This is expected to move to a submission path
against the public repository (`github.com/LLM-Archive/llm-archive.github.io`) — check back here,
since this page will be updated rather than left describing a process that no longer matches
reality.
