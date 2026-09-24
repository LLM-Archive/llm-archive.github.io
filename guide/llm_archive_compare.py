#!/usr/bin/env python3
"""Does your model change its mind when a question is reworded? One file, nothing to install.

    python3 llm_archive_compare.py --model llama3.2            # a local model via Ollama
    python3 llm_archive_compare.py --openai --model gpt-4o-mini
    python3 llm_archive_compare.py --print-prompts my_run      # paste into any chat window,
    python3 llm_archive_compare.py --read-answers my_run       # then read the replies back

It asks your model the 8 questions LLM-Archive publishes in full (2 dilemmas x 4 versions) and
prints your model's answers next to the ones the archive recorded.

This is a spot-check on 2 questions, not a stability score: each real protocol uses a frozen panel of
15 scenarios, and only the first one of each is published. The full numbers are at
https://llm-archive.github.io.

The questions and the archive's answers were copied from the public repository on 2026-09-24.
Every question carries the sha256 the archive published for it, so you can look any of them up in
open-lane/<year>.jsonl at github.com/LLM-Archive/llm-archive.github.io and confirm the wording is
the real one. Python 3.10+, standard library only. CC-BY-4.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

LABEL = {
    "A": "A   baseline",
    "A_prime": "A'  cosmetic edits only     (should NOT change the answer)",
    "B": "B   same meaning, new wording (this is the measurement)",
    "C": "C   a different question      (SHOULD change the answer)",
}

# The archive's own extraction rule (core/measure/grammar_v2.py), copied so this file stands alone:
# plain string matching, no model in the loop. Nothing outside a decision line is read, so
# "I'd go with B in the end" is unparseable rather than "B".
_DECISION = re.compile(r"^decision ?[:：] ?(.*)$")


def extract(text: str) -> str:
    """The model's decision, or why there isn't one."""
    if not text or not text.strip():
        return "no reply"
    found = set()
    for line in text.splitlines():
        clean = unicodedata.normalize("NFC", line).casefold().translate(str.maketrans("", "", "*_`"))
        clean = re.sub(r"\s+", " ", clean).strip().rstrip(".!;· ")
        match = _DECISION.match(clean)
        if match and match.group(1) in ("a", "b"):
            found.add(match.group(1).upper())
    if not found:
        return "no DECISION line"
    return found.pop() if len(found) == 1 else "two different decisions"


def ask(prompt: str, args) -> str:
    """One call to your model. Temperature 0 so the run is as repeatable as your model allows."""
    if args.openai:
        url = (args.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        key = os.environ.get(args.api_key_env, "")
        if not key:
            sys.exit(f"Set your key first:  export {args.api_key_env}=...")
        body = {"model": args.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        headers = {"Authorization": f"Bearer {key}"}
    else:
        url = (args.base_url or "http://localhost:11434").rstrip("/") + "/api/chat"
        body = {"model": args.model, "messages": [{"role": "user", "content": prompt}],
                "stream": False, "options": {"temperature": 0}}
        headers = {}
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode())
    except Exception as error:
        sys.exit(f"Could not reach {url}: {error}\nIs the server running, and does it have '{args.model}'?")
    if args.openai:
        return data["choices"][0]["message"]["content"]
    return (data.get("message") or {}).get("content", "")


def report(questions: list[dict], answers: dict[str, str], name: str) -> None:
    families: dict[str, list[dict]] = {}
    for question in questions:
        families.setdefault(question["family"], []).append(question)

    changed = measurable = 0
    for family, items in families.items():
        print("\n" + "=" * 74)
        print(family.replace("_", " ").upper())
        print("=" * 74)
        decisions = {}
        for question in items:
            decisions[question["version"]] = extract(answers.get(slug(question), ""))
            print(f"\n  {LABEL[question['version']]}")
            print(f"      {name}: {decisions[question['version']]}")
            for model, answer in question["reference"].items():
                print(f"      {model}: {answer}")

        base, reworded = decisions.get("A"), decisions.get("B")
        print()
        if base in ("A", "B") and reworded in ("A", "B"):
            measurable += 1
            if base == reworded:
                print("  -> Kept the same decision when the wording changed but the meaning did not.")
            else:
                changed += 1
                print("  -> CHANGED its decision on a reworded but equivalent question.")
            if decisions.get("A_prime") in ("A", "B") and decisions["A_prime"] != base:
                print("  -> It also moved on a purely cosmetic edit, so that result is not about")
                print("     wording -- this model's answer is simply unsteady.")
            if decisions.get("C") == base:
                print("  -> It did NOT move on a genuinely different question. Read the reply: a model")
                print("     that answers the same regardless is not reading the question.")
        else:
            print("  -> Cannot tell: no reply ended with a 'DECISION: A' or 'DECISION: B' line.")

        for score in ARCHIVE["published_scores"].get(family, []):
            print(f"     (on all 15 questions, {score['model']} scored "
                  f"{score['stability_pct']}% stability, n={score['n']})")

    print("\n" + "=" * 74)
    if measurable:
        print(f"{name}: changed its decision on {changed} of {measurable} equivalent rewordings.\n")
    print("Answering differently from the models above is not instability -- which option a model")
    print("prefers is its own call. And two questions is a spot-check, not a score: the published")
    print("percentages come from all 15 questions asked many times over.")


def slug(question: dict) -> str:
    return f"{question['family']}__{question['version']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="llama3.2", help="the model id your server knows")
    parser.add_argument("--openai", action="store_true", help="use an OpenAI-compatible API instead of Ollama")
    parser.add_argument("--name", help="what to call your model in the output")
    parser.add_argument("--base-url", help="default: Ollama http://localhost:11434, OpenAI https://api.openai.com/v1")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="env var holding your key")
    parser.add_argument("--print-prompts", metavar="DIR", help="write the questions as .txt and stop")
    parser.add_argument("--read-answers", metavar="DIR", help="read replies from DIR/answers/*.txt")
    args = parser.parse_args()

    questions = ARCHIVE["questions"]
    if any(hashlib.sha256(q["prompt"].encode()).hexdigest() != q["sha256"] for q in questions):
        sys.exit("This file's questions no longer match their own hashes -- it has been edited or "
                 "corrupted. Download it again rather than comparing against unknown text.")
    print(f"{len(questions)} questions, each matching its published sha256 "
          f"(copied from the archive on {ARCHIVE['snapshot_date']}).")

    if args.print_prompts:
        directory = Path(args.print_prompts)
        (directory / "answers").mkdir(parents=True, exist_ok=True)
        for question in questions:
            (directory / f"{slug(question)}.txt").write_text(question["prompt"], encoding="utf-8")
        print(f"\nWrote them to {directory}/. Ask each one in a FRESH conversation -- no system")
        print("prompt and no earlier messages, or the history changes the answer. Save each full")
        print(f"reply under {directory}/answers/ with the same filename, then run:")
        print(f"  python3 {Path(sys.argv[0]).name} --read-answers {directory}")
        return 0

    if args.read_answers:
        directory = Path(args.read_answers) / "answers"
        answers = {slug(q): (directory / f"{slug(q)}.txt").read_text(encoding="utf-8")
                   for q in questions if (directory / f"{slug(q)}.txt").exists()}
        missing = [slug(q) for q in questions if slug(q) not in answers]
        if missing:
            print(f"\nMissing {len(missing)} of {len(questions)} replies in {directory}/: " + ", ".join(missing))
        name = args.name or "your model"
    else:
        where = "an OpenAI-compatible API" if args.openai else "Ollama"
        print(f"\nAsking {args.model} via {where} -- {len(questions)} calls.")
        answers = {}
        for question in questions:
            answers[slug(question)] = ask(question["prompt"], args)
            print(f"  answered: {slug(question)}")
        name = args.name or args.model

    report(questions, answers, name)
    return 0


ARCHIVE = json.loads(r"""{"published_scores": {"base_rate_neglect": [{"model": "claude-sonnet-5", "n": 30, "protocol": "base_rate_neglect__wording__v0", "stability_pct": 90.0}, {"model": "qwen2.5-1.5b-instruct-q4_k_m", "n": 30, "protocol": "base_rate_neglect__wording__v0", "stability_pct": 90.0}, {"model": "qwen2.5-1.5b-instruct-q4_k_m", "n": 120, "protocol": "base_rate_neglect__wording__v1", "stability_pct": 95.0}], "risky_choice_framing": [{"model": "claude-sonnet-5", "n": 30, "protocol": "risky_choice_framing__wording__v0", "stability_pct": 100.0}, {"model": "qwen2.5-1.5b-instruct-q4_k_m", "n": 30, "protocol": "risky_choice_framing__wording__v0", "stability_pct": 95.0}, {"model": "qwen2.5-1.5b-instruct-q4_k_m", "n": 120, "protocol": "risky_choice_framing__wording__v1", "stability_pct": 90.5}]}, "questions": [{"family": "base_rate_neglect", "prompt": "A hospital lab uses a rapid test to screen blood samples for a new virus.\n\n1% of the blood samples it checks are infected. The rapid test never misses: every blood sample that is infected gets flagged. But it also wrongly flags 10% of the blood samples that are not infected.\n\nOne blood sample has just been flagged by the rapid test. You must bet on the truth.\n\nOption A: Bet that it is infected. You win \u20ac990 if it is, and nothing if it is not.\nOption B: Bet that it is not infected. You win \u20ac100 if it is not, and nothing if it is.\n\nWhich option do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "B", "qwen2.5-1.5b-instruct-q4_k_m": "A"}, "sha256": "cc0e9f178186b844e218cc8e0a627d15c3c1934daeddf90f476d1db13a84bd6d", "version": "A"}, {"family": "base_rate_neglect", "prompt": "A hospital lab uses a rapid test to screen blood samples for a new virus.\n\n\n1 % of the blood samples it checks are infected; the rapid test never misses \u2014 every blood sample that is infected gets flagged; but it also wrongly flags 10 % of the blood samples that are not infected\n\nOne blood sample has just been flagged by the rapid test: you must bet on the truth:\n\n- Option A \u2014 Bet that it is infected; you win \u20ac990 if it is, and nothing if it is not\n- Option B \u2014 Bet that it is not infected; you win \u20ac100 if it is not, and nothing if it is\n\nWhich option do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "B", "qwen2.5-1.5b-instruct-q4_k_m": "B"}, "sha256": "6fdace3427dfdbb2b6d189c0d14078861ed9473eebde135d193c7ce5a3bf2ed3", "version": "A_prime"}, {"family": "base_rate_neglect", "prompt": "A hospital lab uses a rapid test to screen blood samples for a new virus.\n\nOut of every 1,000 blood samples it checks, 10 are infected. The rapid test never misses: it flags all 10 of them. But of the other 990, it also wrongly flags 99.\n\nOne blood sample has just been flagged by the rapid test. You must bet on the truth.\n\nOption A: Bet that it is infected. You win \u20ac990 if it is, and nothing if it is not.\nOption B: Bet that it is not infected. You win \u20ac100 if it is not, and nothing if it is.\n\nWhich option do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "B", "qwen2.5-1.5b-instruct-q4_k_m": "A"}, "sha256": "201e96927ccbf52a85ff90ccfa3e658eb6b304529e833830a307d285e7d56a3a", "version": "B"}, {"family": "base_rate_neglect", "prompt": "A hospital lab uses a rapid test to screen blood samples for a new virus.\n\n1% of the blood samples it checks are infected. The rapid test never misses: every blood sample that is infected gets flagged. But it also wrongly flags 10% of the blood samples that are not infected.\n\nOne blood sample has just been checked by the rapid test and was not flagged. You must bet on the truth.\n\nOption A: Bet that it is infected. You win \u20ac990 if it is, and nothing if it is not.\nOption B: Bet that it is not infected. You win \u20ac100 if it is not, and nothing if it is.\n\nWhich option do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "B", "qwen2.5-1.5b-instruct-q4_k_m": "A/B"}, "sha256": "a3e6359146b153d5b9926173a0c2582cf146a46f0c7a187488f3c64937531d11", "version": "C"}, {"family": "risky_choice_framing", "prompt": "A country is preparing for an outbreak of an unusual disease. 600 people are at risk.\n\nTwo plans have been proposed.\n\nPlan A: 200 people will be saved.\nPlan B: There is a 1/3 probability that all 600 people will be saved, and a 2/3 probability that none of them will be saved.\n\nWhich plan do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "A", "qwen2.5-1.5b-instruct-q4_k_m": "B/unparseable"}, "sha256": "82a919ffdd0850c8641a7e1e27cdf001c0b687f39af014f286b0dca1d1684579", "version": "A"}, {"family": "risky_choice_framing", "prompt": "A country is preparing for an outbreak of an unusual disease. 600 people are at risk.\n\n\nTwo plans have been proposed:\n\n- Plan A \u2014 200 people will be saved\n- Plan B \u2014 There is a 1 / 3 probability that all 600 people will be saved ; and a 2 / 3 probability that none of them will be saved\n\nWhich plan do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "A", "qwen2.5-1.5b-instruct-q4_k_m": "B/unparseable"}, "sha256": "edc8e86dfb51239157229f508ff6b6d21a8f64e12b98fcbbc34a85c05962d4a1", "version": "A_prime"}, {"family": "risky_choice_framing", "prompt": "A country is preparing for an outbreak of an unusual disease. 600 people are at risk.\n\nTwo plans have been proposed.\n\nPlan A: 400 people will die.\nPlan B: There is a 1/3 probability that none of the 600 people will die, and a 2/3 probability that all 600 people will die.\n\nWhich plan do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "A", "qwen2.5-1.5b-instruct-q4_k_m": "unparseable"}, "sha256": "08a12d85b996707087fe56ef0fb7217cd591c867e1943fe6754f073776f78515", "version": "B"}, {"family": "risky_choice_framing", "prompt": "A country is preparing for an outbreak of an unusual disease. 600 people are at risk.\n\nTwo plans have been proposed.\n\nPlan A: 200 people will be saved.\nPlan B: There is a 1/3 probability that all 600 people will be saved, and a 2/3 probability that 300 of them will be saved.\n\nWhich plan do you choose?\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X", "reference": {"claude-sonnet-5": "B", "qwen2.5-1.5b-instruct-q4_k_m": "unparseable"}, "sha256": "6c9079d4c77962ddbb0633c6b4cbd4a96d8847cf97116cfb2eba9f7347056a2c", "version": "C"}], "snapshot_date": "2026-09-24"}""")

if __name__ == "__main__":
    raise SystemExit(main())
