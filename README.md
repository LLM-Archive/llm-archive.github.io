# LLM-Archive

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22881127.svg)](https://doi.org/10.5281/zenodo.22881127)

A long-running measurement project: does a commercial AI model's decision change when the
same question is asked in different words — and how would we know if our own way of checking that
had broken?

**Live site:** [llm-archive.github.io](https://llm-archive.github.io/) ·
**Guide:** [llm-archive.github.io/guide](https://llm-archive.github.io/guide/) ·
**Spec:** [`SPEC.md`](SPEC.md)

This repository is the public site and the public dataset, and everything in it is free to use.
Start with the [guide](https://llm-archive.github.io/guide/) or, if you would rather just run
something, with "How to run it" below.

## Why it matters

People increasingly hand real decisions to AI models, and the models behind a product name change
without notice. If the same question, worded differently but meaning the same thing, gets a
different decision, then the answer depends on phrasing rather than on the question, and a model
update can shift that without anyone announcing it. LLM-Archive keeps an open, dated, checkable
record of exactly that one property, and of the evidence that the measuring itself still works.

- **Cite a specific measurement**, not the project as a whole: a `run_id` and its `protocol_id`
  (see [`CITATION.cff`](CITATION.cff)). A dated, hash-verified measurement can be checked; "LLM-Archive
  found..." cannot.
- **Watch a model over time** rather than in a single snapshot. The value is the series; one
  measurement is one frame of a video.
- **Build on the data directly.** `stability.csv`, `outcomes.csv` and `open-lane/` are CC-BY-4.0:
  reuse them, merge them with your own data, or build on them commercially, with attribution.
- **Check the numbers instead of trusting them.** The code that produces every number is here, is
  pure standard library, and is checked against fixed test vectors, so you can re-derive a result or
  re-implement it in any language.
- **Read a number with its evidence.** Every `stability_pct` comes with a noise floor, an interval
  and flags, and a flagged measurement is published, never deleted. What the number does *not*
  claim (not a capability score, not a ranking, not comparable across model lines) is in
  [`guide/for-researchers.html`](https://llm-archive.github.io/guide/for-researchers.html).

## How to run it

You need **Python 3.10 or newer** and **git**. There is nothing to install: it is all standard
library, except `pandas`, which only the optional data example uses.

```bash
git clone https://github.com/LLM-Archive/llm-archive.github.io.git
cd llm-archive.github.io
python3 -m core.instrument.verify       # quick check that your copy works: ends with "all vectors pass"
```

### Look at the results

```python
import pandas as pd, json

stability = pd.read_csv("stability.csv")
stability["flags"] = stability["flags"].apply(json.loads)  # a few columns hold JSON inside one cell

headline = stability[stability["on_curve"]]                # the measurements counted on the main curve
```

`stability.csv` has one row per measurement, `outcomes.csv` the same outcome counts one per row, and
`open-lane/` the raw replies for the runs that publish them. Every column is explained in the
[data dictionary](https://llm-archive.github.io/guide/data-dictionary.html), and there is a page
written for [data analysts](https://llm-archive.github.io/guide/for-analysts.html).

### Try it on your own model

A model on your own computer costs nothing and needs no account; only a hosted model needs a key.

```bash
# 1. See what a run prints, with no model at all (a synthetic stand-in):
python3 guide/sample/llm_archive_sample.py --fake

# 2. Run the whole method on your model. Defaults: Ollama on this machine, model llama3.2.
python3 guide/sample/llm_archive_sample.py
python3 guide/sample/llm_archive_sample.py --model qwen2.5:1.5b            # another Ollama model
python3 guide/sample/llm_archive_sample.py --openai --model my-model --base-url http://localhost:1234/v1
#                                                                          # LM Studio, vLLM, llama.cpp, OpenAI...

# 3. Ask your model the real questions we publish, and see our models' answers next to yours:
python3 guide/llm_archive_compare.py --model llama3.2
```

The first two ask your model a small set of practice questions and print a stability score with its
noise floor and interval, next to our own published numbers for the same kind of question. The
practice questions are invented for this purpose, so that result is for you and is never added to
the archive. The third asks the questions the archive itself publishes. For a model you can only
reach through a chat window, `llm_archive_compare.py --print-prompts` writes the questions out for
you to paste in.

### Check one of our numbers yourself

The stability score of every run whose replies are in `open-lane/` can be recomputed from those
replies with a short script and plain arithmetic. It is Step 3 of
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html).

### Or hand it to an AI assistant

[`guide/ai-assistant.md`](guide/ai-assistant.md) is written so you can give it to an AI assistant
(or follow it yourself). It downloads the files, runs the steps in order, checks each one against
what you should see, and stops at the first mismatch instead of guessing.

More step-by-step help, including what each result does and does not tell you, is in
[`guide/for-developers.html`](https://llm-archive.github.io/guide/for-developers.html).

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

## Corrections and errata

If you think a published number, a scenario, or a piece of code here is wrong, please say so. Open
an issue at [github.com/LLM-Archive/llm-archive.github.io/issues](https://github.com/LLM-Archive/llm-archive.github.io/issues)
or write to **mkalognomos@gmail.com**, with the run id (or file) and what you expected instead.

- **Response time: within 30 days.** This is a one-person project reviewed in a monthly batch, so
  that is the honest figure; a faster answer is possible but not promised.
- **Nothing is edited or deleted silently.** A measurement that turns out to be wrong stays in the
  archive, and the correction is disclosed publicly (in the next release notes on GitHub and Zenodo),
  so anyone who already used the old number can see what changed and why.
- **A bug in the frozen measurement code** (`core/measure/`) is not patched in place. It is fixed
  by a disclosed change, and the affected part of the series is marked as a break, because a silent
  patch would make earlier measurements quietly incomparable.
- **What this project publishes:** model responses to invented scenarios, with no personal data
  about real people. If you nevertheless believe something published here concerns you or your
  work, use the same channels.

## Questions or reproducing a result

Start with [`guide/faq.html`](https://llm-archive.github.io/guide/faq.html) and
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html). Contact details
for anything not covered there are in `SPEC.md`. What the project is for, what is sent to models,
and the site's privacy are in [`NOTICE.md`](NOTICE.md).
