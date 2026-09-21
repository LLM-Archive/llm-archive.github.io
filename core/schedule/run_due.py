"""The missing link between "is this due" and "run it": dispatches core/schedule/schedule.py's
due jobs to the real runners that already exist, then records what ran.

    python3 -m core.schedule.run_due                              # --client fake, safe default
    python3 -m core.schedule.run_due --client anthropic --date 2026-09-21

Until this module existed, `schedule.due_jobs()` only ever answered a yes/no question -- nothing
called it (core/schedule/README.md's own "what this doesn't do yet"). This is that caller, and
only that: it does not decide whether to run on a recurring, unattended cron. That's a separate,
deliberate decision (STATUS.md, "a bigger, ongoing-automation decision, not made this session")
because two of the three runners below spend real money and none of them are gated by a human
present at run time the way every real run so far has been. Running this by hand, once, is exactly
as safe or risky as running each underlying tool by hand already was -- `--client fake` (the
default) costs nothing; `--client anthropic` spends real money exactly where
`core.measure.pilot`/`core.plumbing.subject_fingerprint` already would, at the same call sites.

Routing (`cadence.yaml` job name -> runner):
    full_sweep              -> core.measure.pilot.run(), once per protocols/*.json (12 files)
    subject_fingerprint     -> core.plumbing.subject_fingerprint.run()
    runtime_fingerprint     -> core.plumbing.reference_model_runner.run() (does both in one call)
    guard_margin_rotation   -> core.plumbing.reference_model_runner.run() (does both in one call)

Everything else cadence.yaml lists (human_review, checkpoint_sign, coverage_publish,
release_publish, strategy_review, self_revision, integrity_declaration, source_degraded_after,
deadman_alert, vacation_after, dormant_after, succession_offer, concluded_after) is a human or
lifecycle cadence, not code -- `plan()` deliberately leaves these out of what it dispatches, and
`main()` only reports them as due, exactly like `schedule.py due` already did.

Also logs to `state/coverage_log.jsonl` (`core.schedule.coverage`) every time it actually resolves
a due job's outcome -- "ran" or a gap with a named cause -- so `core.plumbing.render
build-coverage` has real, contemporaneous data to build coverage.csv from. See that module's
docstring for which gap causes it can currently name and which it deliberately can't yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from core.budget import budget
from core.measure import pilot
from core.plumbing import subject_fingerprint

from . import coverage, schedule

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOLS_DIR = ROOT / "protocols"
DEFAULT_EXPERIMENTS_OUT = ROOT / "experiments"
DEFAULT_SUBJECT_MODELS = ROOT / "subject_models.yaml"

# job (as spelled in cadence.yaml) -> runner name. Two jobs share one runner because
# reference_model_runner.run() already produces both observations in a single call.
RUNNERS = {
    "full_sweep": "full_sweep",
    "subject_fingerprint": "subject_fingerprint",
    "runtime_fingerprint": "reference_model",
    "guard_margin_rotation": "reference_model",
}

# Cheapest/free first: a real environment problem or exhausted budget in a later runner shouldn't
# stop the free one from completing and being recorded. Also the valid --only vocabulary.
RUNNER_ORDER = ["reference_model", "subject_fingerprint", "full_sweep"]

_SPEND_REMINDER = (
    "\nReal money was spent against the Anthropic API ({label}). Once the bill confirms the "
    "amount, log it -- never an estimate:\n"
    "  python3 -m core.budget.budget record {category} <amount_eur> --run-id {run_id} "
    "--date {date} --ledger {ledger} --config {config}"
)


def plan(due: dict[str, dict]) -> dict[str, list[str]]:
    """Groups today's due, known cadence jobs by which runner covers them. A cadence job with no
    entry in RUNNERS (a human/lifecycle cadence) is left out entirely -- it's reported separately,
    never dispatched. Pure and side-effect-free so it's testable without running anything real."""
    grouped: dict[str, list[str]] = {}
    for job, info in due.items():
        if not info.get("due"):
            continue
        runner = RUNNERS.get(job)
        if runner is None:
            continue
        grouped.setdefault(runner, []).append(job)
    return grouped


