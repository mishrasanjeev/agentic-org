# SPDX-License-Identifier: Apache-2.0
"""The monthly partitions of ``spend_usage_records``.

Partitions are created by migrations only: strict runtimes forbid DDL at
startup and the runtime role may not own the table. Migration
``v6z80_spend_usage`` creates one partition per month from July 2026 to
December 2028 and a default partition, so a record past the horizon lands in
the default partition and is never lost. A daily beat logs
``spend_usage_partitions_horizon_low`` and ``GET /spend/status`` reports the
horizon when fewer than ``LOW_HORIZON_MONTHS`` named months remain; a later
migration adds months. No partition is detached in this phase.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.spend import clock

PARENT = "spend_usage_records"
FIRST_MONTH = (2026, 7)
LAST_MONTH = (2028, 12)
LOW_HORIZON_MONTHS = 6
DEFAULT_PARTITION = f"{PARENT}_default"


def _months(first: tuple[int, int], last: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    year, month = first
    out = []
    while (year, month) <= last:
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(out)


STATIC_MONTHS: tuple[tuple[int, int], ...] = _months(FIRST_MONTH, LAST_MONTH)


def partition_name(year: int, month: int) -> str:
    """``spend_usage_records_y2026m07``."""
    return f"{PARENT}_y{year:04d}m{month:02d}"


STATIC_PARTITIONS: tuple[str, ...] = (*(partition_name(y, m) for y, m in STATIC_MONTHS), DEFAULT_PARTITION)


def partition_ddl(year: int, month: int) -> list[str]:
    """The statement that creates one month's partition (UTC month bounds)."""
    following = (year + 1, 1) if month == 12 else (year, month + 1)
    return [
        f"CREATE TABLE IF NOT EXISTS {partition_name(year, month)} PARTITION OF {PARENT} "
        f"FOR VALUES FROM ('{year:04d}-{month:02d}-01 00:00:00+00') "
        f"TO ('{following[0]:04d}-{following[1]:02d}-01 00:00:00+00');"
    ]


def months_ahead(now: datetime | None = None) -> int:
    """Named months after ``now``'s UTC month (0 once the last named month is the current one or past)."""
    current = (clock.now_utc() if now is None else now).astimezone(UTC)
    year, month = current.year, current.month
    last_year, last_month = LAST_MONTH
    return max(0, (last_year - year) * 12 + (last_month - month))


async def horizon(now: datetime) -> dict[str, Any]:
    """``{"last_month", "months_ahead", "low"}`` for the static partitions at ``now``."""
    ahead = months_ahead(now)
    return {
        "last_month": f"{LAST_MONTH[0]:04d}-{LAST_MONTH[1]:02d}",
        "months_ahead": ahead,
        "low": ahead < LOW_HORIZON_MONTHS,
    }
