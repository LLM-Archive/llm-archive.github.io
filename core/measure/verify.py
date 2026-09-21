"""Replays the golden vectors and validates every protocol file, so the whole pipeline can be
checked for correctness without ever calling a model (spec.md §10, §13).

Run as:

    python3 -m core.measure.verify

The golden vectors themselves (`core/testdata/vectors/*.json`) are fixed input -> output pairs,
independently derived from the rules in grammar.py, stats.py and measurement.py, not just a dump
of whatever those files currently return -- so a future change that quietly breaks one of them
(a sign flip, a `>` that should have been `>=`, a stripped character that shouldn't be) is caught
here instead of showing up for the first time in a published measurement.

This is meant to run before the first real API call and in CI on every change to core/: an
admitted protocol with a design bug, or an estimator that no longer matches its own spec, should
never survive to spend real money.
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

from . import stats
from .grammar import extract
from .invariants import check_protocol
from .measurement import evaluate_gates
from .rng import SplitMix64
from .schema import MEASUREMENT_FIELDS, PCT_UNITS

_VECTORS = Path(__file__).resolve().parent.parent / "testdata" / "vectors"
_PROTOCOLS = Path(__file__).resolve().parents[2] / "protocols"


def _frac(x: str | None) -> Fraction | None:
    return None if x is None else Fraction(x)


def check_grammar(data: dict) -> list[str]:
    errors = []
    for case in data["cases"]:
        got = extract(case["text"], case["options"])
        want = case["expected"]
        if (got.outcome, got.token, got.reason) != (want["outcome"], want["token"], want["reason"]):
            errors.append(f"{case['name']}: got {got}, want {want}")
    return errors


def check_tvd(data: dict) -> list[str]:
    errors = []
    for case in data["tvd"]:
        got = stats.tvd(case["cx"], case["cy"])
        want = _frac(case["expected"])
        if got != want:
            errors.append(f"{case['name']}: got {got}, want {want}")
    return errors


def check_drop_bound_pct(data: dict) -> list[str]:
    errors = []
    for case in data["drop_bound_pct"]:
        got = stats.drop_bound_pct(Fraction(case["u_x"]), Fraction(case["u_y"]))
        want = _frac(case["expected"])
        if got != want:
            errors.append(f"{case['name']}: got {got}, want {want}")
    return errors


def check_entropy_norm(data: dict) -> list[str]:
    """Compared with a small float tolerance, not exact equality -- entropy_norm goes through
    math.log, and the vector's expected value was itself rounded to 12 decimals when it was
    written, so bit-exact equality would fail on the rounding alone rather than on a real bug."""
    errors = []
    for case in data["entropy_norm"]:
        got = stats.entropy_norm(case["c"])
        want = case["expected"]
        if (got is None) != (want is None):
            errors.append(f"{case['name']}: got {got}, want {want}")
        elif got is not None and abs(got - want) > 1e-9:
            errors.append(f"{case['name']}: got {got}, want {want}")
    return errors


def check_null_floor(data: dict) -> list[str]:
    errors = []
    for case in data["null_floor"]:
        rng = SplitMix64(case["seed"])
        got = stats.null_floor(case["cx"], case["cy"], rng, iterations=case["iterations"])
        want = _frac(case["expected"])
        if got != want:
            errors.append(f"{case['name']}: got {got}, want {want}")
    return errors


def check_gates(data: dict, category: str) -> list[str]:
    """Shared replay for gate_ab / gate_aa / gate_ac: all three are the same evaluate_gates()
    call, just with inputs chosen to isolate one of the three checks from spec.md §6."""
    errors = []
    for case in data[category]:
        gap_pct = {k: _frac(v) for k, v in case["gap_pct"].items()}
        bound = {k: Fraction(v) for k, v in case["bound"].items()}
        asym = {k: Fraction(v) for k, v in case["asym"].items()}
        theta = _frac(case["theta"])
        gates, flags, unset = evaluate_gates(gap_pct, bound, asym, theta)
        want = case["expected"]
        if gates != want["gates"] or flags != want["flags"] or unset != want["unset"]:
            errors.append(
                f"{case['name']}: got gates={gates} flags={flags} unset={unset}, "
                f"want gates={want['gates']} flags={want['flags']} unset={want['unset']}"
            )
    return errors


def check_protocols() -> list[str]:
    """Gate 1 (structural invariants) against every protocol file, not just the ones already
    marked 'admitted' -- a candidate has to pass this before it's ever worth a human's time."""
    errors = []
    for path in sorted(_PROTOCOLS.glob("*.json")):
        protocol = json.loads(path.read_text())
        for e in check_protocol(protocol):
            errors.append(f"{path.name}: {e}")
    return errors


def check_twin_pairing() -> list[str]:
    """spec.md §3: every `open` protocol is paired with a `guard` twin. The pair is what makes
    contamination measurable -- an `open` protocol's text is public and will eventually be trained
    on, so its number drifting is expected; only the difference against a twin that stayed private
    separates "the public copy got contaminated" from "the model actually changed". That argument
    only holds if the two really are comparable, so this checks the three properties it rests on:
    the pairing is mutual, it crosses exactly one open/guard boundary, and both halves pose the
    same underlying decision problem (same family, which by construction means the same declared
    lotteries and the same structural invariants -- see mutable/panels.py)."""
    by_id = {}
    for path in sorted(_PROTOCOLS.glob("*.json")):
        p = json.loads(path.read_text())
        by_id[p["protocol_id"]] = p

    errors = []
    for pid, p in sorted(by_id.items()):
        twin_id = p.get("twin_id")
        if twin_id is None:
            continue
        twin = by_id.get(twin_id)
        if twin is None:
            errors.append(f"{pid}: twin_id {twin_id!r} is not a protocol in protocols/")
            continue
        if twin.get("twin_id") != pid:
            errors.append(f"{pid}: twin {twin_id} does not point back (its twin_id is {twin.get('twin_id')!r})")
        if p["family"] != twin["family"]:
            errors.append(f"{pid}: twin {twin_id} is a different family -- not the same phenomenon")
        if p["rewording_type"] == twin["rewording_type"]:
            errors.append(f"{pid}: twin {twin_id} is the same rewording_type -- a twin is a different embodiment")
        if {p["lane"], twin["lane"]} != {"open", "guard"}:
            errors.append(f"{pid}: lanes {p['lane']!r}/{twin['lane']!r} -- a pair must be one open and one guard")
    return errors


def check_pct_naming() -> list[str]:
    """spec.md §6: a published field is a 0-100 percentage if and only if its name contains
    '_pct'. Checked both directions, since either mismatch is the same specification error --
    a percentage hiding under a name that doesn't say so, or a plain count that looks like one."""
    errors = []
    for field, unit in MEASUREMENT_FIELDS.items():
        if ("_pct" in field) != (unit in PCT_UNITS):
            errors.append(f"{field}: unit={unit!r} disagrees with the '_pct' naming rule")
    return errors


