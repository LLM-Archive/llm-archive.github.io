"""Replays core/schedule/vectors.json against schedule.py.

    python3 -m core.schedule.verify
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import schedule

_VECTORS = Path(__file__).resolve().parent / "vectors.json"


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


def main() -> int:
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))

    checks = (
        ("parse_cadence", lambda: check_parse_cadence(data)),
        ("due_jobs", lambda: check_due_jobs(data)),
        ("record_run", lambda: check_record_run(data)),
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
