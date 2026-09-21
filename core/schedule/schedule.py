"""Reads cadence.yaml, checks state/last_run.json, says what's due today.

    python3 -m core.schedule.schedule due [--date YYYY-MM-DD]
    python3 -m core.schedule.schedule mark-run <job> [--date YYYY-MM-DD]

Only handles interval fields ("30d", "36h", ...) in cadence.yaml -- anything else in that file
(on_new_generation, plain numbers, policy strings) isn't a cadence and is skipped. This module
doesn't know which jobs matter or what to do when one is due; it just answers "has enough time
passed". For the two categories budget.py also knows about (full_sweep, subject_fingerprint), a
due job's result also carries the budget.check() decision -- so "due" and "allowed to spend" are
both visible in one place before anything actually runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from core.budget import budget

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CADENCE = ROOT / "cadence.yaml"
DEFAULT_LAST_RUN = ROOT / "state" / "last_run.json"

_INTERVAL = re.compile(r"^(\d+)([dh])$")


def parse_cadence(text: str) -> dict[str, float]:
    """job name -> interval in days. Only lines whose value is a bare "<number>d" or "<number>h"
    count as a cadence; everything else in cadence.yaml (on_new_generation, "4", "0.40",
    "provider_declared_successor", ...) is a different kind of field, not a schedule."""
    intervals = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        m = _INTERVAL.match(value.strip())
        if not m:
            continue
        n, unit = int(m.group(1)), m.group(2)
        intervals[key.strip()] = n if unit == "d" else n / 24
    return intervals


def load_last_run(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record_run(path: Path, job: str, run_date: str) -> None:
    """Overwrites job's last-run date -- unlike spend.json, only the most recent run matters for
    scheduling, so there's no history to preserve here."""
    date.fromisoformat(run_date)  # fail now, not later inside some future due_jobs() call
    last_run = load_last_run(path)
    last_run[job] = run_date
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(last_run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def due_jobs(intervals: dict[str, float], last_run: dict[str, str], today: date) -> dict[str, dict]:
    result = {}
    for job, interval_days in intervals.items():
        last = last_run.get(job)
        if last is None:
            result[job] = {"due": True, "last_run": None, "elapsed_days": None, "interval_days": interval_days}
            continue
        elapsed = (today - date.fromisoformat(last)).days
        result[job] = {"due": elapsed >= interval_days, "last_run": last, "elapsed_days": elapsed, "interval_days": interval_days}
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cadence", type=Path, default=DEFAULT_CADENCE)
    ap.add_argument("--last-run", type=Path, default=DEFAULT_LAST_RUN)
    sub = ap.add_subparsers(dest="command", required=True)

    p_due = sub.add_parser("due", help="what's due today")
    p_due.add_argument("--date", default=date.today().isoformat())
    p_due.add_argument("--budget-ledger", type=Path, default=budget.DEFAULT_LEDGER)
    p_due.add_argument("--budget-config", type=Path, default=budget.DEFAULT_CONFIG)

    p_mark = sub.add_parser("mark-run", help="record that a job just ran")
    p_mark.add_argument("job")
    p_mark.add_argument("--date", default=date.today().isoformat())

    args = ap.parse_args(argv)

    if args.command == "mark-run":
        record_run(args.last_run, args.job, args.date)
        print(f"{args.job} last_run set to {args.date}")
        return 0

    intervals = parse_cadence(args.cadence.read_text(encoding="utf-8"))
    last_run = load_last_run(args.last_run)
    today = date.fromisoformat(args.date)
    result = due_jobs(intervals, last_run, today)

    # For the categories budget.py also knows about, attach whether spending is allowed too --
    # this is the first real caller of budget.check(), not just a demo of it.
    budget_ledger = budget.load_ledger(args.budget_ledger)
    budget_config = budget.load_config(args.budget_config)
    for job, info in result.items():
        if info["due"] and job in budget.CATEGORIES:
            info["budget"] = budget.check(budget_ledger, job, args.date[:7], config=budget_config)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
