# Glossary

Plain-language definitions of every term and code used in LLM-Archive's data and site. Frozen
technical definitions are in [`SPEC.md`](https://github.com/LLM-Archive/llm-archive.github.io/blob/master/SPEC.md); this file exists to explain
them to a reader seeing them for the first time. Terms are grouped by topic, not alphabetically,
because most of them only make sense next to the ones around them.

## The core idea

**Decision** — the one categorical answer a model gives to a protocol's question, extracted from a
required `DECISION: <token>` line at the end of its response. There is no partial credit and no
free-text scoring in v0 — a response either contains exactly one valid decision line, or it
doesn't count (see `unparseable`, below).

**Version (A / A′ / B / C)** — the four differently-worded copies of the same question that make up
one measurement:
- **A** — the base wording.
- **A′** ("A prime") — A with only whitespace and punctuation changed. Nothing that could
  plausibly matter to the decision is different. This is the *null-change control* — the honest
  zero the real comparison is measured against.
- **B** — the *equivalent rewording* being tested (different wording, same facts, same normatively
  correct answer as A).
- **C** — A with one number changed enough that the normatively correct answer flips. This is the
  *positive control* — proof the model is actually reading the question.

**Equivalent rewording** — a B version that this project *declares* equivalent to A, and the
tests that declaration has to pass. There is no objective, complete definition of two sentences
"meaning the same thing": wording always carries some tone, emphasis and implication beyond the
facts. So this project does not claim one. It uses an **operational definition**: B is an
equivalent rewording of A only if it passes **every** one of the gates below. Each gate closes a
different way a B could fail to be equivalent, and they are ordered from the most mechanical to the
most human, so that the human step is left with as little as possible to decide.

1. **Same decision problem, checked by code (gate 1).** Every option has identical odds and
   payoffs in A and B, the same number of options, the same numeric relationships, and the same
   normatively correct answer. This catches a B that secretly changed the numbers — in that case a
   shifted answer would just be a correct response to a different question.
2. **Only the allowed change, checked by code (gate 1b).** For every one of the 15 scenarios, B must
   be A plus *exactly one* transformation from a short, closed, public list, and nothing else:
   - **`order`** — the option lines are reordered; every other line is identical.
   - **`default`** — one line differs (the default sentence), and only by which option it names.
   - **`anchoring`** — one token differs: a number that appears nowhere else in the scenario, so it
     cannot be an informative number.
   - **`wording`** — for `risky_choice_framing`, only the option lines change (gain ↔ loss); for
     `sunk_cost_fallacy`, B is A plus one fixed phrase naming the money already spent; for
     `base_rate_neglect`, only the paragraph with the rates changes, and the natural frequencies
     equal the percentages exactly.

   A stray word, an extra sentence or a changed context line is rejected. The check reads no
   English and calls no model, and it has its own self-test showing it rejects a broken B.
3. **No leaked cue, judged by a person against written criteria (gate 2).** A blind reviewer, reading
   the wording alone, must not be able to infer the base rate, the designer's preferred answer, or an
   outcome consequence that A does not state. Where the wording does leak, that item is published as
   "cue sensitivity" rather than hidden.
4. **The answer format is the same (gate 3).** Both versions use the same required `DECISION:` line.
5. **A person signs off against a fixed checklist (gate 4).** Not open judgment: gates 1 and 1b pass;
   every phrase B adds or changes is one the list above names; the blind reader's verdict is
   recorded. Each admission is recorded (the explicit sign-off and the commit that makes it: who, when),
   and a protocol whose panel is byte-identical to an already-reviewed one inherits that review — the
   `v1` protocols inherit their `v0` counterparts'.

**Then, on every measurement, two more checks keep running.** The null-change control (A′) shows
what a purely cosmetic change does by itself, so a B that does no better than it is flagged
`below_surface_noise`. The positive control (C) shows the model is reading the question at all
(`not_reading`). Three scenario families per type mean that if one panel's "equivalence" is
contested, the disagreement between panels is visible. And because every protocol's fingerprint is
timestamped before it is run, none of this can be adjusted after a result is seen (see
[`timestamps.md`](timestamps.md)).

**What is still a judgment, and where it is made.** That each transformation in the list is
*equivalent* is declared once, in the specification, in public — it is not decided per protocol or
per scenario, and it can be cited and disputed. It is not proven: whether such a change *should* be
treated as irrelevant is exactly what is being measured. A model whose answer moves under a change
the list allows has shown sensitivity to something the list treats as irrelevant; a reader who
draws the line elsewhere is looking at a different protocol, not at a flaw in this one.

**Panel** — the fixed set of 15 scenarios that make up one protocol. It is not a random sample of a
larger population of possible scenarios; it is the instrument itself, frozen the same way a
physical measuring device is. It is never resampled, and results never generalize beyond it — see
`gap_pct` below for what "on this panel" means in practice.

**Protocol** — one family of 15 scenarios paired with one rewording type (`wording`, `anchoring`,
`order`, or `default`). A protocol is a fixed *question*, never a result — the same protocol is run
again every month, against whatever model is current, and produces a new measurement each time.

**Rewording type** — which of the four kinds of change the protocol's B version makes:
- **`wording`** — the same outcome described as a gain or a loss (or, for `base_rate_neglect`, as
  a percentage or a natural frequency). The only type with no caveats attached.
- **`anchoring`** — B mentions an extra, explicitly irrelevant, non-diagnostic reference number
  (e.g., a low vs. high registry ID) before the question. An *informative* number (like a real
  market size) would not qualify — it would leak information, not just anchor attention.
- **`order`** — the same two options, printed in reversed order. Labeled **mechanical**, not
  cognitive, because the classic explanation for order effects is a preference for the letter "A"
  itself — a labeling artifact, not a judgment bias — so this line is excluded from any index
  described as measuring reasoning.
- **`default`** — which option is stated to apply automatically if no decision is recorded.
  Labeled **no human comparison**, because the human "default effect" literature does not survive
  correction for publication bias in multi-lab replication. This is measured as a consistency
  check with no normative claim attached.

## The published numbers

**`gap_pct`** — how far apart two versions' answer distributions are, from 0 (identical) to 100
(no overlap at all). Computed as total variation distance × 100. For a yes/no decision, this is
exactly the classic framing-effect size used in published human research — chosen on purpose so
the number is directly comparable to that literature.

**`stability_pct`** — `100 − gap_pct`. The headline number. High = the model didn't change its
answer when the wording changed. This is a simple, reversible relabeling of `gap_pct` chosen so
that a bigger published number always means better news — `gap_pct` itself reads backwards for a
reader just scanning a chart.

**`gap_null_pct`** — the gap between A and A′ (the null-change control). This is the honest zero: a
perfectly consistent model still won't score exactly 0 here, purely from sampling noise. See
`null_floor_pct`.

**`gap_positive_pct`** — the gap between A and C (the positive control). A model that's actually
reading the question should show a *large* gap here, since C is built so the correct answer
flips.

**`null_floor_pct`** — how much gap two samples from the *identical* distribution would show, on
average, purely from having a finite number of responses (computed by permutation, specific to
the panel's `n` and number of options). A model scoring at or above `100 − null_floor_pct` is
`at_noise_floor`: indistinguishable from perfect invariance, not provably "more stable than" a
model that scored slightly lower.

**`entropy_a` / `entropy_b`** — how spread out a version's answers were across the available
options, from 0 (always the same answer) to 1 (fully spread out). Published because
`stability_pct` alone can't tell "the model got more consistent" apart from "the model collapsed
onto giving the same answer regardless of the question" — a model that always answers "A" scores
100% stability and 0 entropy. **Stability is never shown without entropy next to it.**

**`u_a`, `u_a_prime`, `u_b`, `u_c`** — the fraction of responses in each version that didn't count
as a valid decision (refused, unparseable, truncated, etc.), from 0 to 1.

**`drop_bound_pct`** (per pair: `_ab`, `_aa`, `_ac`) — the worst-case amount the published gap
could be wrong by, purely because of the lost responses counted in `u`. This is not a
confidence interval and not a statistical estimate — it's a hard mathematical bound (total
variation distance can move by at most `u_X + u_Y` when up to that fraction of responses on each
side are unknown), computed with **no assumption at all** about what those lost responses would
have said.

**`drop_asymmetry_pct`** (per pair) — how differently the two versions being compared lost
responses. A wording change that makes one version's answers much harder to parse than the
other's is a warning sign in its own right, independent of the overall drop bound.

**`ci_low_pct` / `ci_high_pct`** — a 95% bootstrap confidence interval on `stability_pct`,
computed by resampling individual responses 10,000 times. It is explicitly **conditional on this
panel** — it describes uncertainty in "what would this same 15-scenario panel show on a slightly
different sample of responses," not uncertainty about the broader category of "wording effects" in
general. No such broader interval is published in v0; see `scenario_gaps_pct` below for what
replaces it.

**`scenario_gaps_pct`** — the individual gap value for each of the panel's 15 scenarios,
published separately rather than only as an average. **`scenario_spread_pct`** summarizes their
range (min/max/median/IQR). **`scenario_concentration_pct`** is the share of the total gap
produced by just the two worst-behaved scenarios — if this is high, a reader can see directly that
the finding rides on a couple of items, without having to trust a single summary number.

## Statuses, flags, and what comes off the curve

**`on_curve`** — whether a measurement is trustworthy enough to appear in the main published time
series. A measurement is **never deleted** for failing a check — it's still published, in full,
just excluded from the headline chart and visibly marked with the reason.

**Flags** (a measurement can carry more than one):

| Flag | Plain meaning |
|---|---|
| `below_surface_noise` | The real wording change didn't move the answer more than a meaningless punctuation-only change did — there's no signal to report. |
| `not_reading` | The positive control (which should always move the answer) didn't. The model doesn't appear to be reading the question. |
| `degenerate_candidate` | Near-zero entropy with high stability — it always gives the same answer, which isn't the same thing as being stable. |
| `drop_confounded` | Too many responses were lost in at least one of the three comparisons to trust the result. |
| `asymmetric_missingness` | One version lost noticeably more responses than the other it's being compared against. |
| `below_drop_bound` | The measured gap is smaller than the worst-case error from lost responses — it could be entirely an artifact of lost responses. |
| `scenario_dominated` | Two of the panel's 15 scenarios account for an unusually large share of the total gap. |
| `instrument_suspect` | The daily reference-model self-check moved that day — the whole pipeline is suspect, not just this measurement. |
| `exploratory` | Outside the one pre-declared comparison for this protocol. Informative, but never charted. |

**`candidate` → `admitted`** — a protocol's lifecycle. Every protocol is born `candidate` (passed
the automatic structural checks, but no human has read it yet) and cannot produce a measurement
that counts until a human explicitly promotes it to `admitted` with a lane assigned. Neither status
nor lane changes the protocol's own content or its hash (`protocol_sha256`) — promotion changes
whether it's *allowed to count*, not what it *asks*.

