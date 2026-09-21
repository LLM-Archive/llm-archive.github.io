"""Daily self-check that the pipeline itself hasn't broken (spec.md §7).

    python3 -m core.instrument.instrument check --runtime-expected PATH [--runtime-observed PATH]
        --guard-baseline PATH --guard-today PATH

Two checks, both must pass or the day is instrument_suspect:
  - runtime_fingerprint: exact equality on a fixed set of prompts against the reference model.
    No --runtime-observed file counts as fail ("a day without runtime_fingerprint publishes no
    measurements -- unavailable counts as fail", spec.md §7).
  - guard margin: today's guard-protocol measurement on the reference model vs. a frozen
    baseline, using the SAME significance rule already used to compare any two measurements
    (core.measure.stats.compare). Only "flat" passes -- "improved" is just as much an alarm as
    "regressed", because the reference model is supposed to never move at all.

Doesn't run anything against a real model -- like core/budget/ and core/schedule/, this is the
guardrail, built before the reference-model runner existed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.measure.stats import compare


def check_runtime_fingerprint(expected: dict[str, str], observed: dict[str, str] | None) -> dict:
    if observed is None:
        return {"ok": False, "reason": "unavailable", "mismatched": []}
    mismatched = sorted(k for k in expected if expected.get(k) != observed.get(k))
    mismatched += sorted(k for k in observed if k not in expected)
    return {"ok": not mismatched, "reason": None if not mismatched else "mismatch", "mismatched": mismatched}


def check_guard_margin(baseline: dict, today: dict) -> dict:
    result = compare(baseline, today)
    return {"ok": result == "flat", "result": result}


def status(runtime: dict, guard: dict) -> dict:
    ok = runtime["ok"] and guard["ok"]
    return {
        "record_type": "instrument_alarm",
        "ok": ok,
        "flag": None if ok else "instrument_suspect",
        "runtime_fingerprint": runtime,
        "guard_margin": guard,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="run both checks and report the day's instrument status")
    p_check.add_argument("--runtime-expected", type=Path, required=True)
    p_check.add_argument("--runtime-observed", type=Path)
    p_check.add_argument("--guard-baseline", type=Path, required=True)
    p_check.add_argument("--guard-today", type=Path, required=True)

    args = ap.parse_args(argv)

    expected = json.loads(args.runtime_expected.read_text(encoding="utf-8"))
    observed = json.loads(args.runtime_observed.read_text(encoding="utf-8")) if args.runtime_observed else None
    baseline = json.loads(args.guard_baseline.read_text(encoding="utf-8"))
    today = json.loads(args.guard_today.read_text(encoding="utf-8"))

    result = status(check_runtime_fingerprint(expected, observed), check_guard_margin(baseline, today))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
