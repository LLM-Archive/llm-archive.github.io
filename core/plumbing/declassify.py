"""Declassification: publishing all 15 scenarios (and the full traces) of a protocol that has left
the rotation (spec.md §10, "Lanes and raw traces").

    python3 -m core.plumbing.declassify status                  # every protocol: could it be declassified today? (free)
    python3 -m core.plumbing.declassify check <protocol_id>     # one protocol, every reason it can't (free)
    python3 -m core.plumbing.declassify bridge <old_run_dir> <new_run_dir>   # old vs new panel, same model
    python3 -m core.plumbing.declassify build [--out-dir .]     # writes declassified/ for the deploy
    python3 -m core.plumbing.declassify verify

Why this exists: spec.md §10 promised "full traces on declassification" for `guard`, but until
2026-10-03 no code did it -- the only thing deciding what a build publishes was the protocol's
`lane` field, and flipping that field is not possible (it would break `protocol_sha256`). Even
for the two `open` protocols only the FIRST scenario's text was ever published (render.py's
`_example_texts`), so nobody outside could run the same panel on their own model.

What it does, and deliberately does not do:

  * Declassification stays an editorial act. The ONLY input is `declassified.json` at the top of
    the private repo, edited by hand: one entry per protocol_id, with the date and a reason. The
    list ships empty. Nothing here ever adds to it, and no calendar or generation count is read
    (`cadence.yaml`'s `declassify_after` is still a policy constant, not a countdown).
  * It refuses, at `build` time, any listed protocol that fails one of these rules -- so a deploy
    with a premature entry stops instead of publishing:
      1. the protocol is `admitted` and its lane is not `sealed` (a `sealed` panel is never opened);
      2. it is no longer current: a later version is admitted (core/plumbing/versions.py), so the
         sweep no longer measures it -- "never open without first closing";
      3. no CURRENT protocol shares its panel (panel_sha256) or its twin's panel. This is what
         makes v0 undeclassifiable while v1 is current: v0 and v1 have the same 15 scenarios, so
         opening v0 would open v1. The twin is included because an `open` protocol and its
         `guard` twin are built from the same base scenarios (spec.md §10's twins);
      4. a bridge exists: the old panel and its current successor were both measured on the same
         subject model within BRIDGE_WINDOW_DAYS of each other, so the series has a link across
         the panel change before the old panel's text becomes public.
    One protocol family at a time, so witnesses always remain, is an editorial pace the owner
    keeps; it is not enforced here.
  * `build` writes, for each listed protocol, `declassified/<protocol_id>.json` (the protocol
    file exactly as measured, so anyone can recompute protocol_sha256 and panel_sha256 with
    core/measure/chain.py and match them against stability.csv) and
    `declassified/<protocol_id>.trials.jsonl` (every trial of every run of it, same shape as
    open-lane/<year>.jsonl). With an empty list it writes nothing at all.
  * `bridge` compares two measurements of DIFFERENT panels on the same model with the same rule
    stats.compare() uses for the same panel (sampling limit in quadrature, drop bounds added).
    stats.compare() itself is frozen and documented for the same protocol only, so its
    "improved"/"regressed" words are not reused here: across panels the answer is only whether
    the two panels give the same number on that model (`flat`) or not (`differs`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from core.measure import stats
from core.measure.chain import compute_panel_sha256, compute_protocol_sha256

from . import versions

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIST = ROOT / "declassified.json"
DEFAULT_PROTOCOLS = ROOT / "protocols"
DEFAULT_EXPERIMENTS = ROOT / "experiments"
OUT_SUBDIR = "declassified"

# The generation bridge (spec.md §4.3) measures old and new in "the same week"; the panel bridge
# uses the same window, for the same reason: the shorter the gap, the less the model can have
# drifted between the two measurements.
BRIDGE_WINDOW_DAYS = 7


def load_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = json.loads(path.read_text(encoding="utf-8")).get("declassified", [])
    for e in entries:
        if not e.get("protocol_id") or not e.get("date") or not e.get("reason"):
            raise ValueError(f"{path}: every entry needs protocol_id, date and reason, got {e!r}")
    return entries


def load_protocols(protocols_dir: Path) -> dict[str, dict]:
    return {p["protocol_id"]: p for p in (json.loads(f.read_text(encoding="utf-8")) for f in sorted(protocols_dir.glob("*.json")))}


def load_measurements(experiments_dir: Path) -> list[dict]:
    """Finished runs only: a `.<run_id>.partial-*` staging dir (core/measure/pilot.py) is skipped."""
    out = []
    for path in sorted(experiments_dir.glob("*/measurement.json")):
        if not path.parent.name.startswith("."):
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def _version_prefix(protocol_id: str) -> str:
    m = versions._VERSION_SUFFIX.match(protocol_id)
    return m["family"] if m else protocol_id


def _twin_panels(protocol: dict, protocols: dict[str, dict]) -> set[str]:
    """The panels of this protocol's twin(s): the one its twin_id names, and any protocol whose
    twin_id names it (the link is stored on one side only for some pairs)."""
    twins = {protocol.get("twin_id")} | {pid for pid, p in protocols.items() if p.get("twin_id") == protocol["protocol_id"]}
    return {protocols[t]["panel_sha256"] for t in twins if t in protocols}


def bridge_runs(protocol: dict, successor: dict, measurements: list[dict]) -> list[tuple[dict, dict]]:
    """(old, new) measurement pairs on the same subject model within BRIDGE_WINDOW_DAYS: old is
    any run on this protocol's panel, new is a run of its current successor."""
    old = [m for m in measurements if m.get("panel_sha256") == protocol["panel_sha256"]]
    new = [m for m in measurements if m.get("protocol_id") == successor["protocol_id"]]
    pairs = []
    for o in old:
        for n in new:
            if o["subject_model_id"] != n["subject_model_id"]:
                continue
            days = abs((date.fromisoformat(n["run_date"]) - date.fromisoformat(o["run_date"])).days)
            if days <= BRIDGE_WINDOW_DAYS:
                pairs.append((o, n))
    return pairs


