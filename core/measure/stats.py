"""The estimator: how 4 × n raw responses become one number, and why that number can be trusted.

Every quantity a gate compares is an exact Fraction, not a float — a gate is a pass/fail line,
and floating-point rounding right at that line would make the same data pass on one machine and
fail on another. Floats appear only at the very end, when a value is rounded for publication.

Standard library only. No numpy: a reader in ten years should be able to re-run this file without
first solving a dependency-resolution problem against a decade-old lockfile.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from math import ceil, log, sqrt

from .rng import SplitMix64

# Fixed forever: a different B or a different confidence level on a later measurement of the same
# protocol would make its interval mean something subtly different, defeating any comparison to
# the earlier one.
B_ITERATIONS = 10_000
CI_LOWER = Fraction(25, 1000)
CI_UPPER = Fraction(975, 1000)


def counts(tokens: list[str], options: list[str]) -> list[int]:
    """Tallies decisions into the option order declared by the protocol — never inferred from
    whatever happens to appear in the data, so an option that nobody chose still reads as 0."""
    c = Counter(tokens)
    unknown = set(c) - set(options)
    if unknown:
        raise ValueError(f"tokens outside options[]: {sorted(unknown)}")
    return [c[o] for o in options]


def _tvd_numerator(cx: list[int], cy: list[int], nx: int, ny: int) -> int:
    """TVD = numerator / (2·nx·ny). Kept as an integer so a resampling loop run 10,000 times
    never accumulates floating-point error — the last iteration is exactly as precise as the first."""
    return sum(abs(a * ny - b * nx) for a, b in zip(cx, cy))


def tvd(cx: list[int], cy: list[int]) -> Fraction | None:
    """Total variation distance between two decision distributions: half the sum of absolute
    differences in each option's share. 0 means the two distributions are identical; 1 means no
    overlap at all — whatever was chosen under X was never chosen under Y.

    Chosen over other distance measures because for a two-option decision it collapses to exactly
    the classical framing-effect size used in the human decision-making literature, so a number
    from this file can be compared directly to a published human result without translation.

    Returns None when one side has zero valid responses — there is no distribution to compare,
    and reporting a distance to nothing would silently imply certainty that doesn't exist.
    """
    nx, ny = sum(cx), sum(cy)
    if nx == 0 or ny == 0:
        return None
    return Fraction(_tvd_numerator(cx, cy, nx, ny), 2 * nx * ny)


def loss_rate(n: int, n_valid: int) -> Fraction:
    """Share of the n calls that did not produce a usable decision (refused, truncated,
    unparseable, ...). This is the raw material for drop_bound_pct below: every lost response is
    a response that COULD have gone either way, and the gap computed from what's left is blind
    to that possibility."""
    return Fraction(n - n_valid, n)


def drop_bound_pct(u_x: Fraction, u_y: Fraction) -> Fraction:
    """The worst-case swing in the gap if every single lost response, on both sides, had actually
    landed in whichever direction makes the two conditions look most different than they are.

    This is not an estimate of what probably happened — it makes no claim about why responses
    were lost. It is a hard ceiling: total variation distance can move by at most u_x + u_y when
    you go from the true (unobservable) distributions to the ones measured from survivors only.
    If this ceiling is bigger than the gap itself, the gap is indistinguishable from an artifact
    of missing data, no matter how it happened to come out.
    """
    return 100 * (u_x + u_y)


def drop_asymmetry_pct(u_x: Fraction, u_y: Fraction) -> Fraction:
    """How differently the two conditions lost responses. A gap can be driven entirely by one
    wording being more likely to trigger a refusal or a malformed reply than the other — that is
    a fact about parsing or about the model's guardrails, not about the decision itself. Checked
    on its own, separately from the size-only drop_bound_pct above, because a small, symmetric
    loss and a small, one-sided loss carry very different risk even at the same total size."""
    return 100 * abs(u_x - u_y)


def entropy_norm(c: list[int]) -> float | None:
    """Normalized Shannon entropy of one condition's decisions, in [0, 1]. 0 means every single
    response picked the same option; 1 means responses were spread as evenly as possible across
    all options.

    This exists because gap alone cannot tell "genuinely stable" apart from "collapsed onto one
    answer regardless of the question": a model that always says the same thing, no matter what
    it's asked, scores a perfect gap of 0 — and a perfect stability score — while telling you
    nothing about whether it read the question at all. Entropy is the only place in this file
    that would notice.
    """
    n, k = sum(c), len(c)
    if n == 0 or k < 2:
        return None
    return -sum((x / n) * log(x / n) for x in c if x) / log(k)


def _expand(c: list[int]) -> list[int]:
    return [i for i, x in enumerate(c) for _ in range(x)]


def null_floor(cx: list[int], cy: list[int], rng: SplitMix64, iterations: int = B_ITERATIONS) -> Fraction | None:
    """The gap you'd expect to see from sampling luck ALONE, if the two conditions were in fact
    driven by the exact same underlying distribution.

    With a finite number of calls, two samples from one identical distribution will almost never
    match exactly — pure chance produces gap > 0 even when nothing real is different. Without
    this number, a small measured gap can't be told apart from noise, and a ceiling of "100%
    stability" turns out to be unreachable even for a perfectly stable model. This function
    estimates that noise floor directly from the data: it pools every valid response from both
    conditions together, so it is testing what the gap looks like when there is, by construction,
    only one condition — then repeats that random split many times and averages the result.

    Implementation: each iteration does a partial Fisher-Yates shuffle of positions 0..nx-1 of
    the pool AS LEFT BY THE PREVIOUS ITERATION (never reset). This still visits an unbiased random
    split every time — the pool's later positions are just as shuffled as its earlier ones after
    the first pass — while reusing one array across all 10,000 iterations instead of rebuilding it.
    """
    nx, ny = sum(cx), sum(cy)
    if nx == 0 or ny == 0:
        return None
    total = [a + b for a, b in zip(cx, cy)]
    pool, size, k = _expand(total), nx + ny, len(cx)
    acc = 0
    for _ in range(iterations):
        for i in range(nx):
            j = i + rng.below(size - i)
            pool[i], pool[j] = pool[j], pool[i]
        sx = [0] * k
        for v in pool[:nx]:
            sx[v] += 1
        acc += _tvd_numerator(sx, [t - s for t, s in zip(total, sx)], nx, ny)
    return Fraction(acc, 2 * nx * ny * iterations)


def _nearest_rank(p: Fraction, size: int) -> int:
    return max(ceil(p * size) - 1, 0)


def bootstrap_gap_interval(
    cx: list[int], cy: list[int], rng: SplitMix64, iterations: int = B_ITERATIONS
) -> tuple[Fraction, Fraction] | None:
    """A 95% interval on the gap, from resampling with replacement at the level of individual
    responses (not scenarios — see measurement.py for why scenario-level resampling is deliberately
    NOT done here). Each of the 10,000 iterations draws a fresh sample of size nx from condition X
    and ny from Y, with replacement, and records the resulting gap; the interval is the 2.5th and
    97.5th percentile of that list, taken at the nearest rank rather than interpolated, so the
    reported bound is always a gap value that a real iteration actually produced.
    """
    nx, ny = sum(cx), sum(cy)
    if nx == 0 or ny == 0:
        return None
    xs, ys, k = _expand(cx), _expand(cy), len(cx)
    nums = []
    for _ in range(iterations):
        sx, sy = [0] * k, [0] * k
        for _ in range(nx):
            sx[xs[rng.below(nx)]] += 1
        for _ in range(ny):
            sy[ys[rng.below(ny)]] += 1
        nums.append(_tvd_numerator(sx, sy, nx, ny))
    nums.sort()
    den = 2 * nx * ny
    return Fraction(nums[_nearest_rank(CI_LOWER, iterations)], den), Fraction(nums[_nearest_rank(CI_UPPER, iterations)], den)


def median(values: list[Fraction]) -> Fraction:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def spread(values: list[Fraction]) -> dict[str, Fraction]:
    """Min, max, median and nearest-rank quartiles of the per-scenario gaps.

    A protocol has only 15 scenarios — far too few for a statistically valid interval on "how
    this generalizes across scenarios of this type" (a proper cluster bootstrap needs closer to a
    few hundred clusters to not be misleadingly narrow). Rather than publish an interval that
    looks rigorous but understates the real uncertainty, this file publishes the raw spread
    instead and lets the reader see the heterogeneity directly.
    """
    s = sorted(values)
    q1, q3 = s[_nearest_rank(Fraction(1, 4), len(s))], s[_nearest_rank(Fraction(3, 4), len(s))]
    return {"min": s[0], "q1": q1, "median": median(s), "q3": q3, "max": s[-1], "iqr": q3 - q1}


def concentration(values: list[Fraction]) -> Fraction | None:
    """Share of the total per-scenario gap contributed by just the two largest scenarios.

    If two scenarios out of fifteen account for most of the measured gap, the headline number is
    really describing a couple of unusual questions, not a general property of the protocol —
    worth surfacing on its own rather than leaving it buried inside one aggregate stability score.
    """
    total = sum(values)
    if total == 0:
        return None
    top = sorted(values, reverse=True)[:2]
    return sum(top) / total


def compare(x: dict, y: dict) -> str:
    """Whether two measurements of the SAME protocol (e.g. across model generations) differ by
    more than could plausibly be chance and more than could plausibly be measurement error.

    Two separate thresholds, checked separately, because they are two different kinds of error
    and do not combine the same way:

    - `limit`: random sampling error, from each measurement's own confidence interval. Independent
      random errors combine in quadrature (Pythagorean sum) — that's ordinary statistics.
    - `bound`: the worst-case error from lost responses (drop_bound_pct) in each measurement. This
      is not random noise to average away; it's a systematic ceiling on how wrong either number
      could be, so the two ceilings simply ADD.

    Both conditions must hold before calling anything "improved" or "regressed" — a difference
    that clears the random-noise bar but not the missing-data bar could still be entirely an
    artifact of one run losing more responses than the other, not a real change in the model.
    """
    delta = y["stability_pct"] - x["stability_pct"]
    half_x = (x["ci_high_pct"] - x["ci_low_pct"]) / 2
    half_y = (y["ci_high_pct"] - y["ci_low_pct"]) / 2
    limit = sqrt(half_x**2 + half_y**2)
    bound = x["drop_bound_pct_ab"] + y["drop_bound_pct_ab"]
    if abs(delta) >= limit and abs(delta) > bound:
        return "improved" if delta > 0 else "regressed"
    return "flat"
