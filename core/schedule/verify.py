"""Replays core/schedule/vectors.json against schedule.py.

    python3 -m core.schedule.verify
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

from core.budget import budget
from core.plumbing.fake_client import FakeClient

from . import coverage, run_due, schedule

_VECTORS = Path(__file__).resolve().parent / "vectors.json"
_REAL_PROTOCOLS_DIR = Path(__file__).resolve().parent.parent.parent / "protocols"
_REAL_SUBJECT_MODELS = Path(__file__).resolve().parent.parent.parent / "subject_models.yaml"


class _StubClient(FakeClient):
    """AnthropicClient's constructor shape (model_id, model_family) over FakeClient's real
    response distribution -- lets check_run_full_sweep_ladder below exercise run_full_sweep's
    real "anthropic" code path (the one with the ladder logic under test) without a real API
    key or network call. Never used outside this test."""

    def __init__(self, model_id: str, model_family: str) -> None:
        super().__init__(seed=model_id)
        self.subject_model_id = model_id
        self.model_family = model_family


def check_parse_cadence(data: dict) -> list[str]:
    errors = []
    for case in data["parse_cadence"]:
        got = schedule.parse_cadence(case["text"])
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got}, want {case['expected']}")
    return errors


def check_due_jobs(data: dict) -> list[str]:
    errors = []
    for case in data["due_jobs"]:
        got = schedule.due_jobs(case["intervals"], case["last_run"], date.fromisoformat(case["today"]))
        for job, want in case["expected"].items():
            for key, expected_value in want.items():
                if got[job][key] != expected_value:
                    errors.append(f"{case['name']}: {job}.{key} got {got[job][key]!r}, want {expected_value!r}")
    return errors


def check_plan(data: dict) -> list[str]:
    errors = []
    for case in data["plan"]:
        got = run_due.plan(case["due"])
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got}, want {case['expected']}")
    return errors


def check_record_run(data: dict) -> list[str]:
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "last_run.json"
        for case in data["record_run"]:
            try:
                schedule.record_run(path, case["job"], case["run_date"])
                if case["expect_error"]:
                    errors.append(f"{case['name']}: expected an error, got none")
            except ValueError:
                if not case["expect_error"]:
                    errors.append(f"{case['name']}: unexpected error")
    return errors


def check_gap_cause_for(data: dict) -> list[str]:
    errors = []
    for case in data["gap_cause_for"]:
        got = coverage.gap_cause_for(case["runner"], set(case["skip_reasons"]))
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got!r}, want {case['expected']!r}")
    return errors


def check_record_observation(data: dict) -> list[str]:
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coverage_log.jsonl"
        for case in data["record_observation"]:
            try:
                coverage.record_observation(
                    case["job"], case["date"], ran=case["ran"], cause=case["cause"], log_path=path
                )
                if case["expect_error"]:
                    errors.append(f"{case['name']}: expected an error, got none")
            except ValueError:
                if not case["expect_error"]:
                    errors.append(f"{case['name']}: unexpected error")
    return errors


def _write_ledger(path: Path, run_date: str, amount_eur: str) -> None:
    path.write_text(json.dumps([{
        "record_type": "spend", "run_id": "synthetic-for-verify",
        "run_date": run_date, "category": "full_sweep", "amount_eur": amount_eur, "note": None,
    }]), encoding="utf-8")


def check_run_full_sweep_ladder(data) -> list[str]:
    """run_full_sweep must actually enforce a rung's max_sweep_protocols/sweep_lane, not just read
    them off budget.check()'s decision -- a real gap found and fixed 2026-09-21: `allowed` is only
    False at the bottom rung (max_sweep_protocols == 0), so the two rungs in between
    (reduced_sweep's protocol cap, guard_only_sweep's lane restriction) were computed but never
    applied. Uses the real protocols/ and the real budget.json (read-only, never modified) so the
    rung math is the actual math, not a hand-copied fixture -- only the ledger (synthetic spend,
    to land on a specific rung) and the client (a FakeClient stand-in, so this stays a zero-cost,
    offline check) are fabricated."""
    del data  # no vectors.json cases for this one -- see docstring for why
    errors = []
    run_date = "2026-01-01"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # reduced_sweep (60-80%): max_sweep_protocols=6, sweep_lane=None -- all 12 real protocols
        # are lane-eligible, so this isolates the *count* cap.
        protocols_dir = tmp / "protocols_count"
        protocols_dir.mkdir()
        for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json")):
            shutil.copy(f, protocols_dir / f.name)
        ledger = tmp / "ledger_count.json"
        _write_ledger(ledger, run_date, "6.50")  # 65% of the flat €10 general ceiling

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            # redirect_stdout: pilot.run()'s progress marks and run_due's own spend reminder
            # print unconditionally on client_kind == "anthropic" -- true for the real client this
            # branch exists to test, misleading here since _StubClient never bills anything real.
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date=run_date,
                protocols_dir=protocols_dir, out_root=tmp / "experiments_count",
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
            )
        ran = [r for r in results if "run_id" in r]
        capped = [r for r in results if r.get("skipped") == "budget_halted" and "caps this sweep" in r.get("detail", "")]
        if len(ran) != 6:
            errors.append(f"reduced_sweep: {len(ran)} protocol(s) ran, want 6 (max_sweep_protocols)")
        if len(capped) != len(results) - len(ran):
            errors.append(f"reduced_sweep: {len(capped)} capped of {len(results) - len(ran)} not-run, want all of them")

        # guard_only_sweep (80-95%): max_sweep_protocols=4, sweep_lane="guard" -- one open-lane
        # and two guard-lane real protocols, well under the count cap, isolates the *lane* check.
        protocols_dir2 = tmp / "protocols_lane"
        protocols_dir2.mkdir()
        open_lane = next(json.loads(f.read_text())["protocol_id"] for f in _REAL_PROTOCOLS_DIR.glob("*.json") if json.loads(f.read_text())["lane"] == "open")
        guard_lane = [json.loads(f.read_text())["protocol_id"] for f in _REAL_PROTOCOLS_DIR.glob("*.json") if json.loads(f.read_text())["lane"] == "guard"][:2]
        for pid in [open_lane, *guard_lane]:
            shutil.copy(_REAL_PROTOCOLS_DIR / f"{pid}.json", protocols_dir2 / f"{pid}.json")
        ledger2 = tmp / "ledger_lane.json"
        _write_ledger(ledger2, run_date, "8.50")  # 85% of the flat €10 general ceiling

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            results2 = run_due.run_full_sweep(
                client_kind="anthropic", run_date=run_date,
                protocols_dir=protocols_dir2, out_root=tmp / "experiments_lane",
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger2, budget_config=budget.DEFAULT_CONFIG,
            )
        by_pid = {r["protocol_id"]: r for r in results2}
        if "run_id" in by_pid.get(open_lane, {}):
            errors.append(f"guard_only_sweep: open-lane protocol {open_lane!r} ran, should have been lane-restricted")
        elif "lane" not in by_pid.get(open_lane, {}).get("detail", ""):
            errors.append(f"guard_only_sweep: open-lane protocol {open_lane!r} skipped for the wrong reason: {by_pid.get(open_lane)}")
        for pid in guard_lane:
            if "run_id" not in by_pid.get(pid, {}):
                errors.append(f"guard_only_sweep: guard-lane protocol {pid!r} should have run (under the cap, right lane): {by_pid.get(pid)}")

    return errors


def main() -> int:
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))

    checks = (
        ("parse_cadence", lambda: check_parse_cadence(data)),
        ("due_jobs", lambda: check_due_jobs(data)),
        ("plan", lambda: check_plan(data)),
        ("record_run", lambda: check_record_run(data)),
        ("gap_cause_for", lambda: check_gap_cause_for(data)),
        ("record_observation", lambda: check_record_observation(data)),
        ("run_full_sweep_ladder", lambda: check_run_full_sweep_ladder(data)),
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
        print(f"core/schedule/verify.py: {len(all_errors)} failure(s)")
        return 1
    print("core/schedule/verify.py: all vectors pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
