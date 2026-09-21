"""Replays core/instrument/vectors.json against instrument.py.

    python3 -m core.instrument.verify
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import instrument

_VECTORS = Path(__file__).resolve().parent / "vectors.json"


def check_runtime_fingerprint(data: dict) -> list[str]:
    errors = []
    for case in data["check_runtime_fingerprint"]:
        got = instrument.check_runtime_fingerprint(case["expected"], case["observed"])
        if got != case["want"]:
            errors.append(f"{case['name']}: got {got}, want {case['want']}")
    return errors


def check_guard_margin(data: dict) -> list[str]:
    errors = []
    for case in data["check_guard_margin"]:
        got = instrument.check_guard_margin(case["baseline"], case["today"])
        if got != case["want"]:
            errors.append(f"{case['name']}: got {got}, want {case['want']}")
    return errors


def check_status(data: dict) -> list[str]:
    errors = []
    for case in data["status"]:
        got = instrument.status(case["runtime"], case["guard"])
        for key, expected_value in case["want"].items():
            if got[key] != expected_value:
                errors.append(f"{case['name']}: {key} got {got[key]!r}, want {expected_value!r}")
    return errors


def main() -> int:
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))

    checks = (
        ("check_runtime_fingerprint", lambda: check_runtime_fingerprint(data)),
        ("check_guard_margin", lambda: check_guard_margin(data)),
        ("status", lambda: check_status(data)),
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
        print(f"core/instrument/verify.py: {len(all_errors)} failure(s)")
        return 1
    print("core/instrument/verify.py: all vectors pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
