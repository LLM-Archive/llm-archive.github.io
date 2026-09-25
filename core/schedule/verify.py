"""Replays core/schedule/vectors.json against schedule.py.

    python3 -m core.schedule.verify
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import date
from fractions import Fraction
from pathlib import Path
from unittest import mock

from core.budget import budget
from core.plumbing.fake_client import FakeClient

from core.plumbing import prereg
from core.plumbing.anthropic_client import CircuitOpen

from . import coverage, run_due, schedule

_VECTORS = Path(__file__).resolve().parent / "vectors.json"
_REAL_PROTOCOLS_DIR = Path(__file__).resolve().parent.parent.parent / "protocols"
_REAL_SUBJECT_MODELS = Path(__file__).resolve().parent.parent.parent / "subject_models.yaml"


class _StubClient(FakeClient):
    """AnthropicClient's constructor shape (model_id, model_family) over FakeClient's real
    response distribution -- lets check_run_full_sweep_ladder below exercise run_full_sweep's
    real "anthropic" code path (the one with the ladder logic under test) without a real API
    key or network call. Never used outside this test."""

    def __init__(self, model_id: str, model_family: str) -> None:
        super().__init__(seed=model_id)
        self.subject_model_id = model_id
        self.model_family = model_family


def check_parse_cadence(data: dict) -> list[str]:
    errors = []
    for case in data["parse_cadence"]:
        got = schedule.parse_cadence(case["text"])
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got}, want {case['expected']}")
    return errors


def check_due_jobs(data: dict) -> list[str]:
    errors = []
    for case in data["due_jobs"]:
        got = schedule.due_jobs(case["intervals"], case["last_run"], date.fromisoformat(case["today"]))
        for job, want in case["expected"].items():
            for key, expected_value in want.items():
                if got[job][key] != expected_value:
                    errors.append(f"{case['name']}: {job}.{key} got {got[job][key]!r}, want {expected_value!r}")
    return errors


def check_plan(data: dict) -> list[str]:
    errors = []
    for case in data["plan"]:
        got = run_due.plan(case["due"])
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got}, want {case['expected']}")
    return errors


def check_record_run(data: dict) -> list[str]:
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "last_run.json"
        for case in data["record_run"]:
            try:
                schedule.record_run(path, case["job"], case["run_date"])
                if case["expect_error"]:
                    errors.append(f"{case['name']}: expected an error, got none")
            except ValueError:
                if not case["expect_error"]:
                    errors.append(f"{case['name']}: unexpected error")
    return errors


def check_gap_cause_for(data: dict) -> list[str]:
    errors = []
    for case in data["gap_cause_for"]:
        got = coverage.gap_cause_for(case["runner"], set(case["skip_reasons"]))
        if got != case["expected"]:
            errors.append(f"{case['name']}: got {got!r}, want {case['expected']!r}")
    return errors


def check_record_observation(data: dict) -> list[str]:
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coverage_log.jsonl"
        for case in data["record_observation"]:
            try:
                coverage.record_observation(
                    case["job"], case["date"], ran=case["ran"], cause=case["cause"], log_path=path
                )
                if case["expect_error"]:
                    errors.append(f"{case['name']}: expected an error, got none")
            except ValueError:
                if not case["expect_error"]:
                    errors.append(f"{case['name']}: unexpected error")
    return errors


def _write_ledger(path: Path, run_date: str, amount_eur: str) -> None:
    path.write_text(json.dumps([{
        "record_type": "spend", "run_id": "synthetic-for-verify",
        "run_date": run_date, "category": "full_sweep", "amount_eur": amount_eur, "note": None,
    }]), encoding="utf-8")


def check_run_full_sweep_ladder(data) -> list[str]:
    """run_full_sweep must actually enforce a rung's max_sweep_protocols/sweep_lane, not just read
    them off budget.check()'s decision -- a real gap found and fixed 2026-09-21: `allowed` is only
    False at the bottom rung (max_sweep_protocols == 0), so the two rungs in between
    (reduced_sweep's protocol cap, guard_only_sweep's lane restriction) were computed but never
    applied. Uses the real protocols/ (read-only) so the rung math is the actual math, not a
    hand-copied fixture -- only the ledger (synthetic spend) and the client (a FakeClient
    stand-in, zero-cost) are fabricated.

    The count-cap scenario uses a synthetic, huge-ceiling config (not the real budget.json) so
    that `run_due._EST_COST_PER_PROTOCOL_EUR`'s per-iteration projection (added 2026-09-22, see
    run_due.run_full_sweep's own docstring) is negligible relative to the ceiling and the count
    cap alone is what's under test -- against the *real*, small €10 ceiling, 6 protocols'
    projected cost alone (~€4.74) is wider than the reduced_sweep rung's whole 60-80% band, so the
    projection -- correctly -- would stop the sweep before the count cap ever bound, which is the
    right real-world behavior but isolates nothing. The lane-restriction scenario is split into
    two single-lane sweeps (rather than one mixed sweep) for the same reason: with a real, narrow
    ceiling, checking a guard-lane protocol first shifts the projection enough to change *why* the
    open-lane one gets skipped, which isn't what this scenario means to test."""
    del data  # no vectors.json cases for this one -- see docstring for why
    errors = []
    run_date = "2026-01-01"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # reduced_sweep (60-80%): max_sweep_protocols=6, sweep_lane=None -- all 12 real protocols
        # are lane-eligible, so this isolates the *count* cap. Huge synthetic ceiling (see
        # docstring) so the per-iteration spend projection can't confound it.
        protocols_dir = tmp / "protocols_count"
        protocols_dir.mkdir()
        for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json")):
            shutil.copy(f, protocols_dir / f.name)
        huge_ceiling_config = tmp / "budget_huge.json"
        real_rungs = json.loads(budget.DEFAULT_CONFIG.read_text())["rungs"]
        huge_ceiling_config.write_text(json.dumps({
            "monthly_ceiling_eur": "100000.00", "bridge_reserve_eur": "1.00", "rungs": real_rungs,
        }))
        ledger = tmp / "ledger_count.json"
        _write_ledger(ledger, run_date, "65000.00")  # 65% of the huge synthetic ceiling

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            # redirect_stdout: pilot.run()'s progress marks and run_due's own spend reminder
            # print unconditionally on client_kind == "anthropic" -- true for the real client this
            # branch exists to test, misleading here since _StubClient never bills anything real.
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date=run_date,
                protocols_dir=protocols_dir, out_root=tmp / "experiments_count",
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger, budget_config=huge_ceiling_config,
                manifests_dir=None,
            )
        ran = [r for r in results if "run_id" in r]
        capped = [r for r in results if r.get("skipped") == "budget_halted" and "caps this sweep" in r.get("detail", "")]
        if len(ran) != 6:
            errors.append(f"reduced_sweep: {len(ran)} protocol(s) ran, want 6 (max_sweep_protocols)")
        if len(capped) != len(results) - len(ran):
            errors.append(f"reduced_sweep: {len(capped)} capped of {len(results) - len(ran)} not-run, want all of them")

        # guard_only_sweep (80-95%): max_sweep_protocols=4, sweep_lane="guard". Two separate,
        # single-lane sweeps against the real, narrow €10 ceiling (see docstring for why not one
        # mixed sweep): the open-lane one alone (protocols_run stays 0, no projection confound)
        # must be lane-skipped; the two guard-lane ones alone must both run (well under the count
        # cap, and 2 * _EST_COST_PER_PROTOCOL_EUR is small enough to stay inside the 80-95% band).
        open_lane = next(json.loads(f.read_text())["protocol_id"] for f in _REAL_PROTOCOLS_DIR.glob("*.json") if json.loads(f.read_text())["lane"] == "open")
        guard_lane = [json.loads(f.read_text())["protocol_id"] for f in _REAL_PROTOCOLS_DIR.glob("*.json") if json.loads(f.read_text())["lane"] == "guard"][:2]

        protocols_dir_open = tmp / "protocols_lane_open"
        protocols_dir_open.mkdir()
        shutil.copy(_REAL_PROTOCOLS_DIR / f"{open_lane}.json", protocols_dir_open / f"{open_lane}.json")
        ledger_open = tmp / "ledger_lane_open.json"
        _write_ledger(ledger_open, run_date, "8.50")  # 85% of the real €10 general ceiling

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            results_open = run_due.run_full_sweep(
                client_kind="anthropic", run_date=run_date,
                protocols_dir=protocols_dir_open, out_root=tmp / "experiments_lane_open",
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger_open, budget_config=budget.DEFAULT_CONFIG,
                manifests_dir=None,
            )
        open_result = next((r for r in results_open if r["protocol_id"] == open_lane), None)
        if "run_id" in (open_result or {}):
            errors.append(f"guard_only_sweep: open-lane protocol {open_lane!r} ran, should have been lane-restricted")
        elif "lane" not in (open_result or {}).get("detail", ""):
            errors.append(f"guard_only_sweep: open-lane protocol {open_lane!r} skipped for the wrong reason: {open_result}")

        protocols_dir_guard = tmp / "protocols_lane_guard"
        protocols_dir_guard.mkdir()
        for pid in guard_lane:
            shutil.copy(_REAL_PROTOCOLS_DIR / f"{pid}.json", protocols_dir_guard / f"{pid}.json")
        ledger_guard = tmp / "ledger_lane_guard.json"
        # 85% of a scaled ceiling with the real rungs: the two protocols (n=120 -> ~3.2 each) must
        # FIT (see check_would_fit below for the rule that stops one that doesn't), so the lane
        # rung is what is under test, not the ceiling.
        _write_ledger(ledger_guard, run_date, "85.00")
        scaled_config = tmp / "budget_scaled.json"
        scaled_config.write_text(json.dumps({"monthly_ceiling_eur": "100.00", "bridge_reserve_eur": "1.00", "rungs": real_rungs}))

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            results_guard = run_due.run_full_sweep(
                client_kind="anthropic", run_date=run_date,
                protocols_dir=protocols_dir_guard, out_root=tmp / "experiments_lane_guard",
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger_guard, budget_config=scaled_config,
                manifests_dir=None,
            )
        by_pid_guard = {r["protocol_id"]: r for r in results_guard}
        for pid in guard_lane:
            if "run_id" not in by_pid_guard.get(pid, {}):
                errors.append(f"guard_only_sweep: guard-lane protocol {pid!r} should have run (under the cap, right lane): {by_pid_guard.get(pid)}")

    return errors


