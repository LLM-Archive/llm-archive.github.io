# LLM-Archive guide

**In one sentence:** LLM-Archive tracks whether commercial AI models keep giving the same decision
when the same question is asked in a different wording — measured openly, over time, so a change
can be caught and dated instead of going unnoticed.

This is written for people **using** LLM-Archive — researchers, developers, journalists,
students. Everything here is in English, kept in sync with the frozen
specification at [`SPEC.md`](https://github.com/LLM-Archive/llm-archive.github.io/blob/master/SPEC.md). If the two ever disagree,
`SPEC.md` is correct and this guide is what needs fixing.

## Where to start, depending on who you are

| You are... | Start here |
|---|---|
| A researcher who wants to cite a number, or understand what it does and doesn't prove | [`for-researchers.md`](for-researchers.md) |
| A developer who wants to run the code locally, try a model of their own, or extend/audit it | [`for-developers.md`](for-developers.md) |
| A data analyst who wants to load the files and get correct answers out of them | [`for-analysts.md`](for-analysts.md) |
| Someone who wants to run the same panel against a different model | [`reproducing.md`](reproducing.md) |
| Someone who wants to know what a specific column or term means | [`data-dictionary.md`](data-dictionary.md) and [`glossary.md`](glossary.md) |
| Someone with a quick question | [`faq.md`](faq.md) |
| Someone who wants to know how "declared before it was run" is proved | [`timestamps.md`](timestamps.md) |
| A cited researcher who found an inaccurate quote or wants to respond | [Open an issue](https://github.com/LLM-Archive/llm-archive.github.io/issues) or write to mkalognomos@gmail.com |

## The one thing to understand before reading anything else

LLM-Archive measures **one number**: how much a model's decision distribution shifts when the same
question is reworded in a way that shouldn't change the answer. It publishes that number alongside
everything needed to doubt it — a noise floor, an entropy series, a worst-case error bound from
lost responses, and a daily check on whether the measurement pipeline itself is still working. It
does not measure how "smart," "human-like," or "aligned" a model is, and it says so explicitly
rather than letting a reader assume otherwise. See [`for-researchers.md`](for-researchers.md) for
the full scope of the claim.