def reasons_not(protocol_id: str, protocols: dict[str, dict], measurements: list[dict]) -> list[str]:
    """Every rule (module docstring, 1-4) this protocol fails today. Empty means it may be declassified."""
    protocol = protocols.get(protocol_id)
    if protocol is None:
        return [f"no protocol file for {protocol_id}"]
    if protocol.get("status") != "admitted":
        return [f"status is {protocol.get('status')!r}, not 'admitted'"]
    if protocol.get("lane") == "sealed":
        return ["lane is 'sealed': a sealed panel is never opened"]

    reasons = []
    current = versions.latest_versions([p for p in protocols.values() if p.get("status") == "admitted"])
    current_ids = {p["protocol_id"] for p in current}
    if protocol_id in current_ids:
        reasons.append("still current: no later version is admitted, so the sweep still measures it")

    exposed = {protocol["panel_sha256"]} | _twin_panels(protocol, protocols)
    sharing = sorted(p["protocol_id"] for p in current if p["panel_sha256"] in exposed and p["protocol_id"] != protocol_id)
    if sharing:
        reasons.append(f"current protocol(s) share its panel or its twin's panel: {', '.join(sharing)}")

    successor = next((p for p in current if _version_prefix(p["protocol_id"]) == _version_prefix(protocol_id)), None)
    if successor is None or successor["protocol_id"] == protocol_id:
        reasons.append("no successor to bridge to")
    elif not bridge_runs(protocol, successor, measurements):
        reasons.append(
            f"no bridge: no run on this panel and a run of {successor['protocol_id']} on the same model "
            f"within {BRIDGE_WINDOW_DAYS} days of each other"
        )
    return reasons


def round_done(protocols: dict[str, dict], measurements: list[dict], model_id: str) -> tuple[int, int]:
    """(measured, total): how many current protocols have at least one run on model_id. When the
    two are equal the owner is asked, once, whether to start declassifying (`decided` in
    declassified.json silences the question) -- see tools/overview.py's section_needs."""
    current = versions.latest_versions([p for p in protocols.values() if p.get("status") == "admitted"])
    done = {m["protocol_id"] for m in measurements if m.get("subject_model_id") == model_id}
    return sum(p["protocol_id"] in done for p in current), len(current)


def load_decided(path: Path) -> str | None:
    """The date the owner answered the round-done question, or None if not yet asked/answered."""
    return json.loads(path.read_text(encoding="utf-8")).get("decided") if path.is_file() else None


def _check_hashes(protocol: dict) -> list[str]:
    errors = []
    if compute_panel_sha256(protocol["scenarios"]) != protocol["panel_sha256"]:
        errors.append("panel_sha256 does not match the scenarios")
    if compute_protocol_sha256(protocol) != protocol["protocol_sha256"]:
        errors.append("protocol_sha256 does not match the protocol")
    return errors


