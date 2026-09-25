"""Upgrades pending OpenTimestamps proofs to Bitcoin proofs (state/timestamps/README.md).

    python3 -m core.plumbing.ots_upgrade status      # which .ots files still lack a Bitcoin attestation
    python3 -m core.plumbing.ots_upgrade run         # `ots upgrade` each of those, in place
    python3 -m core.plumbing.ots_upgrade verify

A freshly stamped manifest starts as a *pending* proof (calendar receipts only); a few hours later
the calendars can turn it into a proof anchored in a Bitcoin block, by rewriting the same file.
That step used to be "run it by hand after a few hours". Nothing here creates a stamp or reads the
manifest: it only asks the calendars to complete a stamp that already exists.

"Has a Bitcoin attestation" is decided by looking for the attestation tag in the file's bytes
(stdlib only, so `status` needs no `ots` install). A file that has one is never touched. A file
that does not is left alone if `ots` says the calendars have nothing yet -- that is normal for the
first hours and is not an error, so this can run on a schedule and stay quiet until it has news.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "state" / "timestamps"

# OpenTimestamps attestation tags (8 bytes each).
BITCOIN_TAG = bytes.fromhex("0588960d73d71901")


def has_bitcoin_attestation(ots_bytes: bytes) -> bool:
    return BITCOIN_TAG in ots_bytes


def pending_files(directory: Path) -> list[Path]:
    return [p for p in sorted(directory.glob("*.ots")) if not has_bitcoin_attestation(p.read_bytes())]


def upgrade(path: Path) -> bool:
    """Runs `ots upgrade`; True when the file now carries a Bitcoin attestation. `ots` keeps the
    old proof next to it as `<name>.ots.bak`."""
    proc = subprocess.run(["ots", "upgrade", str(path)], capture_output=True, text=True)
    print(f"{path.name}: {(proc.stdout + proc.stderr).strip() or 'no output'}")
    return has_bitcoin_attestation(path.read_bytes())


def _verify() -> list[str]:
    errors: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            errors.append(msg)

    expect(has_bitcoin_attestation(b"xx" + BITCOIN_TAG + b"yy"), "a file with the Bitcoin tag must count as attested")
    expect(not has_bitcoin_attestation(bytes.fromhex("83dfe30d2ef90c8e") + b"pending"), "a pending-only file must not")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "done.json.ots").write_bytes(b"a" + BITCOIN_TAG)
        (d / "wait.json.ots").write_bytes(b"pending only")
        (d / "notes.txt").write_bytes(b"ignored")
        expect([p.name for p in pending_files(d)] == ["wait.json.ots"], "only the unattested .ots file is pending")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("status", "run"):
        p = sub.add_parser(name)
        p.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    sub.add_parser("verify")
    args = ap.parse_args(argv)

    if args.command == "verify":
        errors = _verify()
        print(f"[{'FAIL' if errors else 'PASS'}] core/plumbing/ots_upgrade.py")
        for e in errors:
            print(f"    {e}")
        return 1 if errors else 0

    pending = pending_files(args.dir)
    if not pending:
        print("nothing pending: every .ots proof already carries a Bitcoin attestation")
        return 0
    if args.command == "status":
        print("pending (no Bitcoin attestation yet): " + ", ".join(p.name for p in pending))
        return 0
    done = [p for p in pending if upgrade(p)]
    print(f"upgraded {len(done)} of {len(pending)} pending proof(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