## Trust and provenance

**`lane`** — how open a protocol's content is:

| Lane | What's published | When |
|---|---|---|
| `open` | Every raw response, per trial (`open-lane/<year>.jsonl`), plus the four-version text of the panel's **first scenario** on the results page. The other 14 scenarios' wording is not yet published — see the note below | Responses immediately; the rest of the panel's wording, not yet |
| `guard` | Only outcome categories (valid/refused/etc.) — never the wording itself | Full text only after 4 model generations |
| `sealed` | That the protocol exists and how many trials it has — nothing else | Never |

**A gap disclosed rather than glossed (2026-09-23):** "`open` = the wording is public" is the
design, and it is not yet fully true in the data. The per-trial file carries each response with its
`scenario_id`, `version` and `prompt_sha256`, but **not the prompt text**, and the site publishes
only the first scenario's four versions. So an `open` protocol's numbers can be recomputed from
scratch by anyone, while the panel itself cannot yet be reconstructed or re-run from outside —
which is also why no third party can currently produce a `comparison_point` matching
`panel_sha256`.

**`twin_id`** — links an `open` protocol to a `guard` protocol measuring the same underlying
phenomenon (same scenario family, different rewording type) whose text stays private. The reason:
an `open` protocol's public text will eventually be read by a model during training. If only the
`open` number moves later, that's contamination. If **both** the `open` number and its hidden
`guard` twin move together, the model actually changed. Without the twin, the two explanations are
indistinguishable from the outside.

