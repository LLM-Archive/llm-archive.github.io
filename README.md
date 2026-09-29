<p align="center">
  <a href="https://llm-archive.github.io/"><img src="logo.png" alt="LLM-Archive logo" width="120" height="120"></a>
</p>

<h1 align="center">LLM-Archive</h1>

*We check whether an AI changes its mind when a question says the same thing in different words —
and we publish the results.*

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22881127.svg)](https://doi.org/10.5281/zenodo.22881127)
[![Code: AGPL-3.0](https://img.shields.io/badge/code-AGPL--3.0-blue)](LICENSE.md)
[![Data: CC-BY-4.0](https://img.shields.io/badge/data-CC--BY--4.0-blue)](LICENSE.md)
[![Prose: CC-BY-SA-4.0](https://img.shields.io/badge/prose-CC--BY--SA--4.0-blue)](LICENSE.md)
[![core/measure: stdlib only](https://img.shields.io/badge/core%2Fmeasure-stdlib%20only-brightgreen)](core/measure/)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-brightgreen)](#how-to-run-it)

The archive doesn't answer "how smart is an AI." It answers "did something change, when, and for
whom."

**Live site:** [llm-archive.github.io](https://llm-archive.github.io/) ·
**Guide:** [llm-archive.github.io/guide](https://llm-archive.github.io/guide/) ·
**Live results:** [llm-archive.github.io/index.html#results](https://llm-archive.github.io/index.html#results) ·
**Methodology:** [llm-archive.github.io/index.html#how](https://llm-archive.github.io/index.html#how)

This repository **is** the public site and the public dataset — not a mirror or a summary of one.
Everything in it, code and data alike, is free to use. New here? Start with the
[guide](https://llm-archive.github.io/guide/), or skip straight to **"How to run it"** below if
you'd rather just run something and see for yourself.

## Overview

People increasingly hand real decisions to AI models — and the model behind a product name can
change without notice. If the *same* question, worded two different ways that mean the same
thing, gets two *different* decisions, then the answer depended on phrasing, not on the question —
and a silent model update can shift that with nobody announcing it. LLM-Archive keeps an open,
dated, checkable record of exactly that one property, plus the evidence that the measuring itself
still works, so a finding never has to be taken on faith. The full method — the estimator, the
null and positive controls, the noise floor, the daily self-check — is walked through step by step
in the site's own [Methodology](https://llm-archive.github.io/index.html#how) page.

> [!IMPORTANT]
> LLM-Archive measures **one thing**: how much a model's decision shifts when the same question is
> reworded. It is not a capability score, not a benchmark ranking, and not comparable across model
> lines — see [`guide/for-researchers.html`](https://llm-archive.github.io/guide/for-researchers.html)
> for exactly what a number does and doesn't prove.

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

### Check one of our numbers yourself

> [!NOTE]
> Nothing here has to be taken on trust. The stability score of every run whose replies are in
> `open-lane/` can be recomputed from those replies with a short script and plain arithmetic — it's
> Step 3 of [`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html).

## How to compare your own model

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

Security vulnerability, not a data or methodology question? See [`SECURITY.md`](SECURITY.md) instead.

## Questions or reproducing a result

Start with [`guide/faq.html`](https://llm-archive.github.io/guide/faq.html) and
[`guide/reproducing.html`](https://llm-archive.github.io/guide/reproducing.html). Contact details
for anything not covered there are in `SPEC.md`. What the project is for, what is sent to models,
and the site's privacy are in [`NOTICE.md`](NOTICE.md).