def build(list_path: Path, protocols_dir: Path, experiments_dir: Path, out_dir: Path) -> dict[str, int]:
    """Writes <out_dir>/declassified/ for every listed protocol, or nothing if the list is empty.
    Raises ValueError, writing nothing, if any listed protocol fails a rule. Returns
    {protocol_id: trials written}."""
    entries = load_list(list_path)
    target = out_dir / OUT_SUBDIR
    if not entries:
        if target.exists():
            # Generated output only, like open-lane/: a stale copy must not reach a deploy after
            # the list was emptied. (Anything already deployed stays public -- this cannot undo it.)
            shutil.rmtree(target)
        return {}

    protocols = load_protocols(protocols_dir)
    measurements = load_measurements(experiments_dir)
    problems = []
    for e in entries:
        pid = e["protocol_id"]
        problems += [f"{pid}: {r}" for r in reasons_not(pid, protocols, measurements)]
        if pid in protocols:
            problems += [f"{pid}: {r}" for r in _check_hashes(protocols[pid])]
    if problems:
        raise ValueError("refusing to declassify:\n  " + "\n  ".join(problems))

    counts = {}
    staging = Path(tempfile.mkdtemp(dir=out_dir, prefix=f".{OUT_SUBDIR}.partial-"))
    for e in entries:
        pid = e["protocol_id"]
        protocol = protocols[pid]
        (staging / f"{pid}.json").write_text(json.dumps(protocol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        trials = []
        for m in measurements:
            if m.get("protocol_id") != pid:
                continue
            path = experiments_dir / m["run_id"] / "trials.jsonl"
            trials += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        trials.sort(key=lambda t: (t.get("run_id", ""), t.get("index", 0)))
        with open(staging / f"{pid}.trials.jsonl", "w", encoding="utf-8") as f:
            for t in trials:
                f.write(json.dumps(t, sort_keys=True, ensure_ascii=False) + "\n")
        counts[pid] = len(trials)
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    return counts


def compare_panels(old: dict, new: dict) -> dict:
    """Two measurements of different panels on the same model: the difference, and whether it
    clears stats.compare()'s two thresholds (module docstring)."""
    if old["subject_model_id"] != new["subject_model_id"]:
        raise ValueError("a panel bridge needs the same subject model on both sides")
    if old.get("panel_sha256") == new.get("panel_sha256"):
        raise ValueError("same panel on both sides: use stats.compare(), this is not a panel bridge")
    if old.get("stability_pct") is None or new.get("stability_pct") is None:
        return {"result": "no_measurement", "delta_pct": None}
    days = abs((date.fromisoformat(new["run_date"]) - date.fromisoformat(old["run_date"])).days)
    return {
        "result": "flat" if stats.compare(old, new) == "flat" else "differs",
        "delta_pct": round(new["stability_pct"] - old["stability_pct"], 1),
        "days_apart": days,
        "within_window": days <= BRIDGE_WINDOW_DAYS,
    }


def _verify() -> list[str]:
    errors: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    def proto(pid: str, panel: str, lane: str = "guard", twin: str | None = None, status: str = "admitted") -> dict:
        return {"protocol_id": pid, "panel_sha256": panel, "lane": lane, "twin_id": twin, "status": status}

    def meas(pid: str, panel: str, model: str, day: str, stab: float = 50.0) -> dict:
        return {"protocol_id": pid, "panel_sha256": panel, "subject_model_id": model, "run_date": day,
                "run_id": f"{day}__{pid}__{model}__r0", "stability_pct": stab, "ci_low_pct": stab - 5,
                "ci_high_pct": stab + 5, "drop_bound_pct_ab": 1.0}

    # today's shape: v0 and v1 share every panel, v1 current
    ps = {p["protocol_id"]: p for p in (
        proto("f__wording__v0", "W", "open", "f__anchoring__v0"), proto("f__wording__v1", "W", "open", "f__anchoring__v1"),
        proto("f__anchoring__v0", "N", "guard", "f__wording__v0"), proto("f__anchoring__v1", "N", "guard", "f__wording__v1"),
        proto("f__order__v0", "O"), proto("f__order__v1", "O"), proto("s__x__v0", "S", "sealed"))}
    expect(any("share" in r for r in reasons_not("f__order__v0", ps, [])), "v0 must be refused while v1 shares its panel")
    expect(any("still current" in r for r in reasons_not("f__order__v1", ps, [])), "a current protocol must be refused")
    expect(reasons_not("s__x__v0", ps, []) == ["lane is 'sealed': a sealed panel is never opened"], "sealed is never opened")
    expect(reasons_not("nope__v0", ps, []) == ["no protocol file for nope__v0"], "unknown id must be refused")

    # v2 admitted with new panels: v1 leaves the rotation
    ps2 = {**ps, **{p["protocol_id"]: p for p in (
        proto("f__wording__v2", "W2", "open", "f__anchoring__v2"), proto("f__anchoring__v2", "N2", "guard", "f__wording__v2"),
        proto("f__order__v2", "O2"), proto("f__order__v3", "O3", status="candidate"))}}
    bridged = [meas("f__order__v1", "O", "m", "2027-01-02"), meas("f__order__v2", "O2", "m", "2027-01-06")]
    expect(reasons_not("f__order__v1", ps2, bridged) == [], f"bridged, superseded v1 must pass: {reasons_not('f__order__v1', ps2, bridged)}")
    expect(reasons_not("f__order__v0", ps2, bridged) == [], "v0 on the same panel is covered by v1's bridge run")
    expect(any("no bridge" in r for r in reasons_not("f__order__v1", ps2, [])), "no runs: no bridge")
    far = [meas("f__order__v1", "O", "m", "2027-01-02"), meas("f__order__v2", "O2", "m", "2027-01-20")]
    expect(any("no bridge" in r for r in reasons_not("f__order__v1", ps2, far)), "runs 18 days apart are no bridge")
    other = [meas("f__order__v1", "O", "m", "2027-01-02"), meas("f__order__v2", "O2", "q", "2027-01-03")]
    expect(any("no bridge" in r for r in reasons_not("f__order__v1", ps2, other)), "two different models are no bridge")
    # a candidate v3 does not displace the admitted v2 as successor
    expect(reasons_not("f__order__v1", ps2, bridged) == [], "a candidate must not become the successor")

    # the twin: if v2 reused the guard twin's panel, opening the open protocol would expose a current one
    ps3 = {**ps2, "f__anchoring__v2": proto("f__anchoring__v2", "N", "guard", "f__wording__v2")}
    wb = [meas("f__wording__v1", "W", "m", "2027-01-02"), meas("f__wording__v2", "W2", "m", "2027-01-02")]
    expect(any("f__anchoring__v2" in r for r in reasons_not("f__wording__v1", ps3, wb)), "a current twin on the same panel must block")
    expect(reasons_not("f__wording__v1", ps2, wb) == [], "twin with a new panel: no block")

    # round_done: counts current protocols only, on the given model only
    runs = [meas("f__order__v1", "O", "m", "2027-01-01"), meas("f__wording__v1", "W", "m", "2027-01-01"),
            meas("f__anchoring__v1", "N", "q", "2027-01-01"), meas("f__anchoring__v0", "N", "m", "2027-01-01")]
    expect(round_done(ps, runs, "m") == (2, 4), f"2 of the 4 current protocols ran on m: {round_done(ps, runs, 'm')}")

    # compare_panels
    c = compare_panels(meas("a__v1", "P1", "m", "2027-01-01", 50.0), meas("a__v2", "P2", "m", "2027-01-03", 70.0))
    expect(c["result"] == "differs" and c["delta_pct"] == 20.0 and c["within_window"], f"20 points apart must differ: {c}")
    c = compare_panels(meas("a__v1", "P1", "m", "2027-01-01", 50.0), meas("a__v2", "P2", "m", "2027-01-30", 55.0))
    expect(c["result"] == "flat" and not c["within_window"], f"5 points with +-5 intervals is flat: {c}")
    for bad in ((meas("a__v1", "P1", "m", "2027-01-01"), meas("a__v2", "P2", "q", "2027-01-01")),
                (meas("a__v1", "P1", "m", "2027-01-01"), meas("a__v1", "P1", "m", "2027-01-02"))):
        try:
            compare_panels(*bad)
            errors.append("compare_panels must refuse a different model or the same panel")
        except ValueError:
            pass

    # build: empty list writes nothing; a premature entry writes nothing and raises; a good one writes both files
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        pdir, edir, out = t / "protocols", t / "experiments", t / "out"
        for d in (pdir, edir, out):
            d.mkdir()
        scen = [{"scenario_id": "s01", "options": ["A", "B"], "versions": {"A": "a", "A_prime": "a'", "B": "b", "C": "c"}}]
        base = {"family": "f", "rewording_type": "order", "n": 4, "n_per_scenario": 1, "effort": "disabled",
                "grammar_version": 2, "options": ["A", "B"], "prompt_template": "{scenario}", "theta_positive_pct": 15,
                "scenario_dominated_share_pct": 40, "scenarios": scen, "status": "admitted", "lane": "guard", "twin_id": None}
        for pid, text in (("f__order__v1", "a"), ("f__order__v2", "z")):
            p = {**base, "protocol_id": pid, "scenarios": [{**scen[0], "versions": {**scen[0]["versions"], "A": text}}]}
            p["panel_sha256"] = compute_panel_sha256(p["scenarios"])
            p["protocol_sha256"] = compute_protocol_sha256(p)
            (pdir / f"{pid}.json").write_text(json.dumps(p))
            panel = p["panel_sha256"]
            m = meas(pid, panel, "m", "2027-01-02")
            (edir / m["run_id"]).mkdir()
            (edir / m["run_id"] / "measurement.json").write_text(json.dumps(m))
            (edir / m["run_id"] / "trials.jsonl").write_text(json.dumps({"run_id": m["run_id"], "index": 0}) + "\n")
        lst = t / "declassified.json"
        lst.write_text(json.dumps({"declassified": []}))
        expect(build(lst, pdir, edir, out) == {} and not (out / OUT_SUBDIR).exists(), "an empty list must write nothing")
        lst.write_text(json.dumps({"declassified": [{"protocol_id": "f__order__v2", "date": "2027-02-01", "reason": "x"}]}))
        try:
            build(lst, pdir, edir, out)
            errors.append("build must refuse a current protocol")
        except ValueError:
            expect(not (out / OUT_SUBDIR).exists(), "a refused build must write nothing")
        lst.write_text(json.dumps({"declassified": [{"protocol_id": "f__order__v1", "date": "2027-02-01", "reason": "x"}]}))
        counts = build(lst, pdir, edir, out)
        written = json.loads((out / OUT_SUBDIR / "f__order__v1.json").read_text())
        expect(counts == {"f__order__v1": 1} and _check_hashes(written) == [], f"a good entry must write its protocol and trials: {counts}")
        expect(not list(out.glob(".*partial*")), "no staging dir may be left behind")
        lst.write_text(json.dumps({"declassified": [{"protocol_id": "f__order__v1"}]}))
        try:
            build(lst, pdir, edir, out)
            errors.append("an entry without date and reason must be refused")
        except ValueError:
            pass
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST)
    ap.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS)
    ap.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="every admitted protocol: could it be declassified today?")
    p_check = sub.add_parser("check", help="one protocol: every reason it can't be declassified yet")
    p_check.add_argument("protocol_id")
    p_bridge = sub.add_parser("bridge", help="compare two runs of different panels on the same model")
    p_bridge.add_argument("old_run_dir", type=Path)
    p_bridge.add_argument("new_run_dir", type=Path)
    p_build = sub.add_parser("build", help="write declassified/ for every protocol in declassified.json")
    p_build.add_argument("--out-dir", type=Path, default=ROOT)
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        for e in errors:
            print(f"FAIL  {e}")
        print("declassify verify:", "FAILED" if errors else "all cases pass")
        return 1 if errors else 0

    if args.command == "bridge":
        old, new = (json.loads((d / "measurement.json").read_text(encoding="utf-8")) for d in (args.old_run_dir, args.new_run_dir))
        result = compare_panels(old, new)
        print(f"old  {old['protocol_id']:<40} {old['run_date']}  stability {old.get('stability_pct')}")
        print(f"new  {new['protocol_id']:<40} {new['run_date']}  stability {new.get('stability_pct')}")
        print(json.dumps(result))
        if not result.get("within_window", True):
            print(f"warning: {result['days_apart']} days apart, more than {BRIDGE_WINDOW_DAYS}: the model may have moved in between")
        return 0

    if args.command == "build":
        try:
            counts = build(args.list, args.protocols_dir, args.experiments_dir, args.out_dir)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"declassified/: {counts}" if counts else "declassified.json lists nothing: nothing written")
        return 0

    protocols = load_protocols(args.protocols_dir)
    measurements = load_measurements(args.experiments_dir)
    listed = {e["protocol_id"] for e in load_list(args.list)}
    ids = [args.protocol_id] if args.command == "check" else sorted(pid for pid, p in protocols.items() if p.get("status") == "admitted")
    for pid in ids:
        reasons = reasons_not(pid, protocols, measurements)
        mark = "listed" if pid in listed else ""
        print(f"{'READY' if not reasons else 'no':<6}{pid:<44}{mark}")
        for r in reasons:
            print(f"        - {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