**`panel_sha256` / `protocol_sha256`** — cryptographic fingerprints of a protocol's scenario content
and its full definition, proving a protocol wasn't quietly edited after it started being measured.

**`comparison_point`** — a measurement of the same public panel run by someone other than this
project, against a model of their own choosing. Accepted only with an exactly matching panel,
grammar version, `n`, and full outcome data. Shown in its own table, on its own day, never merged
into the main chart or treated as a ranking — see [`reproducing.md`](reproducing.md).

**`series` (`commercial` / `open_weights`)** — two entirely separate tracks that are never drawn on
the same chart. `commercial` is the actual archive — the hosted product being studied, which can
disappear or change silently at any time. `open_weights` is the control experiment — a downloadable
model this project runs itself, used only to prove the method reproduces, never treated as "the
archive."

**Instrument** — the daily self-check that catches the project's own pipeline breaking, using a
fixed local open-weights reference model that is never studied for its own sake and never changes.
Two parts: `runtime_fingerprint` (12 fixed prompts, expected to reproduce byte-for-byte every day)
and a rotating daily replay of one of the 10 `guard` protocols against that same frozen model
(expected to stay flat within normal statistical noise). If either moves, publication for that day
pauses — the measurement is still recorded, flagged `instrument_suspect`, off every curve.

**`generation_bridge`** — a special, immediate measurement run comparing an outgoing model version
against its explicitly declared successor, in the same week, on all 4 protocol types (one
protocol per type, at each protocol's own frozen settings), funded from its own dedicated reserve
rather than the regular monthly sweep budget. Exists
because a retired commercial model can never be measured again at any price — the handoff between
two generations is the one moment that can't wait for the normal monthly schedule.

**Gap causes** — the closed list of reasons a scheduled measurement didn't happen, always disclosed
rather than left as a silent hole in the record: `before_archive_start`, `budget_halted`,
`dormant`, `provider_outage`, `protocol_confounded`, `human_absent`,
`generation_retired_early`, `generation_bridge`.
