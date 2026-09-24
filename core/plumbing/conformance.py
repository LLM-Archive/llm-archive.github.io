"""Admission gate 1b -- transformation conformance, checked by code, never by reading (spec.md §3).

Gate 1 (core/measure/invariants.py, frozen) proves A, A' and B are the same decision problem in
their numbers. It cannot say whether B's WORDS differ from A's only in the way its rewording type
allows. This file does: for every scenario, B must be A plus exactly the transformation the
protocol's rewording type names -- nothing more. It does not read English and does not call a model.

The closed catalog (one rule per rewording type):

  order      B is A with the option lines reordered; every other line identical.
  default    exactly one line differs -- the default sentence -- and only by the option letter
             it names, which is switched A <-> B.
  anchoring  exactly one token differs, and it is a number that appears nowhere else in the
             scenario (so it cannot be an informative number, spec.md §3).
  wording    risky_choice_framing: only option lines differ (gain <-> loss description).
             sunk_cost_fallacy: B is A plus the fixed phrase ", giving up the €S already spent,"
             where €S is the amount A already states.
             base_rate_neglect: only the paragraph giving the rates differs, and the natural
             frequencies equal the percentages exactly (K = 10*p, F = q% of 1000-K).

A B that needs a transformation outside this list is not an equivalent rewording under the
specification; a new transformation is added to the catalog first, in the open, before any
protocol uses it.

What this does NOT establish: that each listed transformation is *equivalent* -- that is declared
once, by the specification, and is the one judgment left to people. It establishes only that no
protocol's B does anything the catalog does not name. Prints only rule messages and counts, never
scenario text, so it is safe to run inside the isolated sealed repository too.

    python3 -m core.plumbing.conformance check [--protocols-dir protocols]
    python3 -m core.plumbing.conformance selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOLS_DIR = ROOT / "protocols"

_NUM = r"\d[\d,]*"
_OPTION_LINE = re.compile(r"^(Plan|Option) [AB]:")
_OPTION_LETTER = re.compile(r"(Plan|Option) ([AB])\b")


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _is_option_line(line: str) -> bool:
    return bool(_OPTION_LINE.match(line))


def check_order(a: str, b: str, family: str) -> str | None:
    la, lb = _lines(a), _lines(b)
    if la == lb:
        return "B identical to A"
    if sorted(la) != sorted(lb):
        return "B is not a pure reordering of A's lines"
    if [l for l in la if not _is_option_line(l)] != [l for l in lb if not _is_option_line(l)]:
        return "non-option lines moved"
    if [l for l in la if _is_option_line(l)] == [l for l in lb if _is_option_line(l)]:
        return "option lines not reordered"
    return None


def check_default(a: str, b: str, family: str) -> str | None:
    la, lb = _lines(a), _lines(b)
    if len(la) != len(lb):
        return "line count differs"
    diff = [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]
    if len(diff) != 1:
        return f"{len(diff)} lines differ (expected exactly 1)"
    x, y = la[diff[0]], lb[diff[0]]
    if "by default" not in x or "by default" not in y:
        return "differing line is not the default sentence"
    ma, mb = _OPTION_LETTER.search(x), _OPTION_LETTER.search(y)
    if not ma or not mb or ma.group(2) == mb.group(2):
        return "default option not switched A<->B"
    if _OPTION_LETTER.sub("X", x) != _OPTION_LETTER.sub("X", y):
        return "default sentence differs by more than the option letter"
    return None


def check_anchoring(a: str, b: str, family: str) -> str | None:
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return "token count differs"
    diff = [(x, y) for x, y in zip(ta, tb) if x != y]
    if len(diff) != 1:
        return f"{len(diff)} tokens differ (expected exactly 1)"
    x, y = diff[0]
    nx, ny = x.rstrip(".,;"), y.rstrip(".,;")
    if not (re.fullmatch(_NUM, nx) and re.fullmatch(_NUM, ny)):
        return "differing token is not a number"
    rest = {t.strip(".,;:€%()") for t in ta if t != x}
    if nx in rest or ny in rest:
        return "anchor value also appears elsewhere in the scenario (not non-diagnostic)"
    return None


def check_wording(a: str, b: str, family: str) -> str | None:
    la, lb = _lines(a), _lines(b)
    if la == lb:
        return "B identical to A"
    if family == "risky_choice_framing":
        if len(la) != len(lb):
            return "line count differs"
        for x, y in zip(la, lb):
            if x != y and not _is_option_line(x):
                return "a non-option line differs"
        return None
    if family == "sunk_cost_fallacy":
        if len(la) != len(lb):
            return "line count differs"
        diff = [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]
        if len(diff) != 1:
            return f"{len(diff)} lines differ (expected exactly 1)"
        m = re.search(r"So far, €(" + _NUM + r") has been spent", a)
        if not m:
            return "cannot find the already-spent amount in A"
        phrase = r", giving up the €" + re.escape(m.group(1)) + r" already spent,"
        if re.sub(phrase, "", lb[diff[0]], count=1) != la[diff[0]]:
            return "B is not A plus the fixed 'giving up the €S already spent' phrase"
        return None
    if family == "base_rate_neglect":
        if [l for l in la if _is_option_line(l)] != [l for l in lb if _is_option_line(l)]:
            return "option lines differ"
        pa = re.search(r"(\d+)% of the .* are (\w+)", a)
        qa = re.search(r"wrongly flags (\d+)%", a)
        kb = re.search(r"Out of every 1,000 .*?, (\d+) are ", b)
        fb = re.search(r"wrongly flags (\d+)", b)
        if not (pa and qa and kb and fb):
            return "cannot parse rates (percent form in A / natural-frequency form in B)"
        p, q, k, f = int(pa.group(1)), int(qa.group(1)), int(kb.group(1)), int(fb.group(1))
        if k != p * 10 or f * 100 != q * (1000 - k):
            return "natural frequencies do not equal the percentages"
        if la[0] != lb[0]:
            return "opening line differs"
        return None
    return f"no rule for family {family}"


RULES = {"order": check_order, "default": check_default, "anchoring": check_anchoring, "wording": check_wording}


def check_protocol(protocol: dict) -> list[tuple[str, str]]:
    """(scenario_id, message) for every scenario whose B breaks its type's rule; [] = conformant."""
    rule = RULES.get(protocol["rewording_type"])
    if rule is None:
        return [("*", f"no rule for rewording_type {protocol['rewording_type']}")]
    fails = []
    for s in protocol["scenarios"]:
        err = rule(s["versions"]["A"], s["versions"]["B"], protocol["family"])
        if err:
            fails.append((s["scenario_id"], err))
    return fails


