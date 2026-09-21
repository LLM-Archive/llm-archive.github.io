"""Tracks whether the 4 jobs core.schedule.run_due actually knows how to run --
`full_sweep`, `subject_fingerprint`, `runtime_fingerprint`, `guard_margin_rotation` -- ran when
they were due, and why not when they didn't. This is spec.md §10's coverage.csv source data ("what
was scheduled, what's missing, and why"); core.plumbing.render's `build-coverage` turns it into
the actual CSV.

Scope deliberately narrow to these 4 jobs (owner's call, 2026-09-21): cadence.yaml lists ~20 jobs
total. The rest -- `human_review`, `checkpoint_sign`, `coverage_publish`, `release_publish`,
`strategy_review`, `self_revision`, `integrity_declaration`, and the whole "Lease on life" section
(`source_degraded_after`, `deadman_alert`, `vacation_after`, `dormant_after`, `succession_offer`,
`concluded_after`) -- are human/lifecycle cadences with no run-tracking code behind them yet, same
boundary core.schedule.run_due's own `RUNNERS` dict already draws. TODO, next time this comes up:
decide whether/how those belong in coverage.csv too.

Why this can't be backfilled before today: `state/last_run.json` only ever keeps the MOST RECENT
run date per job (schedule.record_run()'s own docstring says so explicitly) -- there is no
historical log to reconstruct earlier gaps from, and this project's schema.GAP_CAUSES docstring is
explicit that a cause must never be invented after the fact. So coverage tracking starts now, as
an append-only log (`state/coverage_log.jsonl`, one line per observation, written by
core.schedule.run_due itself the moment it evaluates a due job) -- the same "real,
contemporaneous record, never rewritten" discipline `experiments/` already uses for measurements.
There's no real pre-history problem to solve here: the archive itself was one day old (v0.1,
2026-09-21) when this was built.

Every observation the log holds is either "ran" (job was due and something real happened) or a
gap with one of `schema.GAP_CAUSES` (job was due, nothing happened, and the reason is one this
module can actually name -- see `gap_cause_for`). A day nobody checked at all -- most of them:
`full_sweep`/`subject_fingerprint` stay manual on purpose, per cadence.yaml -- produces no
observation and so never appears in coverage.csv. Reporting "nobody checked" as a gap would
misrepresent a deliberate manual-only design as a failure; coverage.csv only ever reports what the
scheduler actually observed.

Known, currently-unreachable gap causes for these 4 jobs -- not fabricated, just not yet wired:
  - `protocol_confounded`: `core.schedule.run_due.run_full_sweep()` already silently skips a
    non-`admitted` protocol (it never appears in that function's own `results`), so today's code
    can't reach the gap-logging path for this cause at all -- unreachable while all 12 protocols
    stay `admitted`.
  - `provider_outage`: no code path here catches a real Anthropic API failure and turns it into a
    graceful skip; an outage would crash `run_due.py` outright instead. A resilience gap, not a
    `coverage.csv` gap -- left for later.
  - the reference-model runner's own `"environment_unavailable"` skip (local `llama_cpp`/weights
    missing) has no honest match in `schema.GAP_CAUSES` -- the closest, `provider_outage`, is
    specifically about the commercial API, not a dev machine missing a package. Left unmapped on
    purpose: `gap_cause_for()` returns `None` for it, and `run_due.py` prints a warning instead of
    logging a wrong cause. Never fires inside the real pinned CI image
    (`docker/reference-model/`) anyway, since the weights and `llama_cpp` are baked in there.
  - `dormant`, `generation_bridge`, `generation_retired_early`, `before_archive_start`,
    `human_absent`: no state exists yet to detect any of these for real (no dormancy flag, no
    bridge-event code, no review-tracking file). Structurally reachable later without reshaping
    this module, once that state exists.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.measure.schema import GAP_CAUSES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "state" / "coverage_log.jsonl"

KNOWN_JOBS = ("full_sweep", "subject_fingerprint", "runtime_fingerprint", "guard_margin_rotation")

# (runner name, still-due skip reason) -> the one schema.GAP_CAUSES value that honestly explains
# it. A pair with no entry here has no honest match yet -- see the module docstring for why.
_SKIP_CAUSE = {
    ("full_sweep", "budget_halted"): "budget_halted",
}


def gap_cause_for(runner: str, skip_reasons: set[str]) -> str | None:
    """`skip_reasons`: the distinct `skipped` values seen across one runner's outcome, restricted
    to the ones that leave a job due (core.schedule.run_due._STILL_DUE_SKIPS). Returns the one
    schema.GAP_CAUSES value that honestly explains it, or None if there isn't one yet -- including
    when more than one distinct reason was seen at once, since a mixed set isn't one honest cause."""
    if len(skip_reasons) != 1:
        return None
    (reason,) = skip_reasons
    return _SKIP_CAUSE.get((runner, reason))


def record_observation(
    job: str, date_str: str, *, ran: bool, cause: str | None = None, log_path: Path = DEFAULT_LOG
) -> None:
    """Appends one line to state/coverage_log.jsonl -- the only source core.plumbing.render's
    `build-coverage` ever builds coverage.csv from. Never rewritten, never backfilled: this is the
    scheduler's own observation, logged the day it happened."""
    if job not in KNOWN_JOBS:
        raise ValueError(f"not a known coverage job: {job!r}")
    date.fromisoformat(date_str)  # fail now, not later inside some future read of the log
    if ran:
        if cause is not None:
            raise ValueError("a ran=True observation must not carry a cause")
    else:
        if cause not in GAP_CAUSES:
            raise ValueError(f"cause must be one of {GAP_CAUSES}, got {cause!r}")
    record = {"job": job, "date": date_str, "ran": ran, "cause": cause}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_observations(log_path: Path = DEFAULT_LOG) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
