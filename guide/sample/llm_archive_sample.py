#!/usr/bin/env python3
"""Run LLM-Archive's whole method on your own model, on a small practice panel. Free if the model
runs on your own computer. Nothing to install.

    python3 guide/sample/llm_archive_sample.py --fake                       # 1. no model at all: see the output
    python3 guide/sample/llm_archive_sample.py                              # 2. Ollama, model llama3.2 (defaults)
    python3 guide/sample/llm_archive_sample.py --model qwen2.5:1.5b         #    another Ollama model
    python3 guide/sample/llm_archive_sample.py --openai --model my-model \\
        --base-url http://localhost:1234/v1                          # 3. LM Studio, vLLM, llama.cpp server...

Run it from the top of the repository. It asks your model 80 questions (10 scenarios x 4 versions x
2 repetitions, in a fixed shuffled order), extracts each answer with the archive's own rule, and
computes one measurement with the archive's own estimator (core/measure/) -- the same code that
produces every published number. The raw replies and the measurement are written to
sample_results/<run_id>/ and are never overwritten.

Defaults (every one can be changed with the flag beside it):
    --model      llama3.2                    the model name your server knows
    --base-url   http://localhost:11434      Ollama; with --openai: https://api.openai.com/v1
    --api-key-env OPENAI_API_KEY             only with --openai; a local server usually accepts any value
    --out        sample_results
    --date       today

This is a PRACTICE panel: ten invented scenarios (guide/sample/sample_protocol.json), not any protocol
LLM-Archive measures. Its result can never be compared with a published number and is not a
`comparison_point`. Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # guide/, for llm_archive_compare

from core.measure import measurement  # noqa: E402
from core.measure.chain import sha256_hex  # noqa: E402
from core.measure.client import Response, classify  # noqa: E402
from core.measure.invariants import check_protocol  # noqa: E402
from core.measure.rng import SplitMix64, seed_from  # noqa: E402
from core.measure.schema import VERSIONS  # noqa: E402
from llm_archive_compare import ask  # noqa: E402  (the same one-call-to-your-model code as the spot-check)

# The published protocols the sample panel resembles: same family, same rewording type.
PUBLISHED_KIND = "risky_choice_framing__wording__"
PROTOCOL_PATH = Path(__file__).resolve().parent / "sample_protocol.json"

WHAT_FLAGS_MEAN = {
    "below_surface_noise": "the reworded question did not move the answer any more than the cosmetic edit did",
    "not_reading": "the model did not react to the question that really is different (version C)",
    "degenerate_candidate": "the model gave almost the same answer whatever it was asked",
    "drop_confounded": "too many replies were lost (unparseable, refused, cut off) to trust the result",
    "asymmetric_missingness": "one version lost noticeably more replies than the other",
    "below_drop_bound": "the finding is smaller than what the lost replies alone could explain",
    "scenario_dominated": "one or two scenarios produce most of the gap",
}


class YourModel:
    """The Client contract of core/measure/client.py, for a model you run yourself."""

    series = "open_weights"
    explicitly_set = {"temperature": 0}

    def __init__(self, args) -> None:
        self.args = args
        self.subject_model_id = args.model
        self.model_family = "own"

    def complete(self, prompt: str, *, meta: dict) -> Response:
        try:
            text = ask(prompt, self.args)
        except SystemExit as exit_:
            return Response(None, None, None, error=str(exit_))
        return Response(text, None, self.args.model)


def schedule(protocol: dict, run_date: str, model_id: str) -> list[dict]:
    """All calls in a seeded random order, so drift during the run cannot line up with one version."""
    calls = [
        {"version": v, "scenario_id": s["scenario_id"], "rep": r,
         "prompt": protocol["prompt_template"].replace("{scenario}", s["versions"][v])}
        for v in VERSIONS
        for s in protocol["scenarios"]
        for r in range(protocol["n_per_scenario"])
    ]
    rng = SplitMix64(seed_from(protocol["protocol_id"], run_date, model_id, "order"))
    for i in range(len(calls) - 1, 0, -1):
        j = rng.below(i + 1)
        calls[i], calls[j] = calls[j], calls[i]
    return calls


def run(protocol: dict, client, run_date: str, out_root: Path) -> tuple[dict, Path]:
    safe_model = re.sub(r"[^\w.-]+", "-", client.subject_model_id)
    run_id = f"{run_date}__{protocol['protocol_id']}__{safe_model}__r0"
    out_dir = out_root / run_id
    if out_dir.exists():
        sys.exit(f"{out_dir} already exists. Runs are never overwritten: pass --date, --out, or delete it yourself.")

    calls = schedule(protocol, run_date, client.subject_model_id)
    print(f"Asking {client.subject_model_id}: {len(calls)} questions. One # per answer.")
    trials, returned = [], set()
    for index, call in enumerate(calls):
        response = client.complete(call["prompt"], meta={k: call[k] for k in ("version", "scenario_id", "rep")})
        if index == 0 and response.error:
            sys.exit(f"The very first call failed, so nothing was written:\n{response.error}")
        result = classify(response, protocol["options"], grammar_version=protocol["grammar_version"])
        if response.returned_model_id:
            returned.add(response.returned_model_id)
        trials.append({
            "record_type": "trial", "run_id": run_id, "index": index, "version": call["version"],
            "scenario_id": call["scenario_id"], "rep": call["rep"],
            "prompt_sha256": sha256_hex(call["prompt"].encode("utf-8")),
            "response_text": response.text, "stop_reason": response.stop_reason,
            "returned_model_id": response.returned_model_id, "error": response.error,
            "outcome": result.outcome, "token": result.token, "reason": result.reason,
        })
        print("#", end="", flush=True)
        if (index + 1) % 40 == 0 or index + 1 == len(calls):
            print(f" {index + 1}/{len(calls)}")

    context = {
        "run_id": run_id, "run_date": run_date, "subject_model_id": client.subject_model_id,
        "returned_model_id": ",".join(sorted(returned)) or None, "model_family": client.model_family,
        "series": client.series, "lane": protocol["lane"], "twin_id": protocol["twin_id"],
        "condition_profile": "bare", "replicate_index": 0,
    }
    record = measurement.build(protocol, trials, context)
    out_dir.mkdir(parents=True)
    with open(out_dir / "trials.jsonl", "w", encoding="utf-8") as f:
        for trial in trials:
            f.write(json.dumps(trial, ensure_ascii=False) + "\n")
    (out_dir / "measurement.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record, out_dir


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.1f}%"


def compare_with_published(m: dict) -> None:
    """Ours, for orientation: the published measurements of the same family and rewording type,
    read from stability.csv next to this repository's top level. Not the same panel, so never a
    like-for-like comparison."""
    path = ROOT / "stability.csv"
    if not path.is_file():
        return
    latest: dict[tuple[str, str], dict] = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["record_type"] == "measurement" and row["protocol_id"].startswith(PUBLISHED_KIND):
                key = (row["subject_model_id"], row["protocol_id"])
                if key not in latest or row["run_date"] > latest[key]["run_date"]:
                    latest[key] = row
    if not latest:
        return
    print("\nFor orientation, LLM-Archive's own published measurements of the same kind of question")
    print("(risky choice, reworded), from stability.csv. Different panel: read them side by side, not as a score.")
    print(f"  {'model':<30}{'protocol':<38}{'stability':>10}  {'95% interval':<17}{'noise floor':>11}   n")
    yours = f"{'YOURS: ' + m['subject_model_id']:<30}{'sample_risky_choice_wording':<38}{_pct(m['stability_pct']):>10}  "
    print("  " + yours + f"{_pct(m['ci_low_pct']) + ' to ' + _pct(m['ci_high_pct']):<17}{_pct(m['null_floor_pct']):>11}   {m['n']}")
    for (model, protocol), row in sorted(latest.items()):
        interval = f"{float(row['ci_low_pct']):.1f}% to {float(row['ci_high_pct']):.1f}%"
        print(f"  {model:<30}{protocol:<38}{float(row['stability_pct']):>9.1f}%  {interval:<17}{float(row['null_floor_pct']):>10.1f}%   {row['n']}")
    print("Overlapping intervals mean the difference is within what sampling noise alone can produce here.")
    print("For the same questions asked of your model and of ours, use guide/llm_archive_compare.py.")


def report(m: dict, out_dir: Path) -> None:
    line = "=" * 72
    print(f"\n{line}\nPRACTICE PANEL RESULT: {m['subject_model_id']}\n{line}")
    print(f"stability            {_pct(m['stability_pct'])}   (95% interval {_pct(m['ci_low_pct'])} to {_pct(m['ci_high_pct'])})")
    print(f"noise floor          {_pct(m['null_floor_pct'])}   (a gap this small is expected from sampling alone)")
    print(f"cosmetic edit  A-A'  gap {_pct(m['gap_null_pct'])}   (should be near zero: this is the null change)")
    print(f"reworded       A-B   gap {_pct(m['gap_pct'])}   (this is the measurement)")
    print(f"different      A-C   gap {_pct(m['gap_positive_pct'])}   (should be large: proof the model reads the question)")
    print("\nHow each version was answered (option A / option B):")
    for v in VERSIONS:
        d = m["decisions"][v]
        valid = m["outcomes"][v]["valid"]
        total = sum(m["outcomes"][v].values())
        print(f"  {v:<8} A={d['A']:<3} B={d['B']:<3}  valid replies {valid}/{total}")
    print("\nFlags:", ", ".join(m["flags"]) if m["flags"] else "none")
    for flag in m["flags"]:
        print(f"  {flag}: {WHAT_FLAGS_MEAN.get(flag, '')}")
    compare_with_published(m)
    print(f"\nSaved: {out_dir}/ (trials.jsonl = every reply, measurement.json = the record)")
    print("Read it with care: ten invented scenarios and 20 replies per version make a wide interval,")
    print("and this panel is not one LLM-Archive measures, so the published rows above are context, not a benchmark.")
    print("A change of stability between two runs smaller than the interval above is not a finding.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="llama3.2", help="the model name your server knows")
    parser.add_argument("--openai", action="store_true", help="use an OpenAI-compatible API instead of Ollama")
    parser.add_argument("--base-url", help="default: Ollama http://localhost:11434, OpenAI https://api.openai.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="env var holding your key")
    parser.add_argument("--fake", action="store_true", help="no model: a synthetic one, only to see the output")
    parser.add_argument("--out", type=Path, default=Path("sample_results"))
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()

    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    errors = check_protocol(protocol)
    if errors:
        sys.exit("guide/sample/sample_protocol.json fails gate 1 (was it edited?):\n  " + "\n  ".join(errors))

    if args.fake:
        from core.plumbing.fake_client import FakeClient

        client = FakeClient()
        print("--fake: a synthetic model that says nothing about any real one. It only shows what a run looks like.\n")
    else:
        if args.openai and not os.environ.get(args.api_key_env):
            os.environ[args.api_key_env] = "local"  # a local OpenAI-compatible server ignores the key
        client = YourModel(args)

    record, out_dir = run(protocol, client, args.date, args.out)
    report(record, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
