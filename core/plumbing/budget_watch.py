"""Two things a person has to be told, that the ledger knows and nothing told anyone.

    python3 -m core.plumbing.budget_watch rung [--month YYYY-MM]        # exit 10 above the `full` rung
    python3 -m core.plumbing.budget_watch reconcile [--month YYYY-MM]   # the month's ledger, to compare with the bill
    python3 -m core.plumbing.budget_watch verify

`rung`: the ladder in budget.json (60% / 80% / 95%) already cuts the sweep as the month fills, but
silently -- nobody learns the month is nearly spent until a sweep comes back `budget_halted`. This
exits 10 as soon as the month leaves the `full` rung, naming the rung and the percentage.

`reconcile`: since 2026-09-25 the ledger is written by the pipeline itself from the token counts the
API returns (tokens x list price), not from the hand-read Console bill. That is measured usage, but
it is still a calculation: once a month someone must compare the total with the Console and record
any difference by hand. This prints what to compare against, and the exact command to record a
difference. It never records anything itself.

Both only READ `state/spend.json` and `budget.json`. Neither spends, records, or changes any state.
`core/budget/` is untouched; this is a reader.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from fractions import Fraction
from pathlib import Path

from core.budget import budget

EXIT_ACTION_NEEDED = 10
AUTO_NOTE_MARK = "not yet reconciled"  # written into the note of every pipeline-recorded entry


def previous_month(day: str) -> str:
    """'2026-01-06' -> '2025-12' (the month a reconciliation on the 6th is about)."""
    y, m = int(day[:4]), int(day[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def rung_report(ledger: list[dict], month: str, config: dict) -> tuple[dict, list[str]]:
    """(decision, report lines). Empty lines list = nothing to tell (still on the `full` rung)."""
    decision = budget.check(ledger, "full_sweep", month, config=config)
    if decision["level"] == config["rungs"][0]["name"]:
        return decision, []
    return decision, [
        f"{month}: {decision['spent_pct']}% of the {decision['ceiling_eur']} monthly pool is spent "
        f"({decision['spent_eur']}) -> rung '{decision['level']}': {decision['detail'].split(': ', 1)[-1]}",
        "The sweep is being cut by the ladder (budget.json); subject_fingerprint is never cut.",
    ]


def month_entries(ledger: list[dict], month: str) -> list[dict]:
    return [e for e in ledger if e["run_date"][:7] == month]


def reconcile_report(ledger: list[dict], month: str) -> list[str]:
    entries = month_entries(ledger, month)
    auto = [e for e in entries if AUTO_NOTE_MARK in (e.get("note") or "")]
    manual = [e for e in entries if e not in auto]
    total = sum((Fraction(e["amount_eur"]) for e in entries), Fraction(0))
    auto_total = sum((Fraction(e["amount_eur"]) for e in auto), Fraction(0))
    lines = [
        f"Ledger for {month}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, total {float(total):.4f}",
        f"  computed from tokens (unreconciled): {len(auto)}, {float(auto_total):.4f}",
        f"  recorded by hand from a bill:        {len(manual)}, {float(total - auto_total):.4f}",
        "",
        "Compare the total with the Console's usage/cost for the same month (the Console shows USD; the ledger",
        "holds that same number). If the Console is higher or lower, record the difference by hand:",
        f"  python3 -m core.budget.budget record <category> <difference> --run-id reconcile__{month} --note \"Console vs ledger, {month}\"",
        "(a negative difference cannot be recorded: the ledger is append-only and never below zero -- say so in the note of the next entry instead).",
    ]
    for e in auto:
        lines.append(f"  - {e['run_date']} {e['category']:<20} {float(Fraction(e['amount_eur'])):>8.4f}  {e['run_id']}")
    return lines


def _verify() -> list[str]:
    errors: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    config = budget.load_config()
    ledger = [
        {"run_date": "2026-06-03", "category": "full_sweep", "amount_eur": "2.1000", "run_id": "a", "note": "x; tokens x list price, not yet reconciled against the Console bill"},
        {"run_date": "2026-06-04", "category": "full_sweep", "amount_eur": "2.5000", "run_id": "b", "note": "hand-logged bill"},
        {"run_date": "2026-05-30", "category": "full_sweep", "amount_eur": "9.0000", "run_id": "c", "note": None},
    ]
    expect(previous_month("2026-01-06") == "2025-12" and previous_month("2026-10-06") == "2026-09", "previous_month must wrap the year")
    d, lines = rung_report(ledger, "2026-06", config)
    expect(lines == [] and d["level"] == "full", f"46% of a 10 ceiling is still the full rung, got {d['level']} / {lines}")
    d, lines = rung_report(ledger, "2026-05", config)
    expect(d["level"] == "guard_only_sweep" and lines and "90.0%" in lines[0], f"90% must read as the guard_only rung and say so: {d['level']} {lines}")
    rep = reconcile_report(ledger, "2026-06")
    expect("2 entries, total 4.6000" in rep[0], f"reconcile must total the month only: {rep[0]}")
    expect("computed from tokens (unreconciled): 1, 2.1000" in rep[1], f"reconcile must separate computed entries: {rep[1]}")
    expect("recorded by hand from a bill:        1, 2.5000" in rep[2], f"reconcile must separate hand-logged entries: {rep[2]}")
    expect(reconcile_report([], "2026-06")[0].startswith("Ledger for 2026-06: 0 entries"), "an empty month must still report")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("rung", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("--month", default=None, help="YYYY-MM; default: this month (rung), last month (reconcile)")
        p.add_argument("--ledger", type=Path, default=budget.DEFAULT_LEDGER)
        p.add_argument("--config", type=Path, default=budget.DEFAULT_CONFIG)
    sub.add_parser("verify")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/budget_watch.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    today = date.today().isoformat()
    ledger = budget.load_ledger(args.ledger)
    if args.command == "rung":
        month = args.month or today[:7]
        decision, lines = rung_report(ledger, month, budget.load_config(args.config))
        print("\n".join(lines) if lines else f"{month}: {decision['spent_pct']}% of the pool spent, rung '{decision['level']}' -- nothing to report")
        return EXIT_ACTION_NEEDED if lines else 0
    print("\n".join(reconcile_report(ledger, args.month or previous_month(today))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
