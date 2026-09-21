"""Closed lists and published field names (spec.md §10, §13.1).

Every enum in this file (outcomes, flags, record types, ...) is a closed, frozen set on purpose:
a coverage gap, a measurement outcome, or a published record can only ever take a value from a
pre-declared list, never a value invented on the spot to describe whatever just happened. That is
what makes the coverage table auditable — a reader can check "was every gap explained by one of
these causes?" without having to trust that new causes weren't quietly made up after the fact to
explain away something inconvenient. Adding a new value to any list here is a real, visible
change (it changes schema_sha256 below), not something that can happen silently inside one run.
"""

from __future__ import annotations

from .chain import sha256_of_obj

SCHEMA_VERSION = 2

VERSIONS = ("A", "A_prime", "B", "C")
VERSION_KEYS = {"A": "a", "A_prime": "a_prime", "B": "b", "C": "c"}

VALID = "valid"
INVALID_OUTCOMES = ("unparseable", "refused", "blocked_upstream", "truncated", "empty", "off_format")
OUTCOMES = (VALID,) + INVALID_OUTCOMES

# A flag takes a measurement off the main published curve, but never deletes it — the measurement
# is still published, just visibly marked with the reason it isn't trustworthy enough to headline.
FLAGS = (
    "below_surface_noise",     # the real change (A↔B) didn't beat a meaningless one (A↔A′) — no signal
    "not_reading",             # a change that SHOULD move the answer (A↔C) didn't — the model isn't reading
    "degenerate_candidate",    # near-zero entropy: it always gives the same answer, not a stable one
    "drop_confounded",         # too many responses were lost to trust the result at all
    "asymmetric_missingness",  # one condition lost far more responses than the other
    "below_drop_bound",        # the measured gap is smaller than the possible error from lost responses
    "scenario_dominated",      # a couple of the 15 scenarios account for most of the gap
    "instrument_suspect",      # the reference-model sanity check moved that day — the whole pipeline is suspect
    "exploratory",             # outside the protocol's one pre-declared comparison — informative, never on a curve
)

RECORD_TYPES = (
    "protocol",
    "experiment_run",
    "trial",
    "measurement",
    "coverage_gap",
    "render_manifest",
    "status",
    # succession (spec.md §4.3)
    "subject_model",
    "generation_bridge",
    "bridge_incomplete",
    "series_closed",
    "needs_review",
    "comparison_point",
)

LANES = ("open", "guard", "sealed")
SERIES = ("commercial", "open_weights")
REWORDING_TYPES = ("wording", "anchoring", "order", "default")
PROTOCOL_STATUSES = ("candidate", "admitted", "exploratory", "retired")

# Every day this project fails to publish a measurement, that gap must be explained by exactly
# one of these — never left blank, and never explained by free text invented after the fact. A
# gap with a named cause is a disclosed limitation; a gap with no cause at all is indistinguishable
# from data being quietly dropped, which is the one thing an archive like this cannot afford.
GAP_CAUSES = (
    "before_archive_start",     # a generation that existed before this project started measuring
    "budget_halted",            # the monthly spending ceiling was hit
    "dormant",                  # the whole project was paused by its operator
    "provider_outage",          # the model API was unavailable
    "protocol_confounded",      # a protocol failed one of its own gates and was pulled from the curve
    "human_absent",             # a required human review did not happen in time
    "generation_retired_early", # the model was retired before a bridge run could complete
    "generation_bridge",        # the monthly sweep was intentionally skipped to fund a bridge run instead
)

# Unit of every published measurement field. The spec rule (§6): a field is in 0–100 points
# if and only if its name contains "_pct". verify.py enforces it.
PCT_UNITS = ("pct", "pct_list", "pct_summary")
MEASUREMENT_FIELDS = {
    "record_type": "enum",
    "schema_version": "int",
    "run_id": "text",
    "run_date": "date",
    "protocol_id": "text",
    "rewording_type": "enum",
    "subject_model_id": "text",
    "stability_pct": "pct",
    "ci_low_pct": "pct",
    "ci_high_pct": "pct",
    "gap_pct": "pct",
    "gap_null_pct": "pct",
    "gap_positive_pct": "pct",
    "null_floor_pct": "pct",
    "at_noise_floor": "bool",
    "n": "count",
    "n_valid": "count_by_version",
    "n_items": "count",
    "k": "count",
    "entropy_a": "unit_interval",
    "entropy_b": "unit_interval",
    "u_a": "unit_interval",
    "u_a_prime": "unit_interval",
    "u_b": "unit_interval",
    "u_c": "unit_interval",
    "drop_bound_pct_ab": "pct",
    "drop_bound_pct_aa": "pct",
    "drop_bound_pct_ac": "pct",
    "drop_asymmetry_pct_ab": "pct",
    "drop_asymmetry_pct_aa": "pct",
    "drop_asymmetry_pct_ac": "pct",
    "scenario_gaps_pct": "pct_list",
    "scenario_spread_pct": "pct_summary",
    "scenario_concentration_pct": "pct",
    "outcomes": "counts_by_version",
    "decisions": "counts_by_version",
    "flags": "flag_list",
    "gates": "gate_detail",
    "on_curve": "bool",
    "panel_sha256": "sha",
    "protocol_sha256": "sha",
    "extractor_sha": "sha",
    "analysis_code_sha": "sha",
    "schema_sha256": "sha",
    "grammar_version": "int",
    "series": "enum",
    "lane": "enum",
    "twin_id": "text",
    "model_family": "text",
    "returned_model_id": "text",
    "api_surface_sha": "sha",
    "condition_profile": "enum",
    "replicate_index": "int",
}


def schema_sha256() -> str:
    return sha256_of_obj(
        {
            "schema_version": SCHEMA_VERSION,
            "versions": VERSIONS,
            "outcomes": OUTCOMES,
            "flags": FLAGS,
            "record_types": RECORD_TYPES,
            "lanes": LANES,
            "series": SERIES,
            "rewording_types": REWORDING_TYPES,
            "protocol_statuses": PROTOCOL_STATUSES,
            "gap_causes": GAP_CAUSES,
            "measurement_fields": MEASUREMENT_FIELDS,
        }
    )
