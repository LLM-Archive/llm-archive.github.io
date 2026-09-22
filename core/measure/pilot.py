"""Runs one protocol end to end: 4 versions × n calls, then one measurement (spec.md §13.10).

    python3 -m core.measure.pilot protocols/<protocol>.json --client fake

Output goes to experiments/<run_id>/ and is never overwritten: data paths are append-only.
Written to a staging directory first and renamed into place only once complete, so a run
interrupted partway through (a real API run takes minutes) never leaves a half-written
experiments/<run_id>/ blocking a real retry of the same run_id -- see run()'s own comment.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from . import measurement
from .chain import sha256_hex
from .client import Client, classify
from .invariants import check_protocol
from .rng import SplitMix64, seed_from
from .schema import SCHEMA_VERSION, VERSIONS, schema_sha256

# Only imported for its module-level constants (DEFAULT_LEDGER/DEFAULT_CONFIG as argparse
# defaults) and CATEGORIES -- cheap, dependency-free, same reasoning core/schedule/schedule.py
# already uses for importing this unconditionally rather than lazily like anthropic/llama_cpp.
from core.budget import budget

ROOT = Path(__file__).resolve().parents[2]


def render_prompt(protocol: dict, text: str) -> str:
    return protocol["prompt_template"].replace("{scenario}", text)


def schedule(protocol: dict, run_date: str, subject_model_id: str) -> list[dict]:
    """All 4 × n calls, in a seeded random order, so provider drift during the run cannot line
    up with one version."""
    calls = [
        {"version": v, "scenario_id": s["scenario_id"], "rep": r, "prompt": render_prompt(protocol, s["versions"][v])}
        for v in VERSIONS
        for s in protocol["scenarios"]
        for r in range(protocol["n_per_scenario"])
    ]
    rng = SplitMix64(seed_from(protocol["protocol_id"], run_date, subject_model_id, "order"))
    for i in range(len(calls) - 1, 0, -1):
        j = rng.below(i + 1)
        calls[i], calls[j] = calls[j], calls[i]
    return calls


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(protocol: dict, client: Client, run_date: str, replicate_index: int = 0, out_root: Path = ROOT / "experiments") -> dict:
    # A protocol with a design bug should never spend a single API call finding that out — gate 1
    # is free and instant, an API call is neither.
    errors = check_protocol(protocol)
    if errors:
        raise ValueError("protocol fails admission gate 1:\n  " + "\n  ".join(errors))

    run_id = f"{run_date}__{protocol['protocol_id']}__{client.subject_model_id}__r{replicate_index}"
    out_dir = out_root / run_id
    if out_dir.exists():
        # Refusing to overwrite is what makes "the raw responses are logged" a real guarantee
        # rather than a promise that quietly breaks the one time a run is repeated by accident.
        raise FileExistsError(f"{out_dir} exists — runs are append-only, never overwritten")

    # Written to a hidden staging dir first, renamed to out_dir only once every file below is
    # down -- a real API run takes minutes, not milliseconds, so a process interrupted partway
    # through (killed session, crashed machine, anything short of a graceful finish) must never
    # leave out_dir half-written: with the old direct-write, that half-written state still
    # satisfies the FileExistsError guard above, permanently blocking a real retry of this exact
    # run_id without someone finding and deleting the wreckage by hand first. os.rename (what
    # Path.rename wraps) is atomic on the same filesystem, which staging_dir and out_dir always
    # share since both live under out_root -- so out_dir either doesn't exist yet, or exists
    # complete; nothing in between is ever observable. A crash instead leaves an orphaned
    # staging dir next to it, inert clutter (its name marks it, never mistaken for a real run),
    # not cleaned up automatically -- deleting things nobody asked to delete is its own risk.
    out_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(dir=out_root, prefix=f".{run_id}.partial-"))

    started = _now()
    trials, returned_ids = [], set()
    calls = schedule(protocol, run_date, client.subject_model_id)
    total = len(calls)
    with open(staging_dir / "trials.jsonl", "w", encoding="utf-8") as f:
        for index, call in enumerate(calls):
            response = client.complete(call["prompt"], meta={k: call[k] for k in ("version", "scenario_id", "rep")})
            result = classify(response, protocol["options"], grammar_version=protocol["grammar_version"])
            if response.returned_model_id:
                returned_ids.add(response.returned_model_id)
            trial = {
                "record_type": "trial",
                "run_id": run_id,
                "index": index,
                "version": call["version"],
                "scenario_id": call["scenario_id"],
                "rep": call["rep"],
                "prompt_sha256": sha256_hex(call["prompt"].encode("utf-8")),
                "response_text": response.text,
                "stop_reason": response.stop_reason,
                "returned_model_id": response.returned_model_id,
                "error": response.error,
                "outcome": result.outcome,
                "token": result.token,
                "reason": result.reason,
            }
            trials.append(trial)
            f.write(json.dumps(trial, ensure_ascii=False) + "\n")

            # One "#" per finished call, printed right away (flush=True, not buffered until the
            # end). With the fake client this loop finishes instantly, but with a real, slow API
            # client it can take minutes -- this is what shows the run is still working and not
            # stuck. A line break every 50 marks plus a running count keeps it readable instead of
            # one giant unbroken row of "#".
            print("#", end="", flush=True)
            if (index + 1) % 50 == 0 or index + 1 == total:
                print(f" {index + 1}/{total}")

    context = {
        "run_id": run_id,
        "run_date": run_date,
        "subject_model_id": client.subject_model_id,
        # More than one returned id inside a single run is itself worth seeing, so it is kept, not hidden.
        "returned_model_id": ",".join(sorted(returned_ids)) or None,
        "model_family": client.model_family,
        "series": client.series,
        "lane": protocol["lane"],
        "twin_id": protocol["twin_id"],
        "condition_profile": "bare",
        "replicate_index": replicate_index,
    }
    record = measurement.build(protocol, trials, context)

    experiment_run = {
        "record_type": "experiment_run",
        "schema_version": SCHEMA_VERSION,
        "schema_sha256": schema_sha256(),
        "run_id": run_id,
        "run_date": run_date,
        "started_utc": started,
        "finished_utc": _now(),
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "panel_sha256": protocol["panel_sha256"],
        "protocol_status": protocol["status"],
        "subject_model_id": client.subject_model_id,
        "client": type(client).__name__,
        "explicitly_set": client.explicitly_set,
        "grammar_version": protocol["grammar_version"],
        "extractor_sha": measurement.extractor_sha(protocol["grammar_version"]),
        "analysis_code_sha": measurement.analysis_code_sha(),
        "n_trials": len(trials),
    }
    (staging_dir / "run.json").write_text(json.dumps(experiment_run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (staging_dir / "measurement.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    staging_dir.rename(out_dir)
    return record


def _summary(m: dict) -> str:
    lines = [
        f"run_id            {m['run_id']}",
        f"stability_pct     {m['stability_pct']}   (CI {m['ci_low_pct']} – {m['ci_high_pct']}, null floor {m['null_floor_pct']})",
        f"gap A↔B / A↔A′ / A↔C   {m['gap_pct']} / {m['gap_null_pct']} / {m['gap_positive_pct']}",
        f"n_valid           {m['n_valid']}",
        f"drop bounds       ab {m['drop_bound_pct_ab']} · aa {m['drop_bound_pct_aa']} · ac {m['drop_bound_pct_ac']}",
        f"entropy a / b     {m['entropy_a']} / {m['entropy_b']}",
        f"flags             {m['flags'] or '—'}",
        f"unset thresholds  {m['gates']['unset_thresholds'] or '—'}",
        f"on_curve          {m['on_curve']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", type=Path)
    ap.add_argument("--client", choices=["fake", "anthropic"], required=True)
    ap.add_argument("--model-id", default=None, help="required for --client anthropic unless subject_models.yaml already has an active commercial generation")
    ap.add_argument("--model-family", default=None)
    ap.add_argument("--subject-models", type=Path, default=ROOT / "subject_models.yaml")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--replicate", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "experiments")
    ap.add_argument("--budget-ledger", type=Path, default=budget.DEFAULT_LEDGER, help="only read for --client anthropic")
    ap.add_argument("--budget-config", type=Path, default=budget.DEFAULT_CONFIG, help="only read for --client anthropic")
    args = ap.parse_args(argv)

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    if args.client == "fake":
        from core.plumbing.fake_client import FakeClient

        client = FakeClient()
    else:
        from core.plumbing.anthropic_client import AnthropicClient
        from core.plumbing.render import load_active_commercial_model

        # spec.md §9's guardrail, at the one call site that can actually spend real money: a
        # pilot run of one protocol against the real API is a slice of the same "full_sweep"
        # category cadence.yaml/budget.json already describe (12 protocols x 4 versions x n=30) --
        # generation_bridge and subject_fingerprint spend through their own, different call sites
        # (not built on pilot.py), so they're not gated here.
        ledger = budget.load_ledger(args.budget_ledger)
        config = budget.load_config(args.budget_config)
        decision = budget.check(ledger, "full_sweep", args.date[:7], config=config)
        if not decision["allowed"]:
            ap.error(f"budget_halted -- {decision['detail']}")

        model_id, model_family = args.model_id, args.model_family
        if not model_id:
            active = load_active_commercial_model(args.subject_models)
            if active is None:
                ap.error(
                    "--client anthropic needs --model-id (subject_models.yaml has no active "
                    "commercial generation yet -- expected before commit #1)"
                )
            model_id, model_family = active["model_id"], active["model_family"]
        client = AnthropicClient(model_id, model_family or "")
    record = run(protocol, client, args.date, args.replicate, args.out)
    print(_summary(record))
    if args.client == "anthropic":
        # record_spend() is deliberately not called automatically here: it only ever logs an
        # amount a bill has already confirmed, never an estimate (core/budget/budget.py's own
        # docstring) -- and this process has no way to know the confirmed amount, only the call
        # count. Printed, not just left to be remembered, so real spend can't again go
        # unrecorded the way ~20 real calls did before this call site was wired to check()/
        # record_spend() at all.
        print(
            f"\nReal money was spent against the Anthropic API this run (n_valid per version: "
            f"{record['n_valid']}). Once the bill confirms the amount, log it -- never an estimate:\n"
            f"  python3 -m core.budget.budget record full_sweep <amount_eur> --run-id {record['run_id']} "
            f"--date {args.date} --ledger {args.budget_ledger} --config {args.budget_config}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