def check_ladder_projection(data) -> list[str]:
    """Real gap found and fixed 2026-09-22 (run_due.run_full_sweep's own docstring has the full
    incident): the ladder used to be evaluated once per iteration only against the *ledger's*
    confirmed spend, which never moves mid-sweep (real spend is only logged once a bill confirms
    it). A sweep that started well inside a generous rung could keep running protocol after
    protocol without ever re-evaluating against what it had itself already committed to spending
    this run -- found by hand, on a real sweep, requiring a human to stop it and log spend
    mid-run to keep it in check. Confirms the fix directly at the budget.check() layer (cheaper
    and more precise than another run_full_sweep simulation): a ledger with 0 confirmed spend this
    month, checked with a non-zero `additional_spent_eur` large enough to cross a rung boundary on
    its own, must return that stricter rung -- not the generous one a ledger-only read would give."""
    del data
    errors = []
    config = budget.load_config()
    ledger: list[dict] = []  # empty -- 0% confirmed spend this month
    no_projection = budget.check(ledger, "full_sweep", "2026-01", config=config)
    if no_projection["level"] != "full":
        errors.append(f"sanity: empty ledger should read 'full', got {no_projection['level']!r}")
    with_projection = budget.check(ledger, "full_sweep", "2026-01", config=config, additional_spent_eur=Fraction("8.50"))
    if with_projection["level"] != "guard_only_sweep":
        errors.append(
            f"a €8.50 in-flight projection on an otherwise-empty ledger should read 'guard_only_sweep' "
            f"(85% of €10), got {with_projection['level']!r} ({with_projection['spent_pct']}%)"
        )
    if with_projection["allowed"] is not True:
        errors.append(f"guard_only_sweep still allows spending (max_sweep_protocols=4 > 0), got allowed={with_projection['allowed']!r}")
    return errors


