# For data analysts

This page is for anyone who wants to load LLM-Archive's published files into a notebook, a
spreadsheet or a BI tool and get correct answers out of them. It covers what each file is, how
they join, what to filter on before you chart anything, and the mistakes that produce a plausible
but wrong result. For what each column means, see [`data-dictionary.md`](data-dictionary.md); for
what the headline number does and doesn't prove, [`for-researchers.md`](for-researchers.md).

Everything here uses only the public repository, `github.com/LLM-Archive/llm-archive.github.io`,
or the same files from the site's **Downloads** page. The snippets use `pandas`; every one has a
standard-library equivalent, because the files are plain CSV and JSON Lines.

## The files, and how they fit together

| File | One row is... | Use it for |
|---|---|---|
| `stability.csv` | one measurement (one protocol, one model, one day) | the headline number and everything needed to judge it |
| `outcomes.csv` | one `(run_id, version, outcome)` count | the same outcome counts as `stability.csv`, already unpivoted |
| `open-lane/<year>.jsonl` | one trial: one question, one raw response | recomputing a number from scratch; text analysis of responses |
| `coverage.csv` | one scheduled job on one day | what ran, and why anything didn't |
| `croissant.json` | — | machine-readable schema, for tools that read Croissant metadata |

The join key is **`run_id`**. It is unique per measurement, it appears in `stability.csv`,
`outcomes.csv` and `open-lane/`, and it is the right thing to cite. `coverage.csv` has no `run_id`
because it describes jobs, not measurements.

## Load `stability.csv` correctly

A few columns hold a JSON value inside one CSV cell (`flags`, `outcomes`, `decisions`, `gates`,
`n_valid`, `scenario_gaps_pct`, `scenario_spread_pct`). Parse them once, right after loading:

```python
import json
import pandas as pd

JSON_COLUMNS = ["flags", "outcomes", "decisions", "gates", "n_valid",
                "scenario_gaps_pct", "scenario_spread_pct"]

stability = pd.read_csv("stability.csv")
for column in JSON_COLUMNS:
    stability[column] = stability[column].apply(lambda x: json.loads(x) if isinstance(x, str) else None)
```

**You should see** one row per measurement, `on_curve` and `at_noise_floor` as real booleans, and
`flags` as Python lists (an empty list when nothing applied).

> **A pandas trap:** read the column as `stability["flags"]`, never `stability.flags`.
> `DataFrame.flags` is a built-in pandas attribute, so the dot form silently returns something else.

Every column with `_pct` in its name is on a **0–100 scale**, never 0–1. This is a checked
convention, so you never need to guess.

## Filter before you chart

Four columns decide which rows belong in the same picture. Skipping any of them is the most common
way to get a wrong chart out of this data.

| Column | Filter on it because... |
|---|---|
| `series` | `commercial` and `open_weights` are never drawn as one line. The open-weights rows are a frozen reference model, measured to check the pipeline, not a second subject. |
| `model_family` | Never draw one line across model lines: a trend across a boundary between them is an artifact of which products happened to be measured when. |
| `protocol_id` | A protocol is one specific frozen panel. Different protocols are different questions; averaging them together has no meaning. `v0` and `v1` (see `n` and `grammar_version`) are different series of the same design — keep them apart. |
| `on_curve` | `true` marks the measurements that count toward the headline time series. A `false` row is still published in full, with the reason in `flags`. |

```python
headline = stability[(stability["series"] == "commercial") & stability["on_curve"]]
```

Do not treat `on_curve == False` as "bad data". It means "not headlined", for a reason you can
read: every flag takes a row off the main curve and no flag ever deletes one. Which flags a row
carries is often the most interesting thing about it:

```python
stability["flags"].explode().value_counts()
```

## Read a number with its evidence

A `stability_pct` on its own is not a result. The columns that say how much to trust it sit in the
same row:

| Question | Columns |
|---|---|
| How uncertain is it? | `ci_low_pct`, `ci_high_pct` — a 95% bootstrap interval, conditional on this one panel of 15 scenarios |
| Could it be sampling noise? | `null_floor_pct` — the gap you would expect between two samples of the *same* distribution at this `n` — and `at_noise_floor` |
| Did the control that should not move, move? | `gap_null_pct` (A against A′, the null change) |
| Did the control that should move, move? | `gap_positive_pct` (A against C); `not_reading` in `flags` if it did not |
| How many responses were lost? | `u_a`, `u_a_prime`, `u_b`, `u_c` (fractions), and `drop_bound_pct_ab` — the worst-case error those losses could cause |
| Is one scenario doing all the work? | `scenario_gaps_pct`, `scenario_concentration_pct`, and `scenario_dominated` in `flags` |

