"""Produces the daily runtime_fingerprint observation that core/instrument/ checks (spec.md
§7): 12 fixed prompts, greedy, top-5 logprobs rounded, hashed.

    python3 -m core.plumbing.runtime_fingerprint > observed.json

Hashing top-5 logprobs (not just the output text) matters: two runs can decode to the identical
text while the underlying distribution has already drifted -- a tie that greedy decoding hides
today can break tomorrow. Rounding to 4 decimal places absorbs float-formatting noise (e.g.
-0.30000001 vs -0.3) without hiding a real numeric shift, which surfaces well above that
precision.

These 12 prompts are a fixed instrument panel, not a research protocol: their content doesn't
matter, only that they're short, varied enough to exercise different parts of the model, and
never change once frozen (a change here is a core_change, same as anything else in this file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .reference_client import CHAT_TEMPLATE, MAX_TOKENS, STOP, SYSTEM_PROMPT, load_llama

LOGPROBS_ROUND = 4
TOP_LOGPROBS = 5

FIXED_PROMPTS = {
    "p01": "Write the next three numbers after 4, 8, 12, 16.",
    "p02": "Name the three primary colors.",
    "p03": "Spell the word 'lighthouse' one letter at a time, separated by hyphens.",
    "p04": "What is 17 plus 26?",
    "p05": "List the days of the week, starting from Monday.",
    "p06": "Complete this sentence: The opposite of hot is",
    "p07": "Translate 'good morning' into French.",
    "p08": "Give a one-sentence definition of a triangle.",
    "p09": "Count from one to five using words, not digits.",
    "p10": "What is the capital of Japan?",
    "p11": "Rewrite this sentence in the past tense: I walk to the store.",
    "p12": "Name two planets in the solar system.",
}
assert len(FIXED_PROMPTS) == 12


def _round_logprobs(top_logprobs: dict[str, float]) -> dict[str, float]:
    # llama-cpp-python returns numpy.float32, not a plain float -- round() preserves that type,
    # and json.dumps rejects it. float() first makes the value actually JSON-serializable.
    return {tok: round(float(lp), LOGPROBS_ROUND) for tok, lp in top_logprobs.items()}


def fingerprint_one(llama, prompt: str) -> str:
    full_prompt = CHAT_TEMPLATE.format(system=SYSTEM_PROMPT, user=prompt)
    result = llama(
        full_prompt,
        max_tokens=MAX_TOKENS,
        temperature=0.0,
        top_k=1,
        top_p=1.0,
        repeat_penalty=1.0,
        stop=STOP,
        seed=0,
        logprobs=TOP_LOGPROBS,
    )
    choice = result["choices"][0]
    logprobs = choice["logprobs"]["top_logprobs"]
    payload = {
        "text": choice["text"],
        "tokens": [_round_logprobs(step) for step in logprobs],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run(llama, prompts: dict[str, str] = FIXED_PROMPTS) -> dict[str, str]:
    return {prompt_id: fingerprint_one(llama, text) for prompt_id, text in prompts.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None, help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    llama = load_llama(logits_all=True)
    result = run(llama)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
