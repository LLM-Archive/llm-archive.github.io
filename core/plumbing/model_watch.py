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


DEFAULT_EXPERIMENTS = ROOT / "experiments"
BRIDGE_TITLE = "MICHALIS: YOU MUST RUN THE BRIDGE NOW!!!"


def experiment_runs(experiments_dir: Path) -> list[dict]:
    """The finished runs on disk, from directory names `<date>__<protocol_id>__<model_id>__r<N>`.
    Staging directories (leading dot) and anything that does not parse are skipped."""
    runs: list[dict] = []
    if not experiments_dir.exists():
        return runs
    for d in sorted(experiments_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        parts = d.name.split("__")
        if len(parts) < 5:
            continue
        try:
            date.fromisoformat(parts[0])
        except ValueError:
            continue
        runs.append({"date": parts[0], "protocol_id": "__".join(parts[1:-2]), "model_id": parts[-2]})
    return runs


def bridge_pending(
    registry: list[dict], family: str, active_id: str | None, protocols_dir: Path, experiments_dir: Path, today: str,
) -> list[dict]:
    """The bridges that are owed. A model id that `appeared` in the family (and did not vanish or get
    a `bridge_waived` event) owes a bridge until, for every one of the plan's protocols, a finished
    run of the NEW model exists that was made on or after the day it appeared and that has a run of
    the CURRENT model of the same protocol within 7 days (spec.md §4.3: the same week). Derived from
    what is on disk, never from a flag somebody has to remember to set."""
    appeared: dict[str, str] = {}
    waived: set[str] = set()
    for rec in registry:
        if rec.get("family") != family:
            continue
        if rec["event"] == "appeared":
            appeared[rec["model_id"]] = rec["observed_date"]
        elif rec["event"] in ("gone", "baseline"):
            appeared.pop(rec["model_id"], None)
        elif rec["event"] == "bridge_waived":
            waived.add(rec["model_id"])
    runs = experiment_runs(experiments_dir)
    pending: list[dict] = []
    for new_id, since in sorted(appeared.items()):
        if new_id in waived:
            continue
        plan = plan_bridge(protocols_dir, active_id or "", new_id)
        missing = []
        for pid in plan["protocol_ids"]:
            done = False
            for n in runs:
                if n["protocol_id"] != pid or n["model_id"] != new_id or n["date"] < since:
                    continue
                for o in runs:
                    if o["protocol_id"] == pid and o["model_id"] == active_id and abs(
                        (date.fromisoformat(o["date"]) - date.fromisoformat(n["date"])).days
                    ) <= 7:
                        done = True
            if not done:
                missing.append(pid)
        if missing:
            pending.append({
                "new_id": new_id, "old_id": active_id, "since": since, "family": family, "missing": missing,
                "days": (date.fromisoformat(today) - date.fromisoformat(since)).days, "plan": plan,
            })
    return pending


def log_bridge_gap(today: str, log_path: Path | None = None) -> str:
    """spec.md §4.3: the bridge preempts that month's paid sweep, and that is a DISCLOSED gap (cause `generation_bridge`
    in coverage.csv), never a silent one. Nothing else ever wrote that cause. Appends one `full_sweep` gap for `today`,
    unless this month's sweep already ran (nothing was skipped, so nothing to disclose) or the gap is already logged."""
    from core.schedule import coverage

    log = log_path or coverage.DEFAULT_LOG
    month = today[:7]
    seen = [o for o in coverage.load_observations(log) if o["job"] == "full_sweep" and o["date"][:7] == month]
    if any(o["ran"] for o in seen):
        return f"this month's ({month}) full_sweep already ran: nothing was skipped, no gap logged"
    if any(o["cause"] == "generation_bridge" for o in seen):
        return f"the generation_bridge gap for {month} is already logged"
    coverage.record_observation("full_sweep", today, ran=False, cause="generation_bridge", log_path=log)
    return f"logged: full_sweep skipped on {today}, cause generation_bridge"


def _yaml_snippet(new_id: str, family: str) -> str:
    return (f"      - model_id: {new_id}\n        model_family: {family}\n"
            "        status: bridge_pending\n        declared_successor: null")


FOLLOWUP_TITLE = "MICHALIS: BRIDGE RUNS FOUND"


def bridge_followup(
    registry: list[dict], family: str, active_id: str | None, protocols_dir: Path, experiments_dir: Path, today: str,
    subject_models_path: Path, ledger_path: Path, coverage_log: Path,
) -> list[dict]:
    """After the bridge RUNS are in the repository, what is still left to do by hand -- derived from what is on
    disk, like `bridge_pending`, never from a flag somebody has to remember. One item per new model whose bridge
    is complete but whose bookkeeping is not. `steps` are {key, text, done, required}; an item exists only while a
    REQUIRED step is open (the model is listed in subject_models.yaml; the real bill is in the ledger). The other two are
    reminders that no file can prove done: the active-model switch (a decision) and the sweep gap (only the owner knows
    whether the paid sweep was held back)."""
    from core.budget import budget
    from core.plumbing.render import load_commercial_generations
    from core.schedule import coverage

    still_owed = {p["new_id"] for p in bridge_pending(registry, family, active_id, protocols_dir, experiments_dir, today)}
    appeared: dict[str, str] = {}
    waived: set[str] = set()
    for rec in registry:
        if rec.get("family") != family:
            continue
        if rec["event"] == "appeared":
            appeared[rec["model_id"]] = rec["observed_date"]
        elif rec["event"] in ("gone", "baseline"):
            appeared.pop(rec["model_id"], None)
        elif rec["event"] == "bridge_waived":
            waived.add(rec["model_id"])
    runs = experiment_runs(experiments_dir)
    generations = {g["model_id"]: g.get("status") for g in load_commercial_generations(subject_models_path)}
    items: list[dict] = []
    for new_id, since in sorted(appeared.items()):
        if new_id in waived or new_id in still_owed:
            continue
        plan_pids = set(plan_bridge(protocols_dir, active_id or "", new_id)["protocol_ids"])
        mine = [r for r in runs if r["model_id"] == new_id and r["protocol_id"] in plan_pids and r["date"] >= since]
        if not mine:
            continue
        month = max(r["date"] for r in mine)[:7]
        sweep_seen = [o for o in coverage.load_observations(coverage_log) if o["job"] == "full_sweep" and o["date"][:7] == month]
        billed = budget.spent_this_month(budget.load_ledger(ledger_path), month, category="generation_bridge") > 0
        status = generations.get(new_id)
        steps = [
            {"key": "yaml", "required": True, "done": new_id in generations,
             "text": f"Add `{new_id}` to `subject_models.yaml`, under `commercial:` `generations:`, so the website can label its rows:\n"
                     f"```\n{_yaml_snippet(new_id, family)}\n```"},
            {"key": "bill", "required": True, "done": billed,
             "text": f"Log the real bridge bill for {month}: `python3 -m core.budget.budget record generation_bridge <EUR> --run-id bridge__{new_id} --date <YYYY-MM-DD>` "
                     "(only an amount a bill has confirmed, never an estimate)."},
            {"key": "gap", "required": False, "done": bool(sweep_seen),
             "text": f"Only if you held back {month}'s paid sweep for the bridge (spec.md §4.3): `python3 -m core.plumbing.model_watch bridge-gap`. "
                     "Nothing is logged for that month yet; if the sweep ran or you did not hold it back, ignore this."},
            {"key": "switch", "required": False, "done": status == "active",
             "text": f"Decide the switch (yours, never automatic): `{new_id}` is `{status or 'not listed'}`. Only if Anthropic has EXPLICITLY named it the successor, "
                     f"set it to `status: active`, and the current model to `status: retired` with `declared_successor: {new_id}`."},
        ]
        open_required = sum(1 for st in steps if st["required"] and not st["done"])
        if open_required:
            items.append({"new_id": new_id, "family": family, "month": month, "steps": steps, "open_required": open_required})
    return items


def followup_message(item: dict) -> tuple[str, str]:
    """(title, body) of the ONE issue that lists what is left after the bridge runs. The title carries the count, so a
    change in it is what makes the workflow notify; the body is rewritten every day from the current state."""
    n = item["open_required"]
    title = f"{FOLLOWUP_TITLE} - {n} STEP(S) LEFT (new model: {item['new_id']})"
    lines = [f"# THE BRIDGE RUNS FOR `{item['new_id']}` ARE IN THE REPOSITORY. WELL DONE.", "",
             f"Still to do by hand: **{n} required step(s)**. This list is re-read from the repository every day; "
             "this issue closes itself once the required steps are done.", ""]
    for st in item["steps"]:
        mark = "x" if st["done"] else " "
        tag = "" if st["required"] else " _(reminder, not counted)_"
        first, _, rest = st["text"].partition("\n")
        lines.append(f"- [{mark}] {first}{tag}")
        if rest:
            lines.extend("      " + l if l else "" for l in rest.splitlines())
    lines += ["", "The active model is NOT switched automatically (spec.md §4.3)."]
    return title, "\n".join(lines) + "\n"


def loud_message(item: dict) -> tuple[str, str]:
    """(title, body) of the notification that must not be missed. Deliberately loud: this is the one
    job in the project whose delay cannot be repaired later."""
    plan = item["plan"]
    cost = _EST_COST_PER_CALL_EUR * plan["calls_total"]
    commands = "\n".join(
        f"python3 -m core.measure.pilot protocols/{pid}.json --client anthropic --model-id {mid} --model-family {item['family']}"
        for mid in (item["old_id"], item["new_id"]) for pid in item["missing"]
    )
    title = f"\U0001F6A8 {BRIDGE_TITLE} \U0001F6A8 (new model: {item['new_id']})"
    body = f"""# MICHALIS: YOU MUST RUN THE BRIDGE NOW!!!

## NEW MODEL: `{item['new_id']}`  (first seen {item['since']}, {item['days']} day(s) ago)
## THE MODEL WE MEASURE TODAY: `{item['old_id']}`

### WHY THIS CANNOT WAIT
The bridge measures the OLD and the NEW model in the SAME WEEK. When Anthropic retires the old model,
it can NEVER be measured again, at any price. **NOTHING CAN FIX A MISSED BRIDGE LATER.**

### WHAT TO DO (about 10 minutes of your time, about EUR {float(cost):.1f} of real money)
Run these commands one by one, in the repository (they need `ANTHROPIC_API_KEY` in your shell):

```
{commands}
```

Still to do: {len(item['missing'])} protocol(s): {', '.join(item['missing'])}

### WHEN YOU ARE DONE
1. Add the new model to `subject_models.yaml`, under `commercial:` `generations:`, so the website can label its rows:
```
{_yaml_snippet(item['new_id'], item['family'])}
```
   The active model is NOT switched by this. Only if Anthropic has EXPLICITLY named it the successor: set the new one
   to `status: active`, and the old one to `status: retired` with `declared_successor: {item['new_id']}`.
2. Only if you held back this month's paid sweep for the bridge (spec.md §4.3), disclose that gap:
   `python3 -m core.plumbing.model_watch bridge-gap` (it says so and writes nothing if the sweep already ran this month).
3. Commit the new folders in `experiments/` and log the real bill (`python3 -m core.budget.budget record ...`).

This issue closes itself the next morning, once the runs are found. Until then it comments EVERY DAY.

### IF NO BRIDGE IS NEEDED FOR THIS MODEL
`python3 -m core.plumbing.model_watch waive {item['new_id']} --reason "why"`

The active model is NOT switched automatically (spec.md §4.3): that stays your decision.
"""
    return title, body


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


def _verify_pending() -> list[str]:
    errors: list[str] = []
    fam = "claude-sonnet"

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdir = tmp_path / "protocols"
        pdir.mkdir()
        for fam_name in ("base_rate_neglect", "risky_choice_framing"):
            for t in REWORDING_TYPES:
                pid = f"{fam_name}__{t}__v0"
                (pdir / f"{pid}.json").write_text(json.dumps({"protocol_id": pid, "status": "admitted", "n": 30}))
        plan = plan_bridge(pdir, "claude-sonnet-5", "claude-sonnet-6")
        exp = tmp_path / "experiments"
        exp.mkdir()
        reg = [
            {"family": fam, "event": "baseline", "model_id": "claude-sonnet-5", "observed_date": "2026-09-24"},
            {"family": fam, "event": "appeared", "model_id": "claude-sonnet-6", "observed_date": "2026-10-01"},
        ]
        kw = dict(family=fam, active_id="claude-sonnet-5", protocols_dir=pdir, experiments_dir=exp)

        p = bridge_pending(reg, today="2026-10-04", **kw)
        expect(len(p) == 1 and p[0]["new_id"] == "claude-sonnet-6" and p[0]["days"] == 3
               and p[0]["missing"] == plan["protocol_ids"], "a new id with no runs must owe all four protocols")

        def make(day, pid, model):
            (exp / f"{day}__{pid}__{model}__r0").mkdir()

        # only the new model measured: still owed (the old model must be in the same week)
        for pid in plan["protocol_ids"]:
            make("2026-10-03", pid, "claude-sonnet-6")
        expect(len(bridge_pending(reg, today="2026-10-04", **kw)) == 1, "new-model runs alone must not clear the bridge")
        # old model 20 days away: not the same week
        make("2026-09-13", plan["protocol_ids"][0], "claude-sonnet-5")
        expect(plan["protocol_ids"][0] in bridge_pending(reg, today="2026-10-04", **kw)[0]["missing"],
               "an old-model run outside the 7-day window must not count")
        # old model within the week for all four: done
        for pid in plan["protocol_ids"]:
            make("2026-10-02", pid, "claude-sonnet-5")
        expect(bridge_pending(reg, today="2026-10-04", **kw) == [], "both models in the same week clears the bridge")
        # the follow-up: the runs are in, what is still open is read from disk (yaml entry, real bill)
        from core.budget import budget
        from core.schedule import coverage

        fu = dict(registry=reg, family=fam, active_id="claude-sonnet-5", protocols_dir=pdir, experiments_dir=exp, today="2026-10-04",
                  subject_models_path=tmp_path / "sm.yaml", ledger_path=tmp_path / "spend.json", coverage_log=tmp_path / "cov.jsonl")
        items = bridge_followup(**fu)
        expect(len(items) == 1 and items[0]["open_required"] == 2 and items[0]["month"] == "2026-10",
               f"a finished bridge with no bookkeeping must leave 2 required steps: {items!r}")
        ftitle, fbody = followup_message(items[0])
        expect("MICHALIS: BRIDGE RUNS FOUND" in ftitle and "2 STEP(S) LEFT" in ftitle and "claude-sonnet-6" in ftitle,
               "the follow-up title must be recognisable and carry the count")
        expect(fbody.count("- [ ]") == 4 and "status: bridge_pending" in fbody and "core.budget.budget record generation_bridge" in fbody,
               "the follow-up body must list the open steps with the yaml snippet and the ledger command")
        (tmp_path / "sm.yaml").write_text(
            "series:\n  commercial:\n    generations:\n      - model_id: claude-sonnet-5\n        status: active\n"
            "      - model_id: claude-sonnet-6\n        status: bridge_pending\n", encoding="utf-8")
        items = bridge_followup(**fu)
        expect(len(items) == 1 and items[0]["open_required"] == 1 and "- [x]" in followup_message(items[0])[1],
               "listing the model in subject_models.yaml must tick that step")
        coverage.record_observation("full_sweep", "2026-10-02", ran=True, log_path=tmp_path / "cov.jsonl")
        expect(next(st for st in bridge_followup(**fu)[0]["steps"] if st["key"] == "gap")["done"],
               "a full_sweep observation in the bridge month must tick the gap reminder")
        budget.record_spend(tmp_path / "spend.json", run_id="bridge__x", run_date="2026-10-03", category="generation_bridge", amount_eur="6.3")
        expect(bridge_followup(**fu) == [], "yaml entry + real bill logged: nothing left, the follow-up issue may close")
        expect(bridge_followup(**{**fu, "experiments_dir": tmp_path / "nothing"}) == [], "no runs at all is a pending bridge, not a follow-up")
        # a staging directory is never a finished run
        (exp / f".2026-10-05__{plan['protocol_ids'][0]}__claude-sonnet-7__r0.partial-x").mkdir()
        expect(all(r["model_id"] != "claude-sonnet-7" for r in experiment_runs(exp)), "staging dirs must be skipped")
        # waived, or gone again: nothing owed
        fresh = tmp_path / "empty"
        fresh.mkdir()
        kw2 = {**kw, "experiments_dir": fresh}
        expect(bridge_pending(reg + [{"family": fam, "event": "bridge_waived", "model_id": "claude-sonnet-6",
                                      "observed_date": "2026-10-02"}], today="2026-10-04", **kw2) == [],
               "a waived model owes no bridge")
        expect(bridge_pending(reg + [{"family": fam, "event": "gone", "model_id": "claude-sonnet-6",
                                      "observed_date": "2026-10-02"}], today="2026-10-04", **kw2) == [],
               "a model that vanished again owes no bridge")
        # runs made BEFORE the model appeared cannot satisfy it, even with the old model beside them
        early = tmp_path / "early"
        early.mkdir()
        for pid in plan["protocol_ids"]:
            (early / f"2026-09-30__{pid}__claude-sonnet-6__r0").mkdir()
            (early / f"2026-09-30__{pid}__claude-sonnet-5__r0").mkdir()
        early_pending = bridge_pending(reg, today="2026-10-04", **{**kw, "experiments_dir": early})
        expect(len(early_pending) == 1 and len(early_pending[0]["missing"]) == 4,
               "runs dated before the model appeared must not clear the bridge")
        title, body = loud_message(bridge_pending(reg, today="2026-10-04", **kw2)[0])
        expect("MICHALIS: YOU MUST RUN THE BRIDGE NOW!!!" in title and "claude-sonnet-6" in title, "the title must be loud and name the model")
        expect(body.count("core.measure.pilot") == 8, "the body must carry all 8 commands (4 protocols x 2 models)")
        expect("NOTHING CAN FIX A MISSED BRIDGE LATER" in body and "waive claude-sonnet-6" in body, "the body must say why, and how to waive")
        expect("model_id: claude-sonnet-6" in body and "status: bridge_pending" in body and "model_watch bridge-gap" in body,
               "the body must say how to add the new model to subject_models.yaml and how to disclose the gap")
        # bridge-gap: writes once, never when the month's sweep already ran
        from core.schedule import coverage
        log = Path(tmp) / "coverage.jsonl"
        expect("logged" in log_bridge_gap("2026-10-12", log) and coverage.load_observations(log)[0]["cause"] == "generation_bridge",
               "bridge-gap must log a full_sweep gap with cause generation_bridge")
        expect("already logged" in log_bridge_gap("2026-10-13", log) and len(coverage.load_observations(log)) == 1,
               "bridge-gap must not log the same month twice")
        ran_log = Path(tmp) / "coverage_ran.jsonl"
        coverage.record_observation("full_sweep", "2026-11-02", ran=True, log_path=ran_log)
        expect("already ran" in log_bridge_gap("2026-11-10", ran_log) and len(coverage.load_observations(ran_log)) == 1,
               "bridge-gap must write nothing when this month's sweep already ran")
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
    pp = sub.add_parser("pending", help="which bridges are owed, from what is on disk; exit 10 if any (free, offline)")
    pp.add_argument("--date", default=date.today().isoformat())
    pp.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    pp.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    pp.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    pp.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS)
    pp.add_argument("--json", action="store_true", help="print [{title, body, new_id, ...}] for the workflow")
    pp.add_argument("--pretend-new", default=None, help="TEST ONLY: act as if this model id had just appeared; writes nothing")
    wp = sub.add_parser("waive", help="record that no bridge is needed for a model id (appends to the log)")
    wp.add_argument("model_id")
    wp.add_argument("--reason", required=True)
    wp.add_argument("--date", default=date.today().isoformat())
    wp.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    wp.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    fp = sub.add_parser("followup", help="what is left to do by hand once the bridge runs are in the repository; exit 10 if anything is (free, offline)")
    fp.add_argument("--date", default=date.today().isoformat())
    fp.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    fp.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    fp.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    fp.add_argument("--experiments", type=Path, default=DEFAULT_EXPERIMENTS)
    fp.add_argument("--ledger", type=Path, default=None)
    fp.add_argument("--coverage-log", type=Path, default=None)
    fp.add_argument("--json", action="store_true", help="print [{title, body, new_id, open_required}] for the workflow")
    gp = sub.add_parser("bridge-gap", help="disclose that this month's paid sweep was held back for the bridge (coverage.csv gap)")
    gp.add_argument("--date", default=date.today().isoformat())
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "bridge-gap":
        print(log_bridge_gap(args.date))
        return 0

    if args.command == "verify":
        errors = _verify() + _verify_pending()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/model_watch.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    from core.plumbing.render import load_active_commercial_model

    active = load_active_commercial_model(args.subject_models)
    if active is None:
        ap.error(f"{args.subject_models} has no active commercial generation")
    family = active["model_family"]

    if args.command == "waive":
        rec = {"record_type": "model_observed", "observed_date": args.date, "family": family,
               "event": "bridge_waived", "model_id": args.model_id, "reason": args.reason}
        args.registry.parent.mkdir(parents=True, exist_ok=True)
        with args.registry.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"recorded: no bridge needed for {args.model_id} ({args.reason})")
        return 0

    if args.command == "followup":
        from core.budget import budget
        from core.schedule import coverage

        items = bridge_followup(load_registry(args.registry), family, active["model_id"], args.protocols_dir, args.experiments, args.date,
                                args.subject_models, args.ledger or budget.DEFAULT_LEDGER, args.coverage_log or coverage.DEFAULT_LOG)
        out = []
        for item in items:
            title, body = followup_message(item)
            out.append({"title": title, "body": body, "new_id": item["new_id"], "open_required": item["open_required"]})
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        elif out:
            for o in out:
                print(f"BRIDGE FOLLOW-UP: {o['new_id']}: {o['open_required']} required step(s) left")
        else:
            print("no bridge follow-up owed")
        return EXIT_ACTION_NEEDED if out else 0

    if args.command == "pending":
        registry = load_registry(args.registry)
        if args.pretend_new:
            registry = registry + [{"family": family, "event": "appeared", "model_id": args.pretend_new,
                                    "observed_date": args.date, "record_type": "model_observed"}]
        items = bridge_pending(registry, family, active["model_id"], args.protocols_dir, args.experiments, args.date)
        out = []
        for item in items:
            title, body = loud_message(item)
            out.append({"title": title, "body": body, "new_id": item["new_id"], "days": item["days"], "missing": item["missing"]})
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        elif out:
            for o in out:
                print(f"BRIDGE OWED: {o['new_id']} ({o['days']} day(s)); still to run: {', '.join(o['missing'])}")
        else:
            print("no bridge owed")
        return EXIT_ACTION_NEEDED if out else 0

    listed = list_family_models(family)
    result, lines = check(
        listed=listed, family=family, active_model_id=active["model_id"],
        registry_path=args.registry, protocols_dir=args.protocols_dir, today=args.date, write=not args.dry_run,
    )
    print("\n".join(lines))
    return EXIT_ACTION_NEEDED if (result["appeared"] or result["active_gone"]) else 0


if __name__ == "__main__":
    sys.exit(main())