def check_already_run_this_month(data) -> list[str]:
    """Real gap found and fixed 2026-09-22 (run_due.py's own docstring has the full story): the
    old code only checked "already ran today" (pilot.run()'s FileExistsError), so a protocol
    hand-measured earlier in the same month -- a different calendar day -- got silently
    re-measured and re-billed. Confirms the fix: a pre-existing run dated earlier this month is
    skipped as `already_run_this_month` before the (real-money) client is ever constructed, while
    an uncovered protocol in the same sweep still runs normally."""
    del data
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocols_dir = tmp / "protocols"
        protocols_dir.mkdir()
        # two lane-open, unrelated-family protocols so both can run without lane/count capping
        chosen = [json.loads(f.read_text()) for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json"))
                  if json.loads(f.read_text())["lane"] == "open"][:2]
        for p in chosen:
            shutil.copy(_REAL_PROTOCOLS_DIR / f"{p['protocol_id']}.json", protocols_dir / f"{p['protocol_id']}.json")
        already_pid = chosen[0]["protocol_id"]
        new_pid = chosen[1]["protocol_id"]

        out_root = tmp / "experiments"
        out_root.mkdir()
        # a fake earlier-this-month real run for `already_pid`, on a different day than run_date
        (out_root / f"2026-06-03__{already_pid}__stub-model__r0").mkdir()

        ledger = tmp / "ledger.json"
        _write_ledger(ledger, "2026-06-01", "0")  # 0% spent -- isolates this check from the ladder

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date="2026-06-15",
                protocols_dir=protocols_dir, out_root=out_root,
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
                model_id="stub-model", model_family="stub-family", manifests_dir=None,
            )
        by_pid = {r["protocol_id"]: r for r in results}
        if by_pid.get(already_pid, {}).get("skipped") != "already_run_this_month":
            errors.append(f"{already_pid}: expected skipped=already_run_this_month, got {by_pid.get(already_pid)}")
        if "run_id" not in by_pid.get(new_pid, {}):
            errors.append(f"{new_pid}: should have run normally (no prior run this month), got {by_pid.get(new_pid)}")
    return errors