**Read `stability_pct` next to `null_floor_pct`, never above it.** With a finite `n`, even a model
that never changes its answer shows a gap of about `null_floor_pct`, so 100% is not the ceiling you
should expect and a difference smaller than the floor is not a finding. `scenario_dominated`
compares the share of the gap coming from the two worst scenarios with a threshold; it does not
look at how large the gap is, so when the gap is one lone response it is true by construction. Read
it together with `gap_pct` and `null_floor_pct`.

To look at the 15 scenario-level gaps behind one headline number as a table:

```python
row = stability[stability["run_id"] == "<a run_id from the file>"].iloc[0]
scenarios = pd.Series(row["scenario_gaps_pct"], name="gap_pct").rename_axis("scenario")
print(scenarios.sort_values(ascending=False))
```

A scenario with an empty gap had no gap computed for it; it is missing, not zero.

## Repeated measurements

The same protocol on the same model is measured again over time, so `(subject_model_id, protocol_id)`
is **not** a unique key — `run_id` is. To follow one protocol on one model over time, sort by
`run_date` (an absolute date, never a relative one) and plot the rows; to get the latest value per
pair:

```python
latest = (stability.sort_values("run_date")
                   .groupby(["subject_model_id", "protocol_id"], as_index=False)
                   .tail(1))
```

A single point is one frame of a video: the project's own design is the series, not any one
measurement. `replicate_index` marks an occasional same-window repeat used to check `null_floor_pct`
empirically; leave it out of a time series, or filter to `replicate_index == 0`.

## Outcomes: where the responses went

Every response lands in exactly one outcome from a closed list: `valid`, `unparseable`, `refused`,
`truncated`, `empty`, `off_format`, `blocked_upstream`. `outcomes.csv` is the same information as the
`outcomes` column, one row per count:

```python
outcomes = pd.read_csv("outcomes.csv")
by_version = outcomes.pivot_table(index=["run_id", "version"], columns="outcome",
                                  values="count", aggfunc="sum").reset_index()
by_version["valid_share"] = by_version["valid"] / by_version.drop(columns=["run_id", "version"]).sum(axis=1)
```

`version` is `A`, `A_prime`, `B` or `C`. A response that is not `valid` is a **lost response**, and it
is never counted as a decision: the drop bound exists to say how much the finding could change if
the lost responses had been valid. Comparing `valid_share` across versions of one run is the quickest
way to see whether missingness is even.

## Raw responses (`open-lane/`)

Only the two `open`-lane protocols publish their responses (`lane == "open"`); every other row of
`stability.csv` has no trial-level file, by design. A trial has `run_id`, `version`, `scenario_id`,
`rep`, `response_text`, `outcome`, `token`, and `prompt_sha256` — a one-way hash that lets you verify
a prompt you already hold but does not contain the question.

```python
trials = pd.read_json("open-lane/2026.jsonl", lines=True)      # one file per calendar year
valid = trials[trials["outcome"] == "valid"]
decisions = valid.groupby(["run_id", "version", "token"]).size().unstack("token", fill_value=0)
```

`decisions` should match the `decisions` column of the same `run_id` in `stability.csv`. To recompute
the headline number itself, follow [`reproducing.md`](reproducing.md), Step 3. If you find a run
where your recomputation disagrees with the published one, that is exactly what the project wants
reported.

## `coverage.csv`

One row per scheduled job per day: `job`, `date`, `ran`, and `cause`, which is empty when the job
ran and otherwise one value from a closed list. An empty `cause` reads as a missing value in pandas. A day with
no measurement is always explained here — never assume a missing day is a zero.

```python
coverage = pd.read_csv("coverage.csv")
coverage[~coverage["ran"]]["cause"].value_counts()
```

The file starts when tracking started; it is not backfilled.

## Mistakes that look like insights

- **Ranking models by `stability_pct`.** It is not a capability score, not comparable across model
  lines, and each row is one model on one frozen panel on one day.
- **Averaging across protocols** to get "the model's stability". Each protocol is a different frozen
  panel; the average has no defined meaning.
- **Reading a small gap without the floor.** Compare `gap_pct` with `null_floor_pct` and
  `gap_null_pct` first.
- **Dropping the flagged rows** as noise. They are published on purpose; the flag tells you why they
  are off the main curve.
- **Treating a missing value as zero** in `scenario_gaps_pct` or `coverage.csv`'s `cause`.
- **Mixing `commercial` and `open_weights`**, or `v0` and `v1`, in one line.
- **Blending a `comparison_point`** — someone else's run of the same panel on their model — into the
  project's own series. It stays in its own table, next to the main series and never merged into it.

## Reuse and citation

The CSVs and `open-lane/` are CC-BY-4.0: reuse, merge with your own datasets, or build on them
commercially, with attribution. Cite a specific `run_id` and `protocol_id` rather than the project as
a whole — see `CITATION.cff` in the repository. A citation that names a dated, hash-verified
measurement can be checked; "LLM-Archive found..." cannot.

If your tool reads Croissant metadata, `croissant.json` describes `stability.csv`, `outcomes.csv` and
`open-lane/` with a typed field for every column.
