"""Turns the 600 trials of one protocol run into one measurement record (spec.md §6, §10).

The central idea this file implements: a big gap between condition A and condition B does not,
by itself, mean anything. It could be sampling luck, it could be an artifact of one condition
losing more responses than the other, or it could be that the model doesn't read the question
carefully in the first place. So every measurement runs three checks against itself before it is
allowed to headline anything, using two extra conditions collected alongside A and B for exactly
this purpose:

  A′ (null change)      — a change that is NOT supposed to matter. If A vs B doesn't beat A vs A′,
                           the "effect" is no bigger than noise from irrelevant surface changes.
  C  (substantive change) — a change that IS supposed to matter. If A vs C doesn't move at all,
                           the model isn't engaging with the content of the question, and neither
                           the A-vs-B nor the A-vs-A′ comparison can be trusted either.

A measurement that fails any of these still gets published — with the reason attached — rather
than silently dropped. Being wrong in public and saying so is the whole point of an archive; being
quietly filtered would defeat it.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from pathlib import Path

from . import stats
from .chain import sha256_hex, sha256_of_files
from .grammar import GRAMMAR_VERSION
from .grammar_v2 import GRAMMAR_VERSION as GRAMMAR_VERSION_V2
from .rng import SplitMix64, seed_from
from .schema import OUTCOMES, SCHEMA_VERSION, VALID, VERSION_KEYS, VERSIONS, schema_sha256

# Frozen thresholds. Changing any of these after seeing data would let the project quietly redraw
# the line between "trustworthy" and "not" to fit whatever result came back — the whole value of
# a pre-registered threshold is that it was fixed before the data existed to argue about it.
DROP_BOUND_MAX_PCT = Fraction(2)          # worst-case error from lost responses must stay under 2 points
DROP_ASYMMETRY_MAX_PCT = Fraction(1)      # the two conditions may not lose responses more than 1 point apart
DEGENERATE_ENTROPY_MAX = 0.20             # below this, "always the same answer" is suspected, not "stable"
DEGENERATE_STABILITY_MIN_PCT = 90         # ...but only flagged if stability also looks suspiciously perfect

# The three comparisons every measurement needs (see the module docstring): the real one (ab),
# the one that should show nothing (aa), and the one that should show a lot (ac).
PAIRS = {"ab": ("A", "B"), "aa": ("A", "A_prime"), "ac": ("A", "C")}

_MEASURE_DIR = Path(__file__).resolve().parent

# Which source file grammar_sha reports on, per grammar_version -- additive alongside
# invariants.py's KNOWN_GRAMMAR_VERSIONS and client.py's _EXTRACTORS as new versions appear.
_GRAMMAR_FILES = {GRAMMAR_VERSION: "grammar.py", GRAMMAR_VERSION_V2: "grammar_v2.py"}


def extractor_sha(grammar_version: int = GRAMMAR_VERSION) -> str:
    return sha256_hex((_MEASURE_DIR / _GRAMMAR_FILES[grammar_version]).read_bytes())


def analysis_code_sha() -> str:
    return sha256_of_files(sorted(_MEASURE_DIR.glob("*.py")), _MEASURE_DIR)


def _pub(x: Fraction | None) -> float | None:
    return None if x is None else float(round(x, 6))


def _threshold(value) -> Fraction | None:
    return None if value is None else Fraction(str(value))


def evaluate_gates(
    gap_pct: dict[str, Fraction | None],
    bound: dict[str, Fraction],
    asym: dict[str, Fraction],
    theta: Fraction | None,
) -> tuple[dict, list[str], list[str]]:
    """The three checks from the module docstring above, made concrete.

    Every comparison below is deliberately set up to fail in the direction that hurts this
    project's own result, never the direction that flatters it: the drop bound is checked as a
    ceiling that must NOT be exceeded, the null-change gap has to be cleared with room to spare
    (not just barely), and the positive control has to move by more than its own worst-case error
    could explain. A threshold tuned the other way would let a small, favorable measurement error
    manufacture a headline that isn't really there.

    Keys of the three input dicts are "ab" (A vs B, the real comparison), "aa" (A vs A′, the null
    change) and "ac" (A vs C, the positive control). Returns (gate detail, flags, unset thresholds).
    """
    flags: list[str] = []
    unset: list[str] = []
    gates: dict = {}

    for p, (x, y) in PAIRS.items():
        gates[f"{x}-{y}"] = {
            "drop_bound_ok": bound[p] <= DROP_BOUND_MAX_PCT,
            "asymmetry_ok": asym[p] <= DROP_ASYMMETRY_MAX_PCT,
        }
    if any(not g["drop_bound_ok"] for g in gates.values()):
        flags.append("drop_confounded")
    if any(not g["asymmetry_ok"] for g in gates.values()):
        flags.append("asymmetric_missingness")

    above_bound = gap_pct["ab"] is not None and bound["ab"] < gap_pct["ab"]
    gates["A-B"]["above_drop_bound"] = above_bound
    if not above_bound:
        flags.append("below_drop_bound")

    above_noise = (
        gap_pct["ab"] is not None
        and gap_pct["aa"] is not None
        and gap_pct["ab"] - bound["ab"] > gap_pct["aa"] + bound["aa"]
    )
    gates["A-A_prime"]["above_surface_noise"] = above_noise
    if not above_noise:
        flags.append("below_surface_noise")

    if theta is None:
        unset.append("theta_positive_pct")
        gates["A-C"]["reading"] = None
    else:
        reading = gap_pct["ac"] is not None and gap_pct["ac"] - bound["ac"] > theta
        gates["A-C"]["reading"] = reading
        if not reading:
            flags.append("not_reading")
    return gates, flags, unset


def _scenario_gaps_pct(scenario_ids: list[str], by_version: dict, options: list[str]) -> dict[str, Fraction | None]:
    """The A↔B gap computed separately within each of the 15 scenarios, rather than only once
    over all of them pooled together. Published so a reader can see whether the headline gap is
    spread evenly across scenarios or driven by just one or two unusual ones (that's what
    scenario_concentration below is for) — a distinction the pooled gap alone can't show."""

    def scenario_counts(version: str, sid: str) -> list[int]:
        tokens = [t["token"] for t in by_version[version] if t["scenario_id"] == sid and t["outcome"] == VALID]
        return stats.counts(tokens, options)

    gaps = {}
    for sid in scenario_ids:
        g = stats.tvd(scenario_counts("A", sid), scenario_counts("B", sid))
        gaps[sid] = None if g is None else 100 * g
    return gaps