def check_sweep_rotation(data) -> list[str]:
    """A sweep the budget can only run part of must work through the whole rotation over
    successive months, not re-measure the same few protocols forever.

    Real gap, found while sizing the n=120 protocol series (decisions.md §49): run_full_sweep
    iterated protocols in plain alphabetical order, and its already-run-this-month skip resets
    with the calendar. While the whole rotation fit inside one month's budget that was invisible;
    once it doesn't, every month restarts at the top of the alphabet and the tail of the list is
    never reached. Confirms the fix: the protocol that has never been measured is swept before
    one that already has, whatever their names.

    Both are given a prior run in a PREVIOUS month, so neither is skipped by the monthly check --
    what is under test here is purely the order they come back in."""
    del data
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocols_dir = tmp / "protocols"
        protocols_dir.mkdir()
        chosen = [json.loads(f.read_text()) for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json"))
                  if json.loads(f.read_text())["lane"] == "open"][:2]
        for p in chosen:
            shutil.copy(_REAL_PROTOCOLS_DIR / f"{p['protocol_id']}.json", protocols_dir / f"{p['protocol_id']}.json")
        # `chosen` is alphabetical, so measuring the FIRST one is what the old code would have
        # re-run first; the second, never-measured one must now come first instead.
        measured_pid, never_pid = chosen[0]["protocol_id"], chosen[1]["protocol_id"]

        out_root = tmp / "experiments"
        out_root.mkdir()
        (out_root / f"2026-05-04__{measured_pid}__stub-model__r0").mkdir()

        ledger = tmp / "ledger.json"
        _write_ledger(ledger, "2026-06-01", "0")

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date="2026-06-15",
                protocols_dir=protocols_dir, out_root=out_root,
                subject_models_path=_REAL_SUBJECT_MODELS,
                budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
                model_id="stub-model", model_family="stub-family", manifests_dir=None,
            )
        order = [r["protocol_id"] for r in results]
        if order[:1] != [never_pid]:
            errors.append(f"never-measured {never_pid} must be swept first, got order {order}")

    # The projection must scale with each protocol's own n, or a rotation of mixed-n protocols
    # under-projects its own spend and runs past its rung (the same decisions.md §49 finding).
    small, large = {"n": 30}, {"n": 120}
    est_small, est_large = run_due._est_protocol_cost_eur(small), run_due._est_protocol_cost_eur(large)
    if est_large != 4 * est_small:
        errors.append(f"cost estimate must scale with n: n=30 -> {est_small}, n=120 -> {est_large}")
    if est_small != Fraction("0.792"):
        errors.append(f"n=30 estimate drifted from the €0.79/protocol it replaced: {est_small}")
    return errors


