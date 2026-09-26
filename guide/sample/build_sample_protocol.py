#!/usr/bin/env python3
"""Builds guide/sample/sample_protocol.json: a small, invented practice panel for trying the method on
your own model. Standard library plus this repository's own core/measure/, nothing to install.

    python3 guide/sample/build_sample_protocol.py

The panel is a *sample*. Its ten scenarios are made up for this purpose and are not any protocol
LLM-Archive measures, so a result from it can never be compared with a published number and is
never a `comparison_point`. What it does share with the real panels is the method: four versions
per scenario (A, A', B, C), equal-expected-value options, a checked structure, a fixed decision
line, and the same estimator. Running this file again produces the identical JSON, hashes included.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.measure.chain import compute_panel_sha256, compute_protocol_sha256  # noqa: E402
from core.measure.invariants import check_protocol  # noqa: E402

OUT = Path(__file__).resolve().parent / "sample_protocol.json"

# (what is at stake, the unit, the total). Each total is divisible by 6, so 1/3 and 1/2 of it are whole.
SUBJECTS = [
    ("A coastal town is bracing for a storm surge that threatens", "homes", 300),
    ("A regional hospital is preparing for a flu season in which", "patients", 480),
    ("A wildlife reserve is facing a drought that puts at risk", "elephants", 240),
    ("A shipping company is planning around a strike that could delay", "containers", 900),
    ("A university is deciding how to respond to a funding cut that endangers", "scholarships", 600),
    ("A river authority is preparing for a flood that may cut off", "farms", 720),
    ("A city library system is facing a budget crisis that threatens", "library books", 1200),
    ("A mountain rescue service is planning for an avalanche season that endangers", "hikers", 540),
    ("A seed bank is preparing for a power failure that could destroy", "seed varieties", 1500),
    ("A telecom operator is preparing for an outage that could cut off", "households", 1800),
]

INSTRUCTION = "Two plans have been proposed. The exact estimates of their consequences are as follows."


def _lines(head: str, unit: str, total: int, plan_a: str, plan_b: str) -> str:
    return f"{head} {total} {unit}. {INSTRUCTION}\n\n{plan_a}\nPlan B: {plan_b}\n\nWhich plan do you choose?"


def build_scenario(index: int, head: str, unit: str, total: int) -> dict:
    sure, lost = total // 3, total - total // 3
    half = total // 2
    gain_a = f"Plan A: {sure} {unit} will be saved."
    gain_b = f"there is a 1/3 probability that all {total} {unit} will be saved, and a 2/3 probability that none will be saved."
    loss_a = f"Plan A: {lost} {unit} will be lost."
    loss_b = f"there is a 1/3 probability that none will be lost, and a 2/3 probability that all {total} {unit} will be lost."
    control_b = f"there is a 1/3 probability that all {total} {unit} will be saved, and a 2/3 probability that {half} {unit} will be saved."

    text_a = _lines(head, unit, total, gain_a, gain_b)
    # The null change: only whitespace and punctuation differ (checked by gate 1).
    text_a_prime = text_a.replace("Plan A:", "Plan A -").replace("Plan B:", "Plan B -").replace("\n\n", "\n\n\n")
    text_b = _lines(head, unit, total, loss_a, loss_b)
    text_c = _lines(head, unit, total, gain_a, control_b)

    sure_a = [[1, 1, sure]]
    gamble = [[1, 3, total], [2, 3, 0]]
    same = {"lotteries": {"A": sure_a, "B": gamble}, "correct": None}
    return {
        "scenario_id": f"s{index:02d}",
        "options": ["A", "B"],
        "versions": {"A": text_a, "A_prime": text_a_prime, "B": text_b, "C": text_c},
        "structure": {
            "A": same,
            "A_prime": same,
            "B": same,
            "C": {"lotteries": {"A": sure_a, "B": [[1, 3, total], [2, 3, half]]}, "correct": "B"},
        },
    }


def build() -> dict:
    scenarios = [build_scenario(i, *subject) for i, subject in enumerate(SUBJECTS, start=1)]
    protocol = {
        "protocol_id": "sample_risky_choice_wording",
        "family": "risky_choice_framing",
        "rewording_type": "wording",
        "status": "candidate",
        "lane": None,
        "twin_id": None,
        "n": 2 * len(scenarios),
        "n_per_scenario": 2,
        "effort": "disabled",
        "grammar_version": 2,
        "options": ["A", "B"],
        "prompt_template": "{scenario}\n\nEnd your response with a single line in exactly this format, where X is A or B:\n\nDECISION: X",
        "theta_positive_pct": 15,
        "scenario_dominated_share_pct": 40,
        "scenarios": scenarios,
    }
    protocol["panel_sha256"] = compute_panel_sha256(scenarios)
    protocol["protocol_sha256"] = compute_protocol_sha256(protocol)
    return protocol


def main() -> int:
    protocol = build()
    errors = check_protocol(protocol)
    if errors:
        print("the sample protocol fails gate 1:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(protocol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(protocol['scenarios'])} scenarios, gate 1 passes")
    print(f"panel_sha256    {protocol['panel_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
