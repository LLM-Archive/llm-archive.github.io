"""Deterministic decision extraction (spec.md §5). grammar_version 1.

Every response must end with a line of the exact shape `DECISION: <token>`, and this file is the
entire reader for it — plain string matching, no model in the loop. That is a deliberate trade:
a second model reading the response for its "real" answer would introduce a second, undeclared
source of drift into the measurement, one with its own version history and its own quirks, which
would then need its own instrument checks just like the subject model does. A fixed rule has no
version history to drift.

Nothing outside a decision line is ever read — so a reply like "Option A is tempting, but in the
end I go with B" is `unparseable`, not "B". Reading the reasoning would mean interpreting free
text, which is exactly the thing this file exists to avoid.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GRAMMAR_VERSION = 1

_EMPHASIS = str.maketrans("", "", "*_`")
_WHITESPACE = re.compile(r"\s+")
# NFC already folds U+037E (Greek question mark) to ";" and U+0387 (ano teleia) to "·".
_TRAILING_PUNCT = ".!;·"
_DECISION_LINE = re.compile(r"^decision ?[:：] ?(.*)$")


def normalize(line: str) -> str:
    s = unicodedata.normalize("NFC", line)
    s = s.casefold()
    s = s.translate(_EMPHASIS)
    s = _WHITESPACE.sub(" ", s).strip()
    return s.rstrip(_TRAILING_PUNCT + " ")


@dataclass(frozen=True)
class Extraction:
    outcome: str  # "valid" | "unparseable" | "empty"
    token: str | None  # the matching entry of options[], exactly as the protocol spells it
    reason: str | None  # why it is not valid; None when valid


def normalized_options(options: list[str]) -> dict[str, str]:
    table = {normalize(o): o for o in options}
    if len(table) != len(options):
        raise ValueError(f"options collide after normalization: {options}")
    return table


def extract(text: str | None, options: list[str]) -> Extraction:
    """Scans every line independently (never the whole text as one blob), so a decision line
    buried after paragraphs of reasoning is found exactly the same way as one on its own.

    Repeating the SAME token on multiple lines is treated as valid — a model that states its
    decision, then restates it in a closing summary, has not been ambiguous, just repetitive.
    Two DIFFERENT tokens is a real conflict and is rejected outright rather than guessed at
    (e.g. by taking "the last one"): silently picking a winner would hide genuine inconsistency
    in the model's output behind a clean-looking result.
    """
    if text is None or not text.strip():
        return Extraction("empty", None, "empty_response")

    table = normalized_options(options)
    found = []
    for line in text.splitlines():
        m = _DECISION_LINE.match(normalize(line))
        if m:
            found.append(m.group(1))

    if not found:
        return Extraction("unparseable", None, "no_decision_line")
    if any(t not in table for t in found):
        return Extraction("unparseable", None, "token_not_in_options")
    if len(set(found)) > 1:
        return Extraction("unparseable", None, "conflicting_decisions")
    return Extraction("valid", table[found[0]], None)
