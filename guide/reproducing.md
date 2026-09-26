# Reproducing a result / submitting a comparison point

LLM-Archive's panel, decision grammar, and extraction rule are all public by design — the point of
the project isn't "trust us," it's "here is exactly what would have to be true for this number to
be wrong, go check." This page covers the two things an outside party can actually do with that:
reproduce an `open`-lane result, and run the same panel against a different model.

## Run it with an AI agent (or by hand): reproduce a published number

**For you, or for an AI agent you hand this file to.** Everything below uses only the public
repository, `github.com/LLM-Archive/llm-archive.github.io`, and Python 3.10+ (standard library only —
nothing to install). The specification is `SPEC.md` at the repository root.

> **If you are an AI agent reading this:** run the steps in order. After each step compare what you
> got with its **"You should see"** line, and **stop at the first mismatch**, reporting exactly what
> you saw instead of continuing. Every figure you report must come from a file or a command's output —
> never from memory and never estimated. Do not send anything to anyone (including the email address
> in "Submitting one") without the person you are working for asking you to.

### Step 1 — get the files

```bash
git clone https://github.com/LLM-Archive/llm-archive.github.io llm-archive
cd llm-archive
```

*No git?* Use **Code → Download ZIP** on the repository page, or fetch each file from the site's
**Downloads** page. **You should see** these files at the top level: `stability.csv`, `outcomes.csv`,
`coverage.csv`, `SPEC.md`, and the folder `open-lane/` with one `<year>.jsonl` inside.

### Step 2 — read the published numbers

```bash
python3 - <<'PY'
import csv
rows = [r for r in csv.DictReader(open("stability.csv", encoding="utf-8")) if r["record_type"] == "measurement"]
print(f'{"date":<11}{"model":<34}{"protocol":<44}{"stability":>9}  n')
for r in sorted(rows, key=lambda r: (r["run_date"], r["protocol_id"])):
    print(f'{r["run_date"]:<11}{r["subject_model_id"]:<34}{r["protocol_id"]:<44}{float(r["stability_pct"]):>8.1f}%  {r["n"]}')
PY
```

**You should see** one line per measurement. Read a line as: *"on this frozen set of 15 questions,
on this model, on that day, it kept the same decision X % of the time when the wording changed."*
Every column is explained in `guide/data-dictionary.md`.

### Step 3 — recompute the numbers we published from the raw responses

The `open`-lane protocols publish every raw response (see below), so a published score can be
re-derived from scratch instead of taken on trust. Stability = 100 − the gap between the A and B
answer distributions, counting valid answers only.

```bash
python3 - <<'PY'
import csv, json
from collections import Counter

def stability(trials):
    c = {v: Counter(t["token"] for t in trials if t["version"] == v and t["outcome"] == "valid") for v in ("A", "B")}
    n = {v: sum(c[v].values()) for v in c}
    if not n["A"] or not n["B"]:
        return None
    tokens = set(c["A"]) | set(c["B"])
    gap = sum(abs(c["A"][k] / n["A"] - c["B"][k] / n["B"]) for k in tokens) / 2
    return 100 - 100 * gap

published = {r["run_id"]: float(r["stability_pct"])
             for r in csv.DictReader(open("stability.csv", encoding="utf-8")) if r["record_type"] == "measurement"}
trials = [json.loads(l) for l in open("open-lane/2026.jsonl", encoding="utf-8") if l.strip()]

for run_id in sorted({t["run_id"] for t in trials}):
    mine = stability([t for t in trials if t["run_id"] == run_id])
    theirs = published.get(run_id)
    ok = theirs is not None and mine is not None and abs(mine - theirs) < 1e-3
    print(("OK  " if ok else "DIFF"), run_id, f"mine={mine:.3f}", f"published={theirs}")
PY
```

**You should see** `OK` on every line — one per `open`-lane run, covering both the `v0` and the
`v1` series. A `DIFF` means your recomputation disagrees with a published number: stop, do not
"fix" either side, re-clone in case your copy is stale, and if it persists report exactly the
`run_id` and both numbers (see "Submitting one"). A later year has its own file: change `2026` in
the script.

### Step 4 — (optional) test your own model

`guide/llm_archive_compare.py` is a single self-contained file that carries the 8 questions the
archive publishes in full (2 dilemmas × 4 versions), checks each against its published `sha256`,
and prints your model's answers next to the archive's own. Step-by-step, for Ollama, any
OpenAI-compatible API, or a chat window, in [`ai-assistant.md`](ai-assistant.md). This is a
spot-check, not a score: two questions cannot support a stability number.

To run the whole method on your model instead — four versions of ten scenarios, the archive's own
estimator, a noise floor, an interval, and our published numbers printed beside yours — use
`guide/sample/llm_archive_sample.py`, on a small invented practice panel; see
[`for-developers.md`](for-developers.md), steps 5 and 6. That result is never a published number.

### Step 5 — a full, like-for-like comparison (`comparison_point`)

