"""Runs the instrument's own daily check against the reference model (spec.md §7) and produces
one instrument_alarm record: today's runtime_fingerprint (12 fixed prompts), plus ONE guard-lane
protocol's full run (n=30, 120 calls -- was n=150/600 calls; dropped 2026-09-21, see
mutable/panels.py), rotating through all 10 guard-lane protocols on a 10-day cycle (cadence.yaml:
guard_margin_rotation).

    python3 -m core.plumbing.reference_model_runner run [--date YYYY-MM-DD]

Why one protocol/day, not all ten: a real CI cost/throughput test measured ~9.6s/call on the
actual GitHub Actions runner. All 10 guard-lane protocols daily would
now be (10 x 120) + 12 = 1,212 calls/day, ~194 min/day, ~5,820 min/month -- 2.9x the 2,000
min/month free tier for a private repo. One protocol/day is 120 + 12 = 132 calls/day, ~21 min/day,
~634 min/month (~32% of the free tier -- more margin than before the n=30 change, not less), at
the cost of only re-checking any single protocol once every ten days -- runtime_fingerprint still
runs every day and catches most pipeline/environment breaks on its own (spec.md §7).

The first time a given protocol's turn comes up, there is no frozen baseline to compare against
yet -- that run becomes the baseline (written to state/instrument/guard_baselines/), and the day
publishes with no guard-margin alarm possible for that protocol (nothing to compare to is not the
same as "flat"). Same bootstrap rule for runtime_fingerprint's expected file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from core.instrument.instrument import check_guard_margin, check_runtime_fingerprint, status
from core.measure import pilot
from core.plumbing.reference_client import ReferenceClient, load_llama
from core.plumbing.runtime_fingerprint import run as run_fingerprint

ROOT = Path(__file__).resolve().parents[2]
PROTOCOLS_DIR = ROOT / "protocols"
STATE_DIR = ROOT / "state" / "instrument"
BASELINES_DIR = STATE_DIR / "guard_baselines"
RUNTIME_EXPECTED = STATE_DIR / "runtime_fingerprint_expected.json"

# Day zero of the rotation. Fixed once and never changed -- moving it would just relabel which
# protocol runs on which day, but changing it after the fact would silently skip or repeat one.
ROTATION_EPOCH = date(2026, 1, 1)


def guard_protocols() -> list[str]:
    """Every admitted protocol_id whose lane is "guard", sorted for a stable, reproducible
    rotation order -- derived from the protocol files themselves, not a hardcoded list, same
    reasoning as core/schedule/schedule.py's cadence parsing."""
    ids = []
    for path in sorted(PROTOCOLS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("lane") == "guard":
            ids.append(data["protocol_id"])
    return sorted(ids)


def protocol_for_date(run_date: date, protocols: list[str]) -> str:
    day_index = (run_date - ROTATION_EPOCH).days % len(protocols)
    return protocols[day_index]


def _load_baseline(protocol_id: str) -> dict | None:
    path = BASELINES_DIR / f"{protocol_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_baseline(protocol_id: str, measurement: dict) -> None:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = BASELINES_DIR / f"{protocol_id}.json"
    path.write_text(json.dumps(measurement, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(run_date: date) -> dict:
    protocols = guard_protocols()
    protocol_id = protocol_for_date(run_date, protocols)
    protocol = json.loads((PROTOCOLS_DIR / f"{protocol_id}.json").read_text(encoding="utf-8"))

    fp_llama = load_llama(logits_all=True)
    observed_fp = run_fingerprint(fp_llama)
    del fp_llama
    expected_fp = json.loads(RUNTIME_EXPECTED.read_text(encoding="utf-8")) if RUNTIME_EXPECTED.exists() else None
    bootstrapped_fp = expected_fp is None
    if bootstrapped_fp:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        RUNTIME_EXPECTED.write_text(json.dumps(observed_fp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        expected_fp = observed_fp

    client = ReferenceClient()
    today_measurement = pilot.run(protocol, client, run_date.isoformat(), out_root=ROOT / "experiments")
    baseline = _load_baseline(protocol_id)
    bootstrapped_guard = baseline is None
    if bootstrapped_guard:
        _write_baseline(protocol_id, today_measurement)
        baseline = today_measurement

    # When bootstrapped, expected/baseline IS today's own observation, so these naturally come
    # back "ok" (comparing something to itself) -- no separate branch needed for that, but the
    # record says so explicitly, since a true-by-construction match is not the same claim as a
    # real day-over-day comparison.
    record = status(check_runtime_fingerprint(expected_fp, observed_fp), check_guard_margin(baseline, today_measurement))
    record["guard_protocol_id"] = protocol_id
    record["run_date"] = run_date.isoformat()
    record["runtime_fingerprint_bootstrapped"] = bootstrapped_fp
    record["guard_margin_bootstrapped"] = bootstrapped_guard
    return record


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run today's instrument check against the reference model")
    p_run.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)

    if args.command == "run":
        record = run(date.fromisoformat(args.date))
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return 0 if record["ok"] else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
