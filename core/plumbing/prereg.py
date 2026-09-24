"""Checks that a protocol was stamped (OpenTimestamps) BEFORE it is run for real -- the guard
the plan describes as "the runner refuses to execute if the pre-registration's hash isn't already
in a stamped manifest". Plain-words explanation for readers: guide/timestamps.md.

    python3 -m core.plumbing.prereg check        # one line per protocol: stamped or not
    python3 -m core.plumbing.prereg verify

A manifest is a JSON file in `state/timestamps/` listing, per protocol, its `panel_sha256` and
`protocol_sha256`, with a `<manifest>.ots` proof next to it. A protocol counts as pre-registered
when some manifest lists its CURRENT (protocol_id, panel_sha256, protocol_sha256) triple and that
manifest's `.ots` file really belongs to it. "Really belongs" is checked without any dependency:
an `.ots` file starts with a fixed header that holds the sha256 of the file it stamps, so the
header's digest is compared to the manifest's own sha256. A protocol whose text or design changed
after stamping has a different hash, so it is no longer covered -- exactly the point.

What this does NOT check: that the proof has been upgraded to a full Bitcoin proof (that takes
hours and can follow the first run -- the stamp already fixes the date the calendars received it),
nor that the proof verifies cryptographically (that needs the `ots` tool; a maintainer runs
`ots verify`). It is a guard against forgetting, not against a determined insider.

Where it is enforced: `core/schedule/run_due.run_full_sweep`, the unattended paid path. It is NOT
enforced on a hand-typed `python3 -m core.measure.pilot ...`, because `core/measure/` is frozen
(spec.md §13) and cannot be changed to call it; run `check` before a hand run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFESTS_DIR = ROOT / "state" / "timestamps"
DEFAULT_PROTOCOLS_DIR = ROOT / "protocols"

_OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
_OP_SHA256 = 0x08


def ots_digest(ots_path: Path) -> str | None:
    """The sha256 (hex) an `.ots` file says it stamps, or None if it isn't a sha256 OpenTimestamps
    file. Header layout: 31-byte magic, one version byte, one op byte (0x08 = sha256), 32 digest bytes."""
    data = ots_path.read_bytes()
    n = len(_OTS_MAGIC)
    if not data.startswith(_OTS_MAGIC) or len(data) < n + 34 or data[n + 1] != _OP_SHA256:
        return None
    return data[n + 2 : n + 34].hex()


def load_stamped(manifests_dir: Path) -> dict[tuple[str, str, str], str]:
    """(protocol_id, panel_sha256, protocol_sha256) -> manifest file name, for every manifest that
    has a matching `.ots`. A manifest without a proof, or whose proof stamps a different file,
    contributes nothing."""
    stamped: dict[tuple[str, str, str], str] = {}
    if not manifests_dir.is_dir():
        return stamped
    for path in sorted(manifests_dir.glob("*.json")):
        ots = path.with_name(path.name + ".ots")
        if not ots.exists():
            continue
        if ots_digest(ots) != hashlib.sha256(path.read_bytes()).hexdigest():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")).get("protocols", []):
            stamped.setdefault((row["protocol_id"], row["panel_sha256"], row["protocol_sha256"]), path.name)
    return stamped


def is_preregistered(protocol: dict, stamped: dict[tuple[str, str, str], str]) -> str | None:
    """The manifest name covering this protocol as it is right now, else None."""
    return stamped.get((protocol["protocol_id"], protocol["panel_sha256"], protocol["protocol_sha256"]))


def _write_manifest(dirpath: Path, name: str, rows: list[dict], *, stamp_digest: str | None = "own") -> Path:
    """Test helper: write a manifest and an `.ots` whose header carries `stamp_digest` ('own' =
    the manifest's real sha256; None = no .ots at all; any other hex string = a wrong one)."""
    path = dirpath / name
    path.write_text(json.dumps({"protocols": rows}, sort_keys=True), encoding="utf-8")
    if stamp_digest is not None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if stamp_digest == "own" else stamp_digest
        path.with_name(name + ".ots").write_bytes(_OTS_MAGIC + b"\x01" + bytes([_OP_SHA256]) + bytes.fromhex(digest) + b"\x00")
    return path


def _verify() -> list[str]:
    errors: list[str] = []
    row = {"protocol_id": "p__wording__v0", "panel_sha256": "a" * 64, "protocol_sha256": "b" * 64}
    proto = dict(row)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        if load_stamped(d):
            errors.append("an empty directory must stamp nothing")
        _write_manifest(d, "m1.json", [row])
        st = load_stamped(d)
        if is_preregistered(proto, st) != "m1.json":
            errors.append("a stamped, listed protocol must be pre-registered")
        if is_preregistered({**proto, "protocol_sha256": "c" * 64}, st) is not None:
            errors.append("a protocol whose protocol_sha256 changed after stamping must NOT be covered")
        if is_preregistered({**proto, "panel_sha256": "c" * 64}, st) is not None:
            errors.append("a protocol whose panel_sha256 changed after stamping must NOT be covered")
        if is_preregistered({**proto, "protocol_id": "other__v0"}, st) is not None:
            errors.append("a protocol not listed must NOT be covered")

        d2 = Path(tempfile.mkdtemp(dir=tmp))
        _write_manifest(d2, "nostamp.json", [row], stamp_digest=None)
        if load_stamped(d2):
            errors.append("a manifest with no .ots must not count")
        d3 = Path(tempfile.mkdtemp(dir=tmp))
        _write_manifest(d3, "wrong.json", [row], stamp_digest="d" * 64)
        if load_stamped(d3):
            errors.append("a manifest whose .ots stamps a different file must not count")
        d4 = Path(tempfile.mkdtemp(dir=tmp))
        (d4 / "junk.json").write_text(json.dumps({"protocols": [row]}))
        (d4 / "junk.json.ots").write_bytes(b"not an ots file")
        if load_stamped(d4):
            errors.append("a junk .ots must not count")
        if ots_digest(d4 / "junk.json.ots") is not None:
            errors.append("a junk .ots must have no digest")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="one line per protocols/*.json: stamped or not (exit 1 if any admitted one is not)")
    p.add_argument("--manifests-dir", type=Path, default=DEFAULT_MANIFESTS_DIR)
    p.add_argument("--protocols-dir", type=Path, default=DEFAULT_PROTOCOLS_DIR)
    sub.add_parser("verify", help="run this module's own hand-worked cases")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/prereg.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    stamped = load_stamped(args.manifests_dir)
    missing = 0
    for path in sorted(args.protocols_dir.glob("*.json")):
        proto = json.loads(path.read_text(encoding="utf-8"))
        covered = is_preregistered(proto, stamped)
        if covered is None and proto.get("status") == "admitted":
            missing += 1
        print(f"{'stamped ' if covered else 'NOT STAMPED'}  {proto['protocol_id']}" + (f"  ({covered})" if covered else ""))
    print(f"\n{missing} admitted protocol(s) not covered by a stamped manifest")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
