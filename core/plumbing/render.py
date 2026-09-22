"""Turns experiments/*/measurement.json and .../trials.jsonl into the public CSVs and the
open-lane trace file -- the first pieces of spec.md §10's "Files" list.

    python3 -m core.plumbing.render build            [--experiments-dir experiments] [--out stability.csv]
    python3 -m core.plumbing.render build-outcomes    [--experiments-dir experiments] [--out outcomes.csv]
    python3 -m core.plumbing.render build-open-lane   [--experiments-dir experiments] [--out-dir .]
    python3 -m core.plumbing.render build-croissant   [--out croissant.json]
    python3 -m core.plumbing.render build-coverage    [--log state/coverage_log.jsonl] [--out coverage.csv]
    python3 -m core.plumbing.render verify

**stability.csv** -- one row per measurement, one column per core.measure.schema.MEASUREMENT_FIELDS
key -- that closed list is already the single source of truth for what's public about a
measurement (spec.md §10), so this module keeps no field list of its own to drift out of sync
with it. A field whose value is a dict or list (n_valid, outcomes, decisions, scenario_gaps_pct,
scenario_spread_pct, gates, flags) is written as one JSON cell rather than exploded into extra
columns -- spec.md doesn't yet say how those should be split into their own columns, and
guessing that here would be a real design decision, not a formatting one. A measurement missing a
field entirely writes an empty cell rather than failing the whole build -- this is a public
export, not an admission gate.

**outcomes.csv** -- the same `outcomes` field, unpacked instead of left as one JSON cell: one row
per (run_id, version, outcome), in core.measure.schema's own VERSIONS/OUTCOMES order. Nothing new
is decided here either -- it's the same data as the outcomes column of stability.csv, just in a
shape a spreadsheet or a plotting library can group by directly.

**open-lane/<year>.jsonl** -- for every measurement whose lane is "open" (and only those --
spec.md §10's lane table is what makes guard/sealed traces non-public), copies that run's
trials.jsonl records through verbatim, grouped by the run's year. No new field is added or
removed: the trial record already has the shape spec.md §10 wants published for the open lane.

**croissant.json** -- describes stability.csv, outcomes.csv, and open-lane/*.jsonl for tools that
read the MLCommons Croissant format. The two CSVs are each a `cr:FileObject`; open-lane/ is a
`cr:FileSet` (`includes: "open-lane/*.jsonl"`, no `containedIn` -- these files sit at the dataset
root, not inside an archive, and Croissant 1.0's FileSet doesn't require one) with its own
recordSet, TRIAL_FIELDS, sourced via `jsonPath` per field rather than `column` (JSON Lines, not
CSV). Every field's Croissant dataType comes from the same MEASUREMENT_FIELDS/OUTCOME_FIELDS/
TRIAL_FIELDS types this module already uses (pct/unit_interval -> sc:Float, int/count ->
sc:Integer, bool -> sc:Boolean, everything else -- including every JSON-cell field -- -> sc:Text)
-- one small, fixed mapping, not a per-field decision. Doesn't read experiments/ at all: unlike the
CSVs, this file only describes their shape, so it never changes unless the schema itself does.

**coverage.csv** -- one row per observation in `core.schedule.coverage`'s own append-only log
(`state/coverage_log.jsonl`): whether a due job ran, and if not, the one `schema.GAP_CAUSES` value
that honestly explains it. This module doesn't compute any of that itself, only formats the log --
see `core/schedule/coverage.py`'s docstring for why coverage can't be reconstructed further back
than the log itself goes, and which of the 4 jobs it currently covers.

Deliberately not doing yet: the HTML pages, the public/private split itself. Each is a separate
piece.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from core.measure import stats
from core.measure.chain import sha256_hex
from core.measure.schema import MEASUREMENT_FIELDS, OUTCOMES, VERSIONS
from core.schedule.coverage import DEFAULT_LOG as DEFAULT_COVERAGE_LOG
from core.schedule.coverage import load_observations

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPERIMENTS = ROOT / "experiments"
DEFAULT_PROTOCOLS = ROOT / "protocols"
DEFAULT_SUBJECT_MODELS = ROOT / "subject_models.yaml"
DEFAULT_OUT = ROOT / "stability.csv"
DEFAULT_OUTCOMES_OUT = ROOT / "outcomes.csv"
DEFAULT_OUT_DIR = ROOT
DEFAULT_CROISSANT_OUT = ROOT / "croissant.json"
DEFAULT_COVERAGE_OUT = ROOT / "coverage.csv"
DEFAULT_TEMPLATE = ROOT / "website" / "template.html"
DEFAULT_SITE_OUT = ROOT / "website" / "index.html"
# Bumped by hand, not on every commit -- a version that ticks up on every unrelated edit would
# mean nothing to a reader. Starts at v0.1 while the site is still settling; once it's stable,
# bump it when a batch of accumulated changes is worth flagging, not on a fixed schedule.
DEFAULT_VERSION_FILE = ROOT / "VERSION"
DEFAULT_CITATION_FILE = ROOT / "CITATION.cff"

# The two closed vocabularies the site's copy needs a friendly label for -- kept here (not in
# core/measure/schema.py) because these are display strings for the public site, not part of the
# measurement schema itself.
FAMILY_LABELS = {
    "risky_choice_framing": "Risky-choice framing",
    "sunk_cost_fallacy": "Sunk-cost fallacy",
    "base_rate_neglect": "Base-rate neglect",
}
TYPE_LABELS = {
    "wording": "Wording change",
    "anchoring": "Anchoring",
    "order": "Option order",
    "default": "Default option",
}

FIELDS = tuple(MEASUREMENT_FIELDS)
OUTCOME_FIELDS = ("run_id", "version", "outcome", "count")

# The exact keys core.measure.pilot.run() writes into every trials.jsonl line, in that order --
# kept here rather than re-derived, same reasoning as OUTCOME_FIELDS above: this module describes
# shapes, it doesn't own them. "index"/"rep" are the only integers; everything else is text
# (including "error"/"token"/"reason", which are text-or-null, still sc:Text -- Croissant has no
# separate nullable-text type).
TRIAL_FIELDS = (
    ("record_type", "text"),
    ("run_id", "text"),
    ("index", "count"),
    ("version", "text"),
    ("scenario_id", "text"),
    ("rep", "count"),
    ("prompt_sha256", "text"),
    ("response_text", "text"),
    ("stop_reason", "text"),
    ("returned_model_id", "text"),
    ("error", "text"),
    ("outcome", "text"),
    ("token", "text"),
    ("reason", "text"),
)

# schema.py's own field-type labels -> the Croissant/schema.org data type for that column.
# Everything not explicitly a number or a boolean (including every JSON-cell field) is sc:Text --
# one small, fixed mapping, so adding a field type later never means a new per-field decision here.
_CROISSANT_TYPE = {
    "int": "sc:Integer",
    "count": "sc:Integer",
    "pct": "sc:Float",
    "unit_interval": "sc:Float",
    "bool": "sc:Boolean",
}

CROISSANT_CONTEXT = {
    "@language": "en",
    "@vocab": "https://schema.org/",
    "sc": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "dct": "http://purl.org/dc/terms/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileProperty": "cr:fileProperty",
    "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}


def build_row(measurement: dict) -> dict[str, str]:
    row = {}
    for field in FIELDS:
        value = measurement.get(field)
        if value is None:
            row[field] = ""
        elif isinstance(value, (dict, list)):
            row[field] = json.dumps(value, sort_keys=True, ensure_ascii=False)
        else:
            row[field] = str(value)
    return row


def iter_measurement_files(experiments_dir: Path) -> list[Path]:
    return sorted(experiments_dir.glob("*/measurement.json"))


def load_measurements(experiments_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in iter_measurement_files(experiments_dir)]


def write_stability_csv(experiments_dir: Path, out_path: Path) -> int:
    """Writes one row per experiments/*/measurement.json, sorted by run_id so two builds of the
    same data diff cleanly. Returns the number of rows written."""
    measurements = sorted(load_measurements(experiments_dir), key=lambda m: m.get("run_id", ""))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for m in measurements:
            writer.writerow(build_row(m))
    return len(measurements)


def build_outcome_rows(measurement: dict) -> list[dict[str, str]]:
    outcomes = measurement.get("outcomes") or {}
    run_id = measurement.get("run_id", "")
    rows = []
    for version in VERSIONS:
        counts = outcomes.get(version) or {}
        for outcome in OUTCOMES:
            if outcome in counts:
                rows.append({"run_id": run_id, "version": version, "outcome": outcome, "count": str(counts[outcome])})
    return rows


def write_outcomes_csv(experiments_dir: Path, out_path: Path) -> int:
    """One row per (run_id, version, outcome), in schema order. Returns the number of rows written."""
    measurements = sorted(load_measurements(experiments_dir), key=lambda m: m.get("run_id", ""))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTCOME_FIELDS)
        writer.writeheader()
        n = 0
        for m in measurements:
            for row in build_outcome_rows(m):
                writer.writerow(row)
                n += 1
    return n


def open_lane_run_ids(experiments_dir: Path) -> dict[str, list[str]]:
    """year -> sorted run_ids whose measurement.json has lane == "open"."""
    by_year: dict[str, list[str]] = {}
    for m in load_measurements(experiments_dir):
        if m.get("lane") != "open":
            continue
        run_id = m.get("run_id", "")
        year = str(m.get("run_date", ""))[:4]
        by_year.setdefault(year, []).append(run_id)
    for run_ids in by_year.values():
        run_ids.sort()
    return by_year


def write_open_lane(experiments_dir: Path, out_dir: Path) -> dict[str, int]:
    """Writes <out_dir>/open-lane/<year>.jsonl, one line per trial, for every "open"-lane run.
    Returns {year: trial count written}."""
    by_year = open_lane_run_ids(experiments_dir)
    counts = {}
    lane_dir = out_dir / "open-lane"
    lane_dir.mkdir(parents=True, exist_ok=True)
    for year, run_ids in sorted(by_year.items()):
        trials = []
        for run_id in run_ids:
            trials_path = experiments_dir / run_id / "trials.jsonl"
            for line in trials_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    trials.append(json.loads(line))
        trials.sort(key=lambda t: (t.get("run_id", ""), t.get("index", 0)))
        out_path = lane_dir / f"{year}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for trial in trials:
                f.write(json.dumps(trial, sort_keys=True, ensure_ascii=False) + "\n")
        counts[year] = len(trials)
    return counts


def _croissant_field(record_set_id: str, file_id: str, column: str, field_type: str) -> dict:
    return {
        "@type": "cr:Field",
        "@id": f"{record_set_id}/{column}",
        "dataType": _CROISSANT_TYPE.get(field_type, "sc:Text"),
        "source": {"fileObject": {"@id": file_id}, "extract": {"column": column}},
    }


def _croissant_field_jsonpath(record_set_id: str, file_set_id: str, key: str, field_type: str) -> dict:
    """Same as _croissant_field, but for a JSON Lines source: a cr:FileSet (not cr:FileObject),
    extracted by jsonPath (one JSON object per line, not a CSV column)."""
    return {
        "@type": "cr:Field",
        "@id": f"{record_set_id}/{key}",
        "dataType": _CROISSANT_TYPE.get(field_type, "sc:Text"),
        "source": {"fileSet": {"@id": file_set_id}, "extract": {"jsonPath": f"$.{key}"}},
    }


def build_croissant() -> dict:
    """Describes stability.csv and outcomes.csv for Croissant-reading tools. Doesn't touch
    experiments/ -- it describes the two files' shape, which only ever changes when
    MEASUREMENT_FIELDS or OUTCOME_FIELDS does, not from run to run."""
    return {
        "@context": CROISSANT_CONTEXT,
        "@type": "sc:Dataset",
        "name": "LLM-Archive",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "description": "Whether a commercial language model's decision changes under an "
        "equivalent rewording of the same question -- measured on a fixed panel, published "
        "on every run.",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "distribution": [
            {
                "@type": "cr:FileObject",
                "@id": "stability.csv",
                "contentUrl": "stability.csv",
                "encodingFormat": "text/csv",
            },
            {
                "@type": "cr:FileObject",
                "@id": "outcomes.csv",
                "contentUrl": "outcomes.csv",
                "encodingFormat": "text/csv",
            },
            {
                "@type": "cr:FileSet",
                "@id": "open-lane",
                "description": "Full trial-level records (prompt hash, raw response text, "
                "extracted decision) for every measurement in the open lane, one JSON object "
                "per line, one file per year.",
                "encodingFormat": "application/jsonlines",
                "includes": "open-lane/*.jsonl",
            },
        ],
        "recordSet": [
            {
                "@type": "cr:RecordSet",
                "@id": "stability",
                "field": [
                    _croissant_field("stability", "stability.csv", column, field_type)
                    for column, field_type in MEASUREMENT_FIELDS.items()
                ],
            },
            {
                "@type": "cr:RecordSet",
                "@id": "outcomes",
                "field": [
                    _croissant_field("outcomes", "outcomes.csv", column, field_type)
                    for column, field_type in zip(OUTCOME_FIELDS, ("text", "text", "text", "count"))
                ],
            },
            {
                "@type": "cr:RecordSet",
                "@id": "open-lane-trials",
                "field": [
                    _croissant_field_jsonpath("open-lane-trials", "open-lane", key, field_type)
                    for key, field_type in TRIAL_FIELDS
                ],
            },
        ],
    }


def write_croissant(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(build_croissant(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- coverage.csv ------------------------------------------------------------------------------

COVERAGE_FIELDS = ("job", "date", "ran", "cause")


def write_coverage_csv(log_path: Path, out_path: Path) -> int:
    """One row per core.schedule.coverage observation, sorted by (date, job) so two builds of the
    same log diff cleanly. Not derived from experiments/ or any other source -- the log IS the
    coverage history, written the day each observation happened; see that module's docstring for
    why coverage.csv can't be reconstructed any further back than the log itself goes."""
    rows = sorted(load_observations(log_path), key=lambda r: (r["date"], r["job"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COVERAGE_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({"job": r["job"], "date": r["date"], "ran": str(r["ran"]), "cause": r["cause"] or ""})
    return len(rows)


# --- website/index.html -----------------------------------------------------------------------
# The last piece of spec.md §11's closed page list: turns website/template.html (the
# owner-approved mockup's CSS/JS/copy, lifted verbatim -- see website/README.md) into a real page
# by replacing its "@@TOKEN@@" placeholders with values computed from experiments/, protocols/ and
# subject_models.yaml. Deliberately reuses core.measure.stats.compare for the trend badge rather
# than re-deriving "did this improve" here -- that rule already exists in one place and this
# module must not duplicate it. Still not doing: the open-lane croissant fileSet, or the actual
# publish/deploy step.


def _family_of(protocol_id: str) -> str:
    return protocol_id.split("__", 1)[0]


def load_protocols(protocols_dir: Path) -> dict[str, dict]:
    protocols = {}
    for path in sorted(protocols_dir.glob("*.json")):
        protocol = json.loads(path.read_text(encoding="utf-8"))
        protocols[protocol["protocol_id"]] = protocol
    return protocols


def _history_per_protocol(measurements: list[dict]) -> dict[str, list[dict]]:
    """protocol_id -> its measurements, oldest first (by run_date, then run_id to break ties)."""
    by_protocol: dict[str, list[dict]] = {}
    for m in measurements:
        pid = m.get("protocol_id")
        if pid is None:
            continue
        by_protocol.setdefault(pid, []).append(m)
    for ms in by_protocol.values():
        ms.sort(key=lambda m: (m.get("run_date") or "", m.get("run_id") or ""))
    return by_protocol


def latest_measurement_per_protocol(measurements: list[dict]) -> dict[str, dict]:
    """protocol_id -> its most recent measurement -- the Results page's main table shows exactly
    one row per protocol, not the mockup's hardcoded "the 2030 rows"."""
    return {pid: ms[-1] for pid, ms in _history_per_protocol(measurements).items()}


def _trend_for(history: list[dict], current: dict) -> tuple[str, float | None]:
    """("base", None) if `current` is the first measurement ever taken of its protocol, otherwise
    (core.measure.stats.compare's verdict, the raw stability_pct delta) against the one right
    before it."""
    ids = [m.get("run_id") for m in history]
    idx = ids.index(current.get("run_id"))
    if idx == 0:
        return "base", None
    previous = history[idx - 1]
    if previous.get("stability_pct") is None or current.get("stability_pct") is None:
        return "base", None
    delta = current["stability_pct"] - previous["stability_pct"]
    return stats.compare(previous, current), delta


def _example_texts(protocol: dict, run_dir: Path) -> dict[str, str]:
    """The drawer's open-lane example: always the protocol's FIRST scenario (a fixed, arbitrary
    choice, documented in decisions.md -- not bias-relevant, since it doesn't affect any gate,
    threshold or which option is constructed as dominant). Answers come from the first matching
    trial actually recorded for that scenario, never invented."""
    scenarios = protocol.get("scenarios") or []
    if not scenarios:
        return {}
    scenario = scenarios[0]
    versions = scenario.get("versions", {})
    texts = {"qa": versions.get("A", ""), "qn": versions.get("A_prime", ""),
             "qb": versions.get("B", ""), "qg": versions.get("C", "")}
    answers = {"aa": "", "an": "", "ab": "", "ag": ""}
    key_by_version = {"A": "aa", "A_prime": "an", "B": "ab", "C": "ag"}
    trials_path = run_dir / "trials.jsonl"
    if trials_path.exists():
        for line in trials_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            trial = json.loads(line)
            if trial.get("scenario_id") != scenario.get("scenario_id"):
                continue
            key = key_by_version.get(trial.get("version"))
            if key and not answers[key] and trial.get("response_text"):
                answers[key] = trial["response_text"]
    texts.update(answers)
    return texts


def build_site_row(
    measurement: dict, protocol: dict | None, trend: str, trend_delta: float | None, experiments_dir: Path
) -> dict:
    """One row for the results table / drawer's JS data array -- real field names throughout
    (no mockup-style abbreviation layer), so the browser-side code reads the same names this
    module and core/measure/schema.py already use."""
    pid = measurement.get("protocol_id", "")
    row = {
        "run_id": measurement.get("run_id"),
        "run_date": measurement.get("run_date"),
        "protocol_id": pid,
        "rewording_type": measurement.get("rewording_type"),
        "family": _family_of(pid),
        "lane": measurement.get("lane"),
        "series": measurement.get("series"),
        "subject_model_id": measurement.get("subject_model_id"),
        "stability_pct": measurement.get("stability_pct"),
        "ci_low_pct": measurement.get("ci_low_pct"),
        "ci_high_pct": measurement.get("ci_high_pct"),
        "gap_pct": measurement.get("gap_pct"),
        "gap_null_pct": measurement.get("gap_null_pct"),
        "gap_positive_pct": measurement.get("gap_positive_pct"),
        "null_floor_pct": measurement.get("null_floor_pct"),
        "n": measurement.get("n"),
        "n_items": measurement.get("n_items"),
        "entropy_a": measurement.get("entropy_a"),
        "entropy_b": measurement.get("entropy_b"),
        "u_a": measurement.get("u_a"),
        "u_a_prime": measurement.get("u_a_prime"),
        "u_b": measurement.get("u_b"),
        "u_c": measurement.get("u_c"),
        "drop_bound_pct_ab": measurement.get("drop_bound_pct_ab"),
        "drop_bound_pct_aa": measurement.get("drop_bound_pct_aa"),
        "drop_bound_pct_ac": measurement.get("drop_bound_pct_ac"),
        "flags": measurement.get("flags") or [],
        "on_curve": bool(measurement.get("on_curve")),
        "gates": measurement.get("gates") or {},
        "panel_sha256": measurement.get("panel_sha256", ""),
        "protocol_sha256": measurement.get("protocol_sha256", ""),
        "extractor_sha": measurement.get("extractor_sha", ""),
        "scenario_gaps_pct": [v for _, v in sorted((measurement.get("scenario_gaps_pct") or {}).items())],
        "scenario_concentration_pct": measurement.get("scenario_concentration_pct"),
        "trend": trend,
        "trend_delta": trend_delta,
    }
    if measurement.get("lane") == "open" and protocol is not None:
        row.update(_example_texts(protocol, experiments_dir / (measurement.get("run_id") or "")))
    return row


def _indented_block(lines: list[str], header: str) -> list[str]:
    """Every line strictly more indented than the (comment-stripped) line that is exactly
    `header`, stopping at the first line back at that indentation or shallower. Indentation-scoped
    rather than a fixed substring search, so it doesn't get confused by "commercial" appearing in
    a comment elsewhere in the file."""
    header_indent = None
    block: list[str] = []
    for line in lines:
        code = line.split("#", 1)[0]
        stripped = code.strip()
        if header_indent is None:
            if stripped == header:
                header_indent = len(code) - len(code.lstrip(" "))
            continue
        if stripped:
            indent = len(code) - len(code.lstrip(" "))
            if indent <= header_indent:
                break
        block.append(code)
    return block


def _load_active_series_model(path: Path, series_key: str) -> dict | None:
    """Hand-parses subject_models.yaml for one series ('commercial:' or 'open_weights:') for a
    generation with status "active" -- same "extract only the one shape this needs" approach as
    core/schedule/schedule.py's cadence.yaml reader, so this module doesn't need a YAML dependency
    for one field. Returns None if the file is missing or no generation of that series is active."""
    if not path.exists():
        return None
    block = _indented_block(path.read_text(encoding="utf-8").splitlines(), series_key)
    if not block:
        return None

    def finish(gen: dict[str, str]) -> dict[str, str] | None:
        model_id = gen.get("model_id", "").strip("\"'")
        if gen.get("status") != "active" or not model_id or model_id == "null":
            return None
        return {"model_id": model_id, "model_family": gen.get("model_family", "").strip("\"'")}

    current: dict[str, str] = {}
    result: dict[str, str] | None = None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("- model_id:"):
            result = finish(current) or result
            current = {}
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.lstrip("- ").strip()
        if key in ("model_id", "model_family", "status", "declared_successor"):
            current[key] = value.strip()
    return finish(current) or result


def load_active_commercial_model(path: Path) -> dict | None:
    """model_id stays null until the first real run -- see subject_models.yaml's own comment."""
    return _load_active_series_model(path, "commercial:")


def load_active_open_weights_model(path: Path) -> dict | None:
    """The frozen reference/instrument model (spec.md §7) -- a Phase 0 pin that "does not change"
    (core/plumbing/reference_client.py's own docstring), read from the same source of truth as
    the commercial line above rather than hardcoded into the site, so the two can't drift apart."""
    return _load_active_series_model(path, "open_weights:")


def load_citation(path: Path) -> dict | None:
    """Hand-parses CITATION.cff for the handful of top-level scalars the site's "How to cite it"
    block needs (title, version, date-released, family-names/given-names, and the first
    identifier's DOI) -- same "no YAML dependency for a few fields" approach as
    _load_active_series_model, since this file's shape is simple, flat, and hand-maintained, not
    machine-written. Returns None if the file is missing or version/DOI are still `TODO` -- true
    before the first formally tagged, Zenodo-archived release exists (tools/cut_release.py)."""
    if not path.exists():
        return None
    result: dict[str, str | None] = {
        "title": None, "version": None, "date_released": None, "doi": None,
        "family_names": None, "given_names": None,
    }
    in_identifiers = False
    for line in path.read_text(encoding="utf-8").splitlines():
        # YAML list items ("  - family-names: Kalognomos") carry a "- " prefix the second and
        # later fields of the same item don't ("    given-names: Michalis") -- stripped alone
        # wouldn't match startswith("family-names:") for the first, so both are normalized the
        # same way before comparing.
        stripped = line.strip().lstrip("- ")
        if stripped.startswith("title:"):
            result["title"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("version:"):
            result["version"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("date-released:"):
            result["date_released"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("family-names:"):
            result["family_names"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("given-names:"):
            result["given_names"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped == "identifiers:":
            in_identifiers = True
        elif in_identifiers and stripped.startswith("value:"):
            result["doi"] = stripped.split(":", 1)[1].strip().strip("\"'")
            in_identifiers = False  # only the first identifier (the concept DOI) is used

    if not result["version"] or result["version"] == "TODO" or not result["doi"] or result["doi"] == "TODO":
        return None
    return result


def build_site_data(
    experiments_dir: Path, protocols_dir: Path, subject_models_path: Path,
    citation_path: Path = DEFAULT_CITATION_FILE,
) -> dict:
    protocols = load_protocols(protocols_dir)
    measurements = load_measurements(experiments_dir)
    history = _history_per_protocol(measurements)
    latest = latest_measurement_per_protocol(measurements)

    rows = []
    for pid, m in sorted(latest.items()):
        trend, delta = _trend_for(history[pid], m)
        rows.append(build_site_row(m, protocols.get(pid), trend, delta, experiments_dir))

    admitted = {pid: p for pid, p in protocols.items() if p.get("status") == "admitted"}
    # commercial only: the coverage bar names the active *commercial* model (load_active_commercial_model
    # below), so what it counts must match -- an open_weights-only measurement (the daily reference-model
    # self-check) must never inflate the count next to that model's name. spec.md's commercial/open_weights
    # split is never supposed to mix into one number; this is that rule applied to the coverage bar itself.
    measured_protocol_ids = {m.get("protocol_id") for m in measurements if m.get("series") == "commercial"} & set(admitted)

    return {
        "rows": [r for r in rows if r["series"] == "commercial"],
        "orows": [r for r in rows if r["series"] == "open_weights"],
        "admitted_count": len(admitted),
        # Admitted only, never every file in protocols/. This is the denominator of the public
        # coverage bar ("N of TOTAL panels measured"), and a `candidate` protocol has by
        # definition not been through gates 2 and 4 -- it is a draft that no scheduler will run
        # (core/schedule/run_due.py skips any status != admitted) and that no measurement can
        # cite. Counting drafts here would silently deflate the published coverage figure the
        # moment a next-generation panel set is drafted alongside the one in rotation: with the
        # 12 v1 candidates on disk and 9 v0 protocols measured, the bar would have read
        # "9 of 24 panels measured" instead of "9 of 12" (decisions.md §49).
        "protocol_count": len(admitted),
        "measured_count": len(measured_protocol_ids),
        "active_model": load_active_commercial_model(subject_models_path),
        "reference_model": load_active_open_weights_model(subject_models_path),
        "citation": load_citation(citation_path),
        "last_run_date": max((m.get("run_date") for m in measurements if m.get("run_date")), default=None),
        "history": history,
        "protocols": protocols,
    }


_BIBTEX_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def render_citation_bibtex(data: dict) -> str:
    """The real @software entry, from CITATION.cff (data["citation"], load_citation) -- not
    hardcoded here, for the same reason render_reference_model_id isn't: a static placeholder like
    the old `year = {2030}, doi = {10.5281/zenodo.XXXXXXX}` silently stays wrong forever once a
    real release exists (found and fixed the same session `temperature`'s equivalent staleness
    was). Falls back to a plain, honest sentence when no formally tagged release exists yet --
    never a fabricated DOI, same "TODO until it's true" rule CITATION.cff's own fields follow."""
    c = data["citation"]
    if c is None:
        return (
            "<p>No formally tagged, Zenodo-archived release exists yet. Until then, cite the "
            'repository directly: <a href="https://github.com/LLM-Archive/llm-archive.github.io">'
            "github.com/LLM-Archive/llm-archive.github.io</a>.</p>"
        )
    year, month_num, _ = c["date_released"].split("-")
    month = _BIBTEX_MONTHS[int(month_num) - 1]
    key = f"{(c['family_names'] or 'llm_archive').lower()}_{year}_{c['doi'].rsplit('.', 1)[-1]}"
    author = f"{c['family_names']}, {c['given_names']}" if c["family_names"] else "LLM-Archive"
    return (
        f"<pre>@software{{{key},\n"
        f"  author       = {{{author}}},\n"
        f"  title        = {{{c['title']}}},\n"
        f"  month        = {month},\n"
        f"  year         = {year},\n"
        "  publisher    = {Zenodo},\n"
        f"  version      = {{{c['version']}}},\n"
        f"  doi          = {{{c['doi']}}},\n"
        f"  url          = {{https://doi.org/{c['doi']}}}\n"
        "}</pre>"
    )


def render_reference_model_id(data: dict) -> str:
    """The frozen instrument model's id, read from subject_models.yaml's open_weights series
    (data["reference_model"], from load_active_open_weights_model) rather than hardcoded here --
    same reasoning as render_coverage_bar's active_model: a static string in template.html can
    drift from what the code actually pins, the way `temperature` once did (see CHANGELOG)."""
    ref = data["reference_model"]
    return ref["model_id"] if ref else "not pinned yet"


def render_commercial_model_id(data: dict) -> str:
    """The commercial model actually being tracked, read from subject_models.yaml's commercial
    series (data["active_model"], from load_active_commercial_model) -- same reasoning and the
    same data render_coverage_bar's own badge already names, just surfaced here too so the results
    table itself says which model produced the rows above it, the way the open-weights table
    already names its own model just above it (render_reference_model_id)."""
    active = data["active_model"]
    return active["model_id"] if active else "not active yet"


def render_coverage_bar(data: dict) -> str:
    """spec.md §11: "The renderer rejects a homepage with no coverage/status bar." The actual
    reject case is raised by build_site_html below (no protocols at all); this function always
    renders *some* bar, but never presents test-fixture data as if it were a real tracked model --
    see load_active_commercial_model's docstring for why that distinction matters here."""
    admitted, total, measured = data["admitted_count"], data["protocol_count"], data["measured_count"]
    active = data["active_model"]
    if active is None:
        return (
            '<div class="cov"><span class="dot" aria-hidden="true"></span>'
            "<b>Not yet measuring a real model.</b> This is a pre-launch build of the pipeline — "
            f"<b>{admitted} of {total}</b> panels admitted, <b>{measured} of {total}</b> exercised so far, "
            "all against a synthetic test client, never a real subject model."
            '<div style="margin-top:7px">Real, public numbers begin once a real API client and the '
            "first paid run exist · "
            "<a href=\"#\" onclick=\"go('how');return false\">what this means</a></div></div>"
        )
    last = data["last_run_date"] or "—"
    return (
        '<div class="cov"><span class="dot" aria-hidden="true"></span><b>Currently measuring:</b> '
        f'<code>{active["model_id"]}</code> · last full scan <b>{last}</b> · '
        f"<b>{measured} of {total}</b> panels measured"
        '<div style="margin-top:7px">We ask it under fixed settings and with no data of our own. '
        "This shows only how it answered here — not how it will behave in your product · "
        "<a href=\"#\" onclick=\"go('how');return false\">what this means</a></div></div>"
    )


def _family_series_history(data: dict) -> dict[tuple[str, str], list[dict]]:
    """(series, family) -> every measurement of that family+series, across all its rewording
    types, in run_date order -- the trend chart's real replacement for the mockup's hand-typed
    per-family lines. Never mixes two different families or two different series on one line."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for pid, ms in data["history"].items():
        if pid not in data["protocols"]:
            continue
        for m in ms:
            key = (m.get("series"), _family_of(pid))
            grouped.setdefault(key, []).append(m)
    for ms in grouped.values():
        ms.sort(key=lambda m: (m.get("run_date") or "", m.get("run_id") or ""))
    return grouped


def _render_one_trend_chart(series: str, family: str, ms: list[dict]) -> str:
    by_type: dict[str, list[dict]] = {}
    for m in ms:
        by_type.setdefault(m.get("rewording_type"), []).append(m)
    dates = sorted({m["run_date"] for m in ms if m.get("run_date")})
    left, right, top, bottom, width = 70, 640, 30, 236, 800
    x_of = {d: left + i * (right - left) / (len(dates) - 1) for i, d in enumerate(dates)} if len(dates) > 1 else {dates[0]: left}
    values = [m["stability_pct"] for m in ms if m.get("stability_pct") is not None]
    axis_min = max(0, 10 * ((min(values) - 1) // 10)) if values else 0

    def y_of(v: float) -> float:
        return bottom - (v - axis_min) / (100 - axis_min) * (bottom - top) if axis_min < 100 else bottom

    colors = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)"]
    lines, labels = [], []
    for i, (rtype, type_ms) in enumerate(sorted(by_type.items())):
        known = [m for m in type_ms if m.get("stability_pct") is not None]
        # A single point can't show a trend line -- skip it rather than draw a zero-length
        # polyline that would render as an invisible dot.
        if len(known) < 2:
            continue
        color = colors[i % len(colors)]
        pts = " ".join(f"{x_of[m['run_date']]:.1f},{y_of(m['stability_pct']):.1f}" for m in known)
        lines.append(f'<polyline stroke="{color}" points="{pts}"/>')
        # Placed at the line's own last point, just right of the chart -- same convention
        # mockup.html used by hand. Without x/y this text falls back to (0,0) and is clipped
        # by the viewBox, which is what it did before this fix.
        label_y = y_of(known[-1]["stability_pct"])
        labels.append(f'<text x="{right + 12:.1f}" y="{label_y:.1f}" fill="{color}">{TYPE_LABELS.get(rtype, rtype)} {known[-1]["stability_pct"]:.0f}%</text>')
    date_labels = "".join(f'<text x="{x_of[d]:.1f}" y="268">{d}</text>' for d in dates)
    series_label = "commercial" if series == "commercial" else "open weights"
    title = f"{FAMILY_LABELS.get(family, family)} — {series_label}"
    return (
        f"<h3>How it's changed over time — {title}</h3>"
        '<figure><div class="scroll">'
        f'<svg viewBox="0 0 {width} 300" width="{width}" role="img" aria-label="{title} stability over time">'
        '<g stroke="var(--line)" stroke-width="1">'
        f'<line x1="{left}" y1="{top}" x2="{right}" y2="{top}"/>'
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/></g>'
        f'<g fill="var(--dim)" font-size="13" text-anchor="middle" font-family="system-ui,sans-serif">{date_labels}</g>'
        f'<g fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{"".join(lines)}</g>'
        f'<g font-size="12" font-family="system-ui,sans-serif" font-weight="600">{"".join(labels)}</g>'
        "</svg></div></figure>"
    )


def render_trend_section(data: dict) -> str:
    """Only draws a chart for a (series, family) once it has >=2 distinct run_dates -- otherwise a
    single point would look like a trend that isn't there yet. Today every kept run is from the
    same date, so this always renders the placeholder -- documented in decisions.md as a
    deliberate default, not a silent guess."""
    grouped = _family_series_history(data)
    charts = [
        _render_one_trend_chart(series, family, ms)
        for (series, family), ms in sorted(grouped.items())
        if len({m.get("run_date") for m in ms if m.get("run_date")}) >= 2
    ]
    if not charts:
        return (
            "<h3>How it changes over time</h3>"
            '<div class="callout"><p>Not enough history yet — each test currently has just one '
            "measurement. A trend needs at least two dates for the same test on the same model "
            "series; check back once the pipeline has been running for a while.</p></div>"
        )
    return "\n".join(charts)


def render_biggest_move_section(data: dict) -> str:
    """The largest regression seen between two consecutive measurements of the same protocol, if
    any -- empty string (nothing rendered) if every protocol either has no history yet or never
    regressed, rather than manufacturing a claim from a single run."""
    worst: tuple[float, dict] | None = None
    for ms in data["history"].values():
        for i in range(1, len(ms)):
            prev, cur = ms[i - 1], ms[i]
            if prev.get("stability_pct") is None or cur.get("stability_pct") is None:
                continue
            delta = cur["stability_pct"] - prev["stability_pct"]
            if worst is None or delta < worst[0]:
                worst = (delta, cur)
    if worst is None or worst[0] >= 0:
        return ""
    delta, cur = worst
    family = _family_of(cur.get("protocol_id", ""))
    label = f"{FAMILY_LABELS.get(family, family)}: {TYPE_LABELS.get(cur.get('rewording_type'), cur.get('rewording_type'))}"
    return (
        "<h3>Where we saw the biggest change</h3>"
        '<div class="callout" style="border-color:var(--bad)"><p><b>In the '
        f'"{label}" test, stability dropped {abs(delta):.1f} points</b> on {cur.get("run_date")} — '
        "the biggest drop we've seen so far. Open the row in the table above to see exactly which "
        "question caused it.</p></div>"
    )


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _file_manifest(data_dir: Path, open_lane_years: list[str]) -> list[tuple[str, str, Path]]:
    entries = [
        ("stability.csv", "The series — one row per measurement", data_dir / "stability.csv"),
        ("outcomes.csv", "How many answers were valid, how many refusals or errors", data_dir / "outcomes.csv"),
        ("croissant.json", "Machine-readable description of the two CSVs above (MLCommons Croissant)", data_dir / "croissant.json"),
        ("coverage.csv", "What was scheduled, what's missing, and why", data_dir / "coverage.csv"),
    ]
    for year in open_lane_years:
        entries.append(
            (f"open-lane/{year}.jsonl", "Full trial-level answers for the tests that are already open", data_dir / "open-lane" / f"{year}.jsonl")
        )
    return entries


def _rel_prefix(data_dir: Path, out_dir: Path) -> str:
    """The path prefix a link inside a page in out_dir needs to reach a file in data_dir -- e.g.
    "../" when the page lives in website/ and the data one level up, at the repo root (today's
    real layout: --out defaults to website/index.html, --data-dir to the repo root). Computed
    rather than hardcoded, since both are overridable CLI flags and a wrong relative link is
    silent until someone actually clicks it -- which is exactly how this was found."""
    rel = os.path.relpath(data_dir, start=out_dir)
    return "" if rel == "." else rel + "/"


def render_primary_download(data_dir: Path, out_dir: Path) -> str:
    path = data_dir / "stability.csv"
    href = _rel_prefix(data_dir, out_dir) + "stability.csv"
    # Without `download`, a browser navigates the current tab to the CSV and renders it as text
    # instead of saving it -- reaching the site again means hitting Back. The attribute name is
    # also the suggested filename, so it stays "stability.csv" even though href carries the
    # "../" prefix.
    if path.exists():
        return f'<a class="dl" href="{href}" download="stability.csv">↓ stability.csv <span>{_human_size(path.stat().st_size)}</span></a>'
    return f'<a class="dl" href="{href}" download="stability.csv">↓ stability.csv <span>not built yet</span></a>'


def render_file_rows(data_dir: Path, out_dir: Path, open_lane_years: list[str]) -> str:
    rows = []
    prefix = _rel_prefix(data_dir, out_dir)
    for name, desc, path in _file_manifest(data_dir, open_lane_years):
        if path.exists():
            size = _human_size(path.stat().st_size)
            digest = sha256_hex(path.read_bytes())[:16] + "…"
            filename = name.rsplit("/", 1)[-1]
            name_cell = f'<a class="filedl" href="{prefix}{name}" download="{filename}"><code>↓ {name}</code></a>'
        else:
            size, digest = "not built yet", "—"
            name_cell = f'<code>{name}</code>'
        rows.append(f'<tr><td>{name_cell}</td><td>{desc}</td><td class="num">{size}</td><td class="hash">{digest}</td></tr>')
    return "\n".join(rows)


def build_site_html(
    experiments_dir: Path, protocols_dir: Path, subject_models_path: Path, data_dir: Path, template_path: Path,
    out_dir: Path | None = None, version_path: Path = DEFAULT_VERSION_FILE, citation_path: Path = DEFAULT_CITATION_FILE,
) -> str:
    data = build_site_data(experiments_dir, protocols_dir, subject_models_path, citation_path)
    if data["protocol_count"] == 0:
        raise RuntimeError(
            "cannot build the homepage: no protocols found to compute the coverage/status bar from "
            "(spec.md §11: \"The renderer rejects a homepage with no coverage/status bar.\")"
        )

    # Defaults to data_dir itself (no prefix needed) rather than DEFAULT_SITE_OUT.parent, so a
    # caller that doesn't pass out_dir (both existing verify() smoke builds below) still gets a
    # working, if untested, relative link instead of silently assuming the real deploy layout.
    out_dir = out_dir if out_dir is not None else data_dir
    open_lane_years = sorted(open_lane_run_ids(experiments_dir))
    replacements = {
        "@@COVERAGE_BAR@@": render_coverage_bar(data),
        "@@REFERENCE_MODEL_ID@@": render_reference_model_id(data),
        "@@COMMERCIAL_MODEL_ID@@": render_commercial_model_id(data),
        "@@CITATION_BIBTEX@@": render_citation_bibtex(data),
        "@@TREND_SECTION@@": render_trend_section(data),
        "@@BIGGEST_MOVE_SECTION@@": render_biggest_move_section(data),
        "@@PRIMARY_DOWNLOAD@@": render_primary_download(data_dir, out_dir),
        "@@FILE_ROWS@@": render_file_rows(data_dir, out_dir, open_lane_years),
        "@@SITE_VERSION@@": version_path.read_text(encoding="utf-8").strip(),
        "@@COPYRIGHT_YEAR@@": str(date.today().year),
        "/*@@ROWS_JSON@@*/[]": json.dumps(data["rows"], sort_keys=True, ensure_ascii=False),
        "/*@@OROWS_JSON@@*/[]": json.dumps(data["orows"], sort_keys=True, ensure_ascii=False),
    }
    html = template_path.read_text(encoding="utf-8")
    for token, value in replacements.items():
        if token not in html:
            raise RuntimeError(f"{template_path} is missing the {token} placeholder")
        html = html.replace(token, value)
    return html


