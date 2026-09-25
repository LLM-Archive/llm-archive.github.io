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
from fractions import Fraction
from pathlib import Path

from core.budget import budget
from core.measure import pilot
from core.measure.schema import VERSIONS
from core.plumbing import prereg, subject_fingerprint
from core.plumbing.anthropic_client import CircuitOpen

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

# A deliberately pessimistic per-CALL ceiling, used only to project this sweep's own
# not-yet-logged spend so run_full_sweep's rung check can't run stale for an entire long sweep
# (see that function's docstring). Never written to state/spend.json.
#
# Per call, not per protocol: a protocol's real cost scales with how many calls it makes, and `n`
# is a per-protocol field (spec.md §2), so protocols with different `n` cost different amounts in
# the same sweep. This used to be a flat €0.79/protocol, which was silently exact only while every
# protocol in rotation was n=30 -- against an n=120 protocol (4x the calls, ~€2.76 real) the
# projection would have read 3.5x low and let an unattended sweep run well past its own rung,
# which is the precise failure the projection exists to prevent (decisions.md §49).
#
# €0.0066/call is the top of the measured range, not the average: real logged runs are
# €0.575-0.79 for 120 calls (€0.0048-0.0066/call, 2026-09-21/22, n=10 runs), so the projection
# errs toward stopping a sweep a little early rather than a little late. At n=30 this gives
# €0.79/protocol -- the same number as the flat constant it replaces, so nothing changes for a
# rotation that is still all-n=30. Revisit if real per-call spend ever lands above this.
_EST_COST_PER_CALL_EUR = Fraction("0.0066")


def _est_protocol_cost_eur(protocol: dict) -> Fraction:
    """What one run of this protocol is projected to cost: 4 versions × its own `n` calls."""
    return _EST_COST_PER_CALL_EUR * len(VERSIONS) * protocol["n"]


def _last_real_run(out_root: Path, protocol_id: str, model_id: str) -> str:
    """The date of this protocol's most recent real run against this model, as "YYYY-MM-DD", or
    "" if it has never been measured. Sorting on this puts never-measured protocols first and the
    longest-unmeasured next, which is the order a budget-limited sweep should work through."""
    runs = sorted(out_root.glob(f"*__{protocol_id}__{model_id}__r*"))
    return runs[-1].name[:10] if runs else ""

_SPEND_REMINDER = (
    "\nReal money was spent against the Anthropic API ({label}). Once the bill confirms the "
    "amount, log it -- never an estimate:\n"
    "  python3 -m core.budget.budget record {category} <amount_eur> --run-id {run_id} "
    "--date {date} --ledger {ledger} --config {config}"
)