def _active_model(subject_models_path: Path, model_id: str | None, model_family: str | None) -> tuple[str, str]:
    if model_id:
        return model_id, model_family or ""
    from core.plumbing.render import load_active_commercial_model

    active = load_active_commercial_model(subject_models_path)
    if active is None:
        raise RuntimeError(f"{subject_models_path} has no active commercial generation and no --model-id was given")
    return active["model_id"], active["model_family"] or ""


def run_full_sweep(
    *,
    client_kind: str,
    run_date: str,
    protocols_dir: Path = DEFAULT_PROTOCOLS_DIR,
    out_root: Path = DEFAULT_EXPERIMENTS_OUT,
    subject_models_path: Path = DEFAULT_SUBJECT_MODELS,
    budget_ledger: Path = budget.DEFAULT_LEDGER,
    budget_config: Path = budget.DEFAULT_CONFIG,
    model_id: str | None = None,
    model_family: str | None = None,
) -> list[dict]:
    """One call per admitted protocols/*.json (12 today), same shape as cadence.yaml's own
    description ("12 protocols x 4 versions x n=30"). Budget is re-checked before every single
    protocol, not once for the whole sweep -- same discipline core.measure.pilot's own CLI already
    uses at this call site, just looped instead of invoked 12 separate times by hand. A protocol
    that already ran today (FileExistsError) or that the ladder halts partway through is skipped,
    not fatal -- the rest of the sweep, and the rest of due today's jobs, still get a chance.

    The ladder's rung is enforced here, not just read: `budget.check()`'s `allowed` is only False
    at the very bottom rung (`max_sweep_protocols == 0`), so a naive "if not allowed: skip" leaves
    the two rungs in between -- `reduced_sweep` (drop to `max_sweep_protocols`) and
    `guard_only_sweep` (drop to `sweep_lane`) -- computed but never actually applied. Both are
    enforced below, against how many of *this sweep's* protocols have already run and each
    protocol's own `lane` -- the ledger itself doesn't move mid-sweep (real spend is only logged
    after the fact, once the bill confirms it, never estimated), so re-checking the rung on every
    iteration only tells us the rung as of the start of this sweep; the running count here is what
    actually keeps a long sweep inside its own rung's cap."""
    results = []
    protocols_run = 0
    for path in sorted(protocols_dir.glob("*.json")):
        protocol = json.loads(path.read_text(encoding="utf-8"))
        if protocol.get("status") != "admitted":
            continue
        pid = protocol["protocol_id"]

        if client_kind == "fake":
            from core.plumbing.fake_client import FakeClient

            client = FakeClient()
        else:
            ledger = budget.load_ledger(budget_ledger)
            config = budget.load_config(budget_config)
            decision = budget.check(ledger, "full_sweep", run_date[:7], config=config)
            if not decision["allowed"]:
                results.append({"protocol_id": pid, "skipped": "budget_halted", "detail": decision["detail"]})
                continue
            if protocols_run >= decision["max_sweep_protocols"]:
                results.append({
                    "protocol_id": pid, "skipped": "budget_halted",
                    "detail": f"{decision['level']} rung caps this sweep at {decision['max_sweep_protocols']} "
                    f"protocol(s); {protocols_run} already run",
                })
                continue
            if decision["sweep_lane"] is not None and protocol.get("lane") != decision["sweep_lane"]:
                results.append({
                    "protocol_id": pid, "skipped": "budget_halted",
                    "detail": f"{decision['level']} rung restricts this sweep to lane {decision['sweep_lane']!r}; "
                    f"{pid} is lane {protocol.get('lane')!r}",
                })
                continue
            from core.plumbing.anthropic_client import AnthropicClient

            mid, mfam = _active_model(subject_models_path, model_id, model_family)
            client = AnthropicClient(mid, mfam)

        try:
            record = pilot.run(protocol, client, run_date, out_root=out_root)
        except FileExistsError:
            results.append({"protocol_id": pid, "skipped": "already_ran_today"})
            continue

        protocols_run += 1
        results.append({"protocol_id": pid, "run_id": record["run_id"], "n_valid": record["n_valid"], "on_curve": record["on_curve"]})
        if client_kind == "anthropic":
            print(_SPEND_REMINDER.format(
                label=f"full_sweep/{pid}, n_valid {record['n_valid']}", category="full_sweep",
                run_id=record["run_id"], date=run_date, ledger=budget_ledger, config=budget_config,
            ))
    return results