def write_site_html(
    experiments_dir: Path, protocols_dir: Path, subject_models_path: Path, data_dir: Path, out_path: Path, template_path: Path,
    version_path: Path = DEFAULT_VERSION_FILE, citation_path: Path = DEFAULT_CITATION_FILE,
) -> None:
    html = build_site_html(
        experiments_dir, protocols_dir, subject_models_path, data_dir, template_path, out_path.parent,
        version_path, citation_path,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def _verify() -> list[str]:
    errors = []

    row = build_row({"record_type": "measurement", "run_id": "r0", "stability_pct": 82.2, "on_curve": False})
    if row["record_type"] != "measurement" or row["on_curve"] != "False":
        errors.append(f"build_row scalar case: got {row}")
    if row["schema_version"] != "":
        errors.append(f"build_row missing-field case: got {row['schema_version']!r}, want ''")

    row = build_row({"flags": ["a", "b"], "n_valid": {"A": 1, "B": 2}})
    if json.loads(row["flags"]) != ["a", "b"]:
        errors.append(f"build_row list case: got {row['flags']!r}")
    if json.loads(row["n_valid"]) != {"A": 1, "B": 2}:
        errors.append(f"build_row dict case: got {row['n_valid']!r}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for run_id in ("b_run", "a_run"):
            d = tmp / "experiments" / run_id
            d.mkdir(parents=True)
            (d / "measurement.json").write_text(
                json.dumps({"record_type": "measurement", "run_id": run_id}), encoding="utf-8"
            )
        out = tmp / "stability.csv"
        n = write_stability_csv(tmp / "experiments", out)
        if n != 2:
            errors.append(f"write_stability_csv row count: got {n}, want 2")
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        got_order = [r["run_id"] for r in rows]
        if got_order != ["a_run", "b_run"]:
            errors.append(f"write_stability_csv sort order: got {got_order}, want ['a_run', 'b_run']")

    outcome_rows = build_outcome_rows({"run_id": "r0", "outcomes": {"A": {"valid": 3, "refused": 1}}})
    want = [
        {"run_id": "r0", "version": "A", "outcome": "valid", "count": "3"},
        {"run_id": "r0", "version": "A", "outcome": "refused", "count": "1"},
    ]
    if outcome_rows != want:
        errors.append(f"build_outcome_rows: got {outcome_rows}, want {want}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        exp = tmp / "experiments"
        cases = {
            "open_run": ("open", "2026", [{"index": 1, "run_id": "open_run"}, {"index": 0, "run_id": "open_run"}]),
            "guard_run": ("guard", "2026", [{"index": 0, "run_id": "guard_run"}]),
        }
        for run_id, (lane, year, trials) in cases.items():
            d = exp / run_id
            d.mkdir(parents=True)
            (d / "measurement.json").write_text(
                json.dumps({"record_type": "measurement", "run_id": run_id, "lane": lane, "run_date": f"{year}-01-01"}),
                encoding="utf-8",
            )
            with (d / "trials.jsonl").open("w", encoding="utf-8") as f:
                for t in trials:
                    f.write(json.dumps(t) + "\n")

        counts = write_open_lane(exp, tmp)
        if counts != {"2026": 2}:
            errors.append(f"write_open_lane counts: got {counts}, want {{'2026': 2}} (guard run must be excluded)")
        written = [json.loads(line) for line in (tmp / "open-lane" / "2026.jsonl").read_text(encoding="utf-8").splitlines()]
        if [t["index"] for t in written] != [0, 1]:
            errors.append(f"write_open_lane order: got indexes {[t['index'] for t in written]}, want [0, 1]")
        if any(t["run_id"] == "guard_run" for t in written):
            errors.append("write_open_lane: a guard-lane trial leaked into the open-lane file")

    croissant = build_croissant()
    if croissant.get("@type") != "sc:Dataset":
        errors.append(f"build_croissant: @type is {croissant.get('@type')!r}, want 'sc:Dataset'")
    file_ids = {f["@id"] for f in croissant["distribution"]}
    if file_ids != {"stability.csv", "outcomes.csv", "open-lane"}:
        errors.append(f"build_croissant distribution ids: got {file_ids}")
    open_lane_dist = next(f for f in croissant["distribution"] if f["@id"] == "open-lane")
    if open_lane_dist["@type"] != "cr:FileSet":
        errors.append(f"build_croissant: open-lane @type is {open_lane_dist['@type']!r}, want cr:FileSet")
    if "containedIn" in open_lane_dist:
        errors.append("build_croissant: open-lane FileSet shouldn't have containedIn -- it's not inside an archive")
    if open_lane_dist.get("includes") != "open-lane/*.jsonl":
        errors.append(f"build_croissant: open-lane includes is {open_lane_dist.get('includes')!r}, want 'open-lane/*.jsonl'")
    record_sets = {r["@id"]: r for r in croissant["recordSet"]}
    if len(record_sets["stability"]["field"]) != len(MEASUREMENT_FIELDS):
        errors.append("build_croissant: stability recordSet field count doesn't match MEASUREMENT_FIELDS")
    if len(record_sets["outcomes"]["field"]) != len(OUTCOME_FIELDS):
        errors.append("build_croissant: outcomes recordSet field count doesn't match OUTCOME_FIELDS")
    if len(record_sets["open-lane-trials"]["field"]) != len(TRIAL_FIELDS):
        errors.append("build_croissant: open-lane-trials recordSet field count doesn't match TRIAL_FIELDS")
    count_field = next(f for f in record_sets["outcomes"]["field"] if f["@id"] == "outcomes/count")
    if count_field["dataType"] != "sc:Integer":
        errors.append(f"build_croissant: outcomes/count dataType is {count_field['dataType']!r}, want sc:Integer")
    pct_field = next(f for f in record_sets["stability"]["field"] if f["@id"] == "stability/stability_pct")
    if pct_field["dataType"] != "sc:Float":
        errors.append(f"build_croissant: stability_pct dataType is {pct_field['dataType']!r}, want sc:Float")
    index_field = next(f for f in record_sets["open-lane-trials"]["field"] if f["@id"] == "open-lane-trials/index")
    if index_field["dataType"] != "sc:Integer":
        errors.append(f"build_croissant: open-lane-trials/index dataType is {index_field['dataType']!r}, want sc:Integer")
    if index_field["source"] != {"fileSet": {"@id": "open-lane"}, "extract": {"jsonPath": "$.index"}}:
        errors.append(f"build_croissant: open-lane-trials/index source is wrong: {index_field['source']}")

    # --- website/index.html -----------------------------------------------------------------

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pending_yaml = (
            "series:\n"
            "  commercial:  # commercial models\n"
            "    tier: sonnet-class\n"
            "    generations:\n"
            "      - model_id: null\n"
            "        model_family: null\n"
            "        status: pending\n"
            "\n"
            "  open_weights:\n"
            "    tier: null\n"
        )
        active_yaml = pending_yaml.replace("model_id: null", "model_id: claude-sonnet-5", 1).replace(
            "model_family: null", "model_family: claude-sonnet", 1
        ).replace("status: pending", "status: active", 1)
        (tmp / "pending.yaml").write_text(pending_yaml, encoding="utf-8")
        (tmp / "active.yaml").write_text(active_yaml, encoding="utf-8")
        (tmp / "missing.yaml")  # never written
        open_weights_active_yaml = active_yaml + (
            "    generations:\n"
            "      - model_id: qwen2.5-1.5b-instruct-q4_k_m\n"
            "        model_family: qwen2.5\n"
            "        status: active\n"
        )
        (tmp / "open_weights_active.yaml").write_text(open_weights_active_yaml, encoding="utf-8")

        if load_active_commercial_model(tmp / "missing.yaml") is not None:
            errors.append("load_active_commercial_model: missing file should return None")
        if load_active_commercial_model(tmp / "pending.yaml") is not None:
            errors.append("load_active_commercial_model: status 'pending' should return None")
        active = load_active_commercial_model(tmp / "active.yaml")
        if active != {"model_id": "claude-sonnet-5", "model_family": "claude-sonnet"}:
            errors.append(f"load_active_commercial_model: got {active!r}")

        if load_active_open_weights_model(tmp / "pending.yaml") is not None:
            errors.append("load_active_open_weights_model: no open_weights generation should return None")
        ref = load_active_open_weights_model(tmp / "open_weights_active.yaml")
        if ref != {"model_id": "qwen2.5-1.5b-instruct-q4_k_m", "model_family": "qwen2.5"}:
            errors.append(f"load_active_open_weights_model: got {ref!r}")
        if render_reference_model_id({"reference_model": None}) != "not pinned yet":
            errors.append("render_reference_model_id: None case should say 'not pinned yet'")
        if render_reference_model_id({"reference_model": ref}) != "qwen2.5-1.5b-instruct-q4_k_m":
            errors.append("render_reference_model_id: didn't return the pinned model_id")
        if render_commercial_model_id({"active_model": None}) != "not active yet":
            errors.append("render_commercial_model_id: None case should say 'not active yet'")
        if render_commercial_model_id({"active_model": active}) != "claude-sonnet-5":
            errors.append("render_commercial_model_id: didn't return the active model_id")

        todo_cff = (
            'cff-version: 1.2.0\ntitle: "T"\nauthors:\n  - family-names: X\n    given-names: Y\n'
            'version: "0.0.0-prelaunch"\ndate-released: "TODO"\nidentifiers:\n'
            '  - type: doi\n    value: "TODO"\n'
        )
        real_cff = (
            'cff-version: 1.2.0\ntitle: "LLM-Archive: a long-running stability archive"\n'
            "authors:\n  - family-names: Kalognomos\n    given-names: Michalis\n"
            'version: "v0.2"\ndate-released: "2026-09-21"\nidentifiers:\n'
            '  - type: doi\n    value: "10.5281/zenodo.22881127"\n'
            '  - type: other\n    value: "10.5281/zenodo.22881128"\n'
        )
        (tmp / "todo_citation.cff").write_text(todo_cff, encoding="utf-8")
        (tmp / "real_citation.cff").write_text(real_cff, encoding="utf-8")

        if load_citation(tmp / "missing.yaml") is not None:
            errors.append("load_citation: missing file should return None")
        if load_citation(tmp / "todo_citation.cff") is not None:
            errors.append("load_citation: TODO version/doi should return None")
        citation = load_citation(tmp / "real_citation.cff")
        if citation is None or citation["family_names"] != "Kalognomos" or citation["given_names"] != "Michalis":
            errors.append(f"load_citation: family/given names wrong, got {citation!r}")
        if citation is None or citation["doi"] != "10.5281/zenodo.22881127":
            errors.append(f"load_citation: picked the wrong identifier's DOI, got {citation!r}")

        bibtex = render_citation_bibtex({"citation": citation})
        if "kalognomos_2026_22881127" not in bibtex or "author       = {Kalognomos, Michalis}" not in bibtex:
            errors.append(f"render_citation_bibtex: got {bibtex!r}")
        no_release = render_citation_bibtex({"citation": None})
        if "@software" in no_release or "10.5281" in no_release:
            errors.append("render_citation_bibtex: pre-release case must never show a placeholder DOI")

    m_first = {"run_id": "r0", "run_date": "2026-01-01", "protocol_id": "p", "stability_pct": 80.0,
               "ci_low_pct": 75.0, "ci_high_pct": 85.0, "drop_bound_pct_ab": 0.5}
    m_second = {"run_id": "r1", "run_date": "2026-02-01", "protocol_id": "p", "stability_pct": 92.0,
                "ci_low_pct": 89.0, "ci_high_pct": 95.0, "drop_bound_pct_ab": 0.5}
    history = [m_first, m_second]
    trend, delta = _trend_for(history, m_first)
    if trend != "base" or delta is not None:
        errors.append(f"_trend_for first-ever case: got ({trend!r}, {delta!r}), want ('base', None)")
    trend, delta = _trend_for(history, m_second)
    if trend != "improved" or delta != 12.0:
        errors.append(f"_trend_for improved case: got ({trend!r}, {delta!r}), want ('improved', 12.0)")

    multi_flag_measurement = {
        "run_id": "r0", "run_date": "2026-01-01", "protocol_id": "fam__wording__v0",
        "rewording_type": "wording", "lane": "guard", "series": "commercial",
        "subject_model_id": "fake-subject-v1", "stability_pct": 70.0, "ci_low_pct": 60.0, "ci_high_pct": 80.0,
        "flags": ["drop_confounded", "asymmetric_missingness"], "on_curve": False, "gates": {},
        "panel_sha256": "abc", "protocol_sha256": "def", "extractor_sha": "ghi",
        "scenario_gaps_pct": {"s02": 5.0, "s01": 10.0},
    }
    row = build_site_row(multi_flag_measurement, None, "base", None, tmp)
    if row["flags"] != ["drop_confounded", "asymmetric_missingness"]:
        errors.append(f"build_site_row: flags not preserved as a list, got {row['flags']!r}")
    if row["scenario_gaps_pct"] != [10.0, 5.0]:
        errors.append(f"build_site_row: scenario_gaps_pct not sorted by scenario id, got {row['scenario_gaps_pct']!r}")
    if "qa" in row:
        errors.append("build_site_row: a 'guard'-lane row must not carry qa/qn/qb/qg text")

    open_measurement = dict(multi_flag_measurement, lane="open", flags=[], on_curve=True)
    open_protocol = {"scenarios": [{"scenario_id": "s01", "versions": {"A": "text-a", "B": "text-b", "A_prime": "text-n", "C": "text-c"}}]}
    row = build_site_row(open_measurement, open_protocol, "base", None, ROOT)
    if row.get("qa") != "text-a":
        errors.append(f"build_site_row: an 'open'-lane row should carry the protocol's scenario text, got {row.get('qa')!r}")

    coverage_bar_no_model = render_coverage_bar(
        {"admitted_count": 12, "protocol_count": 12, "measured_count": 3, "active_model": None, "last_run_date": None}
    )
    if "Not yet measuring a real model" not in coverage_bar_no_model:
        errors.append("render_coverage_bar: pre-launch state (no active model) not rendered honestly")
    coverage_bar_live = render_coverage_bar(
        {"admitted_count": 12, "protocol_count": 12, "measured_count": 12,
         "active_model": {"model_id": "claude-sonnet-5", "model_family": "claude-sonnet"}, "last_run_date": "2026-09-20"}
    )
    if "claude-sonnet-5" not in coverage_bar_live:
        errors.append("render_coverage_bar: live state should name the active model")

    sparse_data = {"history": {"p": [m_first]}, "protocols": {"p": {}}}
    if "Not enough history" not in render_trend_section(sparse_data):
        errors.append("render_trend_section: a single-date protocol should render the placeholder, not a chart")
    rich_data = {"history": {"fam__wording__v0": [dict(m_first, protocol_id="fam__wording__v0", series="commercial", rewording_type="wording"),
                                                   dict(m_second, protocol_id="fam__wording__v0", series="commercial", rewording_type="wording")]},
                 "protocols": {"fam__wording__v0": {}}}
    if "<svg" not in render_trend_section(rich_data):
        errors.append("render_trend_section: two distinct dates should render an actual chart")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        empty_protocols = tmp / "protocols"
        empty_protocols.mkdir()
        try:
            build_site_html(tmp / "experiments", empty_protocols, tmp / "subject_models.yaml", tmp, DEFAULT_TEMPLATE)
            errors.append("build_site_html: should reject a build with zero protocols (no coverage bar possible)")
        except RuntimeError:
            pass

        # A full smoke build against a minimal-but-real protocol + one kept measurement.
        exp_dir, proto_dir = tmp / "experiments", tmp / "protocols"
        proto_dir.mkdir(exist_ok=True)
        protocol = {
            "protocol_id": "risky_choice_framing__wording__v0", "status": "admitted", "n": 150,
            "scenarios": [{"scenario_id": "s01", "versions": {"A": "qa-text", "A_prime": "qn-text", "B": "qb-text", "C": "qg-text"}}],
        }
        (proto_dir / "risky_choice_framing__wording__v0.json").write_text(json.dumps(protocol), encoding="utf-8")
        run_dir = exp_dir / "run0"
        run_dir.mkdir(parents=True)
        measurement = dict(
            multi_flag_measurement, protocol_id="risky_choice_framing__wording__v0", lane="open",
            flags=[], on_curve=True, run_id="run0", series="commercial",
        )
        (run_dir / "measurement.json").write_text(json.dumps(measurement), encoding="utf-8")
        (run_dir / "trials.jsonl").write_text("", encoding="utf-8")
        (tmp / "stability.csv").write_text("run_id\nrun0\n", encoding="utf-8")

        # out_dir is a subdirectory of data_dir here, same as the real deploy (website/index.html
        # reading data one level up, at the repo root) -- catches the bug where the download link
        # was a bare filename that only worked if the page and the data happened to share a
        # directory, which they never do in the real build.
        out_dir = tmp / "website"
        html = build_site_html(exp_dir, proto_dir, tmp / "subject_models.yaml", tmp, DEFAULT_TEMPLATE, out_dir)
        if "@@" in html:
            errors.append("build_site_html: a template placeholder was left unresolved")
        for page_id in ("out", "results", "data", "what", "does", "how", "value", "glossary"):
            if f'id="{page_id}"' not in html:
                errors.append(f"build_site_html: page id {page_id!r} missing from output")
        if 'href="../stability.csv"' not in html:
            errors.append(
                "build_site_html: primary download link should be relative to out_dir, not data_dir "
                "(e.g. '../stability.csv' when the page lives one directory below the data)"
            )
        if 'download="stability.csv"' not in html:
            errors.append(
                "build_site_html: primary download link is missing the download attribute -- "
                "without it, clicking navigates the tab to the raw CSV instead of saving it"
            )

        # An open_weights-only measurement (the daily reference-model self-check) must never
        # inflate "N of 12 panels measured" next to the *commercial* model's name in the coverage
        # bar -- the two series are never supposed to mix into one number (subject_models.yaml's
        # own comment on this). Add a second, open_weights measurement of a DIFFERENT protocol and
        # confirm the commercial-only count doesn't move.
        (proto_dir / "risky_choice_framing__anchoring__v0.json").write_text(
            json.dumps(dict(protocol, protocol_id="risky_choice_framing__anchoring__v0")), encoding="utf-8"
        )
        ow_run_dir = exp_dir / "run1"
        ow_run_dir.mkdir(parents=True)
        ow_measurement = dict(
            multi_flag_measurement, protocol_id="risky_choice_framing__anchoring__v0", lane="guard",
            flags=[], on_curve=True, run_id="run1", series="open_weights", subject_model_id="qwen2.5-1.5b-instruct-q4_k_m",
        )
        (ow_run_dir / "measurement.json").write_text(json.dumps(ow_measurement), encoding="utf-8")
        (ow_run_dir / "trials.jsonl").write_text("", encoding="utf-8")
        data = build_site_data(exp_dir, proto_dir, tmp / "subject_models.yaml")
        if data["measured_count"] != 1:
            errors.append(
                f"build_site_data: measured_count should count the commercial series only, "
                f"got {data['measured_count']!r} with one commercial + one open_weights measurement present"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="write stability.csv from experiments/")
    p_build.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    p_build.add_argument("--out", type=Path, default=DEFAULT_OUT)

    p_outcomes = sub.add_parser("build-outcomes", help="write outcomes.csv from experiments/")
    p_outcomes.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    p_outcomes.add_argument("--out", type=Path, default=DEFAULT_OUTCOMES_OUT)

    p_open_lane = sub.add_parser("build-open-lane", help="write open-lane/<year>.jsonl from experiments/")
    p_open_lane.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    p_open_lane.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)

    p_croissant = sub.add_parser("build-croissant", help="write croissant.json describing stability.csv/outcomes.csv")
    p_croissant.add_argument("--out", type=Path, default=DEFAULT_CROISSANT_OUT)

    p_coverage = sub.add_parser("build-coverage", help="write coverage.csv from state/coverage_log.jsonl")
    p_coverage.add_argument("--log", type=Path, default=DEFAULT_COVERAGE_LOG)
    p_coverage.add_argument("--out", type=Path, default=DEFAULT_COVERAGE_OUT)

    p_site = sub.add_parser("build-site", help="write website/index.html from experiments/, protocols/ and subject_models.yaml")
    p_site.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    p_site.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS)
    p_site.add_argument("--subject-models", type=Path, default=DEFAULT_SUBJECT_MODELS)
    p_site.add_argument("--data-dir", type=Path, default=DEFAULT_OUT_DIR, help="where stability.csv/outcomes.csv/croissant.json/open-lane/ already live")
    p_site.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    p_site.add_argument("--out", type=Path, default=DEFAULT_SITE_OUT)
    p_site.add_argument("--version-file", type=Path, default=DEFAULT_VERSION_FILE)
    p_site.add_argument("--citation-file", type=Path, default=DEFAULT_CITATION_FILE)

    sub.add_parser("verify", help="run this module's own hand-worked cases")

    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/render.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    if args.command == "build-outcomes":
        n = write_outcomes_csv(args.experiments_dir, args.out)
        print(f"wrote {n} row(s) to {args.out}")
        return 0

    if args.command == "build-open-lane":
        counts = write_open_lane(args.experiments_dir, args.out_dir)
        for year, n in sorted(counts.items()):
            print(f"wrote {n} trial(s) to {args.out_dir / 'open-lane' / (year + '.jsonl')}")
        return 0

    if args.command == "build-croissant":
        write_croissant(args.out)
        print(f"wrote {args.out}")
        return 0

    if args.command == "build-coverage":
        n = write_coverage_csv(args.log, args.out)
        print(f"wrote {n} row(s) to {args.out}")
        return 0

    if args.command == "build-site":
        write_site_html(
            args.experiments_dir, args.protocols_dir, args.subject_models, args.data_dir, args.out, args.template,
            args.version_file, args.citation_file,
        )
        print(f"wrote {args.out}")
        return 0

    n = write_stability_csv(args.experiments_dir, args.out)
    print(f"wrote {n} row(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
