"""Replays guide/ai-assistant.md the way an AI assistant would, so a change to the site, the guide or
the core cannot silently break the procedure the guide tells users to follow.

    python3 -m core.plumbing.verify_agent_guide          # against this repo's files, before publishing
    python3 -m core.plumbing.verify_agent_guide --live   # against what users download RIGHT NOW

It builds a scratch folder holding only what a user's clone/download has (the files the public site
ships), then:
  1. the curl URL in the guide points at a file that exists in guide/;
  2. runs the guide's step 1 for real: --print-prompts, fake replies, --read-answers;
  3. runs the guide's own python snippets (steps 2 and 3) extracted from the markdown, not copies;
  4. checks the standalone script agrees with the archive: its 8 prompts are in open-lane/ by sha256,
     its published scores are in stability.csv, its extraction rule gives the same answers as
     core/measure/grammar_v2.py, its reference answers are in experiments/;
  5. every file the guide names exists in the shipped set.
--live does the same on files fetched over the network from https://llm-archive.github.io/ (the URLs
the guide gives users), so a change or outage on the public side is caught too; the only local input
is core/measure/grammar_v2.py, the reference the script's extraction rule is compared with.
Exit 0 only if all pass. Standard library only.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "guide" / "ai-assistant.md"
SCRIPT_NAME = "llm_archive_compare.py"

# What a user gets from the public site (tools/deploy_public_site.py DIR_ITEMS / FILE_ITEMS).
SHIPPED = {
    "guide": "guide",
    "core/measure": "core/measure",
    "core/plumbing": "core/plumbing",
    "open-lane": "open-lane",
    "stability.csv": "stability.csv",
    "outcomes.csv": "outcomes.csv",
    "coverage.csv": "coverage.csv",
    "docs/spec.md": "SPEC.md",
}

LIVE_BASE = "https://llm-archive.github.io/"
LIVE_CLONE = "https://github.com/LLM-Archive/llm-archive.github.io"
LIVE_ALWAYS = ["guide/ai-assistant.md", f"guide/{SCRIPT_NAME}", "stability.csv", "outcomes.csv", "coverage.csv", "SPEC.md"]
_NAMED = re.compile(r"`((?:guide/)?[\w.\-/]+\.(?:md|csv|jsonl|py))`")

_HEREDOC = re.compile(r"python3 - <<'PY'\n(.*?)\nPY\n", re.S)
_CURL = re.compile(r"curl -O (https://llm-archive\.github\.io/(guide/\S+))")


def _run(args: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120, **kw)


def _stage(scratch: Path) -> None:
    for src, dst in SHIPPED.items():
        source, target = ROOT / src, scratch / dst
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _fetch(url: str) -> bytes:
    last: Exception | None = None
    for _ in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "llm-archive-guide-check"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as error:  # 404, DNS, timeout: retried, then reported
            last = error
    raise OSError(f"{url}: {last}")


def _stage_live(scratch: Path, errors: list[str]) -> str:
    """Download what the guide tells a user to download, exactly as a user would. Returns the guide."""
    def get(path: str) -> str | None:
        try:
            data = _fetch(LIVE_BASE + path)
        except OSError as error:
            errors.append(f"cannot download {error}")
            return None
        if not data.strip():
            errors.append(f"{LIVE_BASE + path} is empty")
            return None
        (scratch / path).parent.mkdir(parents=True, exist_ok=True)
        (scratch / path).write_bytes(data)
        return data.decode("utf-8", "replace")

    guide = get("guide/ai-assistant.md")
    if guide is None:
        return ""
    wanted = set(LIVE_ALWAYS)
    for name in _NAMED.findall(guide):
        if "<" in name:
            continue
        # a bare guide page (`reproducing.md`) lives in guide/; root files and paths are as written
        wanted.add(name if "/" in name or name.endswith(".csv") or name == "SPEC.md" else f"guide/{name}")
    wanted |= {f"open-lane/{year}.jsonl" for year in re.findall(r"open-lane/(\d{4})\.jsonl", guide)}
    curl = _CURL.search(guide)
    if curl:
        wanted.add(curl.group(2))
    for path in sorted(wanted - {"guide/ai-assistant.md"}):
        get(path)
    clone = subprocess.run(["git", "ls-remote", LIVE_CLONE, "HEAD"], capture_output=True, text=True, timeout=60)
    if clone.returncode != 0 or not clone.stdout.strip():
        errors.append(f"`git clone {LIVE_CLONE}` (step 2 of the guide) does not work: {clone.stderr.strip()}")
    return guide


def _load_script(path: Path) -> dict:
    """The standalone script's ARCHIVE data and extract(), without running its main()."""
    namespace: dict = {"__name__": "compare_under_test"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace


def _check_step_1(scratch: Path, guide: str, errors: list[str]) -> dict:
    match = _CURL.search(guide)
    if not match:
        errors.append("ai-assistant.md: no `curl -O https://llm-archive.github.io/guide/...` line found")
    elif not (scratch / match.group(2)).is_file():
        errors.append(f"ai-assistant.md: the curl URL points at {match.group(2)}, which the site does not ship")

    script = scratch / "guide" / SCRIPT_NAME
    work = scratch / "user"
    work.mkdir()
    shutil.copy2(script, work / SCRIPT_NAME)

    made = _run([sys.executable, SCRIPT_NAME, "--print-prompts", "my_run"], work)
    if made.returncode != 0 or "matching its published sha256" not in made.stdout:
        errors.append(f"step 1 --print-prompts failed or missed the sha256 line:\n{made.stdout}{made.stderr}")
        return {}
    prompts = sorted((work / "my_run").glob("*.txt"))
    if len(prompts) != 8:
        errors.append(f"step 1 --print-prompts wrote {len(prompts)} files, the guide says 8")
    for prompt in prompts:
        (work / "my_run" / "answers" / prompt.name).write_text("Reasoning.\n\nDECISION: A", encoding="utf-8")

    read = _run([sys.executable, SCRIPT_NAME, "--read-answers", "my_run"], work)
    if read.returncode != 0:
        errors.append(f"step 1 --read-answers failed:\n{read.stdout}{read.stderr}")
    for wanted in ("your model: A", "claude-sonnet-5", "qwen2.5-1.5b-instruct-q4_k_m", "changed its decision"):
        if wanted not in read.stdout:
            errors.append(f"step 1 --read-answers output no longer contains {wanted!r}")

    # A file edited after download must be refused, as the guide's troubleshooting row promises.
    tampered = work / "tampered.py"
    tampered.write_text(script.read_text(encoding="utf-8").replace("hospital lab", "hospital labx", 1), encoding="utf-8")
    refused = _run([sys.executable, "tampered.py", "--print-prompts", "t"], work)
    if refused.returncode == 0 or "no longer match their own hashes" not in (refused.stdout + refused.stderr):
        errors.append("an edited llm_archive_compare.py was not refused")
    return _load_script(script)


def _check_snippets(scratch: Path, guide: str, errors: list[str]) -> None:
    snippets = _HEREDOC.findall(guide)
    if len(snippets) != 2:
        errors.append(f"ai-assistant.md has {len(snippets)} `python3 - <<'PY'` snippets, expected 2 (steps 2 and 3)")
        return
    summary, arithmetic = snippets

    out = _run([sys.executable, "-"], scratch, input=summary)
    rows = [l for l in out.stdout.splitlines()[1:] if l.strip()]
    if out.returncode != 0 or not rows:
        errors.append(f"step 2 snippet failed or printed no rows:\n{out.stdout}{out.stderr}")

    out = _run([sys.executable, "-"], scratch, input=arithmetic)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    if out.returncode != 0 or not lines:
        errors.append(f"step 3 snippet failed or printed nothing:\n{out.stdout}{out.stderr}")
    bad = [l for l in lines if not l.startswith("OK")]
    if bad:
        errors.append("step 3 snippet: the guide promises OK on every line, got:\n  " + "\n  ".join(bad))


def _check_against_archive(scratch: Path, ns: dict, errors: list[str]) -> None:
    if not ns:
        return
    archive = ns["ARCHIVE"]

    sha_in_open_lane = set()
    for path in (scratch / "open-lane").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sha_in_open_lane.add(json.loads(line).get("prompt_sha256"))
    for q in archive["questions"]:
        if q["sha256"] not in sha_in_open_lane:
            errors.append(f"script question {q['family']}/{q['version']}: sha256 {q['sha256'][:12]}... not in open-lane/")

    published = {(r["protocol_id"], r["subject_model_id"], int(r["n"])): float(r["stability_pct"])
                 for r in csv.DictReader(open(scratch / "stability.csv", encoding="utf-8"))
                 if r["record_type"] == "measurement"}
    for family, scores in archive["published_scores"].items():
        for s in scores:
            got = published.get((s["protocol"], s["model"], s["n"]))
            if got is None or abs(got - s["stability_pct"]) > 0.05:
                errors.append(f"script score {s['protocol']} / {s['model']}: {s['stability_pct']} vs stability.csv {got}")

    sys.path.insert(0, str(ROOT))
    from core.measure import grammar_v2

    for text in ("DECISION: A", "**Decision: B**", "decision：b.", "DECISION: A\nDECISION: B", "I'd pick B", "", "DECISION: C"):
        real = grammar_v2.extract(text, ["A", "B"])
        want = real.token if real.outcome == "valid" else "invalid"
        got = ns["extract"](text)
        if (got in ("A", "B")) != (want in ("A", "B")) or (got in ("A", "B") and got != want):
            errors.append(f"script extract({text!r}) = {got!r} but core grammar_v2 says {want!r}")


def verify(live: bool = False) -> list[str]:
    errors: list[str] = []
    if not live and not GUIDE.is_file():
        return [f"{GUIDE} is missing"]
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        if live:
            guide = _stage_live(scratch, errors)
            if not guide or not (scratch / "guide" / SCRIPT_NAME).is_file():
                return errors  # nothing to replay; the download errors above are the finding
        else:
            guide = GUIDE.read_text(encoding="utf-8")
            _stage(scratch)
        for name in _NAMED.findall(guide):
            if "<" in name or name.startswith("open-lane/") and "<year>" in name:
                continue
            if not (scratch / name).exists() and not (scratch / "guide" / name).exists():
                errors.append(f"ai-assistant.md names `{name}`, which the site does not ship")
        ns = _check_step_1(scratch, guide, errors)
        _check_snippets(scratch, guide, errors)
        _check_against_archive(scratch, ns, errors)
    return errors


def main() -> int:
    live = "--live" in sys.argv[1:]
    errors = verify(live)
    for e in errors:
        print(f"[FAIL] {e}")
    where = f"the live site ({LIVE_BASE})" if live else "this repository"
    print(f"[PASS] the ai-assistant.md procedure replays cleanly against {where}" if not errors
          else f"{len(errors)} problem(s) against {where}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
