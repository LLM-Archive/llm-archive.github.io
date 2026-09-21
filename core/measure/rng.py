"""SplitMix64: the only source of randomness in core/measure/.

Chosen over Python's `random` because its output is defined by a few lines of integer
arithmetic, so a bootstrap interval can be reproduced bit-for-bit in any language.
"""

from __future__ import annotations

import hashlib

_MASK = (1 << 64) - 1


class SplitMix64:
    def __init__(self, seed: int) -> None:
        self.state = seed & _MASK

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """Uniform-ish integer in [0, n). Modulo bias is < 2^-50 for n < 2^14; accepted and frozen."""
        return self.next() % n


def seed_from(*parts: str) -> int:
    """Derives a seed from public identifiers (protocol id, run date, model id, ...) instead of
    drawing one from an entropy source.

    This makes every "random" number in a measurement reproducible by a stranger: given the same
    protocol, the same day and the same model id, anyone re-running this code gets the identical
    bootstrap and the identical null-floor estimate — there is no hidden coin flip anywhere that
    only the original run could have seen. It also rules out a specific way of gaming a result:
    quietly retrying a run until a favorable random seed happens to appear.
    """
    digest = hashlib.sha256("‖".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")