def run_subject_fingerprint(
    *,
    client_kind: str,
    run_date: str,
    out_dir: Path = subject_fingerprint.DEFAULT_OUT_DIR,
    subject_models_path: Path = DEFAULT_SUBJECT_MODELS,
    budget_ledger: Path = budget.DEFAULT_LEDGER,
    budget_config: Path = budget.DEFAULT_CONFIG,
    model_id: str | None = None,
    model_family: str | None = None,
) -> dict:
    """subject_fingerprint is never budget-halted (spec.md §9 "never zero") -- budget.check() is
    still called and its detail surfaced, same as subject_fingerprint.py's own CLI does, purely
    for visibility, not as a gate."""
    if client_kind == "fake":
        client = subject_fingerprint.FakeFingerprintClient()
    else:
        ledger = budget.load_ledger(budget_ledger)
        config = budget.load_config(budget_config)
        decision = budget.check(ledger, "subject_fingerprint", run_date[:7], config=config)
        print(f"budget: {decision['detail']}")
        from core.plumbing.anthropic_client import AnthropicClient

        mid, mfam = _active_model(subject_models_path, model_id, model_family)
        client = AnthropicClient(mid, mfam)

    record = subject_fingerprint.run(client, run_date=run_date)
    try:
        out_path = subject_fingerprint.write_record(record, out_dir)
    except FileExistsError:
        return {"skipped": "already_ran_today"}

    result = {"run_date": run_date, "n_correct": record["n_correct"], "n_items": record["n_items"], "out_path": str(out_path)}
    if client_kind == "anthropic":
        print(_SPEND_REMINDER.format(
            label=f"subject_fingerprint, {record['n_items']} calls", category="subject_fingerprint",
            run_id=f"subject_fingerprint__{run_date}__{record['subject_model_id']}", date=run_date,
            ledger=budget_ledger, config=budget_config,
        ))
    return result


def run_reference_model(*, run_date: str) -> dict:
    """Covers both runtime_fingerprint and guard_margin_rotation in the one call
    reference_model_runner.run() already makes -- zero API cost (local pinned reference model,
    CPU only), so there's no budget gate here, only two non-fatal reasons it might not produce a
    fresh record: the pinned model weights/llama_cpp have to actually be present (true inside
    docker/reference-model's image, not necessarily on an arbitrary dev machine) -- reported as
    "environment_unavailable", cadence left due, since nothing was actually checked; or today's
    guard protocol already ran (pilot.run()'s own append-only refusal) -- reported as
    "already_ran_today", which the caller treats as done, same as core.measure.pilot's own
    FileExistsError handling elsewhere in this module."""
    from core.plumbing.reference_model_runner import run as run_reference

    try:
        record = run_reference(date.fromisoformat(run_date))
    except (ImportError, FileNotFoundError, ValueError) as e:
        return {"skipped": "environment_unavailable", "detail": str(e)}
    except FileExistsError:
        return {"skipped": "already_ran_today"}
    return {"ok": record["ok"], "guard_protocol_id": record["guard_protocol_id"]}


def _dispatch(runner: str, args: argparse.Namespace) -> dict | list[dict]:
    if runner == "reference_model":
        return run_reference_model(run_date=args.date)
    if runner == "subject_fingerprint":
        return run_subject_fingerprint(
            client_kind=args.client, run_date=args.date,
            out_dir=args.subject_fingerprint_out or (args.experiments_out / "subject_fingerprint"),
            subject_models_path=args.subject_models,
            budget_ledger=args.budget_ledger, budget_config=args.budget_config,
            model_id=args.model_id, model_family=args.model_family,
        )
    return run_full_sweep(
        client_kind=args.client, run_date=args.date, protocols_dir=args.protocols_dir,
        out_root=args.experiments_out, subject_models_path=args.subject_models,
        budget_ledger=args.budget_ledger, budget_config=args.budget_config,
        model_id=args.model_id, model_family=args.model_family,
    )


# Skip reasons meaning nothing happened today -- the cadence entry stays due. Any other skip
# reason (e.g. "already_ran_today") means today's job is done, just not through this exact call,
# and should be recorded like a real run so tomorrow's due_jobs() check stays accurate.
_STILL_DUE_SKIPS = {"budget_halted", "environment_unavailable"}


