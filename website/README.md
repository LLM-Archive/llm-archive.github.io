# `website/`

## What you'll see here

- `mockup.html` — the owner-approved visual design (English, 8 pages, JS-driven SPA —
  `decisions.md` §40), hand-built with fake, hardcoded data. Kept as the design reference; not
  read by any code.
- `template.html` — the same page, with the fake data and hand-typed numbers replaced by
  `@@TOKEN@@` placeholders. This is what `core/plumbing/render.py build-site` actually reads.
- `index.html` — the real, generated output of `build-site`. Not committed (same as
  `stability.csv` at the repo root) — it's a generated public-repo artifact, rebuilt from
  `experiments/`, `protocols/` and `subject_models.yaml` on every run.

## Why it needs to exist

The public site (`llm-archive.github.io`, a separate repo) has a fixed, closed set of pages —
Home, Results, Data, Design, The metric, Methodology, The value, Glossary (spec.md §11) — and
nothing else (no per-measurement pages, no search). `mockup.html` is where that design was worked
out and approved before being wired to real data; `template.html` is the same design with a
data-binding layer instead of fake arrays, filled in by `core/plumbing/render.py`'s `build-site`
command (see `core/plumbing/README.md`).

**`index.html` reflects whatever is actually in `experiments/` right now — today that's a handful
of `FakeClient` test runs, not real, public numbers.** It never presents test-fixture data as if
it came from a real tracked model; see `render_coverage_bar` in `core/plumbing/render.py`.