def main() -> int:
    grammar_data = json.loads((_VECTORS / "grammar_vectors.json").read_text())
    stats_data = json.loads((_VECTORS / "stats_vectors.json").read_text())

    checks = (
        ("grammar", lambda: check_grammar(grammar_data)),
        ("tvd", lambda: check_tvd(stats_data)),
        ("drop_bound_pct", lambda: check_drop_bound_pct(stats_data)),
        ("entropy_norm", lambda: check_entropy_norm(stats_data)),
        ("null_floor", lambda: check_null_floor(stats_data)),
        ("gate_ab", lambda: check_gates(stats_data, "gate_ab")),
        ("gate_aa", lambda: check_gates(stats_data, "gate_aa")),
        ("gate_ac", lambda: check_gates(stats_data, "gate_ac")),
        ("protocols (gate 1)", check_protocols),
        ("twin pairing", check_twin_pairing),
        ("_pct field naming", check_pct_naming),
    )

    all_errors: list[str] = []
    for name, fn in checks:
        errors = fn()
        print(f"[{'FAIL' if errors else 'PASS'}] {name}")
        for e in errors:
            print(f"    {e}")
        all_errors += errors

    print()
    if all_errors:
        print(f"verify.py: {len(all_errors)} failure(s)")
        return 1
    print("verify.py: all golden vectors and all protocols pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
