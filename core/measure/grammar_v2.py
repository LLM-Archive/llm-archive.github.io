"""Deterministic decision extraction (spec.md §5). grammar_version 2.

Additive sibling of grammar.py (grammar_version 1), which stays byte-for-byte frozen — see its
own docstring and spec.md §13.2. grammar.py's own real-data bug (decisions.md §42, §45, §46, §48:
4/960 real trials, 100% of the time fatal) is that `extract()` treats ANY line starting with the
word "Decision" as a candidate, including a line of ordinary prose the model wrote on its way to
the real answer (e.g. "**Decision:** Given that Plan B guarantees a strictly better outcome...").
When that prose line's tail isn't a real option, the whole response is rejected even though the
real, correctly-formatted `DECISION: <token>` line is present right below it.

Fixed here the smallest way that still matches this file's own no-model-in-the-loop discipline
(module docstring of grammar.py): a line only counts as a decision line at all if what follows
the colon is already one of the protocol's options — a bare, mechanical membership check, not an
attempt to read or judge the prose. Everything else (scanning every line independently, treating
a repeated identical token as valid, rejecting two different tokens as a real conflict) is
unchanged from v1.

One small, intentional side effect, covered by this file's own golden vectors
(`core/testdata/vectors/grammar_v2_vectors.json`): a response whose ONLY decision-line-shaped
line names something that isn't an option (e.g. "DECISION: C" when options are ["A", "B"]) now
reads as `no_decision_line` rather than v1's `token_not_in_options`. The `outcome` is unparseable
either way — only the diagnostic `reason` differs — because the same membership check that lets
v2 see past a prose false positive cannot also tell that prose apart from a genuinely malformed
single decision line without reading meaning into the text, which this file does not do.

Changing the decision grammar mints a new protocol series (spec.md §2) — this file is never used
to reinterpret a `grammar_version: 1` protocol's already-published data, only new protocols that
declare `grammar_version: 2` for themselves.
"""

from __future__ import annotations

import re

from .grammar import Extraction, normalize, normalized_options

GRAMMAR_VERSION = 2

_DECISION_LINE = re.compile(r"^decision ?[:：] ?(.*)$")


def extract(text: str | None, options: list[str]) -> Extraction:
    if text is None or not text.strip():
        return Extraction("empty", None, "empty_response")

    table = normalized_options(options)
    found = []
    for line in text.splitlines():
        m = _DECISION_LINE.match(normalize(line))
        if m and m.group(1) in table:
            found.append(m.group(1))

    if not found:
        return Extraction("unparseable", None, "no_decision_line")
    if len(set(found)) > 1:
        return Extraction("unparseable", None, "conflicting_decisions")
    return Extraction("valid", table[found[0]], None)