def _selftest() -> list[str]:
    """Each rule must accept a clean B and reject a broken one -- an always-passing check proves nothing."""
    header = "A country faces an outbreak. 600 people are at risk."
    opt_a = "Plan A: 200 people will be saved."
    opt_b = "Plan B: There is a 1/3 probability that all 600 people will be saved."
    tail = "Which plan do you choose?"
    base = "\n".join([header, "", opt_a, opt_b, "", tail])
    cases = [
        ("order ok", check_order, base, "\n".join([header, "", opt_b, opt_a, "", tail]), "risky_choice_framing", True),
        ("order changes a number", check_order, base, "\n".join([header, "", opt_b, opt_a.replace("200", "300"), "", tail]), "risky_choice_framing", False),
        ("default ok", check_default, base + "\nPlan A goes ahead by default.", base + "\nPlan B goes ahead by default.", "risky_choice_framing", True),
        ("default not switched", check_default, base + "\nPlan A goes ahead by default.", base + "\nPlan A goes ahead by default.", "risky_choice_framing", False),
        ("anchoring ok", check_anchoring, header + " Tag, unrelated: 14.", header + " Tag, unrelated: 8,207.", "risky_choice_framing", True),
        ("anchoring number reused", check_anchoring, header + " Tag, unrelated: 14.", header + " Tag, unrelated: 600.", "risky_choice_framing", False),
        ("wording/risky ok", check_wording, base, base.replace("200 people will be saved", "400 people will die"), "risky_choice_framing", True),
        ("wording/risky context altered", check_wording, base, base.replace("at risk", "in grave danger"), "risky_choice_framing", False),
        (
            "wording/sunk ok", check_wording,
            "So far, €800,000 has been spent.\nPlan A: Stop now and sell for €100,000.",
            "So far, €800,000 has been spent.\nPlan A: Stop now, giving up the €800,000 already spent, and sell for €100,000.",
            "sunk_cost_fallacy", True,
        ),
        (
            "wording/sunk wrong amount", check_wording,
            "So far, €800,000 has been spent.\nPlan A: Stop now and sell for €100,000.",
            "So far, €800,000 has been spent.\nPlan A: Stop now, giving up the €900,000 already spent, and sell for €100,000.",
            "sunk_cost_fallacy", False,
        ),
    ]
    errors = []
    for name, fn, a, b, fam, should_pass in cases:
        got = fn(a, b, fam)
        if should_pass and got is not None:
            errors.append(f"selftest '{name}': expected pass, got: {got}")
        if not should_pass and got is None:
            errors.append(f"selftest '{name}': expected a rejection, but it passed")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="check every protocol in a directory")
    p_check.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    sub.add_parser("selftest", help="prove each rule accepts a clean B and rejects a broken one")
    args = ap.parse_args(argv)

    if args.command == "selftest":
        errors = _selftest()
        for e in errors:
            print(f"[FAIL] {e}")
        print("[PASS] conformance selftest" if not errors else f"{len(errors)} selftest failure(s)")
        return 1 if errors else 0

    failing = 0
    for path in sorted(args.protocols_dir.glob("*.json")):
        protocol = json.loads(path.read_text(encoding="utf-8"))
        if "scenarios" not in protocol:
            continue
        fails = check_protocol(protocol)
        failing += bool(fails)
        label = "sealed" if protocol.get("lane") == "sealed" else protocol["protocol_id"]
        n = len(protocol["scenarios"])
        detail = ""
        if fails:
            counts: dict[str, int] = {}
            for _, msg in fails:
                counts[msg] = counts.get(msg, 0) + 1
            detail = " " + str(counts)
        print(f"{'FAIL' if fails else 'ok  '} {label:55s} {n - len(fails)}/{n}{detail}")
    print(f"protocols failing: {failing}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