def check_prereg_guard(data) -> list[str]:
    """A paid sweep must refuse a protocol that no stamped manifest covers, spend nothing on it,
    and still run one that is covered (core/plumbing/prereg.py; guide/timestamps.md says why).

    Two real protocols are copied to a temp dir. Only the first is listed in a stamped manifest.
    Expect: the first runs, the second is skipped as `not_preregistered`. Then the same sweep with an
    empty manifests dir: nothing runs at all. Then the first protocol is 'edited' after stamping (its
    protocol_sha256 changes): it must stop being covered."""
    del data
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        protocols_dir = tmp / "protocols"
        protocols_dir.mkdir()
        chosen = sorted(_REAL_PROTOCOLS_DIR.glob("*__v0.json"))[:2]
        protos = [json.loads(f.read_text()) for f in chosen]
        for f in chosen:
            shutil.copy(f, protocols_dir / f.name)
        covered_pid, uncovered_pid = protos[0]["protocol_id"], protos[1]["protocol_id"]

        def sweep(manifests_dir: Path, out: str) -> list[dict]:
            ledger = tmp / f"ledger_{out}.json"
            _write_ledger(ledger, "2026-06-01", "0")
            with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _StubClient), contextlib.redirect_stdout(io.StringIO()):
                return run_due.run_full_sweep(
                    client_kind="anthropic", run_date="2026-06-15",
                    protocols_dir=protocols_dir, out_root=tmp / out,
                    subject_models_path=_REAL_SUBJECT_MODELS,
                    budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
                    model_id="stub-model", model_family="stub-family", manifests_dir=manifests_dir,
                )

        def row(p: dict, **over) -> dict:
            return {"protocol_id": p["protocol_id"], "panel_sha256": p["panel_sha256"],
                    "protocol_sha256": p["protocol_sha256"], **over}

        manifests = tmp / "manifests"
        manifests.mkdir()
        prereg._write_manifest(manifests, "m.json", [row(protos[0])])
        by_pid = {r["protocol_id"]: r for r in sweep(manifests, "exp_a")}
        if "run_id" not in by_pid.get(covered_pid, {}):
            errors.append(f"a stamped protocol must run: {by_pid.get(covered_pid)}")
        if by_pid.get(uncovered_pid, {}).get("skipped") != "not_preregistered":
            errors.append(f"an unstamped protocol must be skipped as not_preregistered: {by_pid.get(uncovered_pid)}")

        empty = tmp / "empty"
        empty.mkdir()
        results = sweep(empty, "exp_b")
        if any("run_id" in r for r in results):
            errors.append("with no stamped manifest nothing may run")
        if list((tmp / "exp_b").glob("*")) if (tmp / "exp_b").exists() else []:
            errors.append("a refused protocol must not create any run directory")

        edited = tmp / "edited"
        edited.mkdir()
        prereg._write_manifest(edited, "m.json", [row(protos[0], protocol_sha256="0" * 64), row(protos[1], panel_sha256="0" * 64)])
        if any("run_id" in r for r in sweep(edited, "exp_c")):
            errors.append("a protocol whose hash differs from the stamped one must not run")
    return errors


