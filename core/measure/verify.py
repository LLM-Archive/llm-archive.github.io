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

import contextlib
import io
import json
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from core.plumbing.fake_client import FakeClient

from . import pilot, stats
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


def check_crash_safety() -> list[str]:
    """pilot.run() must never leave a half-written experiments/<run_id>/ behind after a real crash
    partway through -- fixed 2026-09-21 after finding the old code wrote straight into out_dir,
    so an interrupted run (killed session, crashed machine -- a real run takes minutes, not
    milliseconds) left just enough on disk to satisfy the FileExistsError "already ran" guard
    forever, blocking any real retry without someone deleting the wreckage by hand first. Proves
    both halves: a client that raises partway through leaves out_dir absent (only an orphaned,
    clearly-named staging dir), and a normal retry of that same run_id afterward succeeds -- not
    just "doesn't crash the test", the same run_id, for real."""
    errors = []
    protocol = json.loads(next(_PROTOCOLS.glob("*.json")).read_text())

    class _CrashingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def complete(self, prompt, *, meta):
            self.calls += 1
            if self.calls > 3:
                raise RuntimeError("simulated crash mid-run")
            return super().complete(prompt, meta=meta)

    with tempfile.TemporaryDirectory() as tmp:
        out_root = Path(tmp)
        run_date = "2026-01-16"
        run_id = f"{run_date}__{protocol['protocol_id']}__fake-subject-v1__r0"
        out_dir = out_root / run_id

        with contextlib.redirect_stdout(io.StringIO()):  # pilot.run()'s own progress marks
            try:
                pilot.run(protocol, _CrashingClient(), run_date, out_root=out_root)
                errors.append("expected the crashing client to raise, but pilot.run() returned normally")
            except RuntimeError:
                pass

        if out_dir.exists():
            errors.append(f"a crashed run left a real out_dir behind: {out_dir}")
        staging_leftovers = list(out_root.glob(f".{run_id}.partial-*"))
        if len(staging_leftovers) != 1:
            errors.append(f"expected exactly 1 orphaned staging dir after the crash, found {len(staging_leftovers)}")

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                record = pilot.run(protocol, FakeClient(), run_date, out_root=out_root)
        except FileExistsError as e:
            errors.append(f"retrying the same run_id after a crash should succeed, got FileExistsError: {e}")
        else:
            if record["run_id"] != run_id:
                errors.append(f"retry run_id mismatch: got {record['run_id']!r}, want {run_id!r}")
            if not out_dir.exists() or sorted(p.name for p in out_dir.iterdir()) != ["measurement.json", "run.json", "trials.jsonl"]:
                errors.append(f"retry didn't produce a complete out_dir: {sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else 'missing'}")

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
        ("crash safety", check_crash_safety),
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
