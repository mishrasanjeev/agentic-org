# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Run latency buckets: the bounds ``insights_daily.latency_buckets`` counts against, and percentiles from them.

Twelve buckets with inclusive upper bounds in milliseconds; the last one is
open ended. A run of ``ms`` milliseconds falls in the first bucket whose bound
is ``>= ms`` (so 500 ms is in bucket 0 and 501 ms in bucket 1). Summing the
bucket arrays of several daily rows gives the distribution over all of them,
and a percentile is estimated as the upper bound of the bucket that holds the
quantile; in the open-ended last bucket it is only a lower bound (see
``percentile_estimate``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

# Inclusive upper bounds, in milliseconds. The last bucket has no upper bound.
LATENCY_BOUNDS_MS: tuple[float, ...] = (
    500,
    1_000,
    2_000,
    5_000,
    10_000,
    20_000,
    30_000,
    60_000,
    120_000,
    300_000,
    600_000,
    math.inf,
)
BUCKET_COUNT = len(LATENCY_BOUNDS_MS)
# The finite bounds, in order; what the SQL bucketing compares against.
FINITE_BOUNDS_MS: tuple[int, ...] = tuple(int(bound) for bound in LATENCY_BOUNDS_MS if math.isfinite(bound))
# The open-ended last bucket starts above this.
OPEN_BUCKET_FLOOR_MS: int = FINITE_BOUNDS_MS[-1]


def empty_buckets() -> list[int]:
    """Twelve zero counts."""
    return [0] * BUCKET_COUNT


def bucket_index(ms: float | int) -> int:
    """The bucket a duration of ``ms`` milliseconds falls in (a negative duration counts as 0 ms)."""
    if ms is None or (isinstance(ms, float) and math.isnan(ms)):
        raise ValueError("a duration is a number of milliseconds")
    value = max(0.0, float(ms))
    for index, bound in enumerate(LATENCY_BOUNDS_MS):
        if value <= bound:
            return index
    return BUCKET_COUNT - 1  # unreachable: the last bound is +inf


def bucket_counts(durations_ms: Iterable[float | int | None]) -> list[int]:
    """Bucket counts of ``durations_ms``; ``None`` (an unmeasured run) is not counted."""
    counts = empty_buckets()
    for ms in durations_ms:
        if ms is None:
            continue
        counts[bucket_index(ms)] += 1
    return counts


def sum_buckets(arrays: Iterable[Sequence[int] | None]) -> list[int]:
    """Element-wise sum of bucket arrays (``None`` arrays are skipped)."""
    total = empty_buckets()
    for array in arrays:
        if array is None:
            continue
        if len(array) != BUCKET_COUNT:
            raise ValueError(f"a latency bucket array has {BUCKET_COUNT} counts, not {len(array)}")
        for index, count in enumerate(array):
            total[index] += int(count or 0)
    return total


def percentile_estimate(buckets: Sequence[int], q: float) -> tuple[float | None, bool]:
    """The estimated ``q`` quantile (``0 <= q <= 1``) of the durations counted in ``buckets``, in ms, and a flag.

    In a bounded bucket the estimate is the bucket's upper bound, so it does
    not understate latency, and the flag is False. A quantile that lands in
    the open-ended last bucket has no upper bound: the answer is the last
    finite bound, ``OPEN_BUCKET_FLOOR_MS`` (600000 ms, ten minutes), and the
    flag is True, meaning the true value is *more than* that (a lower bound,
    which understates). ``(None, False)`` when nothing was counted.
    """
    if len(buckets) != BUCKET_COUNT:
        raise ValueError(f"a latency bucket array has {BUCKET_COUNT} counts, not {len(buckets)}")
    q = float(q)
    if math.isnan(q) or q < 0 or q > 1:
        raise ValueError("a quantile is between 0 and 1")
    counts = [max(0, int(count or 0)) for count in buckets]
    total = sum(counts)
    if total == 0:
        return None, False
    # The rank of the quantile among the counted durations, 1-based.
    rank = max(1, math.ceil(q * total))
    cumulative = 0
    for index, count in enumerate(counts):
        cumulative += count
        if cumulative >= rank:
            bound = LATENCY_BOUNDS_MS[index]
            if math.isfinite(bound):
                return float(bound), False
            return float(OPEN_BUCKET_FLOOR_MS), True
    return float(OPEN_BUCKET_FLOOR_MS), True  # unreachable: cumulative reaches total


def percentile_from_buckets(buckets: Sequence[int], q: float) -> float | None:
    """The value of :func:`percentile_estimate` without its flag.

    An upper bound for a quantile in a bounded bucket; ``OPEN_BUCKET_FLOOR_MS``
    (a lower bound: the true value is more) for one in the open-ended last
    bucket; ``None`` when nothing was counted. Callers that show the number
    should use :func:`percentile_estimate` and say "over" when it is flagged.
    """
    return percentile_estimate(buckets, q)[0]