def check_would_fit(data) -> list[str]:
    """A protocol that would push the month past its ceiling must not start, even though the rung
    (which only reads what is already spent) still allows it: at EUR 8.00 of 10.00 the ladder says
    `guard_only_sweep`, and a v1 protocol (~3.2) is a guard-lane one -- allowed by the rung, but it
    would end near 11. It is skipped, and nothing is spent."""
    del data
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pdir = tmp / "protocols"
        pdir.mkdir()
        big = next(json.loads(f.read_text()) for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json"))
                   if json.loads(f.read_text())["lane"] == "guard" and json.loads(f.read_text())["n"] == 120)
        (pdir / f"{big['protocol_id']}.json").write_text(json.dumps(big))
        ledger = tmp / "ledger.json"
        _write_ledger(ledger, "2026-06-01", "8.00")
        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _MeteredStub), contextlib.redirect_stdout(io.StringIO()):
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date="2026-06-15", protocols_dir=pdir, out_root=tmp / "exp",
                subject_models_path=_REAL_SUBJECT_MODELS, budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
                model_id="stub-model", model_family="stub-family", manifests_dir=None,
            )
        if not results or results[0].get("skipped") != "budget_halted" or "would not fit" not in results[0].get("detail", ""):
            errors.append(f"a protocol that would exceed the ceiling must be skipped as 'would not fit': {results}")
        if len(json.loads(ledger.read_text())) != 1:
            errors.append("a skipped protocol must spend, and log, nothing")
    return errors


class _MeteredStub(_StubClient):
    """A stub that reports token usage the way AnthropicClient does: 1000 in / 500 out per call, so
    a call costs exactly $0.007 at Sonnet 5's list price ($2 / $10 per 1M). `break_after` makes it
    raise CircuitOpen on that call number, as the real client's breaker would."""

    break_after: int | None = None

    def __init__(self, model_id: str, model_family: str) -> None:
        super().__init__(model_id, model_family)
        self.input_tokens = 0
        self.output_tokens = 0
        self._calls = 0

    def complete(self, prompt, *, meta):
        self._calls += 1
        if self.break_after is not None and self._calls > self.break_after:
            raise CircuitOpen("stub breaker")
        self.input_tokens += 1000
        self.output_tokens += 500
        return super().complete(prompt, meta=meta)

    def cost_usd(self):
        return Fraction(self.input_tokens * 2 + self.output_tokens * 10, 1_000_000)


def _one_v0_protocol(dest: Path) -> dict:
    """Copies one real admitted protocol into `dest` (a fresh dir) and returns its JSON."""
    dest.mkdir()
    for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json")):
        p = json.loads(f.read_text())
        if p["status"] == "admitted" and p["n"] == 30:
            shutil.copy(f, dest / f.name)
            return p
    raise RuntimeError("no admitted n=30 protocol left to test against")


