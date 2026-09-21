"""Promotes a protocol from `candidate` to `admitted` and assigns it a lane (spec.md §3, gate 4).

    python3 -m core.measure.promote protocols/<protocol>.json --lane guard --human-reviewed
    python3 -m core.measure.promote protocols/<protocol>.json --lane open --twin-of <id> --human-reviewed

Gate 1 (structural invariants) is mechanical and re-checked here, both before and after the edit.
Gates 2 and 4 are not: gate 2 is a blind reviewer reading the wording, and gate 4 is a human
signing off before a candidate ever enters rotation (spec.md §3) — neither is something this
file can verify. So it refuses to run without --human-reviewed, and it never picks a lane on its
own: which of `open` / `guard` / `sealed` a protocol belongs to is an editorial decision (spec.md
§3's 2/10/2 split), not something derivable from the protocol's own content, so it's a required
argument, never a default. Same for --twin-of: the pairing is a judgment about which two protocols
measure the same phenomenon closely enough to be compared, which is the owner's call, not a
string-similarity guess.

One file per call, deliberately. Writing both halves of a twin pair at once would make this
script the only thing that knows a pair is well-formed; instead each half is written on its own
and verify.py's `twin pairing` check — which sees the whole protocols/ folder — is what confirms
the two halves agree. A half-finished pair is therefore a loud verify.py failure, not a silent one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .invariants import check_protocol
from .schema import LANES


def promote(protocol: dict, lane: str, twin_id: str | None = None) -> dict:
    if protocol["status"] != "candidate":
        raise ValueError(f"{protocol['protocol_id']!r} is {protocol['status']!r}, not candidate — nothing to promote")
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}, must be one of {LANES}")

    errors = check_protocol(protocol)
    if errors:
        raise ValueError("refusing to promote — gate 1 fails before promotion:\n  " + "\n  ".join(errors))

    promoted = dict(protocol)
    promoted["status"] = "admitted"
    promoted["lane"] = lane
    promoted["twin_id"] = twin_id

    errors = check_protocol(promoted)
    if errors:
        raise ValueError("refusing to promote — gate 1 fails after promotion:\n  " + "\n  ".join(errors))
    return promoted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("protocol", type=Path)
    ap.add_argument("--lane", choices=LANES, required=True)
    ap.add_argument("--twin-of", metavar="PROTOCOL_ID", help="the protocol_id this one is paired with")
    ap.add_argument(
        "--human-reviewed",
        action="store_true",
        help="confirms a human has already completed gates 2 and 4 (spec.md §3) for this protocol",
    )
    args = ap.parse_args(argv)

    if not args.human_reviewed:
        sys.exit(
            "refusing to promote without --human-reviewed: gate 4 (spec.md §3) requires a human to "
            "have read this protocol's design and text before it enters rotation, and this script has "
            "no way to check that for you."
        )

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    try:
        promoted = promote(protocol, args.lane, args.twin_of)
    except ValueError as e:
        sys.exit(str(e))

    args.protocol.write_text(json.dumps(promoted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    twin = f", twin_of={promoted['twin_id']}" if promoted["twin_id"] else ""
    print(f"{promoted['protocol_id']}: candidate -> admitted, lane={promoted['lane']}{twin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
