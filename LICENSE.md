# Licensing

LLM-Archive does not use one single license, because it does not treat all of its own output the
same way. This file states which regime applies to which part of the project. The authoritative,
frozen version of this table is `docs/spec.md` §10; this file exists to explain it in more detail,
not to override it.

| What | License | Why |
|---|---|---|
| **Published code** (`core/measure/`, `core/schedule/`, `core/plumbing/`, `core/testdata/`, and anything else that ships to the public repo) | **GNU Affero General Public License v3.0 (AGPL-3.0-only)** | Copyleft, including for network use — if someone runs a modified copy of the pipeline as a service, they owe the modified source back to its users. Appropriate for a project whose entire value proposition is that its measurement code can be independently re-run and checked. |
| **Published data** (`stability.csv`, `outcomes.csv`, `open-lane/<year>.jsonl`, `croissant.json`) | **Creative Commons Attribution 4.0 (CC-BY-4.0)** | Deliberately permissive, not ShareAlike. A ShareAlike requirement on a *data table* is a real obstacle to reuse — a downstream analysis that merges this data with other sources under an incompatible license becomes legally impossible. Attribution alone is what the underlying literature and data-reuse guidance (Croissant, FAIR) actually asks for. |
| **Published prose** (`website/`, `docs/spec.md`'s text, `guide/`) | **Creative Commons Attribution-ShareAlike 4.0 (CC-BY-SA 4.0)** | Prose explaining the project can be freely translated, quoted, and adapted, provided the result stays open under the same terms and the source is credited. |
| **Everything in the private tier** — `protocols/`, `mutable/`, `core/budget/` and its data, `experiments/`, `state/`, the operator's private planning notes, and anything in the fully separate `llm-archive-sealed` repository | **All rights reserved. Confidential.** | Not licensed to anyone outside the project. Publishing a `guard`-lane protocol's wording, in particular, is not a licensing preference — it would break the contamination check the whole `open`/`guard` twin-pairing mechanism depends on (see `docs/spec.md` §3). |

## What this means in practice

- **You can** take the published code, run it, modify it, and even offer it as a service to
  others — as long as you make your modified source available to the people using that service
  (AGPL-3.0's core condition).
- **You can** take the published CSV/JSON data, republish it, merge it with your own datasets, and
  build on it commercially, as long as you credit LLM-Archive as the source (CC-BY-4.0).
- **You can** translate or adapt the website text and the specification's prose, as long as your
  version is credited and stays under CC-BY-SA 4.0.
- **You cannot** get access to the exact wording of a `guard`-lane or `sealed`-lane protocol
  through licensing — it isn't offered under any license, at any tier, until the disclosed
  declassification point (four model generations after admission, for `guard`; never, for
  `sealed`).
- **You cannot** copy `core/budget/`'s data (real spending figures) — the module's *code* is
  AGPL-3.0 like the rest of `core/`, but its own data files (`budget.json`, `state/spend.json`)
  are kept out of the public repo entirely, for the owner's own reasons rather than any technical
  sensitivity.

## Full license texts

Rather than reproducing lengthy legal text in this repository (and risking a transcription error
in a document meant to be relied on), each license's canonical text is linked here:

- AGPL-3.0-only: <https://www.gnu.org/licenses/agpl-3.0.txt>
- CC-BY-4.0: <https://creativecommons.org/licenses/by/4.0/legalcode>
- CC-BY-SA-4.0: <https://creativecommons.org/licenses/by-sa/4.0/legalcode>

## Current status

This repository (`llm-archive`) is **private**. None of the above is actually available to anyone
outside the project yet — the table states what each part *will be* licensed as once it reaches
the public repo (`llm-archive.github.io`) or the public website, per the release process described
in `docs/spec.md` §10.
