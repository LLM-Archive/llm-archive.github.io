"""Notices when the unattended pipeline has gone quiet (cadence.yaml: `deadman_alert: 36h`).

    python3 -m core.plumbing.deadman check          # exit 0 = alive, 10 = silent too long
    python3 -m core.plumbing.deadman verify

Why this exists: every workflow in `.github/workflows/` reports its own failures, but a workflow
that never starts (schedule disabled, Actions off, the repo archived, a broken secret that fails
before the first step) reports nothing at all. This is the one check that looks from the outside:
when did the automation last commit anything?

The heartbeat is the newest commit made by `github-actions[bot]` -- the daily reference-model
check commits every day it runs, so a healthy pipeline always has one within a day. A commit by a
person does not count: it says nothing about whether the automation is alive.

It DETECTS only. Exit status 10 makes the calling workflow open an issue (once, not every run).
It never spends money and never touches state. `cadence.yaml` is frozen (spec.md §13.5), so the
threshold is read from it, never written back.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CADENCE = ROOT / "cadence.yaml"
EXIT_SILENT = 10
BOT_AUTHOR = "github-actions"


def parse_threshold_hours(cadence_text: str) -> int:
    """`deadman_alert: 36h` -> 36. Also accepts `Nd`. A missing or unparseable entry is an error,
    not a silent default: an alert whose threshold nobody can see is worse than none."""
    m = re.search(r"^deadman_alert:\s*(\d+)\s*([hd])\b", cadence_text, re.MULTILINE)
    if not m:
        raise ValueError("cadence.yaml has no parseable `deadman_alert: <N>h` entry")
    return int(m.group(1)) * (24 if m.group(2) == "d" else 1)


def assess(now_epoch: int, last_bot_commit_epoch: int | None, threshold_hours: int) -> dict:
    """Pure: is the pipeline alive? `None` (no bot commit in the history at all) counts as silent."""
    if last_bot_commit_epoch is None:
        return {"alive": False, "silent_hours": None, "threshold_hours": threshold_hours,
                "reason": "no commit by the automation exists in this repository's history"}
    silent = (now_epoch - last_bot_commit_epoch) / 3600
    return {"alive": silent <= threshold_hours, "silent_hours": round(silent, 1), "threshold_hours": threshold_hours,
            "reason": None if silent <= threshold_hours else f"no automation commit for {silent:.1f} h (limit {threshold_hours} h)"}


def last_bot_commit_epoch(repo: Path = ROOT) -> int | None:
    out = subprocess.run(
        ["git", "log", "-1", f"--author={BOT_AUTHOR}", "--format=%ct"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out) if out else None


def _verify() -> list[str]:
    errors: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    expect(parse_threshold_hours("subject_fingerprint: 1d\ndeadman_alert:            36h\n") == 36, "36h must parse")
    expect(parse_threshold_hours("deadman_alert: 2d # x\n") == 48, "2d must read as 48 hours")
    try:
        parse_threshold_hours("subject_fingerprint: 1d\n")
        errors.append("a missing threshold must raise")
    except ValueError:
        pass
    now = 1_800_000_000
    expect(assess(now, now - 35 * 3600, 36)["alive"], "35 h of silence is alive at a 36 h limit")
    expect(assess(now, now - 36 * 3600, 36)["alive"], "exactly at the limit is still alive")
    r = assess(now, now - 37 * 3600, 36)
    expect(not r["alive"] and "37.0 h" in r["reason"], "37 h of silence must be reported, with the number")
    expect(not assess(now, None, 36)["alive"], "no bot commit at all must count as silent")
    # the real cadence.yaml must carry a readable threshold (it is what the workflow reads)
    expect(parse_threshold_hours(DEFAULT_CADENCE.read_text(encoding="utf-8")) == 36, "cadence.yaml must say deadman_alert: 36h")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="exit 10 if the automation has not committed within the cadence.yaml threshold")
    p.add_argument("--cadence", type=Path, default=DEFAULT_CADENCE)
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/deadman.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    result = assess(int(time.time()), last_bot_commit_epoch(), parse_threshold_hours(args.cadence.read_text(encoding="utf-8")))
    if result["alive"]:
        print(f"alive: last automation commit {result['silent_hours']} h ago (limit {result['threshold_hours']} h)")
        return 0
    print(f"SILENT: {result['reason']}")
    return EXIT_SILENT


if __name__ == "__main__":
    sys.exit(main())
