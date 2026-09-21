# LLM-Archive

A long-running measurement project: does a commercial AI model's decision change when the
same question is asked in different words — and how would we know if our own way of checking that
had broken?

**Live site:** [llm-archive.github.io](https://llm-archive.github.io/)

## What's in this repo

This repository *is* the public site and the public dataset — everything here ships as-is to
`https://llm-archive.github.io/`.

| Path | What it is |
|---|---|
| `index.html`, `website/` | The public site (`index.html` is generated; `website/` holds its source template) |
| `stability.csv`, `outcomes.csv`, `croissant.json`, `open-lane/<year>.jsonl` | The published data, regenerated fresh on every release |
| `guide/` | A plain-English guide — start at [`guide/index.html`](https://llm-archive.github.io/guide/) if you want to use the data or reproduce a run |
| `SPEC.md` | The frozen specification: exactly what's measured, how, and what every published field means |
| `core/measure/`, `core/schedule/`, `core/plumbing/`, `core/testdata/` | The full pipeline source code |
| `cadence.yaml`, `subject_models.yaml` | The measurement schedule and which model(s) are currently tracked |
| `LICENSE.md` | Full license breakdown (see below) |

## License

This project doesn't use one single license — different parts of it are licensed differently on
purpose:

| What | License |
|---|---|
| Code (`core/`) | AGPL-3.0 |
| Data (the CSVs, `open-lane/`) | CC-BY-4.0 |
| Prose (the site, `guide/`, `SPEC.md`) | CC-BY-SA 4.0 |

See [`LICENSE.md`](LICENSE.md) for the reasoning behind each choice.

## How this repo is maintained

Nothing here is hand-edited. This repo is regenerated from a private working repository on every
release and pushed as a fresh commit — it shares no git history with that repo, and never receives
anything outside `SPEC.md` §10's published-file list (development notes, the private question bank,
and unpublished protocols all stay out). If something here looks wrong, it's a bug in the generator
or the underlying data, not a one-off edit worth patching directly.

## Questions, corrections, or reproducing a result

Start with [`guide/faq.md`](https://llm-archive.github.io/guide/faq.html) and
[`guide/reproducing.md`](https://llm-archive.github.io/guide/reproducing.html). Contact details for
anything not covered there are in `SPEC.md`.