def check_measured_spend(data) -> list[str]:
    """An unattended paid run must leave the ledger true without a human: after a protocol runs,
    run_full_sweep writes tokens x price into spend.json, and the sweep's own projection is NOT
    added on top of it (that would count the same money twice and cut the sweep short). And a
    breaker that opens partway through must stop the whole sweep, log the calls already billed, and
    leave that protocol unrecorded as a finished run."""
    del data
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pdir = tmp / "protocols"
        proto = _one_v0_protocol(pdir)
        ledger = tmp / "ledger.json"
        _write_ledger(ledger, "2026-06-01", "0")
        calls = proto["n"] * 4
        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _MeteredStub), contextlib.redirect_stdout(io.StringIO()):
            results = run_due.run_full_sweep(
                client_kind="anthropic", run_date="2026-06-15", protocols_dir=pdir, out_root=tmp / "exp",
                subject_models_path=_REAL_SUBJECT_MODELS, budget_ledger=ledger, budget_config=budget.DEFAULT_CONFIG,
                model_id="stub-model", model_family="stub-family", manifests_dir=None,
            )
        if not results or "run_id" not in results[0]:
            return [f"measured spend: the protocol did not run: {results}"]
        entries = [e for e in json.loads(ledger.read_text()) if e["run_id"] == results[0]["run_id"]]
        if len(entries) != 1 or Fraction(entries[0]["amount_eur"]) != Fraction(calls * 7, 1000):
            errors.append(f"measured spend: want one ledger entry of {calls * 0.007:.3f}, got {entries}")
        elif "tokens x list price" not in (entries[0]["note"] or ""):
            errors.append(f"measured spend: the note must say how the amount was derived: {entries[0]['note']!r}")
        if "spend_recorded" not in results[0]:
            errors.append("measured spend: result must say the ledger was written")

        # breaker: opens on call 10 of the first protocol -> sweep stops, aborted spend is logged
        pdir2 = tmp / "protocols2"
        _one_v0_protocol(pdir2)
        for f in sorted(_REAL_PROTOCOLS_DIR.glob("*.json"))[:2]:
            shutil.copy(f, pdir2 / f.name)
        ledger2 = tmp / "ledger2.json"
        _write_ledger(ledger2, "2026-06-01", "0")

        class _Breaking(_MeteredStub):
            break_after = 10

        with mock.patch("core.plumbing.anthropic_client.AnthropicClient", _Breaking), contextlib.redirect_stdout(io.StringIO()):
            results2 = run_due.run_full_sweep(
                client_kind="anthropic", run_date="2026-06-15", protocols_dir=pdir2, out_root=tmp / "exp2",
                subject_models_path=_REAL_SUBJECT_MODELS, budget_ledger=ledger2, budget_config=budget.DEFAULT_CONFIG,
                model_id="stub-model", model_family="stub-family", manifests_dir=None,
            )
        if [r.get("skipped") for r in results2] != ["circuit_open"]:
            errors.append(f"circuit breaker: the sweep must stop at the first open breaker, got {results2}")
        aborted = [e for e in json.loads(ledger2.read_text()) if "aborted" in e["run_id"]]
        if len(aborted) != 1 or Fraction(aborted[0]["amount_eur"]) != Fraction(10 * 7, 1000):
            errors.append(f"circuit breaker: the 10 billed calls must be logged as one aborted entry of 0.07, got {aborted}")
        if any(d.name.startswith("2026-06-15__") and not d.name.startswith(".") for d in (tmp / "exp2").glob("*")):
            errors.append("circuit breaker: an abandoned run must not leave a finished run directory")
    if "circuit_open" not in run_due._STILL_DUE_SKIPS:
        errors.append("circuit_open must leave the job due, not record it as done")
    return errors


