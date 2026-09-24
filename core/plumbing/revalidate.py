"""Re-derives already-kept runs from their raw records and reports any difference -- the
`revalidation_daily` job in cadence.yaml ("10 historical records, local, zero cost"), which had a
schedule but no code.

    python3 -m core.plumbing.revalidate run [--count 10] [--date YYYY-MM-DD]
    python3 -m core.plumbing.revalidate verify

Why: a run is kept as raw responses (`trials.jsonl`) plus a computed `measurement.json`. Nothing
else ever looks at an old run again, so a silent change would go unseen -- code that no longer
reproduces an old number, an edited file, a protocol that drifted. Re-deriving a few old runs every
day and comparing them with what was stored makes such a change visible within days, at no cost
(no API call: every input is already on disk).

For each chosen run it re-checks four things, each independently:

  1. `protocol_hash`  the protocol file today still has the `protocol_sha256` the run recorded.
  2. `prompts`        rebuilding the run's call schedule from the protocol reproduces every trial's
                      `prompt_sha256`, in order (so the panel wording is still the one that was sent).
  3. `classification` re-classifying each stored `response_text` with the frozen grammar reproduces
                      its stored outcome/token/reason.
  4. `measurement`    re-running the frozen measurement code on the stored trials reproduces
                      `measurement.json` exactly.

One difference is reported but does NOT count as a failure: `analysis_code_sha` alone. That field
records which version of `core/measure/*.py` produced a run, and that code has legitimately gained
files and edits since the earliest runs (a new grammar version, a crash-safe writer -- each its own
commit). If every other field reproduces, the numbers are unchanged, so the run is `ok` with a
NOTE saying which hash it recorded. If anything else differs too, it is a failure.

Runs are chosen by a fixed rule, not at random: sort by sha256("<date>:<run_id>") and take the first
`count`, so the same date always picks the same runs, different dates walk through the whole
archive, and nothing needs to be remembered between days.

Results are appended to `state/revalidation_log.jsonl` (append-only, one line per run per day,
`record_type: "revalidation"` -- not in the frozen `RECORD_TYPES`, same precedent as
`instrument_alarm`). Exit status is 1 if any checked run differs.

It only reads `core/measure/`; nothing here can change how a measurement is computed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from core.measure import measurement, pilot
from core.measure.chain import sha256_hex
from core.measure.client import Response, classify

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENTS = ROOT / "experiments"
DEFAULT_PROTOCOLS = ROOT / "protocols"
DEFAULT_LOG = ROOT / "state" / "revalidation_log.jsonl"

_CONTEXT_KEYS = (
    "run_id", "run_date", "subject_model_id", "returned_model_id", "model_family",
    "series", "lane", "twin_id", "condition_profile", "replicate_index",
)


def run_dirs(experiments: Path) -> list[Path]:
    """Every kept run: a directory holding a `measurement.json`. (subject_fingerprint records and
    hidden staging directories are not runs.)"""
    return sorted(d for d in experiments.iterdir() if d.is_dir() and not d.name.startswith(".") and (d / "measurement.json").exists())


def choose(dirs: list[Path], on_date: str, count: int) -> list[Path]:
    """The fixed daily selection described in the module docstring."""
    key = lambda d: hashlib.sha256(f"{on_date}:{d.name}".encode("utf-8")).hexdigest()
    return sorted(dirs, key=key)[:count]


def check_run(run_dir: Path, protocols_dir: Path) -> tuple[list[str], list[str]]:
    """(problems, notes). No problems = the run reproduces. A problem is a short string starting
    with the check that found it; a note is the analysis_code_sha-only difference described in the
    module docstring."""
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    stored = json.loads((run_dir / "measurement.json").read_text(encoding="utf-8"))
    trials = [json.loads(line) for line in (run_dir / "trials.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    protocol_path = protocols_dir / f"{run['protocol_id']}.json"
    if not protocol_path.exists():
        return [f"protocol_hash: protocols/{run['protocol_id']}.json no longer exists"], []
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))

    problems: list[str] = []
    notes: list[str] = []
    if protocol["protocol_sha256"] != run["protocol_sha256"]:
        problems.append(f"protocol_hash: run recorded {run['protocol_sha256'][:12]}, protocol file now {protocol['protocol_sha256'][:12]}")

    calls = pilot.schedule(protocol, run["run_date"], run["subject_model_id"])
    if len(calls) != len(trials):
        problems.append(f"prompts: schedule has {len(calls)} calls, run kept {len(trials)} trials")
    else:
        for call, trial in zip(calls, trials):
            if (call["version"], call["scenario_id"], call["rep"]) != (trial["version"], trial["scenario_id"], trial["rep"]):
                problems.append(f"prompts: trial {trial['index']} is not the scheduled call")
                break
            if sha256_hex(call["prompt"].encode("utf-8")) != trial["prompt_sha256"]:
                problems.append(f"prompts: trial {trial['index']} prompt hash differs from the rebuilt prompt")
                break

    bad = 0
    for trial in trials:
        result = classify(
            Response(text=trial["response_text"], stop_reason=trial["stop_reason"],
                     returned_model_id=trial["returned_model_id"], error=trial["error"]),
            protocol["options"], grammar_version=protocol["grammar_version"],
        )
        if (result.outcome, result.token, result.reason) != (trial["outcome"], trial["token"], trial["reason"]):
            bad += 1
    if bad:
        problems.append(f"classification: {bad} of {len(trials)} stored outcomes differ from a fresh classification")

    context = {k: stored[k] for k in _CONTEXT_KEYS}
    rebuilt = json.loads(json.dumps(measurement.build(protocol, trials, context)))
    diff = sorted(k for k in set(rebuilt) | set(stored) if rebuilt.get(k) != stored.get(k))
    if diff == ["analysis_code_sha"]:
        notes.append(f"analysis_code_sha: run recorded {stored['analysis_code_sha'][:12]}, code now {rebuilt['analysis_code_sha'][:12]}; every number reproduces")
    elif diff:
        problems.append(f"measurement: {len(diff)} field(s) differ: {', '.join(diff[:8])}{' ...' if len(diff) > 8 else ''}")
    return problems, notes


def revalidate(experiments: Path, protocols_dir: Path, on_date: str, count: int, log_path: Path | None) -> list[dict]:
    """Check the day's chosen runs; append one record each to `log_path` (None = don't write)."""
    records = []
    for d in choose(run_dirs(experiments), on_date, count):
        problems, notes = check_run(d, protocols_dir)
        records.append({"record_type": "revalidation", "date": on_date, "run_id": d.name, "ok": not problems, "problems": problems, "notes": notes})
    if log_path is not None and records:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def _verify() -> list[str]:
    from core.plumbing.fake_client import FakeClient

    errors: list[str] = []
    proto = json.loads(next(iter(sorted(DEFAULT_PROTOCOLS.glob("*__v0.json")))).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        exp = tmp / "experiments"
        pilot.run(proto, FakeClient(), "2026-01-15", out_root=exp)
        (run_dir,) = run_dirs(exp)

        if check_run(run_dir, DEFAULT_PROTOCOLS) != ([], []):
            errors.append(f"an untouched run must reproduce with no notes: {check_run(run_dir, DEFAULT_PROTOCOLS)}")

        def tampered(edit) -> tuple[list[str], list[str]]:
            copy = tmp / "copy"
            if copy.exists():
                shutil.rmtree(copy)
            shutil.copytree(run_dir, copy)
            edit(copy)
            return check_run(copy, DEFAULT_PROTOCOLS)

        def edit_measurement(d: Path) -> None:
            m = json.loads((d / "measurement.json").read_text())
            m["gap_pct"] = (m["gap_pct"] or 0) + 1
            (d / "measurement.json").write_text(json.dumps(m))

        def edit_response(d: Path) -> None:
            lines = (d / "trials.jsonl").read_text().splitlines()
            t = json.loads(lines[0])
            t["response_text"] = "no decision line at all"
            lines[0] = json.dumps(t)
            (d / "trials.jsonl").write_text("\n".join(lines) + "\n")

        def edit_prompt_hash(d: Path) -> None:
            lines = (d / "trials.jsonl").read_text().splitlines()
            t = json.loads(lines[3])
            t["prompt_sha256"] = "0" * 64
            lines[3] = json.dumps(t)
            (d / "trials.jsonl").write_text("\n".join(lines) + "\n")

        def edit_protocol_hash(d: Path) -> None:
            r = json.loads((d / "run.json").read_text())
            r["protocol_sha256"] = "0" * 64
            (d / "run.json").write_text(json.dumps(r))

        for name, edit, want in (
            ("measurement", edit_measurement, "measurement:"),
            ("response_text", edit_response, "classification:"),
            ("prompt_sha256", edit_prompt_hash, "prompts:"),
            ("protocol_sha256", edit_protocol_hash, "protocol_hash:"),
        ):
            got, _ = tampered(edit)
            if not any(g.startswith(want) for g in got):
                errors.append(f"editing {name} must be caught by the {want!r} check, got {got}")

        # an older code hash alone is a NOTE, not a failure; with a real difference too, a failure.
        def edit_code_hash(d: Path) -> None:
            m = json.loads((d / "measurement.json").read_text())
            m["analysis_code_sha"] = "0" * 64
            (d / "measurement.json").write_text(json.dumps(m))

        def edit_code_hash_and_number(d: Path) -> None:
            edit_code_hash(d)
            edit_measurement(d)

        problems, notes = tampered(edit_code_hash)
        if problems or not any(n.startswith("analysis_code_sha:") for n in notes):
            errors.append(f"a code-hash-only difference must be a note, not a failure: {problems}, {notes}")
        problems, _ = tampered(edit_code_hash_and_number)
        if not any(g.startswith("measurement:") for g in problems):
            errors.append("a code-hash difference plus a changed number must still fail")

        # selection: fixed per date, differs across dates, honours count.
        dirs = [tmp / f"run{i}" for i in range(30)]
        for d in dirs:
            d.mkdir()
        a, b = choose(dirs, "2026-01-01", 10), choose(dirs, "2026-01-02", 10)
        if a != choose(dirs, "2026-01-01", 10):
            errors.append("the same date must always choose the same runs")
        if len(a) != 10 or a == b:
            errors.append("selection must honour count and change across dates")

        # log: one line per run, append-only.
        log = tmp / "log.jsonl"
        revalidate(exp, DEFAULT_PROTOCOLS, "2026-02-01", 10, log)
        revalidate(exp, DEFAULT_PROTOCOLS, "2026-02-02", 10, log)
        lines = log.read_text().splitlines()
        if len(lines) != 2 or not all(json.loads(l)["ok"] for l in lines):
            errors.append(f"log must hold one ok line per run per day (got {len(lines)})")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="re-derive today's chosen runs and append the result to the log")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS)
    p.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--dry-run", action="store_true", help="report only; don't append to the log")
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/revalidate.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    records = revalidate(args.experiments, args.protocols_dir, args.date, args.count, None if args.dry_run else args.log)
    for r in records:
        print(f"{'ok  ' if r['ok'] else 'DIFF'}  {r['run_id']}")
        for prob in r["problems"]:
            print(f"        {prob}")
        for note in r["notes"]:
            print(f"        note: {note}")
    bad = sum(not r["ok"] for r in records)
    noted = sum(bool(r["notes"]) for r in records)
    print(f"\n{len(records)} run(s) checked, {bad} differ, {noted} with an older analysis_code_sha (numbers reproduce)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
