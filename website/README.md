# `website/`

## What you'll see here

- `template.html` — the site's markup, styling and JS, with `@@TOKEN@@` placeholders standing in
  for real data. This is what `core/plumbing/render.py build-site` actually reads.
- `index.html` — the real, generated output of `build-site`. Not committed (same as
  `stability.csv` at the repo root) — it's a generated public-repo artifact, rebuilt from
  `experiments/`, `protocols/` and `subject_models.yaml` on every run.

## Why it needs to exist

The public site (`llm-archive.github.io`, a separate repo) has a fixed, closed set of pages —
Home, Results, Data, Design, The metric, Methodology, The value, Glossary (spec.md §11) — and
nothing else (no per-measurement pages, no search). `template.html` is that design with a
data-binding layer instead of hardcoded numbers, filled in by `core/plumbing/render.py`'s
`build-site` command (see `core/plumbing/README.md`).

**`index.html` reflects whatever is actually in `experiments/` right now.** It never presents
test-fixture data as if it came from a real tracked model; see `render_coverage_bar` in
`core/plumbing/render.py`.

