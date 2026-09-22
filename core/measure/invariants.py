"""Admission gate 1 — structural invariants, checked mechanically (spec.md §3).

Before a protocol is ever run against a real model, it has to prove something about its own
design: that A, A′ and B are really the same decision problem restated, and that C is really a
different decision problem where the right answer changes. This file cannot read English and
cannot tell whether the wording of a scenario is honest — a human still has to read it and agree
(that's admission gates 2 and 4). What this file CAN do is check that the numbers the protocol
author declares alongside the text (the `structure` block: who gets what, with what probability)
are internally consistent, and that they say what the protocol claims they say. A protocol that
fails here has a design bug, independent of whether any model has ever seen it.
"""

from __future__ import annotations

import re
from fractions import Fraction

from .chain import compute_panel_sha256, compute_protocol_sha256
from .grammar import GRAMMAR_VERSION, normalized_options
from .grammar_v2 import GRAMMAR_VERSION as GRAMMAR_VERSION_V2

# Every decision-grammar version a protocol may declare, additive as each new one is introduced
# (spec.md §2: changing the decision grammar mints a new protocol series, never a silent rewrite
# of what an existing one means) -- grammar_version 1 stays exactly what it always was.
KNOWN_GRAMMAR_VERSIONS = (GRAMMAR_VERSION, GRAMMAR_VERSION_V2)
from .schema import LANES, PROTOCOL_STATUSES, REWORDING_TYPES, VERSIONS

_NON_WORD = re.compile(r"[\W_]+")

REQUIRED = (
    "protocol_id",
    "family",
    "rewording_type",
    "status",
    "lane",
    "twin_id",
    "n",
    "n_per_scenario",
    "effort",
    "grammar_version",
    "options",
    "prompt_template",
    "theta_positive_pct",
    "scenario_dominated_share_pct",
    "scenarios",
    "panel_sha256",
    "protocol_sha256",
)


def surface_skeleton(text: str) -> str:
    """What remains when whitespace and punctuation are removed. A′ must match A exactly."""
    return _NON_WORD.sub("", text.casefold())


def _lotteries(version: dict) -> dict[str, list[tuple[Fraction, Fraction]]]:
    return {
        opt: sorted((Fraction(p_num, p_den), Fraction(value)) for p_num, p_den, value in outcomes)
        for opt, outcomes in version["lotteries"].items()
    }


def _check_structure(sid: str, options: list[str], structure: dict) -> list[str]:
    """The heart of gate 1. Two properties, one per pair of versions:

    - A, A′ and B must be the SAME decision problem: identical odds and payoffs for every option,
      and the same normatively correct answer. Only the words are allowed to differ. If B secretly
      changed the odds, a shift in the model's answer wouldn't be a framing effect — it would just
      be a correct response to a genuinely different question, which is not what this project measures.
    - C must be a decision problem where the correct answer FLIPS relative to A, and flips clearly:
      the option that becomes correct must strictly dominate every other option (better in its
      worst case than they are in their best case). A weak or ambiguous flip would make it
      impossible to tell "the model didn't notice the change" apart from "the model noticed, but
      the change was too subtle to matter."
    """
    errors = []
    lots = {}
    for v in VERSIONS:
        if v not in structure:
            return [f"{sid}: structure missing version {v}"]
        lots[v] = _lotteries(structure[v])
        if sorted(lots[v]) != sorted(options):
            errors.append(f"{sid}/{v}: lotteries must cover exactly options[] (same number of options)")
            continue
        for opt, lot in lots[v].items():
            if sum(p for p, _ in lot) != 1:
                errors.append(f"{sid}/{v}/{opt}: probabilities do not sum to 1")
    if errors:
        return errors

    for v in ("A_prime", "B"):
        if lots[v] != lots["A"]:
            errors.append(f"{sid}: {v} must state the same lotteries as A")
        if structure[v].get("correct") != structure["A"].get("correct"):
            errors.append(f"{sid}: {v} must keep the normatively correct answer of A")

    evs = {opt: sum(p * x for p, x in lot) for opt, lot in lots["A"].items()}
    if len(set(evs.values())) != 1:
        errors.append(f"{sid}: options in A must have equal expected value, got {evs}")

    correct = structure["C"].get("correct")
    if correct not in options:
        errors.append(f"{sid}: C must declare a normatively correct option")
    elif correct == structure["A"].get("correct"):
        errors.append(f"{sid}: C must change the normatively correct answer relative to A")
    else:
        worst_correct = min(x for _, x in lots["C"][correct])
        for opt in options:
            if opt != correct and max(x for _, x in lots["C"][opt]) >= worst_correct:
                errors.append(f"{sid}: in C, option {correct} must strictly dominate {opt}")
    return errors


