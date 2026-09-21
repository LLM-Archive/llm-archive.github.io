"""Canonical hashing for panels, protocols, code and schema (spec.md §13).

These hashes are what let a stranger verify, without asking anyone for anything, that the panel
they're looking at today is the exact same one a measurement from a year ago used. A hash of a
Python dict would depend on key insertion order, which is an accident of how the file happened to
be written, not a property of its content — two people describing the identical panel could get
two different hashes. canonical_json fixes that by always sorting keys before hashing, so content
is the only thing that determines the hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def sha256_of_files(paths: list[Path], root: Path) -> str:
    """Hash of several files, order-independent: relative path + content, sorted by path."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda q: q.relative_to(root).as_posix()):
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes() + b"\0")
    return h.hexdigest()


def compute_panel_sha256(scenarios: list[dict]) -> str:
    """Only what the model sees defines the panel: ids, options, and the four version texts.
    Deliberately excludes bookkeeping fields like `structure` (the declared lotteries used to
    check invariants) — those can be corrected without the panel itself having changed, because
    the model was never shown them in the first place."""
    return sha256_of_obj(
        [{"scenario_id": s["scenario_id"], "options": s["options"], "versions": s["versions"]} for s in scenarios]
    )


# Every field that, if changed after a measurement has been published, would make that old
# measurement no longer comparable to a new one under the same protocol_id — so instead of
# allowing a silent edit, changing any of these is required to mint a new id and start a new
# series (spec.md §2). `panel_sha256` is included rather than the scenarios themselves: any
# change to the panel already changes panel_sha256, so including the hash (not the content)
# keeps this hash's own definition small while still depending on every scenario transitively.
PROTOCOL_HASH_FIELDS = (
    "family",
    "rewording_type",
    "n",
    "n_per_scenario",
    "effort",
    "grammar_version",
    "options",
    "prompt_template",
    "theta_positive_pct",
    "scenario_dominated_share_pct",
    "panel_sha256",
)


def compute_protocol_sha256(protocol: dict) -> str:
    return sha256_of_obj({k: protocol[k] for k in PROTOCOL_HASH_FIELDS})