def _ran_something(runner: str, outcome: dict | list[dict]) -> bool:
    if runner == "full_sweep":
        return any(r.get("skipped") not in _STILL_DUE_SKIPS for r in outcome)
    return outcome.get("skipped") not in _STILL_DUE_SKIPS


def _skip_reasons(runner: str, outcome: dict | list[dict]) -> set[str]:
    """The distinct still-due skip reasons seen in one runner's outcome -- coverage.gap_cause_for()'s
    input. Only meaningful when _ran_something() is False; called only in that case below."""
    if runner == "full_sweep":
        return {r.get("skipped") for r in outcome if r.get("skipped") in _STILL_DUE_SKIPS}
    reason = outcome.get("skipped")
    return {reason} if reason in _STILL_DUE_SKIPS else set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", choices=["fake", "anthropic"], default="fake", help="default is fake: this command spends nothing unless told to")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--cadence", type=Path, default=schedule.DEFAULT_CADENCE)
    ap.add_argument("--last-run", type=Path, default=schedule.DEFAULT_LAST_RUN)
    ap.add_argument("--coverage-log", type=Path, default=coverage.DEFAULT_LOG)
    ap.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    ap.add_argument("--experiments-out", type=Path, default=DEFAULT_EXPERIMENTS_OUT)
    ap.add_argument("--subject-fingerprint-out", type=Path, default=None, help="default: <experiments-out>/subject_fingerprint")
    ap.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    ap.add_argument("--budget-ledger", type=Path, default=budget.DEFAULT_LEDGER)
    ap.add_argument("--budget-config", type=Path, default=budget.DEFAULT_CONFIG)
    ap.add_argument("--model-id", default=None)
    ap.add_argument("--model-family", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print what would run, run nothing, record nothing")
    ap.add_argument(
        "--only", default=None,
        help="comma-separated runner names to actually dispatch (reference_model,subject_fingerprint,full_sweep); "
             "others that are due are reported, not run -- e.g. a zero-cost cron restricts itself with "
             "--only reference_model so it can never touch the paid runners even if they're also due",
    )
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    if only is not None and not only <= set(RUNNER_ORDER):
        ap.error(f"--only must be a subset of {RUNNER_ORDER}, got {sorted(only)}")

    intervals = schedule.parse_cadence(args.cadence.read_text(encoding="utf-8"))
    last_run = schedule.load_last_run(args.last_run)
    today = date.fromisoformat(args.date)
    due = schedule.due_jobs(intervals, last_run, today)
    grouped = plan(due)

    excluded_by_only = sorted(j for r, jobs in grouped.items() if only is not None and r not in only for j in jobs)
    if only is not None:
        grouped = {r: jobs for r, jobs in grouped.items() if r in only}

    informational = sorted(j for j, info in due.items() if info.get("due") and j not in RUNNERS)
    informational += excluded_by_only
    if informational:
        print(f"due today, not dispatched this run (human/lifecycle cadence, or excluded by --only): {', '.join(sorted(informational))}")

    if not grouped:
        print("nothing due today with a known runner.")
        return 0

    exit_code = 0
    for runner in sorted(grouped, key=RUNNER_ORDER.index):
        jobs = grouped[runner]
        print(f"\n=== {runner} (covers: {', '.join(jobs)}) ===")
        if args.dry_run:
            print("--dry-run: skipped")
            continue

        outcome = _dispatch(runner, args)
        print(json.dumps(outcome, indent=2, ensure_ascii=False))

        if _ran_something(runner, outcome):
            for job in jobs:
                schedule.record_run(args.last_run, job, args.date)
                coverage.record_observation(job, args.date, ran=True, log_path=args.coverage_log)
        else:
            exit_code = 1
            print(f"{runner}: nothing actually ran -- {', '.join(jobs)} left due, not recorded.")
            cause = coverage.gap_cause_for(runner, _skip_reasons(runner, outcome))
            if cause is None:
                print(
                    f"{runner}: no schema.GAP_CAUSES match for this skip reason yet -- "
                    "coverage.csv gap NOT logged (see core/schedule/coverage.py's docstring)."
                )
            else:
                for job in jobs:
                    coverage.record_observation(job, args.date, ran=False, cause=cause, log_path=args.coverage_log)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
