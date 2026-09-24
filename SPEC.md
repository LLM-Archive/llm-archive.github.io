# LLM-Archive — IMPLEMENTATION SPECIFICATION v0

*September 14, 2026 · freezes at commit #1 · last substantively updated September 20, 2026*

This is the **single, self-contained specification** of the measurement system. Every value,
threshold, formula, and rule the pipeline runs on is stated here in full — nothing is deferred to
another file, and no other file needs to be read to build or check any part of the system.

This document **is not append-only**. It is corrected in place, so what's written here is always
the current, governing version.

---

Kalognomos Michalis
mkalognomos@gmail.com

---

> **Editorial notes, kept for history.**
>
> *(The reserved decision-line keyword, originally `ΑΠΟΦΑΣΗ:`, was changed to `DECISION:` by an
> explicit decision on September 17, 2026 — before commit #1, so no `grammar_version` bump was
> needed.)*
>
> *(The grammar's homoglyph table (`Α/A, Β/B, Ε/E, Ο/O, Ρ/P, Χ/X, Υ/Y`) was removed on
> September 19, 2026 — the project addresses a global audience, and option tokens are Latin-only,
> so treating Greek look-alikes as equal was scope the reader never needed. Same as above: before
> commit #1, so no `grammar_version` bump was needed.)*
>
> This document uses the Latin **A / A′ / B / C** labeling for the four measurement versions (base,
> null-change, equivalent-rewording, substantive-change) throughout, matching the project's own
> code (`versions: {A, A_prime, B, C}`) — a labeling convention for the four conditions, not a
> literal string the model has to produce.

---

## 1. What we measure

Does a model's **decision distribution** change when a question says the same thing in different
words?

The metric is the **model's stability with itself**. It requires no human, no psychological
literature, and no construct-validity claim.

### 1.1 The yardstick is the PANEL, not the model

**This defines what the project is, which is why it comes first.**

The subject model (`claude-sonnet-*`) is **the thing being studied**, not the yardstick. No one is
going to "compare against the 2027 Sonnet" — and there would be no reason to.

The **yardstick** is the frozen panel: 15 scenarios, `panel_sha256`, together with the decision
grammar and the extraction rule. The grammar, the extraction rule and every panel's `panel_sha256`
are public; the scenario *wording* is published per lane (§10), which for the two `open` protocols
is what makes an outside run on the same panel possible at all — so that, on that same panel, there
is a **multi-year trail from a real commercial product** to compare against (`comparison_point`,
§4.2).

> **Disclosed gap, as of 2026-09-23 — the wording needed for an outside run is not yet fully
> published.** For an `open` protocol, what reaches the public today is: every per-trial response
> (`open-lane/<year>.jsonl`), each carrying its `scenario_id`, `version`, extracted `token` and
> `prompt_sha256` — plus the full four-version text of **the panel's first scenario only**, embedded
> in the published results page. The remaining 14 scenarios' wording is in no published artifact.
> So an outside party can today **recompute** every published statistic of an `open` run from the
> responses, and can **verify** a prompt it already holds against `prompt_sha256` — but cannot
> reconstruct the panel, and therefore cannot produce a run matching `panel_sha256` (§4.2's first
> acceptance condition). Closing this means publishing the `open` protocols' full panel text; until
> that happens, this section's claim is scoped to what the list above actually covers.

> The value is **the historical context, not the opponent.**

**Two consequences that constrain the design:**

| | |
|---|---|
| **The choice of model is reversible** | if the family ends, the series closes with `series_closed` and a new one starts. Continuity is lost, not validity |
| **The panel is the one frozen choice** | it is **the definition of the measurement**, not a parameter of it. Changing it produces a new ID and a new series |

**This is why the panel has four admission gates (§3) and three detectors running live:**

1. **Three panels per type.** If one behaves differently, **it does not move together with the
   other two** — the disagreement is itself the signal. This is the main reason there are three,
   not one.
2. **The 15 scenarios are published separately.** A panel being driven by two scenarios shows up
   immediately, flagged `scenario_dominated`.
3. **The pilot run** measures `u` per panel **before** commit #1; a panel that doesn't pass the
   thresholds does not enter the main curve.

**And this is also why better future models increase the value rather than decrease it:** every
new model on the same panel adds a point to a history that starts in 2026. Ten points with no
history do not replace one point **with** history.

> **The choice of subject is partly arbitrary — and that is acceptable, on one condition: it is
> disclosed, and it does not change.** The Mauna Loa station has measured CO₂ since 1958; its
> value is not that it's the right spot on the planet, but that **the series never broke**. Any
> station that starts today has today as its starting point.

---

> **The exact scope of the claim, and it is never widened anywhere:**
> *"on **this frozen panel** of 15 scenarios, on this model, on that day."*
>
> **Never** "the model is X% stable under wording" — always naming the exact panel it was measured
> on, by `protocol_id` and `panel_sha256` (there is no separate short panel-id scheme; those two
> fields *are* the panel's identity). The panel is an **instrument, not a sample**; we do not
> generalize to the category — see §6 below for why no generalization interval is published in v0.

`human_model_gap` is **secondary**, reported per protocol, only where a comparable study exists
(`multi_lab` or `meta_analysis`), always with the collection year. **Never aggregated, never a
headline.**

---

## 2. The unit of measurement

| | |
|---|---|
| Versions per measurement | **4** — A baseline · A′ null change · B equivalent rewording · C substantive change |
| `n` per version | **30** in the `v0` series · **120** in the `v1` series — frozen per protocol, never per run |
| Scenarios per protocol | **15, a frozen panel** — the `n` responses are distributed across them, 2 per scenario in `v0` (was 10; dropped once real calls against the real API showed repeated identical prompts come back deterministic with no client-controllable sampling parameter — see §5), 8 per scenario in `v1`. **It is an instrument, not a sample**: it is not resampled and does not generalize |
| Calls per measurement | **120** in `v0` · **480** in `v1` |
| Session | **fresh per call**. No response ever sees a previous one |

`item_id` and `n_items` are stamped on every response. Without them, "the model is unstable"
cannot be told apart from "this particular scenario is unstable."

**Changing `n`, `effort`, the decision grammar, or the scenario panel ⇒ a new protocol version,
with a new ID and a new series.** Never a continuation of the old one.

**This rule has been exercised once, and that is what the `v1` series is.** On 2026-09-22 a second
series of 12 protocols was built (`<family>__<type>__v1`), changing exactly two things against
`v0`: `n` 30 → 120 (`n_per_scenario` 2 → 8) and `grammar_version` 1 → 2 (§5). Same panels — every
`v1` file's `panel_sha256` is byte-for-byte identical to its `v0` counterpart, confirmed — so only
`protocol_sha256` differs, which is precisely what this rule requires: **new IDs, a new series, no
continuation of the `v0` line.** As of 2026-09-23 all 12 `v1` protocols are `candidate`: none has
been through gate 4, none has a lane, none has been measured. The `v0` series carries every real
measurement published so far.

**The confidence interval is conditional on the panel** — a bootstrap at the response level.
**No generalization interval is published in v0**: a cluster bootstrap over 15 groups comes out
anti-conservative, and we know it. In its place, **the raw heterogeneity** is published — the 15
per-scenario `gap` values, their spread, and how much of the total gap the two worst scenarios
account for.

*The indication of generalizability does not come from statistics: it comes from **three
independent panels per change type** (§3) moving together.*

---

## 3. The v0 protocols

**Protocol = one family of 15 scenarios × one rewording type.**

| | |
|---|---|
| In rotation | **12** — 3 scenario families × 4 types |
| Lanes | **2 `open`** (fully public) · **10 `guard`** · of which 2 are the twins of the `open` ones, sharing a `twin_id`. Which ones, and by what rule: "Lane assignment and twin pairing" below |
| Sealed | **2 `sealed`** — off rotation, run **only** at generation boundaries. Existence and count are disclosed; content and results, never |
| Full sweep | **monthly** — 1,440 calls, at the measured per-call rate (§9). The real 12-protocol sweep completed 2026-09-22, fully billed |
| Points per protocol | **12 per year** |

*(A `v1` sweep would be 12 × 480 = 5,760 calls — several times the v0 sweep's cost, which is why
the `v1` series cannot run at this cadence inside the same ceiling; see §9 and §2's note on the
series.)*

### The three scenario families

Chosen to be three **different kinds of judgment**, so that the three panels per type are
independent: if one moves and the other two don't, the disagreement is itself the signal (§1).

| Family | Classic design | The decision | What the `wording` type's B changes |
|---|---|---|---|
| `risky_choice_framing` | Tversky & Kahneman, the "disease problem" | a sure outcome vs. an equal-EV gamble | the same outcomes described as gains (A) or as losses (B) |
| `sunk_cost_fallacy` | Arkes & Blumer | stop a project (sure value) vs. finish it (equal-EV gamble); the money already spent is stated in every version | the "stop" plan is reworded to name the money already spent |
| `base_rate_neglect` | Gigerenzer & Hoffrage | after a screening test flags an item, bet that it is or isn't a true case; payouts make the two bets equal in EV | the same numbers given as percentages (A) or as natural frequencies, "10 out of 1,000" (B) |

In every family, C changes the numbers so that one option **strictly dominates** (§3, gate 1).

### The four types and their status

| Type | Status | Constraint |
|---|---|---|
| **Wording** | full | — |
| **Anchoring** | full | **only non-diagnostic numbers** — random, from an unrelated domain. An informative number ("the market is worth $800bn") is mechanically rejected |
| **Order** | **mechanical** | excluded from any aggregate index described as cognitive |
| **Default** | **no human comparison** | a consistency measure, with no normative claim. Same exclusion |

**Default × the A↔C gate — a known reading of `not_reading`.** In a `default` protocol, C keeps
A's default option while making the OTHER option strictly dominant (the same construction as
every other type's C, per §3's dominance rule above). A model that follows the default
irrespective of the payoff therefore answers alike in A and C: `gap_pct(A,C)` stays at zero, the
A↔C gate fails, and the measurement is flagged `not_reading` and comes off the curve — for the
one model that would have shown the **strongest possible** default effect. This is accepted, not
a defect to fix: the alternative constructions are worse — pointing C's default at the dominant
option would make the gate pass for readers and blind default-followers alike, and dropping C's
default entirely would change two things about C at once. So a `default` protocol simply cannot
measure a default effect on a model whose default-following is total; `not_reading` is the
correct, if misleadingly named, outcome for that case, not a bug to fix by loosening the gate.

### Admission gates, mechanically checked

1. **Structural invariants:** equal expected value, identity of the normatively correct answer,
   same number of options, preserved numeric relationships.
2. **Informational equivalence:** a blind reviewer *(one-off, at design time — not part of the
   cycle)* must not be able to infer the base rate or the designer's preference **from the wording
   alone**. Where it leaks, the item is published as **"cue sensitivity."**
3. **Decision grammar** (§5) — mandatory.
4. **Human ✅** before a `candidate` becomes a `guard`.

A protocol is born `candidate`, with no lane and no twin. Passing gate 4 is what sets
`status: admitted` plus a lane, and it is a **human** action, performed by `promote.py`, which
refuses to run without an explicit lane and an explicit acknowledgement of this gate. Neither
`status` nor `lane` nor `twin_id` is part of `protocol_sha256` (§13): admission changes whether a
protocol may count, never what it asks, so the hash a measurement cites is unaffected.

### Lane assignment and twin pairing

**The rule, applied to all 12 at once on 2026-09-20, before any protocol had ever been run
against a real model:**

1. The two `open` protocols come from **two different scenario families** — the publicly
   reproducible sample should span two different *kinds* of judgment, not two views of one.
2. Both are the **`wording`** type — the only type with **full** status and no constraint
   (§3's type table), so what a stranger can reproduce in full is the type that carries no
   caveats.
3. Each `open` protocol's twin is the **`anchoring`** protocol of the **same family** — the other
   **full**-status type. A twin of the `order` or `default` type would pair a headline number with
   one that is excluded from any cognitive aggregate, which is not a comparison.
4. Of the three families, the two chosen are `risky_choice_framing` and `base_rate_neglect`.
   `sunk_cost_fallacy` is the closer conceptual neighbour of `risky_choice_framing` (both turn on
   gain/loss framing of a monetary decision), so excluding it maximises the distance between the
   two public panels; `base_rate_neglect` is the one family testing statistical rather than
   framing judgment.

| `protocol_id` | Lane | `twin_id` |
|---|---|---|
| `risky_choice_framing__wording__v0` | `open` | `risky_choice_framing__anchoring__v0` |
| `risky_choice_framing__anchoring__v0` | `guard` | `risky_choice_framing__wording__v0` |
| `base_rate_neglect__wording__v0` | `open` | `base_rate_neglect__anchoring__v0` |
| `base_rate_neglect__anchoring__v0` | `guard` | `base_rate_neglect__wording__v0` |
| the remaining 8 | `guard` | — |

**Why the twin exists.** An `open` protocol's full text is public, so it **will** end up in a
training corpus — that is designed, not a failure. But then a later shift in its number has two
possible causes that look identical: the model changed, or the model has now read the panel. The
`guard` twin is the discriminator: same phenomenon, same declared lotteries, not public. Only the
*difference* between the two separates contamination from a real change.

**Why "same family, different type" is what makes them twins.** Within one family, all four types
share one panel and one `structure` block — the same 15 decision problems, the same odds and
payoffs, the same normatively correct answer (`mutable/panels.py`). Only the rewording differs.
So a same-family pair is, precisely, *the same phenomenon in a different embodiment*. These
properties are **checked by code, not asserted**: `verify.py`'s `twin pairing` check fails the
build if a pairing is one-sided, crosses families, repeats a rewording type, or is not exactly one
`open` and one `guard`.

**Why this is stated here rather than decided later.** Which protocols become public is a
bias-relevant choice: publishing a self-selected subset after seeing results is how a flattering
sample gets built. The rule above is mechanical, was fixed before the first paid run existed, and
picks the two public panels by *design distance*, not by any property of any measurement — there
were no measurements to consult.

**`twin_id` is also carried into every `measurement` record** (§10), not just the protocol file,
the same way `lane` already is: the point of the pairing is comparing two *time series* of
measurements against each other, and a measurement is meant to be interpretable on its own, from
the published record, without a reader having to separately fetch and parse a protocol file to
learn what it's paired against. Like `status` and `lane`, `twin_id` is not part of `protocol_sha256`
— it can change (an `open` protocol has none until admitted) without the protocol's own content
changing. Adding this field is why `SCHEMA_VERSION` moved from 1 to 2, done now, before the first
real run, because a schema change afterward would be a break in the citation chain rather than a
free edit.

---

## 4. The two series

| Series | What it is | Rate | Lanes |
|---|---|---|---|
| `commercial` | **the archive** — product history that would otherwise be lost | monthly sweep | open · guard · sealed |
| `open_weights` | **the control experiment** — proves the method reproduces | **once per generation** of the commercial model | **all `open`** |

### 4.1 One curve = one product family, sequentially

**v0 tracks ONE commercial series: the Sonnet series,** because it fits the budget.

```
Claude Sonnet series — one protocol's panel (protocol_id + panel_sha256)
  2026   claude-sonnet-5      89%
  2027   claude-sonnet-5-5    92%
  2028   claude-sonnet-6      88%   ← bridged with the previous one, same week

  By type (2028):  Wording 93% · Anchoring 86% · Order 91% · Default 89%
```

**Opus does NOT go on the same line as Sonnet.** Different product, different alignment
decisions. If it is ever added, it gets **its own series, on its own axis**: Sonnet series ·
Opus series · GPT series — **never merged**.

**The frozen code refuses to produce a curve** that crosses `model_family`, or that mixes
`series`. This is not a presentation rule — it is a check inside the generator.

*Why:* a line joining three products shows a "trend" that is an **artifact of our own selection**.
The same mistake as measuring sea level at three points on the planet and connecting them.

### 4.2 Third-party comparison points

The grammar and the extraction rule are public, and the `open` lane exists so that **the panel can
be run against someone else's model and checked against this project's own trail**.

**`comparison_point`** — accepted only with: the same `panel_sha256` · the same `grammar_version`
· the same `n` · **all four versions** · the full outcome distribution per version · the exact
`model_id`.

**Not yet reachable from outside, and disclosed as such (2026-09-23):** the first of those
conditions requires the panel's wording, and only one of each `open` protocol's 15 scenarios is
currently published (§1.1). Until the full `open` panels are published, no third party can
construct a submission that satisfies `panel_sha256`, so this route is specified and built but has
no way to be used from outside. It is listed here as a designed capability with an open
prerequisite — not as something already available.

**Appears in a separate context table, with the measurement date — never on the same line, never
in the main chart, never in an aggregate.**

> **What NO ONE is allowed to conclude:** *"my model beat every AI."* A `comparison_point` is
> **one model, on one frozen 15-scenario panel, on one day.** It is not a ranking and it does not
> generalize. This is written next to the table, not in a footnote.

It is a **read-only view**: it cannot trigger a measurement, it does not change the rotation.

### 4.3 Switching the active model — three cases, no guessing

**Two decisions, kept separate, because they carry a different cost of failure:**

| Decision | Character | Rule |
|---|---|---|
| **Run the bridge now** | **irreversible if missed** — the old model is retired and can never be re-measured, at any cost | **automatic, immediate, no human** |
| **Switch the active model** | interpretive — *"is this the successor?"* | **only with an explicit provider declaration** |

> The expensive-if-missed decision fires immediately; the interpretive one waits for proof. If it
> turns out it wasn't the successor, the bridge data **isn't lost** — it becomes a
> `comparison_point`.

| What happened | What the system does |
|---|---|
| **Explicitly declared successor** within the same family | bridge **and** automatic active-model switch. Records: `generation_bridge` + `subject_model` |
| **New model ID with a similar name**, no declaration | **bridge: yes** *(the window never reopens)* · **active-model switch: no** → **`needs_review`**, one click in the monthly batch |
| **The old model disappeared with no successor** | **no guessing.** `series_closed`, a gap with cause `generation_retired_early`, and a **new series from zero** if and when it is decided |

**"Explicitly declared," operationally** — one of exactly two things, nothing else: **(a)** a
machine-readable successor field on the models/deprecation endpoint; **(b)** a dated archived
snapshot of the provider's deprecation page that names the successor.

**A naming pattern is NOT a declaration of succession.** The old rule `family: claude-*-5*` with
`requires_human: false` **is revoked as an authorization to switch** — a fuzzy naming rule is not
allowed to rewrite which series we are measuring. It survives **only** as a bridge trigger.

**Bridge:** 4 protocols — **one per type** — at **each protocol's own frozen `n_per_scenario=2`**,
four versions, **old and new**, same week. `2 × 4 × 120 = 960 calls` (measured rate, §9). *(This
section originally sized the bridge at `n_per_scenario=1`, 480 calls, on the argument that a bridge
only needs to catch a gross discontinuity. Corrected 2026-09-24: protocols are frozen at
`n_per_scenario=2` (§13) and `core/measure/` cannot run a protocol at a different `n` without
becoming a different protocol, so the bridge runs them as they are. The comment on
`generation_bridge_protocols` in `cadence.yaml`, which is frozen, still says n=1 / 480 calls; the
budget and this section are what apply.)* Funded from `bridge_reserve_eur` in `budget.json`
(private — §10 — sized with a small margin over this), never from the general monthly pool — see
`core/budget/budget.py`'s `_general_ceiling`.

**Funding: the bridge preempts that month's sweep** — the sweep is skipped with cause
`generation_bridge` in the coverage table. **A disclosed gap, not a hidden one.**

**If it doesn't complete** because the old model was retired in the meantime: `bridge_incomplete`
and a **permanent discontinuity**, disclosed on the curve.

`open_weights` has no sealed lane: frozen weights **cannot be contaminated**. It runs on CPU
inside Actions' free minutes — **free, no rented accelerator**. One model, archived with a sha256
in the chain.

---

## 5. The subject and the decision grammar

| | |
|---|---|
| Tier | **Sonnet-class** — Opus-class yields ~3 measurements/month, not sustainable |
| `temperature` | **not client-controllable** — removed from the provider's Messages API (2026); the model's own default sampling is used, sent as no explicit parameter at all |
| Thinking / `effort` | **disabled**, frozen in the pre-registration. A 38× cost factor. Must be sent explicitly (`thinking: {"type": "disabled"}`) — the API's own default is adaptive (automatic) thinking, not off |
| System instruction | **none** |
| Retrieval | **none** |
| Every other parameter | sent **explicitly**, stored in `explicitly_set[]` |

Disclosed in the coverage table: **we are measuring one frozen configuration, not "the model"** —
proof-of-concept filtering, and nothing more.

### The grammar — one, for all of v0

```
The response ends with one line, in exactly this format:

    DECISION: <token>            where <token> ∈ options[]
```

**Extraction is entirely deterministic. In v0 there is NO judge.**

Normalization (frozen code, with its own golden vectors): NFC · casefold · markdown-emphasis
stripping · whitespace collapsing · trailing-punctuation stripping. No script/look-alike folding:
a Greek letter is a different token from its Latin look-alike, not an equivalent spelling of it.

| What's found | Outcome |
|---|---|
| **Exactly one** decision line with `token ∈ options[]` | `valid` |
| Multiple decision lines with **the same** token | `valid` — repetition, not ambiguity |
| Multiple lines with **different** tokens · no line at all · `token ∉ options[]` | **`unparseable`** |

**Nothing outside a decision line is ever read.** So *"Option A is tempting, but in the end I go
with B"* → `unparseable`, and mentions of the options inside the reasoning are never touched.

**v0 does not accept** protocols scored from free text with no closed set. They run
`exploratory`, off the curve.

Every `experiment_run` stamps `grammar_version` and `extractor_sha`. **`judge_sha` is removed.**

---

## 6. Statistics — the estimator, the checks, and the loss bound

Two of a protocol's four versions are compared directly, pairwise. Every response is scored into
exactly one of `k` closed categories (§5's decision grammar) — **there are no paired
observations**: the two versions of a question are never seen by the same call, so no statement of
the form "in 9 out of 10 matched pairs it gave the same answer" is computable from this design, and
none is ever published. What's measured is **distance between two independent distributions**.

The deterministic extraction rule (§5) doesn't make the risk of unreadable responses disappear —
**it turns it from estimated into measured**, via the loss bound below. If one version produces
more unreadable responses than the other, the two distributions are being compared over different
populations.

### The estimator: total variation distance

```
gap = ½ · Σᵢ |p̂_Xi − p̂_Yi|                gap ∈ [0, 1]
```

where `p̂_X` and `p̂_Y` are the observed category frequencies of the two versions being compared,
each built from `n` independent, fresh completions. `gap = 0` means the two distributions
coincide; `gap = 1` means no overlap at all — nothing chosen under X was ever chosen under Y.

For a binary decision (`k = 2`) this reduces to `gap = |p̂_X1 − p̂_Y1|` — exactly the classic
framing-effect size used in the published literature, on purpose: our number compares directly
against published human results. **One estimator, permanently — no alternative distance metric is
ever published as a headline.**

### Units — one convention, everywhere

The estimator operates on `[0,1]`; **every published field and every threshold is in `_pct`,
0–100**. A rule written without `_pct` is **a specification error**, checked with a CI assertion
on field names.

```
gap_pct = 100·gap      stability_pct = 100 − gap_pct      drop_bound_pct = 100·(u_X + u_Y)
```

### The noise floor — published next to every measurement

**A perfectly invariant model does not score 100%.** With finite `n`, two samples drawn from the
*same* distribution still produce `gap > 0` from sampling alone.

Computed by permutation, for whatever `k` and `n` the protocol actually uses: pool the `2n` valid
responses of the two versions into one bag, draw two samples of size `n` without replacement,
compute `gap`, repeat many times, and average. Published as `null_floor_pct` **on every
measurement record**. A measurement with `stability_pct ≥ 100 − null_floor_pct` is
indistinguishable from perfect invariance and is flagged `at_noise_floor`.

For a binary decision around `p = 0.5`, the floor shrinks with `n`:

| `n` per version | noise floor | max achievable `stability_pct` |
|---|---|---|
| 30 (the `v0` series — every measurement published so far) | ~10.1 | ~89.9 |
| 120 (the `v1` series, §2 — built, not yet admitted or measured) | ~5.1 | ~94.9 |
| 150 (v0's original choice, before real per-call cost was known) | ~4.6 | ~95.4 |
| 450 | ~2.7 | ~97.3 |

*(Every figure in this table is produced by this project's own `core.measure.stats.null_floor`,
for a binary decision at `p = 0.5` — not by a closed-form approximation.)*

**Binding consequence: the noise floor depends on `n`, so `n` freezes per protocol.** Changing it
produces a new protocol version, with a new ID and a new series — never a continuation of the old
one (already stated in §2, restated here because it follows directly from this formula). v0's own
drop from `n=150` to `n=30` (§2, §5) happened pre-admission, before any protocol had a published
series to break comparability with — the rule binds the *next* change, not this one. **That next
change has since happened and the rule held:** the `v1` series raised `n` to 120 and took new IDs
and a new series rather than continuing the `v0` line (§2).

**A disclosed tradeoff, not a hidden one: `n=30` roughly doubles the noise floor** (~4.6% → ~10.1%
in the worst case, a binary decision at `p=0.5`) relative to v0's original `n=150` choice. This
table already treats that worst case as the baseline to design against — a real measurement's
actual floor is usually well below it, and every measurement still publishes its own
`null_floor_pct` rather than this table's illustrative figure.

### The entropy series — the defense against collapse

**The problem:** a model that gives the same answer no matter what the question says produces
`gap = 0` and scores 100%. A decade-long curve that climbs cannot, on `stability_pct` alone, tell
"became more stable" apart from "collapsed onto one answer." Published, mandatorily, per version,
as its own primary series:

```
H_norm(p̂) = −Σᵢ p̂ᵢ · log(p̂ᵢ) / log(k)        H_norm ∈ [0, 1]
```

Fields `entropy_a`, `entropy_b`. `1` = choices fully spread out; `0` = always the same answer.
**Mechanical flagging rule:** if `max(entropy_a, entropy_b) < 0.20` **and** `stability_pct > 90`,
the measurement is flagged `degenerate_candidate` and excluded from the main curve, the fact
published. Stability is never shown on a chart without entropy beside it.

### Four `u` values, three pairs, three bounds

Every version has its own `u`: **`u_a` · `u_a_prime` · `u_b` · `u_c`**, where
`u = 1 − n_valid/n`. `u` includes **all** invalid outcomes: `unparseable` · `refused` ·
`blocked_upstream` · `truncated` · `empty` · `off_format`.

| Pair | What it measures | Bound |
|---|---|---|
| **A ↔ B** | the main measurement | `drop_bound_pct(A,B)` |
| **A ↔ A′** | the null-change check | `drop_bound_pct(A,A′)` |
| **A ↔ C** | the positive control | `drop_bound_pct(A,C)` |

`drop_bound_pct` follows from total variation distance being 1-Lipschitz in each argument: if the
true (uncensored) distribution is a mixture `p_X = (1−u_X)·p̂_X + u_X·q_X` with `q_X` unknown, then
`|gap_true − gap_observed| ≤ u_X + u_Y`. **`drop_bound_pct` is not a confidence interval and not an
estimate** — it is the **worst possible error**, with no assumption whatsoever about why the
responses were lost.

### The gates — every check set at the edge that does NOT favor us

```
A↔B   drop_bound_pct(A,B) ≤ 2.0           otherwise  drop_confounded
      100·|u_a − u_b|      ≤ 1.0           otherwise  asymmetric_missingness
      drop_bound_pct(A,B)  < gap_pct(A,B)  otherwise  below_drop_bound

A↔A′  gap_pct(A,B) − drop_bound_pct(A,B)  >  gap_pct(A,A′) + drop_bound_pct(A,A′)
      lower bound of the real signal          upper bound of the noise
                                              otherwise  below_surface_noise

A↔C   gap_pct(A,C) − drop_bound_pct(A,C)  >  θ_positive
      lower bound, because here we WANT a large value
                                              otherwise  not_reading
```

**Pre-declared per protocol, in the protocol file and inside its `protocol_sha256`:**
`theta_positive_pct` (θ_positive above) and `scenario_dominated_share_pct` (§10, the
`scenario_dominated` flag). **v0 value, the same for all 12 protocols: `theta_positive_pct` = 15,
`scenario_dominated_share_pct` = 40.** For scale: if the gap were spread evenly over the 15
scenarios, the two worst would account for 2/15 ≈ 13% of it.

**The first two thresholds are checked on EACH pair separately.** If `u_c` is 4%, the positive
control is `drop_confounded` even if the main measurement passes — and the measurement comes off
the curve, because **a check that doesn't hold up isn't checking anything**.

**If `u` comes out large:** a clearer format instruction *(a new protocol version)* → a smaller
`options[]` → the protocol runs `exploratory`. **Never a loosened threshold.**

### Confidence interval — bootstrap, conditional on the panel

**Bootstrap, with replacement, at the level of the individual response** — responses are
independent by design (a fresh completion per call).

```
B    = 10,000                                                        frozen
seed = sha256(protocol_id ‖ run_date ‖ subject_model_id)   → 64-bit, recorded
```

For each of the `B` resamples: draw `n` with replacement from each version being compared, compute
`gap`. The interval is the **2.5th and 97.5th percentiles** of the `B` values. Fields `ci_low_pct`,
`ci_high_pct`, in `stability_pct` units.

**The 15-scenario panel is a frozen instrument, not a sample, so it is never itself resampled.**
Cluster-resampling 15 groups would be anti-conservative and understate uncertainty — publishing
such an interval would be exactly the false confidence this project exists to avoid. So
`ci_low_pct` / `ci_high_pct` mean: *"this happened, on this specific 15-scenario panel, on this
model, on that day"* — never a claim about the category "framing" or "anchoring" in general, and
**no generalization interval is published in v0**.

In its place, raw heterogeneity is published, per measurement: **`scenario_gaps_pct`** (all 15
per-scenario `gap` values, by `item_id`), **`scenario_spread_pct`** (min, max, median, IQR), and
**`scenario_concentration_pct`** (the share of the total gap the two worst scenarios produce). If 2
of 15 scenarios produce 80% of the gap, a reader sees that directly, without trusting a summary
statistic. The complementary source of generalization evidence is design, not statistics: **three
independent scenario families per rewording type** (§3), run as separate protocols, moving
together.

`n_items` is always published beside `n_valid`, and the interval is read relative to it.

**`core/measure/` is pure standard library — `numpy` is forbidden there, enforced by a CI
assertion** — so the estimator can be re-read, and re-implemented in any language, without relying
on a numerical library whose own version might not build decades from now.

### When do we say something changed

Comparing two measurements `x` and `y` of the same protocol (different years, or different models):

```
Δ_pct     = stability_pct(y) − stability_pct(x)
bound_pct = √( halfwidth_pct(x)² + halfwidth_pct(y)² )         random error, combined in quadrature
cap_pct   = drop_bound_pct(x) + drop_bound_pct(y)                systematic worst case, summed

|Δ_pct| ≥ bound_pct   AND   |Δ_pct| > cap_pct   →  improved / regressed
otherwise                                        →  flat
```

**Why two separate terms, combined differently:** `bound_pct` is *random* sampling error, so it
composes in quadrature; `cap_pct` is a *systematic* worst-case bound, so it adds directly — mixing
the two under one square root would be a real statistical error. **Both checks must pass.** If the
2029 measurement lost 0.3% of responses and the 2030 one lost 1.8%, an apparent "2-point
improvement" could be entirely the difference in loss rates, and this rule rejects it.

This label is computed **exclusively by frozen code**, never written by hand, and is the same rule
the trend badge on the public site reuses rather than re-deriving.

**Aggregates.** The mean of `m` measurements is published **only** with its own bootstrap interval,
computed from the same resampling — **never** with the `CI/√m` shortcut, which assumes an
independence the data doesn't have and that assumption is never stated.

**Multiplicity.** Exactly one pre-declared primary contrast per protocol. Any other comparison is
marked `exploratory: true`, excluded from every curve, and visually flagged.

---

## 7. The instrument

The instrument's own control is a **frozen, local, open-weights reference model** — measuring its
own drift, not the subject's, so a change in the curve can be told apart from a change in our own
pipeline. Pinned artifact, frozen now and never changed afterward:
`Qwen/Qwen2.5-1.5B-Instruct-GGUF`, revision `91cad51170dc346986eccefdc2dd33a9da36ead9`, file
`qwen2.5-1.5b-instruct-q4_k_m.gguf`, sha256
`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`, Apache-2.0, published directly
by the model's own team (no third-party quantizer sits between us and the published weights).
Runs greedy (`temperature=0`, `top_k=1`) inside a CPU-only container pinned to a fixed image
digest, with fixed `n_threads` and `n_batch` (thread count and batch size both change
llama.cpp's floating-point reduction order, which can move the last bit even at temperature 0 —
an unpinned value would make "byte-identical" mean nothing) and a chat template written out
literally rather than left to library auto-detection. The model file's sha256 is checked against
the pin before every load. `container_digest`, `runner_arch`, and the chat-template's own sha256
are recorded on every scan.

| What | Rule | Threshold |
|---|---|---|
| `runtime_fingerprint` — 12 fixed prompts, greedy, temp 0, fixed seed, top-5 logprobs per token hashed | **exact equality** | *any* difference → `instrument_alarm` |
| Guard protocols on the reference model — one protocol per day, rotating through all 10 guard protocols on a 10-day cycle | statistical | beyond the composite margin → `instrument_alarm` |
| `subject_fingerprint` — 24 frozen items against the remote model | **daily**, public series | small, measured monthly amount (§9) |

**`subject_fingerprint`'s 24 items are meant to be short, simple, single-fact checks (e.g. "Is 7
a prime number?"), not full 250-400-word scenarios** — the same design already used for
`runtime_fingerprint`'s 12 fixed prompts on the reference model. This is what keeps its real cost
low: a live 3-call sample of that style measured ~103 tokens/call total, against ~496 for a full
scenario. The first real run (2026-09-21, 24/24 correct) confirmed this design keeps the series'
real monthly cost well under what this section originally projected from token counts alone, and
far under what a full-scenario-length item would cost at the same daily count. This category is
never budget-gated (below) — its cost has to stay small by design, not by the ladder.

**The guard-protocol rotation runs one protocol a day, not all 10 daily, purely for cost:**
measured at ~9.6s/call on the pinned reference-model container (this is the reference model's own
local inference speed, unrelated to the commercial API), all 10 daily would now be
(10 × 120) + 12 = 1,212 calls/day, ~194 min/day, ~5,820 min/month — 2.9× a private repo's 2,000
min/month free tier. One protocol/day is 120 + 12 = 132 calls/day, ~634 min/month (~32% of the
free tier). `runtime_fingerprint` itself still runs in full every day regardless, and catches most
pipeline/environment breaks on its own.

**Consequence:** the cycle doesn't stop — **publication stops**. Measurements are still written,
with `instrument_suspect: true`, off every curve, with the reason. Recovery only via a human
`core_change`.

**A day without `runtime_fingerprint` publishes no measurements** — `unavailable` counts as
`fail`.

**What this does NOT prove:** if the instrument doesn't move, **only our own pipeline** is ruled
out. A router, a hidden system instruction, or provider-side quantization all remain untouched as
possible causes.

---

## 8. Cadence — `cadence.yaml`

```yaml
# The subject
subject_fingerprint:      1d     # 24 items, temp 0, public series
full_sweep:               30d     # 12 protocols × 4 versions × n=30
open_weights_series:  on_new_generation   # NOT on the rotation — CPU, free
sealed_scan:          on_new_generation   # the 2 sealed ones, only at generation boundaries

# The instrument
runtime_fingerprint:      1d
guard_margin_rotation:    1d     # ONE guard-lane protocol/day, rotating through all 10 on a
                                   # 10-day cycle — see §7 for why (CI cost, not a design choice)
revalidation_daily:        10     # historical records, local, zero cost

# Generations
generation_bridge_protocols: 4    # one per type · old AND new, same week · frozen n=2, 960 calls
                                   # (the frozen cadence.yaml still says n=1 / 480; see §4.3)
                                   # preempts that month's sweep (cause: generation_bridge)
bridge_trigger:      any_new_model_id_in_family   # BRIDGE: automatic, immediate
active_switch:       provider_declared_successor  # ACTIVE-MODEL SWITCH: only with an explicit
                                   # declaration, otherwise needs_review — one click in the monthly batch
                                   # bridge reserve: a private budget setting (budget.json), never spent on
                                   # anything else, drawn on its own, never subtracted from the general
                                   # monthly pool (core/budget/budget.py)
retirement_threshold:       4     # generations AND equivalence
declassify_after:           4     # generations

# Human
human_review:            30d      # the monthly batch, ~10 minutes
checkpoint_sign:         30d
coverage_publish:        30d
release_publish:         90d      # Zenodo + DOI, automatic
strategy_review:         90d
self_revision:          180d      # core/plumbing/ only
integrity_declaration:  365d

# Lease on life
source_degraded_after:    7d
deadman_alert:           36h
vacation_after:          75d
dormant_after:          180d
succession_offer:       365d
concluded_after:        730d
```

---

## 9. Budget — a fixed, disclosed monthly ceiling

> **The project sets itself a hard, fixed monthly spending ceiling and is not allowed to need more
> than that to remain valid and alive.** The exact figure is a private operating decision, not
> published in this document — see "The private / public boundary" (§10) for why.

The ceiling covers a handful of fixed and usage-based costs — domain renewal, password-manager
emergency access, API calls for the sweep and fingerprinting, and a small reserve — with margin
kept deliberately unspent. The full breakdown lives in `budget.json`, private, never in this
document or in `cadence.yaml`.

### Degradation ladder — `spend.json`, at the start of every run

| Spend used | What gets cut |
|---|---|
| 60% | the sweep drops to 6 protocols, disclosed |
| 80% | only 4 guard protocols |
| 95% | only `subject_fingerprint` |
| — | **never zero.** `heartbeat_reserve` is never consumed by the daily cycle |

### Order of cuts, if actual cost turns out higher

1. **protocols drop** — 12 → 8 → 6 · 2. **frequency drops** — monthly → quarterly ·
3. **tier drops** to Haiku-class, with a disclosed series discontinuity.

**Never:** `n` · the two checks · the mechanical extraction · the logging of the actual
inputs/outputs.

> **When the budget isn't enough, less coverage is published, with a disclosed cause
> (`budget_halted`). `n` and the checks are never quietly reduced.**

**Cost per call is now measured** (Sonnet-class, `thinking` disabled), first measured 2026-09-21
from one full real pilot protocol (120 calls) and since confirmed across **13 billed protocol runs
/ 1,560 calls** (`state/spend.json`, 2026-09-23): the per-protocol figure has stayed consistent
throughout. The old placeholder numbers in `budget.json` have already been replaced by this real,
measured rate.

A second, much cheaper rate applies to `subject_fingerprint`'s short items (§7) — because those
prompts are a fraction of a full scenario's length. The two are never averaged into one "cost per
call."

Unlike `cadence.yaml` (§13: frozen at commit #1), the ceiling, the reserve, and the ladder's rungs
are ordinary recalibratable data — editing them after the first real cost is known is a data edit,
never a code change, and never a spec change.

---

## 10. What gets published

### Per measurement

`record_type` · `schema_version` · `run_id` · `run_date` · `protocol_id` · `rewording_type` ·
`subject_model_id` · `stability_pct` · `ci_low_pct` / `ci_high_pct` *(conditional on the panel)* ·
`gap_pct` · `gap_null_pct` · `gap_positive_pct` · `null_floor_pct` · `at_noise_floor` · `n` ·
`n_valid` *(per version)* · `item_id` / `n_items` · `k` · `entropy_a` / `entropy_b` ·
`u_a` / `u_a_prime` / `u_b` / `u_c` · `drop_bound_pct_ab` / `_aa` / `_ac` ·
`drop_asymmetry_pct_ab` / `_aa` / `_ac` · `scenario_gaps_pct` · `scenario_spread_pct` ·
`scenario_concentration_pct` · the full outcome distribution **per version**, and the extracted
`decisions` **per version** · `flags[]` · `gates` *(pass/fail detail, one entry per check)* ·
`on_curve` · `panel_sha256` · `protocol_sha256` · `extractor_sha` · `analysis_code_sha` ·
`schema_sha256` · `grammar_version` · `series` · `lane` · `twin_id` · `model_family` ·
`returned_model_id` · `api_surface_sha` · `condition_profile` · `replicate_index`.

**Every record carries a `record_type` from one closed list:** `protocol` · `experiment_run` ·
`trial` · `measurement` · `coverage_gap` · `render_manifest` · `status` — plus, for succession
(§4.3): `subject_model` · `generation_bridge` · `bridge_incomplete` · `series_closed` ·
`needs_review` · `comparison_point`.

### Flags

| Flag | What it means |
|---|---|
| `below_surface_noise` | the equivalent rewording did not exceed the null change |
| `not_reading` | the positive control did not move the response |
| `degenerate_candidate` | low entropy with high stability — always answers the same way |
| `drop_confounded` | `drop_bound_pct > 2.0` on **any** of the three pairs |
| `asymmetric_missingness` | the losses differ by more than 1.0 point within one pair |
| `below_drop_bound` | the finding cannot be told apart from the losses |
| `scenario_dominated` | the two worst scenarios produce more than a pre-declared share of the gap (v0: 40%) |
| `instrument_suspect` | the instrument moved that day |
| `exploratory` | outside the pre-declared primary contrast; never on a curve |

**Every flag takes the measurement off the main curve. No flag ever deletes a measurement** — the
measurement is published flagged, with the reason.

### Coverage — every missing day has a named cause

`coverage.csv` reports, for every scheduled job, whether it ran — and if not, why. A gap is never
left unexplained: it is always exactly one of a closed list of causes —
`before_archive_start` · `budget_halted` · `dormant` · `provider_outage` ·
`protocol_confounded` · `human_absent` · `generation_retired_early` · `generation_bridge`.

The mapping from an observed condition to one of these causes runs in frozen code, never written
by hand after the fact — a day with no output and no cause from this list is exactly the failure
mode coverage reporting exists to prevent.

### Files

`stability.csv` · `outcomes.csv` · `coverage.csv` · `open-lane/<year>.jsonl` · `croissant.json` ·
**all of `core/measure/`** (not `verify.py` alone — it imports `chain.py`, `grammar.py`,
`grammar_v2.py`, `invariants.py`, `measurement.py`, `rng.py`, `schema.py`, `stats.py`, and doesn't
run without them) + `core/testdata/vectors/` + `SPEC.md` (a copy of this spec)

`stability.csv` is one row per measurement, one column per `core.measure.schema.MEASUREMENT_FIELDS`
key (`core/plumbing/render.py`) — a field whose value isn't a plain scalar (`n_valid`, `outcomes`,
`decisions`, `scenario_gaps_pct`, `scenario_spread_pct`, `gates`, `flags`) is written as one JSON
cell rather than split into its own columns, left open for later rather than decided here.

**Both series live in this one file, separated by its `series` column** — there is no separate
`open-weights/stability.csv`, and no separate `instrument.csv`; earlier drafts of this section
named both, and neither was ever built. The renderer keeps the two series apart where it matters
(the coverage bar counts `commercial` only; the site draws them as two tables, never one line),
which is the rule §4 actually requires — a second file was never what enforced it.

`outcomes.csv` is the same `outcomes` field unpacked instead: one row per
`(run_id, version, outcome, count)`. `open-lane/<year>.jsonl` is every `trials.jsonl` record from a
`lane: "open"` run, copied through unchanged and grouped by the run's year — `guard`/`sealed`
trials never enter this file, per the lane table above.

`croissant.json` describes `stability.csv`, `outcomes.csv` **and `open-lane/<year>.jsonl`** for the
MLCommons Croissant format: one `sc:Field` per column, its Croissant data type read off the same
field-type label
`MEASUREMENT_FIELDS`/`OUTCOME_FIELDS` already carry (`pct`/`unit_interval` → `sc:Float`,
`int`/`count` → `sc:Integer`, `bool` → `sc:Boolean`, everything else → `sc:Text`).

### The private / public boundary

**The default is public, not private.** The source code is not a secret — `core/measure/`,
`core/schedule/`, `core/plumbing/`, `core/testdata/`, `core/instrument/`, `cadence.yaml`,
`subject_models.yaml`, `website/`, `logo.png`, `CITATION.cff`, `.zenodo.json` all ship to the
public repo as-is, on top of the generated files listed above. Delivered as freshly generated
content on every release, never as exported git history — the public repo shares no commits with
the private one, and there is no code-hiding mechanism beyond just not copying a file over.

`.zenodo.json` is what the GitHub–Zenodo webhook (§8's `release_publish`, once enabled on
zenodo.org — an account-level toggle, not something any script here can do) reads to fill in the
metadata of the deposit it mints automatically from a tagged GitHub Release on the public repo.
`tools/cut_release.py` prepares that release; see its own docstring for the exact division between
what it automates and what stays a human decision (which version, when).

**Four things stay private, and only these:**

1. **Secrets.** API keys and credentials are never committed to *either* repo — kept in
   environment variables / a secrets manager, full stop. This isn't a repo-split rule, it's a
   never-commit rule.
2. **`core/budget/` and everything it reads or writes** — `budget.json`, `state/spend.json`,
   `state/last_run.json`. Not because the mechanism is sensitive (it's ordinary code), but because
   the owner doesn't want the project's real financial numbers public. The whole folder stays
   private, code and data together, rather than trying to split "the mechanism is fine, only the
   euro figures aren't" — that split isn't clean (the code's own docstrings/tests use realistic
   numbers) and isn't worth the risk of getting it wrong.
3. **`protocols/` and `mutable/`, in full.** This one is **not** a preference — it's the mechanism
   in §3 breaking if violated. A `guard` protocol's text must never be seen by a crawler (or end up
   in training data), because the whole contamination check is comparing it against its `open`
   twin's *measured drift*; a `guard` protocol whose text is already public is indistinguishable
   from an `open` one, and the comparison stops meaning anything. `mutable/build_protocol_*.py`
   generates that exact text (and `panels.py` holds the shared scenario base every type builds
   from), so both folders stay private, not just the JSON output. The `open` lane's text still
   reaches the public — through `open-lane/<year>.jsonl`, generated from `experiments/` per trial
   — never by publishing `protocols/`/`mutable/` directly.
4. **The private strategy trail** — `my_help/decisions.md`, `my_help/plan.md`,
   `my_help/research-findings.md`, `my_help/explanations.md`, `my_help/repo_structure.md`. Not code, and not
   data the public site needs: competitive reasoning, legal/trademark notes, planning history.

**`experiments/` and `state/` stay private** for the same reason as point 3 above (and point 2):
`experiments/` mixes all three lanes' raw data (only `open` is meant to be fully public, and only
through the renderer's extraction, never as a wholesale copy); `state/` is transient bookkeeping
the public site has no use for in raw form.

`llm-archive-sealed` stays fully isolated at all times — a separate, disconnected repository, never
pushed anywhere near the public or private archive repos. Only two hashes (`panel_sha256`,
`protocol_sha256`) ever cross out of it, copied by hand into a `sealed` lane record.

### Licenses

| Data | **CC-BY-4.0** |
|---|---|
| Prose | **CC-BY-SA 4.0** |
| Published code | **AGPL-3.0** |
| Private tier | All rights reserved. Confidential. |

### Lanes and raw traces

| Lane | Now | Later |
|---|---|---|
| `open` | **full traces per trial** | — |
| `guard` | outcome categories | **full traces on declassification**, +4 generations |
| `sealed` | existence and count only | **never** |

**`guard` ALWAYS stores full traces, on every run.** The 10% sample applies only to repeats of
`open`. The unit of declassification is **the protocol**, not the model.

---

## 11. The pages — a closed list

**Home** *(the title links there)* · **Results** · **Data** · **Design** ("what we're building")
· **The metric** ("what it does") · **Methodology** ("how") · **The value** · **Glossary**

The homepage is a short **overview**: one-sentence definition → coverage/status bar → a 30-second
worked example → what you get / don't get → links onward. All of the dashboard content — the
current-scan table, the stability-over-time chart, the biggest-move callout, the instrument
self-check, the coverage note, and the second (open-model) table — lives on **Results**, one click
away, not on the homepage itself. Every other mechanism (why two model series, why four wordings,
how the score is computed, the monthly pipeline, why it matters) lives on its own page, linked from
Home and Results. The Glossary explains every code and term used across the tables in plain
language, for a reader who's never seen the site before.

Single static HTML document · SVG from the generator · **no dependency, no CDN** · client-side tab
switching and the per-row drill-down drawer are done in a small vanilla-JS script embedded in the
page (no framework, no build step) · works at 400px · no relative time in the markup. The page
requires JavaScript to be usable — this replaces the earlier "readable with JS off" rule, once the
dashboard-style Results page (a sortable table with a per-row drawer) turned out to need it.

**The renderer rejects** a homepage with no coverage/status bar.

**Not built:** a page per measurement *(the drawer covers this in place)* · a "Map" page · a
"Diff" page · an "Open Lane" page · search · infinite scroll · a DOI per measurement · sitemap
sharding · incremental build.

---

## 12. Off the critical path in v0

These remain in the plan as future enrichment; **they do not block the first measurement:**

the ~50-concept map · the dependency on Psych-101 and its license · the word-for-word quote check
· the concept pages · the `author_response` engine · `self_knowledge_gap` · multilingual support ·
the P2 and P3 derivatives.

*All of these follow from §1: the project's primary claim is a model's stability with itself, not
a comparison to human judgment — so nothing that exists only to compute or present that
comparison blocks the first measurement.*

---

## 13. What freezes before commit #1

1. **The schema** — a closed list of record types, `schema_version` + `schema_sha256`, the three
   rules for additive evolution.
2. **`core/measure/`** — the estimator, the two checks, the floor, entropy, bootstrap, the
   significance rule, the grammar analyzer, the structural invariants, the leak guard, the chain,
   `verify.py`, `vectors/`.
3. **§6 above, in full** — the estimator, the noise floor, the entropy series, the loss bound and
   its gates, the bootstrap confidence interval, and the comparison rule.
4. **`subject_models.yaml`** — tier, `effort`, `model_family`, `auto_bridge` (`temperature` dropped
   from this list — see §5: no longer a client-controllable parameter on the commercial API).
5. **`cadence.yaml`** — §8.
6. **The decision grammar**, `grammar_version` = 1, **the deterministic extraction rule** with its
   golden vectors, and **the loss bound** with its two thresholds.
   *Since 2026-09-22 a second grammar exists beside it, `grammar_version` = 2
   (`core/measure/grammar_v2.py`, its own golden vectors in `core/testdata/vectors/`), used by the
   `v1` protocols only. It differs in exactly one rule: a line counts as a decision line only if
   what follows the colon is already a valid option. Version 1's own file is **byte-for-byte
   unchanged** and every `grammar_version: 1` protocol keeps precisely the behaviour it always
   had — the addition is dispatched on the protocol's declared `grammar_version` at four call
   sites, never by replacing version 1. What froze here is version 1's behaviour, not the count of
   grammars that may ever exist; a new version is a new protocol series (§2), never a silent
   re-scoring of an old one.*
6a. **The 15 scenarios per protocol**, with `panel_sha256` — a frozen panel, not a sample. The
    three families (§3) and the two per-protocol thresholds (§6: `theta_positive_pct` = 15,
    `scenario_dominated_share_pct` = 40).
7. **`protocol_sha256`** as a schema field, checked in the daily preflight.
8. **The mapping from observed condition → gap cause**, from the closed list.
9. **`status.json`** — schema, producer, public path, inside `render_manifest`.
10. **A pilot run** of the 12 protocols that **measures `u` per condition** and demonstrates that
    every protocol passes the two thresholds — otherwise it does not enter the main curve.
    *No manual labeling is required anymore.*
    *Status, 2026-09-23: done for real, not just in pilot — all 12 `v0` protocols have now been
    run against `claude-sonnet-5` with billed calls (13 runs; `base_rate_neglect__anchoring__v0`
    was measured twice). Every run recorded its own `u` per version and its own gate results; which
    of them cleared all the gates is published per measurement in `on_curve` and `flags`, not
    summarised here.*

**And before any of this — a legal/IP gate that precedes even the schema freeze:** a written IP
carve-out · re-creating the plan on personal media · EUIPO + USPTO · provider terms, as a dated
PDF.
