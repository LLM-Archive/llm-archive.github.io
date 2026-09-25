"""A durable trace of the instrument's daily self-check (state/instrument/alarm_log.jsonl).

    python3 -m core.plumbing.alarm_log recent [--days 8]   # exit 0 = no alarm in the window, 10 = an alarm
    python3 -m core.plumbing.alarm_log verify

Why this exists: the reference-model check (core/instrument) decides every day whether the whole
pipeline is suspect (`instrument_suspect`, spec.md §7), but core/schedule/run_due.py used to print
that verdict and forget it. Nothing on disk said a day had alarmed, so nothing that runs later
(the auto-merge gate, tools/auto_merge_gate.py) could refuse to publish on it. One line per run
now: the date, the verdict, and which half moved. The daily workflow already commits
state/instrument/, so the log needs no new plumbing. It is private-repo state: deploy_public_site.py
does not mirror it.

It RECORDS only. It changes no exit status and stops nothing by itself; a reader decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "state" / "instrument" / "alarm_log.jsonl"
EXIT_ALARM = 10


def append(record: dict, run_date: str, log_path: Path = DEFAULT_LOG) -> None:
    """One line from an `instrument_alarm` record (core.instrument.instrument.status)."""
    line = {
        "run_date": run_date,
        "ok": bool(record["ok"]),
        "flag": record.get("flag"),
        "runtime_fingerprint_ok": bool(record["runtime_fingerprint"]["ok"]),
        "guard_margin_ok": bool(record["guard_margin"]["ok"]),
        "guard_protocol_id": record.get("guard_protocol_id"),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def alarms_since(today: date, days: int, log_path: Path = DEFAULT_LOG) -> list[str]:
    """Dates (newest last) with a not-ok line within the last `days` days, today included. A missing
    log means no alarm has been recorded, not that none happened before the log existed."""
    if not log_path.exists():
        return []
    earliest = today - timedelta(days=days - 1)
    found = set()
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if not row["ok"] and earliest <= date.fromisoformat(row["run_date"]) <= today:
            found.add(row["run_date"])
    return sorted(found)


def _verify() -> list[str]:
    import tempfile

    errors: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    def rec(rt: bool, guard: bool) -> dict:
        return {"ok": rt and guard, "flag": None if rt and guard else "instrument_suspect",
                "runtime_fingerprint": {"ok": rt}, "guard_margin": {"ok": guard}, "guard_protocol_id": "p"}

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "nested" / "alarm_log.jsonl"
        today = date(2026, 9, 28)
        expect(alarms_since(today, 8, log) == [], "a missing log means no alarm recorded")
        append(rec(True, True), "2026-09-27", log)
        expect(alarms_since(today, 8, log) == [], "an ok day is not an alarm")
        append(rec(False, True), "2026-09-22", log)   # inside the window
        append(rec(True, False), "2026-09-21", log)   # the window's first day (today + 7 days back = 8 days)
        append(rec(False, False), "2026-09-20", log)  # one day too old
        expect(alarms_since(today, 8, log) == ["2026-09-21", "2026-09-22"], f"window edges wrong: {alarms_since(today, 8, log)}")
        expect(alarms_since(date(2026, 9, 23), 1, log) == [], "a 1-day window sees only today, not an earlier alarm")
        expect(alarms_since(date(2026, 9, 22), 1, log) == ["2026-09-22"], "a 1-day window sees an alarm on today")
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[1])
        expect(row["runtime_fingerprint_ok"] is False and row["guard_margin_ok"] is True and row["flag"] == "instrument_suspect",
               "the line must say which half moved")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("recent", help="exit 10 if the log holds an alarm within the last N days")
    p.add_argument("--days", type=int, default=8)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/alarm_log.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    found = alarms_since(date.today(), args.days, args.log)
    if found:
        print(f"ALARM: instrument_suspect on {', '.join(found)} (last {args.days} days)")
        return EXIT_ALARM
    print(f"no instrument alarm recorded in the last {args.days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
