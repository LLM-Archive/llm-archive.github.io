"""Produces the daily subject_fingerprint observation against the REAL commercial model
(spec.md §7): 24 fixed, short, single-fact yes/no questions, asked the same way every day.

    python3 -m core.plumbing.subject_fingerprint run --client fake
    python3 -m core.plumbing.subject_fingerprint run --client anthropic --model-id <id> --model-family <family>
    python3 -m core.plumbing.subject_fingerprint verify

Unlike `runtime_fingerprint.py` (12 prompts against the pinned LOCAL reference model, hashing top-5
logprobs for exact-equality drift detection), this targets the REMOTE commercial subject, which
exposes no logprobs over the Messages API and isn't byte-pinned the way the reference model is
(spec.md §7's table draws exactly this line: "runtime_fingerprint" is exact equality, this row is
just "daily, public series"). So the observable here is coarser but still mechanical: each item has
a pre-declared correct answer, the model is asked to end its reply with a `DECISION: yes` or
`DECISION: no` line -- the exact same frozen grammar every protocol prompt already uses
(`core.measure.grammar`/`client.classify`, spec.md §5), not a second, bespoke parser -- and the
day's record is how many of the 24 came back valid-and-correct, valid-and-wrong, or unparseable/
refused/truncated/empty/blocked. A future model suddenly missing "is 7 a prime number" is itself a
signal, the same way runtime_fingerprint moving at all is a signal for the reference model.

**Frozen panel, same discipline as `runtime_fingerprint.py`'s 12 prompts:** these 24 items never
change once run for real; a change here is a core_change like anything else on spec.md §13's list.
Deliberately kept simple/short (spec.md §7: "not full 250-400-word scenarios") -- a live 3-call
sample of this exact style measured ~103 tokens/call, projecting 24×30 = 720 calls/month to
~$0.83, against ~$4/month for full-scenario-length items at the same daily count. Balanced 12
yes / 12 no on purpose (in same-topic pairs, e.g. p01/p02 both about primality) so an "always
answer yes" degenerate strategy would score only 50%, not 100% -- the same reasoning
`core/measure/`'s own `degenerate_candidate` flag exists for, just against a different failure mode.

Deliberately does NOT touch `core/measure/schema.py`'s closed `RECORD_TYPES` list (frozen for good,
spec.md §13/twentieth session) -- `record_type: "subject_fingerprint"` here follows the exact
precedent `core/instrument/instrument.py`'s `status()` already set with `"instrument_alarm"`: a
record type that describes pipeline/model health, invented in `core/plumbing`/`core/instrument`
rather than added to the frozen schema, since nothing there actually validates record_type values
against that list (it only feeds `schema_sha256()`).

Output goes to experiments/subject_fingerprint/<date>__<subject_model_id>.json and is never
overwritten, same append-only discipline as core/measure/pilot.py's experiments/<run_id>/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from core.budget import budget
from core.measure.client import Response, classify
from core.measure.schema import schema_sha256

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "experiments" / "subject_fingerprint"

OPTIONS = ["yes", "no"]
PROMPT_TEMPLATE = "{question}\n\nEnd your response with a single line in exactly this format, where X is yes or no:\n\nDECISION: X"

# Frozen once run for real -- see module docstring. 12 yes / 12 no, in same-topic pairs.
ITEMS = {
    "p01": ("Is 7 a prime number?", "yes"),
    "p02": ("Is 9 a prime number?", "no"),
    "p03": ("Is the chemical symbol for gold 'Au'?", "yes"),
    "p04": ("Is the chemical symbol for silver 'Si'?", "no"),
    "p05": ("Does water boil at 100 degrees Celsius at standard atmospheric pressure?", "yes"),
    "p06": ("Does water freeze at 10 degrees Celsius at standard atmospheric pressure?", "no"),
    "p07": ("Is the Pacific Ocean the largest ocean on Earth?", "yes"),
    "p08": ("Is the Mediterranean Sea the largest ocean on Earth?", "no"),
    "p09": ("Does a triangle have three sides?", "yes"),
    "p10": ("Does a triangle have four sides?", "no"),
    "p11": ("Is the square root of 81 equal to 9?", "yes"),
    "p12": ("Is the square root of 81 equal to 7?", "no"),
    "p13": ("Is 18 an even number?", "yes"),
    "p14": ("Is 17 an even number?", "no"),
    "p15": ("Does the Earth orbit the Sun?", "yes"),
    "p16": ("Does the Sun orbit the Earth?", "no"),
    "p17": ("Is Canberra the capital city of Australia?", "yes"),
    "p18": ("Is Sydney the capital city of Australia?", "no"),
    "p19": ("Does a right angle measure 90 degrees?", "yes"),
    "p20": ("Does a right angle measure 45 degrees?", "no"),
    "p21": ("Are there seven days in a week?", "yes"),
    "p22": ("Are there ten days in a week?", "no"),
    "p23": ("Is the Sun a star?", "yes"),
    "p24": ("Is the Moon a star?", "no"),
}
assert len(ITEMS) == 24
assert sorted(ans for _, ans in ITEMS.values()).count("yes") == 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_prompt(question: str) -> str:
    return PROMPT_TEMPLATE.format(question=question)


def run(client, items: dict[str, tuple[str, str]] = ITEMS, run_date: str | None = None) -> dict:
    run_date = run_date or date.today().isoformat()
    outcome_counts: dict[str, int] = {}
    n_correct = n_incorrect = 0
    per_item = []
    returned_ids: set[str] = set()

    for item_id, (question, correct) in items.items():
        prompt = render_prompt(question)
        response: Response = client.complete(prompt, meta={"item_id": item_id})
        if response.returned_model_id:
            returned_ids.add(response.returned_model_id)
        extraction = classify(response, OPTIONS)
        outcome_counts[extraction.outcome] = outcome_counts.get(extraction.outcome, 0) + 1
        correct_match = extraction.outcome == "valid" and extraction.token == correct
        if extraction.outcome == "valid":
            n_correct += correct_match
            n_incorrect += not correct_match
        per_item.append({
            "item_id": item_id,
            "expected": correct,
            "outcome": extraction.outcome,
            "token": extraction.token,
            "correct": correct_match if extraction.outcome == "valid" else None,
        })

    return {
        "record_type": "subject_fingerprint",
        "schema_sha256": schema_sha256(),
        "run_date": run_date,
        "subject_model_id": client.subject_model_id,
        "model_family": client.model_family,
        "series": client.series,
        "returned_model_id": ",".join(sorted(returned_ids)) or None,
        "n_items": len(items),
        "n_correct": n_correct,
        "n_incorrect": n_incorrect,
        "outcome_counts": outcome_counts,
        "items": per_item,
        "generated_utc": _now(),
    }


def write_record(record: dict, out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record['run_date']}__{record['subject_model_id']}.json"
    if out_path.exists():
        raise FileExistsError(f"{out_path} exists — subject_fingerprint runs are append-only, never overwritten")
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def _summary(record: dict) -> str:
    return (
        f"run_date          {record['run_date']}\n"
        f"subject_model_id  {record['subject_model_id']}\n"
        f"n_correct         {record['n_correct']} / {record['n_items']}\n"
        f"n_incorrect       {record['n_incorrect']} / {record['n_items']}\n"
        f"outcome_counts    {record['outcome_counts']}"
    )


class FakeFingerprintClient:
    """Synthetic, zero-cost stand-in -- answers correctly with high probability, occasionally
    wrong/refused/unparseable, so a `--client fake` run exercises every outcome branch without
    spending anything. Says nothing about any real model, same disclaimer as fake_client.py's
    FakeClient (which this deliberately doesn't reuse: its table is keyed on protocol-specific
    version/scenario_id meta this module's items don't have)."""

    subject_model_id = "fake-subject-fingerprint-v1"
    model_family = "fake"
    series = "commercial"
    explicitly_set = {"temperature": "not_client_controllable", "max_tokens": 1024, "thinking": "disabled", "system": None}

    def __init__(self, seed: str = "fake-fp-v1") -> None:
        self.seed = seed

    def complete(self, prompt: str, *, meta: dict) -> Response:
        from core.measure.rng import SplitMix64, seed_from

        item_id = meta["item_id"]
        correct = ITEMS[item_id][1]
        rng = SplitMix64(seed_from(self.seed, item_id))
        r = rng.next() / 2**64
        if r < 0.02:
            return Response("I'd rather not answer that.", "refusal", self.subject_model_id)
        if r < 0.04:
            return Response("Let me think through this carefully, considering", "max_tokens", self.subject_model_id)
        if r < 0.07:
            return Response("It depends on how you look at it.", "end_turn", self.subject_model_id)
        wrong = "no" if correct == "yes" else "yes"
        token = wrong if r < 0.12 else correct
        return Response(f"Reasoning about it briefly.\n\nDECISION: {token}", "end_turn", self.subject_model_id)


def _verify() -> list[str]:
    errors = []
    record = run(FakeFingerprintClient(), run_date="2026-01-01")
    if record["n_items"] != 24:
        errors.append(f"n_items: got {record['n_items']}, want 24")
    if sum(record["outcome_counts"].values()) != 24:
        errors.append(f"outcome_counts sums to {sum(record['outcome_counts'].values())}, want 24")
    if record["n_correct"] + record["n_incorrect"] != record["outcome_counts"].get("valid", 0):
        errors.append("n_correct + n_incorrect must equal the 'valid' outcome count")
    if len(record["items"]) != 24 or {i["item_id"] for i in record["items"]} != set(ITEMS):
        errors.append("items must cover exactly the 24 frozen item_ids")

    # A client that only ever answers "yes" must score ~50% correct, not 100% -- the whole point
    # of the balanced 12/12 panel (module docstring).
    class AlwaysYes:
        subject_model_id, model_family, series = "always-yes", "fake", "commercial"
        explicitly_set = FakeFingerprintClient.explicitly_set

        def complete(self, prompt: str, *, meta: dict) -> Response:
            return Response("DECISION: yes", "end_turn", self.subject_model_id)

    degenerate = run(AlwaysYes(), run_date="2026-01-01")
    if degenerate["n_correct"] != 12:
        errors.append(f"an always-yes client should score exactly 12/24 correct, got {degenerate['n_correct']}")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run today's 24 items and write the record")
    p_run.add_argument("--client", choices=["fake", "anthropic"], required=True)
    p_run.add_argument("--model-id", default=None, help="required for --client anthropic unless subject_models.yaml already has an active commercial generation")
    p_run.add_argument("--model-family", default=None)
    p_run.add_argument("--subject-models", type=Path, default=ROOT / "subject_models.yaml")
    p_run.add_argument("--date", default=date.today().isoformat())
    p_run.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p_run.add_argument("--budget-ledger", type=Path, default=budget.DEFAULT_LEDGER)
    p_run.add_argument("--budget-config", type=Path, default=budget.DEFAULT_CONFIG)

    sub.add_parser("verify", help="run this module's own hand-worked cases")

    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/subject_fingerprint.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    # command == "run"
    if args.client == "fake":
        client = FakeFingerprintClient()
    else:
        from core.plumbing.anthropic_client import AnthropicClient
        from core.plumbing.render import load_active_commercial_model

        ledger = budget.load_ledger(args.budget_ledger)
        config = budget.load_config(args.budget_config)
        decision = budget.check(ledger, "subject_fingerprint", args.date[:7], config=config)
        print(f"budget: {decision['detail']}")

        model_id, model_family = args.model_id, args.model_family
        if not model_id:
            active = load_active_commercial_model(args.subject_models)
            if active is None:
                ap.error(
                    "--client anthropic needs --model-id (subject_models.yaml has no active "
                    "commercial generation yet)"
                )
            model_id, model_family = active["model_id"], active["model_family"]
        client = AnthropicClient(model_id, model_family or "")

    record = run(client, run_date=args.date)
    out_path = write_record(record, args.out_dir)
    print(_summary(record))
    print(f"\nwrote {out_path}")
    if args.client == "anthropic":
        # Same discipline as core/measure/pilot.py: never auto-record an estimate, only an amount
        # a bill has already confirmed. subject_fingerprint is never budget-gated, but the spend
        # still needs logging so state/spend.json stays a true append-only ledger of real charges.
        print(
            f"\nReal money was spent against the Anthropic API this run ({record['n_items']} calls). "
            f"Once the bill confirms the amount, log it -- never an estimate:\n"
            f"  python3 -m core.budget.budget record subject_fingerprint <amount_eur> --run-id "
            f"subject_fingerprint__{record['run_date']}__{record['subject_model_id']} --date {args.date} "
            f"--ledger {args.budget_ledger} --config {args.budget_config}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
