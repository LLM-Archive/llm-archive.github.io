# For developers

This page is for anyone who wants to run LLM-Archive on their own machine — to check that the
published numbers really come from the published code, or to try the method on **their own
model** — and for anyone auditing or extending the code. Everything here uses only the public
repository, `github.com/LLM-Archive/llm-archive.github.io`; nothing on this page needs access to
anything else. For what each published column means, see [`data-dictionary.md`](data-dictionary.md);
for the authoritative behavior, the [specification](https://github.com/LLM-Archive/llm-archive.github.io/blob/master/SPEC.md)
always wins over this page.

## Quickstart: run it on your machine

About ten minutes. You need **Python 3.10 or newer** and **git**. There is nothing to install: the
code is pure standard library.

### 1. Get the code

```bash
git clone https://github.com/LLM-Archive/llm-archive.github.io llm-archive
cd llm-archive
```

*No git?* Use **Code → Download ZIP** on the repository page and unzip it. **You should see** these
at the top level: `core/`, `guide/`, `open-lane/`, `stability.csv`, `outcomes.csv`, `coverage.csv`,
`SPEC.md`.

### 2. Check your copy works

```bash
python3 -m core.instrument.verify
```

**You should see** a list of `[PASS]` lines and, last, `core/instrument/verify.py: all vectors pass`.
If Python says the command is not found, try `python` instead of `python3`, and check that
`python --version` is 3.10 or newer.

### 3. Check a published number yourself

Follow **Step 3** of [`reproducing.md`](reproducing.md): a short script that recomputes the
stability of every `open`-lane run from its raw responses and compares it with `stability.csv`.
**You should see** `OK` on every line. This is the quickest way to convince yourself the published
data and the published formula agree, before you compare anything of your own with them.

### 4. Ask your model our real questions, and see ours next to yours

`guide/llm_archive_compare.py` asks **your** model the 8 questions LLM-Archive publishes in full
(2 dilemmas × 4 versions), extracts each answer with the archive's own rule, and prints your
model's answers next to the ones the archive's models gave. This is the one place where you and we
answer *exactly the same questions*. Pick the line that matches where your model runs:

```bash
# A model on your own machine, through Ollama (ollama.com). Start Ollama first.
python3 guide/llm_archive_compare.py --model llama3.2

# Any OpenAI-compatible API: OpenAI, vLLM, LM Studio, llama.cpp's server, most hosted providers.
export OPENAI_API_KEY=your-key-here
python3 guide/llm_archive_compare.py --openai --model gpt-4o-mini
#   Not OpenAI itself? Add --base-url, for example  --base-url http://localhost:1234/v1

# A model you can only reach through a chat window.
python3 guide/llm_archive_compare.py --print-prompts my_run
#   Ask each .txt in my_run/ in a FRESH conversation (no system prompt, no earlier messages),
#   save each full reply in my_run/answers/ under the same file name, then:
python3 guide/llm_archive_compare.py --read-answers my_run
```

Add `--name my-model` to control how your model is labelled, and end the command with
`> results.txt` to keep a copy: the report is printed to the terminal and not saved anywhere else.
Every call is sent at temperature 0.

**You should see**, first, `8 questions, each matching its published sha256`, then one block per
dilemma:

```text
  B   same meaning, new wording (this is the measurement)
      my-model: A
      claude-sonnet-5: B
      qwen2.5-1.5b-instruct-q4_k_m: A
```

and, at the end of each block, one plain sentence telling you whether your model kept or changed
its decision when the wording changed.

### 5. Run the whole method on your model (the practice panel)

Two questions are a spot-check, not a measurement. To run the real mechanism — four versions of
every scenario, many repetitions, the archive's own extraction rule and estimator, a noise floor, a
confidence interval and the quality flags — use the practice panel:

```bash
python3 guide/sample/llm_archive_sample.py --fake     # first: no model at all, just to see what a run prints
python3 guide/sample/llm_archive_sample.py            # then: your model, with the defaults below
```

It asks 80 questions (10 scenarios × 4 versions × 2 repetitions, in a fixed shuffled order) and
prints one `#` per answer. How long that takes depends on your hardware and model; a small model on
a laptop is usually a matter of minutes. If your server is not reachable, the very first call fails
and the script stops before writing anything, telling you which address it tried.

Every setting has a default, and every default can be changed with the flag beside it:

| Setting | Default | Change it when |
|---|---|---|
| `--model` | `llama3.2` | your server knows the model under another name (Ollama: `ollama list`) |
| `--base-url` | `http://localhost:11434` (Ollama) | your server is elsewhere, e.g. `http://localhost:1234/v1` with `--openai` for LM Studio |
| `--openai` | off | your server speaks the OpenAI-compatible API instead of Ollama's |
| `--api-key-env` | `OPENAI_API_KEY` | the name of the environment variable holding your key; a local server accepts any value |
| `--out` | `sample_results` | you want the results somewhere else |
| `--date` | today | you want to re-run on the same day (a finished run is never overwritten) |

```bash
python3 guide/sample/llm_archive_sample.py --model qwen2.5:1.5b                                  # another Ollama model
python3 guide/sample/llm_archive_sample.py --openai --model my-model --base-url http://localhost:1234/v1
```

**You should see**, at the end, a block like this (this one is from `--fake`, a synthetic model that
says nothing about any real one):

```text
stability            95.0%   (95% interval 65.0% to 100.0%)
noise floor          12.3%   (a gap this small is expected from sampling alone)
cosmetic edit  A-A'  gap 0.0%   (should be near zero: this is the null change)
reworded       A-B   gap 5.0%   (this is the measurement)
different      A-C   gap 65.0%   (should be large: proof the model reads the question)
```

followed by how many times each version was answered A or B, the quality flags with a plain-words
explanation of each, and **a table of LLM-Archive's own published measurements of the same kind of
question** (risky choice, reworded) with yours as the first row. Every reply and the full
measurement are saved in `sample_results/<run_id>/`: `trials.jsonl` holds each reply exactly as your
model wrote it, and `measurement.json` holds the record, with the same field names as a row of
`stability.csv` ([`data-dictionary.md`](data-dictionary.md)), except `api_surface_sha`, which only applies to runs
against a commercial API.

### 6. Read your result, and compare it with ours

Read it in this order, the same order the archive reads its own numbers:

1. **Is the measurement even valid?** The `A-A'` gap (a cosmetic edit) should be near zero and the
   `A-C` gap (a genuinely different question) should be large. If not, the flags say which failed:
   `below_surface_noise` (the reworded question moved the answer no more than the cosmetic edit did),
   or `not_reading` (the model did not react to the different question).
2. **Then read stability against the noise floor.** With this few replies, even a model that never
   changes its answer shows a gap of about the noise floor. A difference smaller than the floor, or
   inside the interval, is not a finding.
3. **Then compare with ours.** Two comparisons are honest, and they answer different things:
   - *Same questions, yours and ours:* step 4. Which option each model chose on the 8 published
     questions.
   - *Same method, same kind of question:* the table in step 5. It shows our published stability,
     interval and noise floor for risky-choice rewording next to yours. The panel is a different one,
     so read the rows side by side, not as a score. Overlapping intervals mean the difference is
     within what sampling noise alone can produce.

To compare two models of your own, run the script once per model and read the two
`measurement.json` files. The practice panel is deterministic: the same model, the same day and the
same settings ask the same questions in the same order.

Two things this result does not support:

- **Answering differently from the archive's models is not instability.** Which option a model
  prefers is its own call. Only whether *your* model's answer moved between A and B counts.
- **It is not a published measurement and never becomes one.** The practice panel's ten scenarios
  are invented for this purpose (`guide/sample/build_sample_protocol.py` rebuilds them from scratch and
  they pass the same structural check as the real panels), so its result cannot be cited as an
  LLM-Archive number, cannot be a `comparison_point`, and cannot be ranked against anything.

If a reply is scored as lost, the script counts it, never guesses: a reply must end with a line of
the form `DECISION: A` or `DECISION: B`. Look at the reply in `trials.jsonl` to see what your model
wrote instead, and see `outcome` there for why it was lost.

### 7. What you can't do yet

A real, citable `stability_pct` on your model needs all 15 scenarios of one of the archive's own
protocols, and those are not published: only the 8 questions above are. This is a disclosed gap,
not an oversight; see [`reproducing.md`](reproducing.md) for exactly what a like-for-like comparison
would require and how one is submitted.

## The trust boundary, in code terms

The single most important fact about this codebase: **`core/measure/` is frozen forever**, as of
the first real measurement. It contains the estimator, the two statistical checks (null-change and
positive control), the noise floor, the entropy series, the bootstrap confidence interval, the
deterministic decision-extraction grammar, the structural admission gate, and the hash chain. Every
value in it is checked against hand-derived golden test vectors (`core/testdata/vectors/`) so it can
be re-implemented from scratch, in any language, and checked bit-for-bit against this project's own
numbers — that reproducibility is the entire point of freezing it.

| Folder | Can it change? | How |
|---|---|---|
| `core/measure/` | Never | A confirmed bug is fixed by declaring a `core_change` and a disclosed discontinuity — never a silent patch, because that would make earlier measurements incomparable without saying so. |
| `core/plumbing/` | Yes, reviewed | Renderer, source adapters (any client implementing `core/measure/client.py`'s contract), templates, formatting. Never a number or a key. |
| `core/schedule/`, `core/instrument/` | Yes, reviewed | "Is a job due today" and the daily reference-model self-check. Each reads already-computed observations — none of them runs a model itself. |

Scenario-building code and unpublished protocol files are kept out of this repository on purpose:
a contamination check only works while that text stays unseen (see the specification's section on
what gets published). This is why the `open-lane/` data and the 8 published questions are the
public entry points.

## Architecture in one pass

```text
a protocol            frozen text + declared thresholds
    → core/measure/pilot.py runs it end to end against a Client (fake or real) — in the project's
      own repository; this public copy has no core/budget/, which pilot.py imports, so here
      guide/sample/llm_archive_sample.py runs the same steps with the same core/measure/ code
    → core/measure/grammar_v2.py extracts a DECISION: token deterministically
    → core/measure/measurement.py turns the trials into one measurement record:
        - core/measure/stats.py computes gap_pct, the noise floor, entropy, drop bounds,
          the three gates, and the bootstrap CI
        - core/measure/invariants.py has already gate-1-checked the protocol at admission time
    → an append-only record of every trial and one measurement

core/plumbing/render.py
    → turns those records into stability.csv, outcomes.csv, open-lane/<year>.jsonl,
      croissant.json, coverage.csv and the site
```

`core/schedule/` and `core/instrument/` sit alongside this pipeline rather than inside it:
`schedule.py` decides *what's due* and `instrument.py` decides *whether today's pipeline run can be
trusted at all* — neither runs a model or touches `core/measure/`'s own logic.

## The `Client` contract

Every source of model responses — the synthetic `fake_client.py` used for tests, the open-weights
reference model in `reference_client.py`, and any commercial API client — implements the same small
interface defined in `core/measure/client.py`: a `complete(prompt, meta)` method that returns a
`Response` (text, stop reason, the model id the server reported, or an error). This is what lets a
protocol run identically against a zero-cost synthetic model during development and a real, billed
API, with zero changes to `core/measure/`. If you are adding a new model, this is the one file whose
contract you need to satisfy, and `core/plumbing/reference_client.py` is a worked example;
everything downstream (grammar, statistics, rendering) is already generic over it.

`client.classify()` is where a raw response becomes one outcome from a closed list — `valid`,
`unparseable`, `refused`, `truncated`, `empty`, `off_format`, `blocked_upstream` — in a fixed
order of precedence, so a truncated reply is never scored as a decision just because the model
happened to finish the important line first.

## Build checks

```bash
python3 -m core.instrument.verify       # the daily "can today's data be trusted" self-check, on fixed vectors
python3 -m core.plumbing.conformance selftest   # the rewording-type checker, on its own examples
```

Both replay fixed examples: they never call a model, never touch the network, and never spend
anything. The synthetic `FakeClient` exercises the full pipeline shape but picks answers by
version and scenario number only, never by reading the actual scenario text, so it can confirm the
plumbing works without ever telling you whether a panel's *design* works. Only a real model run — or
a `comparison_point` submitted by someone else, see [`reproducing.md`](reproducing.md) — can answer
that.

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
change — it changes `schema_sha256`, and per the specification's §13, additive evolution follows
three rules: names are never reused, fields are never deleted (only dated as superseded), and a new
required field ships with a declared default so old records remain valid.

## Reporting a bug

Open an issue at
[github.com/LLM-Archive/llm-archive.github.io/issues](https://github.com/LLM-Archive/llm-archive.github.io/issues)
with the file or `run_id` and what you expected instead. A bug in `core/measure/` is not patched in
place: it is fixed by a disclosed change, and the affected part of the series is marked as a break.
A bug in `core/plumbing/`, `core/schedule/` or `core/instrument/` is an ordinary reviewed fix.