Possible today only for someone who holds all 15 scenarios of a protocol; the published data gives
you one (see "In plain words", step 4). If you do hold them, the submission must match, exactly:
same `panel_sha256` · same `grammar_version` · same `n` per version (30 in `v0`, 120 in `v1`) · all
four versions A, A′, B, C · the full outcome distribution per version · the exact, verifiable model
id. `core/measure/client.py` defines the small interface a model has to implement,
`core/plumbing/reference_client.py` is a worked example, and `core/measure/pilot.py` runs a whole
protocol end to end. **If any requirement cannot be met, say which one and stop** — do not
substitute a paraphrase of the scenarios; it is not the same panel.

### What an agent must not conclude

- **Answering differently from the archive's models is not instability.** Which option a model
  prefers is its own call; stability is only whether *that* model's answer moved between A and B.
- **One comparison is not a ranking.** A `comparison_point` is one model, on one frozen panel, on one
  day (see "What a `comparison_point` is not", below).
- **A `DIFF` or a missing file is a finding to report, not something to work around.**

---

## In plain words: how to download our results and compare with your own model

**1. Get our published numbers.** On the site, go to **Downloads** and get `stability.csv`. It's a
spreadsheet of every measurement we've run: which question, which model, how stable the answer was (0 to
100), and whether it passed our quality checks.

**2. Get the real answers — for 2 of the 12 protocols.** Only two protocols publish their
trial-level data: `risky_choice_framing__wording` and `base_rate_neglect__wording` (marked
`lane: open` — see [`glossary.md`](glossary.md)). For those, download `open-lane/<year>.jsonl` from
Downloads: for every trial, the model's exact response, which option it picked, and which scenario
and version it belongs to. **Not the questions themselves** — each line carries a `prompt_sha256`,
not the prompt, so the file lets you verify a question you already hold, not read one you don't.
The only wording published today is the first scenario's, on the Results page (step 4 below).

**3. What you can do with that today: check our math, for your own sake.** Take the real responses
from step 2 and recompute the published stability score yourself — the formula is public
(`SPEC.md` §6, plain arithmetic, no special software needed). This is the difference
between citing a number because a website says so and knowing it's right because you re-derived it
yourself. If your number matches ours, you can now cite it with that confidence. If it doesn't,
you've caught something worth not trusting yet — worth telling us too, since a wrong published
number helps no one, but either way you now know before you relied on it (open an
[issue](https://github.com/LLM-Archive/llm-archive.github.io/issues) or write to mkalognomos@gmail.com if you want to send it our way).

**4. What you can't do yet: run your own model on our exact questions.** To fairly compare a model
of your own, you'd need the wording of all 15 questions in a protocol. Today, only the **first**
question's wording is public (shown on the Results page when you open that row) — the other 14
aren't published anywhere yet. This is a known, disclosed gap, not an oversight — see
[`SPEC.md`](https://github.com/LLM-Archive/llm-archive.github.io/blob/master/SPEC.md) §1.1. Once it's closed, this page will say so, with
step-by-step instructions for submitting your own comparison.

**5. What a result would tell you, once this opens up.** Not "is my model smarter." Only: does it
change its answer when asked the exact same question worded differently, on this one day, on this
one frozen set of 15 questions. It's never merged into the project's own chart or averaged with
anything — see "What a `comparison_point` is not" below.

## Reproducing an `open`-lane result

Two of the twelve protocols publish their real trial-level data (every response, in full), in
both the `v0` and the `v1` series: `risky_choice_framing__wording` and `base_rate_neglect__wording` (see
[`glossary.md`](glossary.md) for what `lane: open` means and why only these two). **This is not the
same as the full question wording** — see "In plain words," step 4 above, for what's actually
public today (14 of each protocol's 15 questions aren't yet).

**Where the real trial data lives:** not in a standalone protocol file — the protocol definitions are
not published, even for `open` protocols (`SPEC.md` §10). Every trial from an `open` run
reaches the public only through `open-lane/<year>.jsonl`, copied through unchanged. Each line has
`run_id` (the protocol id is embedded in it, as a substring — there is no separate `protocol_id`
field), `scenario_id`, `version`, the model's real `response_text`, the extracted `token`, and
`prompt_sha256` (a one-way hash — lets you confirm a prompt you already hold is the exact one used,
but doesn't reveal it). See [`data-dictionary.md`](data-dictionary.md) for the exact field list.

To check a specific published number:

1. Pull every trial for the `run_id` you're checking from `open-lane/<year>.jsonl`.
2. Re-run the same deterministic extraction rule described in `SPEC.md` §5 (`DECISION:
   <token>` on its own line, `token` from the protocol's closed option set) against each raw
   response.
3. Recompute `gap_pct`, `null_floor_pct`, and the three gates using the formulas in
   `SPEC.md` §6 — pure standard-library arithmetic, no numerical library required.
4. Compare against the published `stability_pct`, `gap_null_pct`, `gap_positive_pct`, and `flags`
   for that `run_id` in `stability.csv`.

If your recomputation disagrees with the published record, that's exactly the kind of finding this
project wants reported — open an [issue](https://github.com/LLM-Archive/llm-archive.github.io/issues) or write to mkalognomos@gmail.com.

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
- The same `n` per version (30 in `v0`, 120 in `v1`).
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
