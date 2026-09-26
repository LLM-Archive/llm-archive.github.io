# Compare your own model with LLM-Archive

**For you, or for an AI assistant you hand this file to.** LLM-Archive measures one thing: does a
model keep the same decision when a question is reworded without changing its meaning? This file
shows you how to ask that about **your** model, and how to get every number the project publishes.

Everything comes from the public repository, `github.com/LLM-Archive/llm-archive.github.io`.
Python 3.10+ is the only requirement. Nothing to install.

> **If you are an AI assistant reading this:** run the steps in order and stop at the first one
> whose "you should see" line does not match, reporting what you saw instead of continuing. Do not
> invent numbers — every figure you report must come from a file or a command's output.

---

## 1. Test your own model (one file, two minutes)

```bash
curl -O https://llm-archive.github.io/guide/llm_archive_compare.py
```

That single file already contains the 8 questions the archive publishes in full — 2 dilemmas in 4
versions each — plus the answers the archive's own models gave. Then pick how to reach your model:

```bash
# a model on your own machine, via Ollama (ollama.com)
python3 llm_archive_compare.py --model llama3.2

# any OpenAI-compatible API: openai.com, vLLM, LM Studio, llama.cpp, most hosted providers
export OPENAI_API_KEY=...
python3 llm_archive_compare.py --openai --model gpt-4o-mini

# a model you can only reach through a chat window
python3 llm_archive_compare.py --print-prompts my_run
#   ...ask each .txt in a FRESH conversation, save the replies in my_run/answers/, then:
python3 llm_archive_compare.py --read-answers my_run
```

**You should see** a line confirming all 8 questions match their published sha256, then your
model's answers next to `claude-sonnet-5` and `qwen2.5-1.5b-instruct-q4_k_m`.

### Reading the result

Each question comes in four versions. **Only one of them is the measurement.**

| | What it is | What a change means |
|---|---|---|
| **A** | the baseline | — |
| **A′** | cosmetic edits only | Should **not** change. If it does, the model's answer is just unsteady, and the A→B result below means nothing. |
| **B** | same meaning, different wording | **This is the measurement.** A change here is the model being swayed by how the question was put. |
| **C** | a genuinely different question | **Should** change. If it doesn't, read the reply — a model answering the same regardless isn't reading the question. |

Two things the output will not support:

- **Answering differently from our models is not instability.** Which option a model prefers is its
  own call. Stability is only about whether *your* model's answer moved between A and B.
- **Two questions is a spot-check, not a score.** Each real protocol uses a frozen panel of 15
  scenarios asked many times over. A difference here is a reason to look closer, not a result.

---

## 2. Get every published number

```bash
git clone https://github.com/LLM-Archive/llm-archive.github.io llm-archive
cd llm-archive
```

*No git?* Use **Code → Download ZIP** on the repository page, or grab the files one at a time from
the **Downloads** page of the site.

| File | What's in it |
|---|---|
| `stability.csv` | one row per measurement — the main file |
| `outcomes.csv` | how many replies were valid / refused / unparseable |
| `coverage.csv` | what ran, what didn't, and why |
| `open-lane/<year>.jsonl` | every raw response, for the two `open` protocols |
| `SPEC.md` | the method, with the formulas in §6 |

A readable summary of the main file:

```bash
python3 - <<'PY'
import csv
rows = [r for r in csv.DictReader(open("stability.csv", encoding="utf-8")) if r["record_type"] == "measurement"]
print(f'{"date":<11}{"model":<34}{"protocol":<44}{"stability":>9}  n')
for r in sorted(rows, key=lambda r: (r["run_date"], r["protocol_id"])):
    print(f'{r["run_date"]:<11}{r["subject_model_id"]:<34}{r["protocol_id"]:<44}{float(r["stability_pct"]):>8.1f}%  {r["n"]}')
PY
```

Read a row as: *"on this frozen set of 15 questions, on this model, on that day, it kept the same
decision X % of the time when the wording changed."* Every column is explained in
`guide/data-dictionary.md`.

---

## 3. Check our arithmetic yourself

The two `open` protocols publish every raw response, so a published score can be re-derived from
scratch rather than taken on trust. Stability = 100 − the gap between the A and B answer
distributions, counting valid answers only.

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

**You should see** `OK` on every line. A `DIFF` means your recomputation disagrees with a published
number — exactly the kind of thing the project wants to hear about, at **mkalognomos@gmail.com**.
(A later year has its own file: change `2026` in the script.)

---

## 4. Run the whole method on your model (optional)

The 8 questions above are a spot-check. To run the full mechanism — four versions of ten scenarios,
the archive's own extraction rule and estimator, a noise floor, an interval and quality flags — use
the practice panel. It is invented for this purpose, so its result is never a published number.

```bash
python3 guide/sample/llm_archive_sample.py --fake    # no model: just see what a run prints
python3 guide/sample/llm_archive_sample.py           # your model; defaults: Ollama, llama3.2
python3 guide/sample/llm_archive_sample.py --openai --model my-model --base-url http://localhost:1234/v1
```

**You should see** 80 `#` marks (one per answer), then a block with `stability`, `noise floor` and
the three gaps, then the published measurements of the same kind of question with yours as the first
row. Everything is saved in `sample_results/<run_id>/`. Read stability next to the noise floor, never
above it; the printed flags explain themselves. Full reading guide: `guide/for-developers.md`,
steps 5 and 6.

---

## Why only two questions, and what a full comparison needs

Each real protocol is a frozen panel of 15 scenarios, and only the **first** scenario of each of the
two `open` protocols is published in full. The raw records carry a `prompt_sha256` — enough to
*verify* a prompt you already hold, never enough to reveal one. So a complete, like-for-like run of
your model against a real protocol is possible today only for someone who holds the panel. This is a
disclosed gap, not an oversight: see `SPEC.md` §1.1.

If you do hold one, `guide/reproducing.md` lists what a submittable `comparison_point` must match —
same `panel_sha256`, same `grammar_version`, same `n`, all four versions, the full outcome
distribution, and a verifiable model id. `core/measure/client.py` defines the small interface a
model has to implement and `core/plumbing/reference_client.py` is a worked example. Send results to
**mkalognomos@gmail.com**.

---

## If something goes wrong

| You see | Do |
|---|---|
| `command not found: python3` | Install Python 3.10+ from python.org |
| `Could not reach http://localhost:11434` | Ollama isn't running. `ollama serve` in another terminal |
| `no DECISION line` for your model | It didn't end its reply with `DECISION: A` / `DECISION: B`. Read the reply — most models need a fresh chat with no system prompt |
| The file's hashes don't match | Download `llm_archive_compare.py` again; don't compare against edited text |
| `The very first call failed` in step 4 | The server isn't reachable at `--base-url`, or doesn't know `--model`; nothing was written, fix it and re-run |
| `DIFF` in step 3 | Re-clone in case your copy is stale; if it persists, report it |

## Where to go next

- `guide/for-researchers.md` — how to cite, and what the numbers do and don't mean
- `guide/data-dictionary.md` — every column of every file
- `guide/for-developers.md` — run it locally, the practice panel, how to read and compare a result
- `guide/reproducing.md` — the full rules for a comparison
- `SPEC.md` — the method itself
