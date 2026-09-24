# LLM-Archive guide

This folder is written for people **using** LLM-Archive — researchers, developers, journalists,
students. Everything here is in English, kept in sync with the frozen
specification at [`../docs/spec.md`](../docs/spec.md). If the two ever disagree,
`docs/spec.md` is correct and this guide is what needs fixing.

These `.md` files are the source; `python3 -m core.plumbing.render_guide build` turns each into a
matching `.html` page (`README.md` → `index.html`) with a shared nav bar, sitting next to its
source the same way `website/index.html` sits next to `website/template.html` — a small, standalone
docs site alongside the closed 8-page main site (spec.md §11), not inside it. The generated `.html`
files aren't committed (see `.gitignore`); see `core/plumbing/render_guide.py`'s docstring for why
that renderer is a hand-rolled markdown converter rather than a dependency.

The public site is live. The public repository is `github.com/LLM-Archive/llm-archive.github.io`,
serving `llm-archive.github.io`; this guide ships as part of it. Everything below describes how
the finished project is designed to work. Where something described here isn't built yet, it's
marked as such — this project's own principle (`docs/spec.md` §9, §10) is to disclose gaps rather
than imply a feature exists before it does, and this guide follows the same rule about itself.

## Where to start, depending on who you are

| You are... | Start here |
|---|---|
| A researcher who wants to cite a number, or understand what it does and doesn't prove | [`for-researchers.md`](for-researchers.md) |
| A developer who wants to load the data, or extend/audit the code | [`for-developers.md`](for-developers.md) |
| Someone who wants to run the same panel against a different model | [`reproducing.md`](reproducing.md) |
| Someone who wants to know what a specific column or term means | [`data-dictionary.md`](data-dictionary.md) and [`glossary.md`](glossary.md) |
| Someone with a quick question | [`faq.md`](faq.md) |
| Someone who wants to know how "declared before it was run" is proved | [`timestamps.md`](timestamps.md) |
| A cited researcher who found an inaccurate quote or wants to respond | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |

## The one thing to understand before reading anything else

LLM-Archive measures **one number**: how much a model's decision distribution shifts when the same
question is reworded in a way that shouldn't change the answer. It publishes that number alongside
everything needed to doubt it — a noise floor, an entropy series, a worst-case error bound from
lost responses, and a daily check on whether the measurement pipeline itself is still working. It
does not measure how "smart," "human-like," or "aligned" a model is, and it says so explicitly
rather than letting a reader assume otherwise. See [`for-researchers.md`](for-researchers.md) for
the full scope of the claim.