def build(protocol: dict, trials: list[dict], context: dict) -> dict:
    """context: run_id, run_date, subject_model_id, returned_model_id, model_family, series,
    lane, condition_profile, replicate_index."""
    options = protocol["options"]
    by_version = {v: [t for t in trials if t["version"] == v] for v in VERSIONS}

    n = {v: len(ts) for v, ts in by_version.items()}
    valid = {v: [t["token"] for t in ts if t["outcome"] == VALID] for v, ts in by_version.items()}
    decision_counts = {v: stats.counts(valid[v], options) for v in VERSIONS}
    u = {v: stats.loss_rate(n[v], len(valid[v])) for v in VERSIONS}
    gap = {p: stats.tvd(decision_counts[x], decision_counts[y]) for p, (x, y) in PAIRS.items()}
    gap_pct = {p: None if g is None else 100 * g for p, g in gap.items()}
    bound = {p: stats.drop_bound_pct(u[x], u[y]) for p, (x, y) in PAIRS.items()}
    asym = {p: stats.drop_asymmetry_pct(u[x], u[y]) for p, (x, y) in PAIRS.items()}

    gates, flags, unset = evaluate_gates(gap_pct, bound, asym, _threshold(protocol.get("theta_positive_pct")))

    # Stability alone can't tell "this model reads every version the same way" apart from "this
    # model gives the same answer no matter what it's asked" — both produce gap ≈ 0. Entropy can
    # tell them apart: a model that always answers the same way has near-zero entropy regardless
    # of the question, while a model that's actually engaging with varied scenarios does not.
    entropy_a = stats.entropy_norm(decision_counts["A"])
    entropy_b = stats.entropy_norm(decision_counts["B"])
    stability_pct = None if gap_pct["ab"] is None else 100 - gap_pct["ab"]
    degenerate = (
        entropy_a is not None
        and entropy_b is not None
        and max(entropy_a, entropy_b) < DEGENERATE_ENTROPY_MAX
        and stability_pct > DEGENERATE_STABILITY_MIN_PCT
    )
    # grammar_version doubles as this protocol's whole analysis-code series (spec.md §2: changing
    # n or the decision grammar already mints a new series on its own, so a v1-series protocol
    # bundles every analysis-code difference of that same series behind one field rather than a
    # second version number for no added information). Real data (decisions.md §47, §48) showed
    # this flag firing on measurements where the A-C gate had already, directly, proven the model
    # engages with the content of the question — degenerate_candidate exists to catch "answers
    # the same regardless of the question", and a passing positive control is direct evidence
    # against exactly that, stronger than the entropy proxy this flag otherwise relies on. v1
    # protocols are entirely unaffected: this only narrows the flag for grammar_version >= 2.
    if degenerate and protocol["grammar_version"] >= GRAMMAR_VERSION_V2 and gates["A-C"]["reading"]:
        degenerate = False
    if degenerate:
        flags.append("degenerate_candidate")

    # The one random stream of this measurement: bootstrap first, then the null floor.
    rng = SplitMix64(seed_from(protocol["protocol_id"], context["run_date"], context["subject_model_id"]))
    interval = stats.bootstrap_gap_interval(decision_counts["A"], decision_counts["B"], rng)
    floor = stats.null_floor(decision_counts["A"], decision_counts["B"], rng)
    ci_low_pct = None if interval is None else 100 - 100 * interval[1]
    ci_high_pct = None if interval is None else 100 - 100 * interval[0]
    null_floor_pct = None if floor is None else 100 * floor

    scenario_ids = [s["scenario_id"] for s in protocol["scenarios"]]
    scenario_gaps = _scenario_gaps_pct(scenario_ids, by_version, options)
    known = [g for g in scenario_gaps.values() if g is not None]
    concentration = stats.concentration(known) if known else None
    concentration_pct = None if concentration is None else 100 * concentration

    share = _threshold(protocol.get("scenario_dominated_share_pct"))
    if share is None:
        unset.append("scenario_dominated_share_pct")
        gates["scenario_dominated"] = None
    else:
        dominated = concentration_pct is not None and concentration_pct > share
        gates["scenario_dominated"] = dominated
        if dominated:
            flags.append("scenario_dominated")

    gates["unset_thresholds"] = unset
    gates["all_passed"] = not flags and not unset
    # A measurement only counts toward the published curve if it is clean (no flags, no thresholds
    # left undecided) AND its protocol has actually been admitted (not still a candidate awaiting
    # review) AND it's from a lane the public curve is allowed to draw from — a sealed protocol's
    # result exists in this record, but must never surface on any chart.
    on_curve = gates["all_passed"] and protocol["status"] == "admitted" and context["lane"] in ("open", "guard")

    return {
        "record_type": "measurement",
        "schema_version": SCHEMA_VERSION,
        "schema_sha256": schema_sha256(),
        "run_id": context["run_id"],
        "run_date": context["run_date"],
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "panel_sha256": protocol["panel_sha256"],
        "rewording_type": protocol["rewording_type"],
        "subject_model_id": context["subject_model_id"],
        "returned_model_id": context["returned_model_id"],
        "model_family": context["model_family"],
        "series": context["series"],
        "lane": context["lane"],
        "twin_id": context["twin_id"],
        "condition_profile": context["condition_profile"],
        "replicate_index": context["replicate_index"],
        "grammar_version": protocol["grammar_version"],
        "extractor_sha": extractor_sha(protocol["grammar_version"]),
        "analysis_code_sha": analysis_code_sha(),
        "stability_pct": _pub(stability_pct),
        "ci_low_pct": _pub(ci_low_pct),
        "ci_high_pct": _pub(ci_high_pct),
        "gap_pct": _pub(gap_pct["ab"]),
        "gap_null_pct": _pub(gap_pct["aa"]),
        "gap_positive_pct": _pub(gap_pct["ac"]),
        "null_floor_pct": _pub(null_floor_pct),
        "at_noise_floor": None
        if stability_pct is None or null_floor_pct is None
        else stability_pct >= 100 - null_floor_pct,
        "n": protocol["n"],
        "n_valid": {v: len(valid[v]) for v in VERSIONS},
        "n_items": len(scenario_ids),
        "k": len(options),
        "entropy_a": None if entropy_a is None else round(entropy_a, 6),
        "entropy_b": None if entropy_b is None else round(entropy_b, 6),
        **{f"u_{VERSION_KEYS[v]}": _pub(u[v]) for v in VERSIONS},
        **{f"drop_bound_pct_{p}": _pub(bound[p]) for p in PAIRS},
        **{f"drop_asymmetry_pct_{p}": _pub(asym[p]) for p in PAIRS},
        "scenario_gaps_pct": {sid: _pub(g) for sid, g in scenario_gaps.items()},
        "scenario_spread_pct": None if not known else {k: _pub(v) for k, v in stats.spread(known).items()},
        "scenario_concentration_pct": _pub(concentration_pct),
        "outcomes": {v: {o: Counter(t["outcome"] for t in by_version[v])[o] for o in OUTCOMES} for v in VERSIONS},
        "decisions": {v: dict(zip(options, decision_counts[v])) for v in VERSIONS},
        "flags": flags,
        "gates": gates,
        "on_curve": on_curve,
    }
