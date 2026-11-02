"""Resumes a real protocol run that was cut off partway, without repeating the calls already made.

    python3 -m core.plumbing.resume_run protocols/<protocol>.json --client anthropic
    python3 -m core.plumbing.resume_run --verify

"Resume" is new in this repo (no other word for it existed): the same run_id is run again, but the
first k calls are answered from the trials a cut-off run had already written, and only call k+1
onwards goes to the API.

core/measure/pilot.py is frozen (spec.md §13) and is NOT changed. It writes each trial into a
hidden `experiments/.<run_id>.partial-<random>/trials.jsonl` as it goes and renames that directory
into place only when all calls are done; a cut-off run leaves that directory behind. Two facts make
a resume possible from outside:

  - `pilot.schedule()` is seeded by (protocol_id, run_date, model id), so the order of the calls is
    the same every time for the same run_id. Trial `index` i is always the same call.
  - `pilot.run()` only talks to its client through `complete()`. So this module hands it a client
    that answers the first k calls from the saved trials and forwards the rest to the real one.
    pilot.run() itself, its measurement and its budget check are used unchanged.

It refuses (and spends nothing) when any of these is not true, because a resumed run must be the
same run, not a mix of two:

  - the date: the saved trials must be from the same run_date, and that date must be today. The
    seed contains the date, and a model can change behind the same name from one day to the next.
  - each saved trial's `prompt_sha256` must equal the hash of the prompt the schedule has at that
    index (catches a changed protocol, date or model id).
  - the model that answers the first new call must return the same `returned_model_id` as the
    saved trials. The one call that finds this out is spent and lost.

Safe on the files: a cut-off write can leave a last, half-written line; it is dropped and that call
is simply made again. The partial directory it read is never modified or deleted -- remove it by
hand once the resumed run is in place. The finished run folder gets one extra file, `resume.json`
(how many calls were replayed and from which directory), so the resume is visible in the data.
The ledger is untouched: as after any real run, the amount is logged by hand from the bill.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from core.measure import pilot
from core.measure.chain import sha256_hex
from core.measure.client import Response

ROOT = Path(__file__).resolve().parents[2]


class ResumeMismatch(Exception):
    """The saved trials do not belong to the run being resumed -- nothing was (or will be) spent on it."""


def partial_dirs(out_root: Path, run_id: str) -> list[Path]:
    return sorted(out_root.glob(f".{run_id}.partial-*"))


def load_saved_trials(partial: Path) -> list[dict]:
    """The trials of a cut-off run, in order. A half-written last line is dropped; a gap or a
    reordering anywhere else means the file is not what pilot.py wrote, so it is refused."""
    trials: list[dict] = []
    path = partial / "trials.jsonl"
    if not path.exists():
        return trials
    lines = path.read_text(encoding="utf-8").split("\n")
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            trial = json.loads(line)
        except json.JSONDecodeError:
            if all(not rest.strip() for rest in lines[number + 1 :]):
                break  # the cut-off write itself: that call is made again
            raise ResumeMismatch(f"{path}: line {number + 1} is not valid JSON and is not the last line")
        if trial.get("record_type") != "trial" or trial.get("index") != len(trials):
            raise ResumeMismatch(f"{path}: line {number + 1} is not trial index {len(trials)}")
        trials.append(trial)
    return trials


class ResumingClient:
    """Wraps the real client. The first len(saved) calls are answered from the saved trials (checked
    against the prompt the schedule has at that index); the rest are forwarded."""

    def __init__(self, real, saved: list[dict], source: Path) -> None:
        self._real = real
        self._saved = saved
        self.source = source
        self._next = 0
        self._saved_model_ids = {t["returned_model_id"] for t in saved if t.get("returned_model_id")}
        self._checked_model = not self._saved_model_ids

    def __getattr__(self, name):  # subject_model_id, model_family, series, explicitly_set, cost_usd ...
        return getattr(self._real, name)

    @property
    def replayed(self) -> int:
        return min(self._next, len(self._saved))

    def complete(self, prompt: str, *, meta: dict) -> Response:
        index = self._next
        self._next += 1
        if index < len(self._saved):
            trial = self._saved[index]
            if trial["prompt_sha256"] != sha256_hex(prompt.encode("utf-8")):
                raise ResumeMismatch(
                    f"trial {index}: the saved prompt differs from today's schedule (protocol, date or model id changed)"
                )
            return Response(trial["response_text"], trial["stop_reason"], trial["returned_model_id"], trial["error"])
        response = self._real.complete(prompt, meta=meta)
        if not self._checked_model and response.returned_model_id:
            if response.returned_model_id not in self._saved_model_ids:
                raise ResumeMismatch(
                    f"the model now answers as {response.returned_model_id}, the saved trials as "
                    f"{sorted(self._saved_model_ids)}: not the same run any more"
                )
            self._checked_model = True
        return response


def _run_id(protocol: dict, run_date: str, model_id: str, replicate: int) -> str:
    return f"{run_date}__{protocol['protocol_id']}__{model_id}__r{replicate}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", type=Path, nargs="?")
    ap.add_argument("--client", choices=["fake", "anthropic"])
    ap.add_argument("--verify", action="store_true", help="self-test against the fake client (free)")
    ap.add_argument("--partial", type=Path, default=None, help="the partial directory to resume (default: the one with most trials)")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--replicate", type=int, default=0)
    ap.add_argument("--out", type=Path, default=ROOT / "experiments")
    args, rest = ap.parse_known_args(argv)
    if args.verify:
        return verify()
    if args.protocol is None or args.client is None:
        ap.error("protocol and --client are required")
    if args.date != date.today().isoformat():
        ap.error(f"--date {args.date} is not today ({date.today().isoformat()}): a run is only resumed on its own day")

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    captured: dict = {}
    real_factory = _client_factory(args.client)

    def factory(*a, **kw):
        real = real_factory(*a, **kw)
        run_id = _run_id(protocol, args.date, real.subject_model_id, args.replicate)
        candidates = [args.partial] if args.partial else partial_dirs(args.out, run_id)
        loaded = [(len(t), p, t) for p in candidates for t in [load_saved_trials(p)]]
        loaded = [x for x in loaded if x[0] > 0]
        if not loaded:
            raise ResumeMismatch(f"no partial directory with trials for {run_id} in {args.out}")
        _, source, saved = max(loaded, key=lambda x: x[0])
        if saved[0].get("run_id") != run_id:
            raise ResumeMismatch(f"{source} belongs to {saved[0].get('run_id')}, not {run_id}")
        if len(saved) >= len(pilot.schedule(protocol, args.date, real.subject_model_id)):
            raise ResumeMismatch(f"{source} already holds every call; nothing to resume -- run pilot.py normally")
        wrapped = ResumingClient(real, saved, source)
        captured.update(client=wrapped, run_id=run_id, started=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print(f"resuming {run_id}: {len(saved)} calls from {source.name}, the rest from the API", flush=True)
        return wrapped

    pilot_args = [str(args.protocol), "--client", args.client, "--date", args.date, "--replicate", str(args.replicate), "--out", str(args.out), *rest]
    try:
        with _patched_client(args.client, factory):
            code = pilot.main(pilot_args)
    except ResumeMismatch as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if code == 0 and captured:
        wrapped = captured["client"]
        record = {
            "record_type": "resume",
            "run_id": captured["run_id"],
            "replayed_calls": wrapped.replayed,
            "resumed_from_index": wrapped.replayed,
            "source_partial_dir": wrapped.source.name,
            "resumed_utc": captured["started"],
        }
        (args.out / captured["run_id"] / "resume.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"\nresume.json written; {wrapped.source.name} was read, not changed -- delete it by hand when you like.")
    return code


def _client_factory(kind: str):
    if kind == "fake":
        from core.plumbing.fake_client import FakeClient

        return FakeClient
    from core.plumbing.anthropic_client import AnthropicClient

    return AnthropicClient


def _patched_client(kind: str, factory):
    """pilot.main builds its client from the class named in the module; swap that name for the factory."""
    module = "core.plumbing.fake_client.FakeClient" if kind == "fake" else "core.plumbing.anthropic_client.AnthropicClient"
    return mock.patch(module, factory)


# ----------------------------------------------------------------------------- self-test


def verify() -> int:
    """Free checks against the fake client: a resumed run gives the same measurement as an
    uninterrupted one, and every refusal refuses."""
    from core.plumbing.fake_client import FakeClient

    protocol = json.loads((ROOT / "protocols" / "base_rate_neglect__order__v1.json").read_text(encoding="utf-8"))
    today = date.today().isoformat()
    failures: list[str] = []

    def quiet(fn, *a):  # pilot.run prints one "#" per call
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a)

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failures.append(name)

    class Dies(FakeClient):
        """Answers `limit` calls, then dies the way a killed process does."""

        def __init__(self, limit: int) -> None:
            super().__init__()
            self.limit, self.n = limit, 0

        def complete(self, prompt, *, meta):
            if self.n >= self.limit:
                raise KeyboardInterrupt
            self.n += 1
            return super().complete(prompt, meta=meta)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        whole_root, cut_root = tmp_path / "whole", tmp_path / "cut"
        whole = quiet(pilot.run, protocol, FakeClient(), today, 0, whole_root)

        try:
            quiet(pilot.run, protocol, Dies(37), today, 0, cut_root)
        except KeyboardInterrupt:
            pass
        run_id = whole["run_id"]
        (partial,) = partial_dirs(cut_root, run_id)
        saved = load_saved_trials(partial)
        check("a cut-off run leaves its trials behind", 0 < len(saved) <= 37)

        # a half-written last line is dropped, the call is made again
        with open(partial / "trials.jsonl", "a", encoding="utf-8") as f:
            f.write('{"record_type": "trial", "index": ')
        check("a half-written last line is dropped", len(load_saved_trials(partial)) == len(saved))

        resuming = ResumingClient(FakeClient(), load_saved_trials(partial), partial)
        resumed = quiet(pilot.run, protocol, resuming, today, 0, cut_root)
        same = {k: v for k, v in resumed.items()} == {k: v for k, v in whole.items()}
        check("resumed measurement equals the uninterrupted one", same)
        check("only the missing calls reached the real client", resuming.replayed == len(saved))
        a = (whole_root / run_id / "trials.jsonl").read_text(encoding="utf-8")
        b = (cut_root / run_id / "trials.jsonl").read_text(encoding="utf-8")
        check("trials.jsonl is identical to the uninterrupted run's", a == b)

        # refusals
        saved = load_saved_trials(partial)
        bad = [dict(t) for t in saved]
        bad[3]["prompt_sha256"] = "0" * 64
        try:
            quiet(pilot.run, protocol, ResumingClient(FakeClient(), bad, partial), today, 0, tmp_path / "x1")
            check("a changed prompt is refused", False)
        except ResumeMismatch:
            check("a changed prompt is refused", True)

        other = [dict(t, returned_model_id="some-other-model") for t in saved]
        try:
            quiet(pilot.run, protocol, ResumingClient(FakeClient(), other, partial), today, 0, tmp_path / "x2")
            check("a different returned model id is refused", False)
        except ResumeMismatch:
            check("a different returned model id is refused", True)

        gap = tmp_path / "gap"
        gap.mkdir()
        lines = (partial / "trials.jsonl").read_text(encoding="utf-8").split("\n")
        (gap / "trials.jsonl").write_text("\n".join(lines[:2] + lines[3:]), encoding="utf-8")
        try:
            load_saved_trials(gap)
            check("a gap in the saved trials is refused", False)
        except ResumeMismatch:
            check("a gap in the saved trials is refused", True)

    if failures:
        print(f"resume_run.py: {len(failures)} check(s) failed")
        return 1
    print("resume_run.py: all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