def _check_lane_and_twin(p: dict) -> list[str]:
    """Lane and twin are the two things a human decides at admission, so they are checked
    together. What this can see is one file at a time: that a candidate claims neither, that an
    `open` protocol names a twin at all, and that it doesn't name itself. Whether the twin exists,
    points back, and is the same family is a property of the whole protocols/ folder, not of any
    one file — verify.py's `twin pairing` check owns that."""
    errors = []
    if p["lane"] is not None and p["lane"] not in LANES:
        errors.append(f"unknown lane {p['lane']!r}")
    if p["status"] == "candidate":
        if p["lane"] is not None:
            errors.append("a candidate has no lane until admitted (spec.md §3, gate 4)")
        if p["twin_id"] is not None:
            errors.append("a candidate has no twin until admitted (spec.md §3, gate 4)")
    # Without a twin, an `open` protocol's contamination — which is expected, by design, since its
    # full text is public — cannot be told apart from a real change in the model.
    if p["status"] == "admitted" and p["lane"] == "open" and p["twin_id"] is None:
        errors.append("an admitted `open` protocol must name its guard twin (spec.md §3)")
    if p["twin_id"] == p["protocol_id"]:
        errors.append("twin_id points at the protocol itself")
    return errors


def check_protocol(p: dict) -> list[str]:
    missing = [k for k in REQUIRED if k not in p]
    if missing:
        return [f"missing fields: {missing}"]

    errors = []
    if p["rewording_type"] not in REWORDING_TYPES:
        errors.append(f"unknown rewording_type {p['rewording_type']!r}")
    if p["status"] not in PROTOCOL_STATUSES:
        errors.append(f"unknown status {p['status']!r}")
    errors += _check_lane_and_twin(p)
    if p["grammar_version"] not in KNOWN_GRAMMAR_VERSIONS:
        errors.append(f"grammar_version {p['grammar_version']} not in {KNOWN_GRAMMAR_VERSIONS}")
    if p["effort"] != "disabled":
        errors.append("effort must be 'disabled' (spec.md §5)")
    if "{scenario}" not in p["prompt_template"]:
        errors.append("prompt_template has no {scenario} placeholder")
    try:
        normalized_options(p["options"])
    except ValueError as e:
        errors.append(str(e))

    scenarios = p["scenarios"]
    ids = [s["scenario_id"] for s in scenarios]
    if len(set(ids)) != len(ids):
        errors.append("duplicate scenario_id")
    if p["n"] != p["n_per_scenario"] * len(scenarios):
        errors.append(f"n={p['n']} != n_per_scenario × scenarios = {p['n_per_scenario']} × {len(scenarios)}")

    for s in scenarios:
        sid = s["scenario_id"]
        if s["options"] != p["options"]:
            errors.append(f"{sid}: options differ from the protocol's")
        if sorted(s["versions"]) != sorted(VERSIONS):
            errors.append(f"{sid}: versions must be exactly {VERSIONS}")
            continue
        if any(not s["versions"][v].strip() for v in VERSIONS):
            errors.append(f"{sid}: empty version text")
        if surface_skeleton(s["versions"]["A"]) != surface_skeleton(s["versions"]["A_prime"]):
            errors.append(f"{sid}: A_prime changes more than whitespace and punctuation")
        if s["versions"]["A"] == s["versions"]["A_prime"]:
            errors.append(f"{sid}: A_prime is identical to A — the null change must change something")
        if "structure" not in s:
            errors.append(f"{sid}: no declared structure — invariants cannot be checked")
        else:
            errors += _check_structure(sid, p["options"], s["structure"])

    if p["panel_sha256"] != compute_panel_sha256(scenarios):
        errors.append("panel_sha256 does not match the scenarios")
    if p["protocol_sha256"] != compute_protocol_sha256(p):
        errors.append("protocol_sha256 does not match the protocol")
    return errors
