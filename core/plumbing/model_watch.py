"""Notices when a new model id appears in the commercial family, so the generation bridge
(spec.md §4.3) can be run before the old model is retired.

    python3 -m core.plumbing.model_watch check            # asks the API which models exist (free)
    python3 -m core.plumbing.model_watch verify

Why this exists: spec.md §4.3 says the bridge -- measuring the old and the new model in the same
week -- is "automatic, immediate", because it is the one thing in the project that cannot be done
later at any price: once the provider retires the old model it can never be measured again.
`cadence.yaml` names the trigger (`bridge_trigger: any_new_model_id_in_family`) but nothing in the
code ever noticed a new model. This is that missing eye and nothing more.

What it does, and deliberately does not do:

  * It DETECTS. `check` lists the models the API exposes (`GET /v1/models`; no tokens, no cost),
    keeps those whose id starts with the active series' `model_family`, and compares them with
    what earlier `check` runs already saw.
  * It never spends money. A new id makes it print the bridge plan -- which four protocols, which
    two models, how many calls, what the budget says -- and exit with status 10 so a person (or a
    caller) knows action is needed. Running the bridge is a separate, deliberate step, because
    the owner keeps every paid job manual. A provider retires a model months after announcing it,
    so a same-day human decision does not lose the window; a missed detection would.
  * It never switches the active model. spec.md §4.3: a similar-looking name is not a declaration
    of succession, so a new id is reported as `needs_review`, never adopted.
  * The very first run only records a baseline (every model already listed), so it can't raise a
    false alarm about models that existed before this module did.

State is one append-only log, `state/model_registry.jsonl`. What has been seen is always derived by
replaying it, never kept in a second file that could drift out of step. Event kinds: `baseline`
(seen on the first run), `appeared` (new since), `gone` (previously listed, no longer). A `gone`
event for the ACTIVE model is the third row of spec.md §4.3's table -- the old model vanished, no
guessing: this module only reports it, `series_closed` is a human decision.

`record_type: "model_observed"` is not in `core/measure/schema.py`'s frozen `RECORD_TYPES`; same
precedent as `instrument_alarm` and `subject_fingerprint`.

Sizing, decided 2026-09-24: the bridge runs the four protocols at their own frozen n_per_scenario=2
(960 calls for old + new, projected ~EUR 6.3), not the n=1 spec.md §4.3 first proposed --
`core/measure/` cannot run a protocol at another n without it becoming a different protocol.
`budget.json`'s `bridge_reserve_eur` was raised to match; the plan printed below shows projected
cost against that reserve.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from core.budget import budget
from core.measure.schema import REWORDING_TYPES, VERSIONS
from core.schedule.run_due import _EST_COST_PER_CALL_EUR

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "state" / "model_registry.jsonl"
DEFAULT_SUBJECT_MODELS = ROOT / "subject_models.yaml"
DEFAULT_PROTOCOLS_DIR = ROOT / "protocols"

EXIT_ACTION_NEEDED = 10  # a new model appeared, or the active one vanished


def list_family_models(family: str) -> list[str]:
    """Every model id the API currently lists whose id starts with `family`. Lazy import, same
    reasoning as anthropic_client.py: importing this module never needs the package."""
    import os

    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ids = [m.id for m in client.models.list(limit=1000)]
    return sorted(i for i in ids if i.startswith(family))


def load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def known_ids(registry: list[dict], family: str) -> set[str]:
    """Ids currently considered listed: replay the log in order, adding on baseline/appeared and
    removing on gone. An id that vanished and later came back is `appeared` again, so replaying
    handles it without special cases."""
    seen: set[str] = set()
    for rec in registry:
        if rec.get("family") != family:
            continue
        if rec["event"] in ("baseline", "appeared"):
            seen.add(rec["model_id"])
        elif rec["event"] == "gone":
            seen.discard(rec["model_id"])
    return seen


def diff(listed: list[str], registry: list[dict], family: str, active_model_id: str | None) -> dict:
    """Pure: what changed between the log and the API's current list. `first_run` means the log
    has nothing for this family yet, so everything listed becomes a baseline, not an alarm."""
    has_history = any(r.get("family") == family for r in registry)
    seen = known_ids(registry, family)
    listed_set = set(listed)
    if not has_history:
        return {"first_run": True, "baseline": sorted(listed_set), "appeared": [], "gone": [], "active_gone": False}
    gone = sorted(seen - listed_set)
    return {
        "first_run": False,
        "baseline": [],
        "appeared": sorted(listed_set - seen),
        "gone": gone,
        "active_gone": active_model_id in gone if active_model_id else False,
    }


def _type_of(protocol_id: str) -> str:
    return protocol_id.split("__")[1]


def plan_bridge(protocols_dir: Path, old_id: str, new_id: str) -> dict:
    """The bridge's shape per spec.md §4.3: four protocols, one per rewording type, old AND new.
    Picks, per type, the alphabetically first admitted `v0` protocol -- the spec says "one per
    type" and names no family, so a fixed deterministic rule keeps two runs of this function from
    ever choosing differently. Pure apart from reading protocol files."""
    chosen: dict[str, dict] = {}
    for path in sorted(protocols_dir.glob("*.json")):
        p = json.loads(path.read_text(encoding="utf-8"))
        if p.get("status") != "admitted" or not p["protocol_id"].endswith("__v0"):
            continue
        chosen.setdefault(_type_of(p["protocol_id"]), p)
    missing = [t for t in REWORDING_TYPES if t not in chosen]
    if missing:
        raise RuntimeError(f"no admitted v0 protocol for type(s) {missing}; cannot plan a bridge")
    protocols = [chosen[t] for t in REWORDING_TYPES]
    calls_per_model = sum(p["n"] * len(VERSIONS) for p in protocols)
    return {
        "old_model_id": old_id,
        "new_model_id": new_id,
        "protocol_ids": [p["protocol_id"] for p in protocols],
        "calls_per_model": calls_per_model,
        "calls_total": 2 * calls_per_model,
    }


def _report(result: dict, plan: dict | None, decision: dict | None, family: str, active: str | None, reserve=None) -> list[str]:
    lines: list[str] = []
    if result["first_run"]:
        lines.append(f"baseline recorded: {len(result['baseline'])} {family}* model(s) already listed -- no bridge triggered")
        return lines
    if not result["appeared"] and not result["gone"]:
        lines.append(f"no change in {family}* models")
    for mid in result["gone"]:
        tag = "  <-- the ACTIVE model: spec.md §4.3 row 3, no guessing; series_closed is a human decision" if mid == active else ""
        lines.append(f"GONE: {mid}{tag}")
    for mid in result["appeared"]:
        lines.append(f"NEW MODEL: {mid}  -> bridge needed (spec.md §4.3); active-model switch NOT made: needs_review")
    if plan:
        lines.append(f"bridge plan: {plan['old_model_id']}  vs  {plan['new_model_id']}, same week")
        lines.append(f"  protocols: {', '.join(plan['protocol_ids'])}")
        lines.append(f"  calls: {plan['calls_per_model']} per model, {plan['calls_total']} total at the protocols' frozen n")
        if decision:
            lines.append(f"  budget (generation_bridge): allowed={decision['allowed']} -- {decision['detail']}")
        if reserve is not None:
            projected = _EST_COST_PER_CALL_EUR * plan["calls_total"]
            warn = "  <-- EXCEEDS the reserve: a bridge at this size cannot be funded as-is" if projected > reserve else ""
            lines.append(f"  projected cost ~EUR {float(projected):.2f} (pessimistic per-call rate) vs bridge reserve EUR {float(reserve):.2f}{warn}")
        lines.append("  run, per protocol and per model, by hand (spends money; log the real bill afterwards):")
        for mid in (plan["old_model_id"], plan["new_model_id"]):
            for pid in plan["protocol_ids"]:
                lines.append(f"    python3 -m core.measure.pilot protocols/{pid}.json --client anthropic --model-id {mid} --model-family {family}")
    return lines


def check(
    *,
    listed: list[str],
    family: str,
    active_model_id: str | None,
    registry_path: Path,
    protocols_dir: Path,
    today: str,
    ledger_path: Path = budget.DEFAULT_LEDGER,
    config_path: Path = budget.DEFAULT_CONFIG,
    write: bool = True,
) -> tuple[dict, list[str]]:
    """Diff `listed` against the log, append what changed, return (diff, report lines). `listed`
    is passed in (not fetched here) so the same logic is exercised by `verify` with no network."""
    registry = load_registry(registry_path)
    result = diff(listed, registry, family, active_model_id)

    events: list[dict] = []
    for mid in result["baseline"]:
        events.append({"model_id": mid, "event": "baseline"})
    for mid in result["appeared"]:
        events.append({"model_id": mid, "event": "appeared"})
    for mid in result["gone"]:
        events.append({"model_id": mid, "event": "gone"})

    plan = decision = reserve = None
    if result["appeared"]:
        old_id = active_model_id or ""
        plan = plan_bridge(protocols_dir, old_id, result["appeared"][0])
        config = budget.load_config(config_path)
        reserve = config["bridge_reserve_eur"]
        decision = budget.check(budget.load_ledger(ledger_path), "generation_bridge", today[:7], config=config)

    if write and events:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        with registry_path.open("a", encoding="utf-8") as fh:
            for e in events:
                rec = {"record_type": "model_observed", "observed_date": today, "family": family, **e}
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    return result, _report(result, plan, decision, family, active_model_id, reserve)


def _verify() -> list[str]:
    errors: list[str] = []
    fam = "claude-sonnet"

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        reg = tmp_path / "reg.jsonl"
        pdir = tmp_path / "protocols"
        pdir.mkdir()
        for fam_name in ("base_rate_neglect", "risky_choice_framing"):
            for t in REWORDING_TYPES:
                pid = f"{fam_name}__{t}__v0"
                (pdir / f"{pid}.json").write_text(json.dumps({"protocol_id": pid, "status": "admitted", "n": 30}))
        (pdir / "base_rate_neglect__wording__v1.json").write_text(
            json.dumps({"protocol_id": "base_rate_neglect__wording__v1", "status": "admitted", "n": 120})
        )
        (pdir / "risky_choice_framing__order__v0.json").write_text(
            json.dumps({"protocol_id": "risky_choice_framing__order__v0", "status": "candidate", "n": 30})
        )
        kw = dict(family=fam, active_model_id="claude-sonnet-5", registry_path=reg, protocols_dir=pdir, today="2026-10-01",
                  ledger_path=tmp_path / "none.json", config_path=budget.DEFAULT_CONFIG)

        # 1. first run: baseline only, no trigger.
        r, _ = check(listed=["claude-sonnet-4-6", "claude-sonnet-5"], **kw)
        expect(r["first_run"] and not r["appeared"], "first run must only baseline")
        # 2. unchanged: nothing.
        r, _ = check(listed=["claude-sonnet-4-6", "claude-sonnet-5"], **kw)
        expect(not r["first_run"] and not r["appeared"] and not r["gone"], "unchanged list must report nothing")
        # 3. a new id appears: bridge plan, active model untouched.
        r, lines = check(listed=["claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-5-5"], **kw)
        expect(r["appeared"] == ["claude-sonnet-5-5"], "new id must be detected")
        expect(any("needs_review" in l for l in lines), "a new id must be reported as needs_review, not adopted")
        expect(any("bridge plan" in l for l in lines), "a new id must print the bridge plan")
        expect(not any("EXCEEDS" in l for l in lines), "the real reserve must cover a bridge at the frozen n (960 calls)")
        tight = json.loads(budget.DEFAULT_CONFIG.read_text(encoding="utf-8"))
        tight["bridge_reserve_eur"] = "1.00"
        (tmp_path / "tight.json").write_text(json.dumps(tight))
        tight_reg = tmp_path / "tight_reg.jsonl"
        check(listed=["claude-sonnet-5"], **{**kw, "registry_path": tight_reg})  # baseline
        _, tight_lines = check(listed=["claude-sonnet-5", "claude-sonnet-5-5"], write=False,
                               **{**kw, "registry_path": tight_reg, "config_path": tmp_path / "tight.json"})
        expect(any("EXCEEDS the reserve" in l for l in tight_lines), "a bridge whose projected cost tops the reserve must say so")
        # 4. it is now known: not raised again.
        r, _ = check(listed=["claude-sonnet-4-6", "claude-sonnet-5", "claude-sonnet-5-5"], **kw)
        expect(not r["appeared"], "an id already logged must not trigger twice")
        # 5. the active model disappears.
        r, lines = check(listed=["claude-sonnet-4-6", "claude-sonnet-5-5"], **kw)
        expect(r["gone"] == ["claude-sonnet-5"] and r["active_gone"], "vanished active model must be flagged")
        expect(any("series_closed" in l for l in lines), "vanished active model must mention series_closed")
        # 6. a vanished non-active id is `gone` but not `active_gone`.
        r, _ = check(listed=["claude-sonnet-5-5"], **kw)
        expect(r["gone"] == ["claude-sonnet-4-6"] and not r["active_gone"], "non-active vanish must not flag the active model")
        # 7. a returning id counts as appeared again (replay handles it).
        r, _ = check(listed=["claude-sonnet-4-6", "claude-sonnet-5-5"], **kw)
        expect(r["appeared"] == ["claude-sonnet-4-6"], "an id that comes back must be seen as appeared")
        # 8. another family's history never leaks in.
        r = diff(["claude-opus-5"], load_registry(reg), "claude-opus", None)
        expect(r["first_run"], "a family with no history must baseline, not alarm")
        # 9. dry run writes nothing.
        before = reg.read_text()
        check(listed=["claude-sonnet-5-5", "claude-sonnet-9"], write=False, **kw)
        expect(reg.read_text() == before, "write=False must not touch the log")

        # 10. plan: one per type, admitted v0 only, deterministic, alphabetically first family.
        plan = plan_bridge(pdir, "old", "new")
        expect([_type_of(p) for p in plan["protocol_ids"]] == list(REWORDING_TYPES), "plan must be one protocol per type, in type order")
        expect(all(p.endswith("__v0") for p in plan["protocol_ids"]), "plan must use v0 protocols only")
        expect(plan["protocol_ids"][0] == "base_rate_neglect__wording__v0", "plan must pick the alphabetically first family")
        expect(plan["calls_per_model"] == 4 * 30 * 4 and plan["calls_total"] == 2 * 4 * 30 * 4, "call count must be 4 protocols x n x 4 versions x 2 models")
        # 11. a missing type is an error, not a silent short bridge.
        (pdir / "base_rate_neglect__default__v0.json").unlink()
        (pdir / "risky_choice_framing__default__v0.json").unlink()
        try:
            plan_bridge(pdir, "old", "new")
            errors.append("a missing type must raise")
        except RuntimeError:
            pass
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="list the family's models from the API, diff against the log, print the bridge plan if needed")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    p.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    p.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    p.add_argument("--dry-run", action="store_true", help="report only; don't append to the log")
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/model_watch.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    from core.plumbing.render import load_active_commercial_model

    active = load_active_commercial_model(args.subject_models)
    if active is None:
        ap.error(f"{args.subject_models} has no active commercial generation")
    family = active["model_family"]
    listed = list_family_models(family)
    result, lines = check(
        listed=listed, family=family, active_model_id=active["model_id"],
        registry_path=args.registry, protocols_dir=args.protocols_dir, today=args.date, write=not args.dry_run,
    )
    print("\n".join(lines))
    return EXIT_ACTION_NEEDED if (result["appeared"] or result["active_gone"]) else 0


if __name__ == "__main__":
    sys.exit(main())