def _record_measured_spend(client, *, run_id: str, run_date: str, category: str, ledger_path: Path, label: str) -> Fraction | None:
    """Writes what a finished (or aborted) run cost into the ledger, from the token counts the API
    returned -- measured usage x list price, see anthropic_client.PRICES_USD_PER_MTOK. Returns the
    amount, or None when the client cannot say (a test double, or a model with no listed price); the
    caller then prints the manual "log the real bill" reminder instead, as before. Never a guess:
    the entry's note says how it was derived, so a monthly check against the Console bill can tell
    it from a hand-logged bill."""
    cost = client.cost_usd() if hasattr(client, "cost_usd") else None
    if cost is None:
        return None
    budget.record_spend(
        ledger_path, run_id=run_id, run_date=run_date, category=category, amount_eur=cost,
        note=f"{label}; tokens x list price ({client.input_tokens} in / {client.output_tokens} out), "
             "not yet reconciled against the Console bill",
    )
    return cost


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
    manifests_dir: Path | None = prereg.DEFAULT_MANIFESTS_DIR,
) -> list[dict]:
    """One call per admitted protocols/*.json (12 today), same shape as cadence.yaml's own
    description ("12 protocols x 4 versions x n=30"). Budget is re-checked before every single
    protocol, not once for the whole sweep -- same discipline core.measure.pilot's own CLI already
    uses at this call site, just looped instead of invoked 12 separate times by hand. A protocol
    that already has a real run earlier this same month, that already ran today specifically
    (FileExistsError -- kept as a fallback even though the month-level check above should always
    catch it first), or that the ladder halts partway through, is skipped, not fatal -- the rest of
    the sweep, and the rest of due today's jobs, still get a chance.

    **Real gap found and fixed 2026-09-22**: this used to only check "already ran today" (via
    pilot.run()'s own FileExistsError), not "already ran this month" -- full_sweep is monthly
    (cadence.yaml), so a protocol hand-measured yesterday, or by an earlier, interrupted sweep
    attempt earlier today, got silently re-measured and re-billed by the next invocation, purely
    because the calendar day differed. Found by hand, real money already spent on the redundant
    re-run before the gap was caught (base_rate_neglect__anchoring__v0, 2026-09-22 -- see
    decisions.md). Fixed by scanning `out_root` for any existing `<protocol_id>__<model_id>` run
    dated this month before spending anything, client_kind == "anthropic" only (the fake client is
    for tests, never billed, re-running it is free and sometimes wanted).

    The ladder's rung is enforced here, not just read: `budget.check()`'s `allowed` is only False
    at the very bottom rung (`max_sweep_protocols == 0`), so a naive "if not allowed: skip" leaves
    the two rungs in between -- `reduced_sweep` (drop to `max_sweep_protocols`) and
    `guard_only_sweep` (drop to `sweep_lane`) -- computed but never actually applied. Both are
    enforced below, against each protocol's own `lane` and against `budget.check()`'s new
    `additional_spent_eur` (added 2026-09-22): the running sum of `_est_protocol_cost_eur()` over
    the protocols this sweep has already run, a conservative in-memory projection of what it has
    already committed to spending, added on top of the ledger's confirmed total before picking the
    rung on every iteration. Summed per protocol rather than multiplied by a count because `n` is
    per protocol, so two protocols in one sweep can cost different amounts (decisions.md §49). Never
    written to the ledger -- `state/spend.json` still only ever gets a real, bill-confirmed number,
    same discipline as always -- this only shifts which rung THIS decision reads as, so a sweep
    that starts well inside `full` but would cross into `reduced_sweep`/`guard_only_sweep` partway
    through (exactly what happened by hand on 2026-09-22, requiring a human to stop the sweep and
    log spend mid-run to keep it in check -- see decisions.md) now degrades itself in real time
    instead of running past its own rung on a stale reading, unattended. The per-protocol estimate
    is a deliberately pessimistic ceiling (not the measured average), so it errs toward stopping
    the sweep a little early rather than a little late: `state/spend.json`'s logged real amounts so
    far range €0.64-0.79; the estimate used here is the top of that range."""
    results = []
    protocols_run = 0
    projected_eur = Fraction(0)

    admitted = []
    for path in sorted(protocols_dir.glob("*.json")):
        protocol = json.loads(path.read_text(encoding="utf-8"))
        if protocol.get("status") == "admitted":
            admitted.append(protocol)

    mid = mfam = None
    if client_kind != "fake":
        mid, mfam = _active_model(subject_models_path, model_id, model_family)
        # Least-recently-measured first (never-measured first of all), protocol_id breaking ties
        # so the order stays deterministic. Plain alphabetical order was silently fine only while
        # the whole rotation fit inside one month's budget: once it doesn't -- which is exactly
        # what a higher `n` buys with the same ceiling -- an alphabetical sweep re-measures the
        # same first few protocols every month and never reaches the rest, because the
        # already-run-this-month skip below resets with the calendar (decisions.md §49).
        admitted.sort(key=lambda p: (_last_real_run(out_root, p["protocol_id"], mid), p["protocol_id"]))

    for protocol in admitted:
        pid = protocol["protocol_id"]

        if client_kind == "fake":
            from core.plumbing.fake_client import FakeClient

            client = FakeClient()
        else:
            already_this_month = sorted(out_root.glob(f"{run_date[:7]}-*__{pid}__{mid}__r*"))
            if already_this_month:
                results.append({
                    "protocol_id": pid, "skipped": "already_run_this_month",
                    "detail": f"real run already exists this month: {already_this_month[0].name} "
                    "-- full_sweep is monthly (cadence.yaml), not daily, so a protocol already "
                    "measured for real earlier this month is not re-spent on",
                })
                continue
            # A paid run only for a protocol whose CURRENT hashes were stamped (OpenTimestamps)
            # before it ran -- core/plumbing/prereg.py and guide/timestamps.md say why.
            # `manifests_dir=None` switches this off (tests that exercise the ladder, not this).
            if manifests_dir is not None and prereg.is_preregistered(protocol, prereg.load_stamped(manifests_dir)) is None:
                results.append({
                    "protocol_id": pid, "skipped": "not_preregistered",
                    "detail": "no stamped manifest in state/timestamps/ lists this protocol's current "
                    "panel_sha256/protocol_sha256 -- stamp a manifest first (state/timestamps/README.md), "
                    "then re-run; nothing was spent",
                })
                continue
            ledger = budget.load_ledger(budget_ledger)
            config = budget.load_config(budget_config)
            decision = budget.check(ledger, "full_sweep", run_date[:7], config=config, additional_spent_eur=projected_eur)
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
            # Would THIS protocol fit? The rung above only looks at what is already spent, so
            # without this a protocol could start at 79% and end at 106%. The estimate is the
            # deliberately pessimistic per-call ceiling, so it errs toward not starting one. Once
            # spend is logged from measured usage (`_record_measured_spend`), `projected_eur` is
            # empty and `spent` is the true month-to-date figure.
            spent = budget.spent_this_month(ledger, run_date[:7]) + projected_eur
            est = _est_protocol_cost_eur(protocol)
            if spent + est > config["monthly_ceiling_eur"]:
                results.append({
                    "protocol_id": pid, "skipped": "budget_halted",
                    "detail": f"would not fit the monthly ceiling: spent so far {float(spent):.2f} + "
                    f"projected {float(est):.2f} for this protocol > {float(config['monthly_ceiling_eur']):.2f}",
                })
                continue
            from core.plumbing.anthropic_client import AnthropicClient

            client = AnthropicClient(mid, mfam)

        try:
            record = pilot.run(protocol, client, run_date, out_root=out_root)
        except FileExistsError:
            results.append({"protocol_id": pid, "skipped": "already_ran_today"})
            continue
        except CircuitOpen as e:
            # The run was abandoned (its staging dir is left behind, never a finished run), but the
            # calls made before the breaker opened were real and billed. Log them, then stop the
            # whole sweep: whatever broke this protocol breaks the next one the same way.
            _record_measured_spend(
                client, run_id=f"{run_date}__{pid}__{mid}__r0__aborted", run_date=run_date,
                category="full_sweep", ledger_path=budget_ledger, label=f"full_sweep/{pid} ABORTED by circuit breaker",
            )
            results.append({"protocol_id": pid, "skipped": "circuit_open", "detail": str(e)})
            break

        protocols_run += 1
        if client_kind != "anthropic":
            projected_eur += _est_protocol_cost_eur(protocol)
        results.append({"protocol_id": pid, "run_id": record["run_id"], "n_valid": record["n_valid"], "on_curve": record["on_curve"]})
        if client_kind == "anthropic":
            recorded = _record_measured_spend(
                client, run_id=record["run_id"], run_date=run_date, category="full_sweep",
                ledger_path=budget_ledger, label=f"full_sweep/{pid}, n_valid {record['n_valid']}",
            )
            if recorded is not None:
                results[-1]["spend_recorded"] = format(float(recorded), ".4f")
                continue  # the ledger now holds the real amount, so no projection on top of it
            projected_eur += _est_protocol_cost_eur(protocol)
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

    try:
        record = subject_fingerprint.run(client, run_date=run_date)
    except CircuitOpen as e:
        _record_measured_spend(
            client, run_id=f"subject_fingerprint__{run_date}__{client.subject_model_id}__aborted", run_date=run_date,
            category="subject_fingerprint", ledger_path=budget_ledger, label="subject_fingerprint ABORTED by circuit breaker",
        )
        return {"skipped": "environment_unavailable", "detail": str(e)}
    try:
        out_path = subject_fingerprint.write_record(record, out_dir)
    except FileExistsError:
        return {"skipped": "already_ran_today"}

    result = {"run_date": run_date, "n_correct": record["n_correct"], "n_items": record["n_items"], "out_path": str(out_path)}
    if client_kind == "anthropic":
        run_id = f"subject_fingerprint__{run_date}__{record['subject_model_id']}"
        recorded = _record_measured_spend(
            client, run_id=run_id, run_date=run_date, category="subject_fingerprint",
            ledger_path=budget_ledger, label=f"subject_fingerprint, {record['n_items']} calls",
        )
        if recorded is not None:
            result["spend_recorded"] = format(float(recorded), ".4f")
        else:
            print(_SPEND_REMINDER.format(
                label=f"subject_fingerprint, {record['n_items']} calls", category="subject_fingerprint",
                run_id=run_id, date=run_date, ledger=budget_ledger, config=budget_config,
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
_STILL_DUE_SKIPS = {"budget_halted", "environment_unavailable", "circuit_open"}


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
        if runner == "full_sweep" and any(r.get("skipped") == "circuit_open" for r in outcome):
            # A sweep the breaker cut short may still have finished protocols, which are recorded
            # below like any other run -- but it must never look like a clean success.
            exit_code = 1

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
