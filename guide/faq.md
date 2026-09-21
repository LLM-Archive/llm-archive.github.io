# FAQ

Quick answers. For the full reasoning behind any of these, follow the link to
[`../docs/spec.md`](../docs/spec.md), which is the governing document if anything here is unclear
or seems to conflict with it.

**What does LLM-Archive actually measure?**
Whether a commercial AI model's decision changes when the same question is reworded in a way that
shouldn't change the answer — measured as a distance between two response distributions, not a
per-question "did it flip" count. See [`glossary.md`](glossary.md) for the exact terms and
[`for-researchers.md`](for-researchers.md) for what the number does and doesn't prove.

**Why isn't a perfectly consistent model reported as 100% stable?**
Because with a finite number of responses, two samples from the *identical* distribution still
produce some measured gap by chance alone. This "noise floor" is published next to every
measurement (`null_floor_pct`) so a reader can see how much of any gap is just sampling noise. At
the project's chosen sample size (`n=30` per version), the ceiling is about 89.9% in the worst
case (a binary decision at 50/50) — see `../docs/spec.md` §6 for why `n` is this small: real calls
against the real API come back deterministic without a client-controllable sampling parameter, so
this project no longer pays for repeat samples that would just repeat the same answer.

**Is there an AI "judge" grading the model's answers?**
No. Extraction is entirely deterministic: a response must end with a `DECISION: <token>` line, with
`token` from a fixed, closed list of options — checked by ordinary code, not by another model.
Anything outside that exact line is never read, and a response without one, or with conflicting
tokens, is scored `unparseable` rather than interpreted.

**Why measure a second, frozen model every day?**
To catch the project's own pipeline breaking before mistaking that for a change in the model being
studied. If a fixed, never-changing reference model suddenly answers differently, something in the
project's own setup changed — not the subject model. This is disclosed as a real limit, not a
complete guarantee: if the reference model stays flat, that rules out the project's own pipeline as
a cause, but it does **not** rule out the commercial provider silently changing something upstream
(a router, a hidden system instruction, quantization) — no local check can see behind a hosted
API. See `../docs/spec.md` §7.

**Why track a downloadable ("open-weights") model at all, separately from the commercial one?**
As a control experiment proving the method itself reproduces, run once per model generation rather
than monthly, since a downloadable model can't be silently changed by a provider — it's re-run
under the project's own control instead. The commercial series and the open-weights series are
never drawn on the same chart or averaged together; they answer different questions.

**Why not just compare the model to how humans answer the same question?**
Any such comparison (`human_model_gap`) is published, but only as a secondary, per-protocol note
where a directly comparable multi-lab or meta-analytic human study exists — never as a headline,
and never aggregated across protocols. The reason: proving a human number and a model number
measure "the same underlying thing" is a much harder claim than measuring whether a model is
consistent with *itself*, and the project's headline metric is built to avoid depending on that
harder, contested claim.

**What's the difference between `open`, `guard`, and `sealed` protocols?**
How much of a protocol's exact wording is published, and when:

- `open` — full text, immediately.
- `guard` — only outcome categories now; full text after four model generations.
- `sealed` — not even that a specific scenario exists in detail; only that the protocol exists and
  how many trials it has. Never opens.

Full explanation, including why this matters for detecting contamination: [`glossary.md`](glossary.md).

**Why keep most protocols hidden at all — isn't that against the spirit of an open project?**
Because a protocol whose exact wording is public will eventually be read by a model during
training, and once that happens, "the model's answer moved" and "the model has now seen the test"
become indistinguishable from the outside. Keeping most protocols private for a while, and pairing
each public one with a hidden "twin" measuring the same underlying phenomenon, is what makes it
possible to tell those two explanations apart later. Full text does eventually become public for
`guard` protocols (after four generations) — `sealed` protocols are the deliberate exception, kept
back permanently as an uncontaminated instrument for the very long term.

**Can I run the same test against a different model and get it published?**
Yes — see [`reproducing.md`](reproducing.md) for exactly what's required for a submission to count
as a valid `comparison_point`, and what such a point does and doesn't mean.

**What happens if the project goes quiet — is the data lost?**
No. Two independent mechanisms exist for this specifically: the project publishes a disclosed
degradation ladder rather than going silent (reduced scope before reduced rigor — `n` and the
statistical checks are never quietly loosened to save money, `../docs/spec.md` §9), and every
scheduled measurement that doesn't happen is logged with a specific cause rather than left
unexplained (`../docs/spec.md` §10). A gap the project names itself is a disclosed limitation; a
gap discovered by someone else later would be a much bigger problem — which is exactly why it's
designed not to happen that way.

**Does the metric say anything about model capability, alignment, or "how human" a model is?**
No, deliberately. See the "What it does not claim" section of the root [`README.md`](../README.md)
and the fuller version in [`for-researchers.md`](for-researchers.md).

**I'm a researcher whose work is cited (or miscited) here — what do I do?**
See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for the correction process, including how an author
response is verified before being treated as authoritative.

**Is this live yet?**
Partly. As of this writing, one real measurement exists (`claude-sonnet-5`, one of the 12 v0
protocols, 2026-09-21) — but there is still no public repository or website. See the root
[`README.md`](../README.md) and [`CHANGELOG.md`](../CHANGELOG.md) for the current build state.