def check_anthropic_client_metering(data) -> list[str]:
    """AnthropicClient counts the tokens the API returns, prices them, and opens its breaker on a
    dead credential at once or on repeated failures -- exercised against a stand-in `anthropic`
    module, so nothing is billed."""
    del data
    import sys
    import types

    errors = []

    class _APIError(Exception):
        def __init__(self, status=None):
            super().__init__(f"status {status}")
            self.status_code = status

    class _Msg:
        content = [types.SimpleNamespace(type="text", text="x\nDECISION: A")]
        stop_reason = "end_turn"
        model = "claude-sonnet-5"
        usage = types.SimpleNamespace(input_tokens=1000, output_tokens=500)

    script: list = []

    class _Messages:
        def create(self, **kw):
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class _Anthropic:
        def __init__(self, **kw):
            self.messages = _Messages()

    fake = types.SimpleNamespace(Anthropic=_Anthropic, APIError=_APIError)
    from core.plumbing.anthropic_client import AnthropicClient

    with mock.patch.dict(sys.modules, {"anthropic": fake}):
        c = AnthropicClient("claude-sonnet-5", "claude-sonnet", api_key="k")
        script[:] = [_Msg(), _Msg()]
        c.complete("p", meta={}); c.complete("p", meta={})
        if (c.input_tokens, c.output_tokens) != (2000, 1000):
            errors.append(f"usage must add up per call, got {(c.input_tokens, c.output_tokens)}")
        if c.cost_usd() != Fraction(14, 1000):
            errors.append(f"2 calls x (1000 in, 500 out) at $2/$10 must cost 0.014, got {c.cost_usd()}")

        other = AnthropicClient("some-unpriced-model", "f", api_key="k")
        if other.cost_usd() is not None:
            errors.append("a model with no listed price must have no cost (fall back to the manual log)")

        # a dead credential opens the breaker on the first call
        script[:] = [_APIError(401)]
        try:
            c.complete("p", meta={})
            errors.append("a 401 must open the breaker at once")
        except CircuitOpen:
            pass

        # scattered failures do not; five in a row do; a success resets the count
        c2 = AnthropicClient("claude-sonnet-5", "claude-sonnet", api_key="k")
        script[:] = [_APIError(500)] * 4 + [_Msg()] + [_APIError(500)] * 4
        for _ in range(4):
            r = c2.complete("p", meta={})
            if r.error is None:
                errors.append("a failed call below the limit must come back as an error Response, not raise")
        c2.complete("p", meta={})
        for _ in range(4):
            c2.complete("p", meta={})  # 4 more failures after a success: still below 5 in a row
        script[:] = [_APIError(500)]
        try:
            c2.complete("p", meta={})
            errors.append("the 5th failure in a row must open the breaker")
        except CircuitOpen:
            pass
    return errors


def main() -> int:
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))

    checks = (
        ("parse_cadence", lambda: check_parse_cadence(data)),
        ("due_jobs", lambda: check_due_jobs(data)),
        ("plan", lambda: check_plan(data)),
        ("record_run", lambda: check_record_run(data)),
        ("gap_cause_for", lambda: check_gap_cause_for(data)),
        ("record_observation", lambda: check_record_observation(data)),
        ("run_full_sweep_ladder", lambda: check_run_full_sweep_ladder(data)),
        ("ladder_projection", lambda: check_ladder_projection(data)),
        ("already_run_this_month", lambda: check_already_run_this_month(data)),
        ("sweep_rotation", lambda: check_sweep_rotation(data)),
        ("prereg_guard", lambda: check_prereg_guard(data)),
        ("would_fit", lambda: check_would_fit(data)),
        ("measured_spend", lambda: check_measured_spend(data)),
        ("anthropic_client_metering", lambda: check_anthropic_client_metering(data)),
    )

    all_errors: list[str] = []
    for name, fn in checks:
        errors = fn()
        print(f"[{'FAIL' if errors else 'PASS'}] {name}")
        for e in errors:
            print(f"    {e}")
        all_errors += errors

    print()
    if all_errors:
        print(f"core/schedule/verify.py: {len(all_errors)} failure(s)")
        return 1
    print("core/schedule/verify.py: all vectors pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
