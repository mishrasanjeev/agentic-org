# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Five-field cron schedules, evaluated in a time zone.

``next_after(expression, after, zone)`` is the first minute strictly after
``after`` that matches ``minute hour day-of-month month day-of-week`` in
``zone`` (as an aware UTC instant). Fields take ``*``, numbers, ranges
(``1-5``), steps (``*/15``, ``0-30/10``) and lists of these; day of week runs
0-7 with both 0 and 7 for Sunday. When both day fields are restricted a day
matches either (the cron rule). ``croniter`` is not a dependency: without this
the scheduler fell back to "tomorrow" for every cron string, so weekly and
monthly schedules ran daily.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
# Far enough for any valid expression (29 February comes at least once in eight years).
_SEARCH_DAYS = 366 * 8


class CronError(ValueError):
    """Not a five-field cron expression this evaluator reads."""


def _field(text: str, lo: int, hi: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            if not raw_step.isdigit() or int(raw_step) < 1:
                raise CronError(f"bad step {raw_step!r}")
            step = int(raw_step)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"bad range {part!r}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
            if step != 1:
                end = hi
        else:
            raise CronError(f"bad value {part!r}")
        if not (lo <= start <= end <= hi):
            raise CronError(f"{part!r} is outside {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse(expression: str) -> tuple[frozenset[int], ...]:
    fields = (expression or "").split()
    if len(fields) != 5:
        raise CronError("a cron expression has five fields")
    parsed = [_field(text, lo, hi) for text, (lo, hi) in zip(fields, _RANGES, strict=True)]
    # Day of week 7 is Sunday, as 0 is.
    if 7 in parsed[4]:
        parsed[4] = frozenset((parsed[4] - {7}) | {0})
    return tuple(parsed)


def _day_matches(day: date, dom: frozenset[int], dow: frozenset[int], dom_any: bool, dow_any: bool) -> bool:
    weekday = (day.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0
    in_dom, in_dow = day.day in dom, weekday in dow
    if dom_any and dow_any:
        return True
    if dom_any:
        return in_dow
    if dow_any:
        return in_dom
    return in_dom or in_dow


def next_after(expression: str, after: datetime, zone: ZoneInfo | None = None) -> datetime:
    """The next matching minute strictly after ``after``, in ``zone`` (UTC when None), as a UTC instant."""
    minutes, hours, dom, months, dow = parse(expression)
    fields = expression.split()
    dom_any, dow_any = fields[2] == "*", fields[4] == "*"
    zone = zone or ZoneInfo("UTC")
    local = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = local.date()
    for offset in range(_SEARCH_DAYS):
        current = day + timedelta(days=offset)
        if current.month not in months or not _day_matches(current, dom, dow, dom_any, dow_any):
            continue
        for hour in sorted(hours):
            for minute in sorted(minutes):
                candidate = datetime(current.year, current.month, current.day, hour, minute, tzinfo=zone)
                if candidate >= local:
                    return candidate.astimezone(UTC)
    raise CronError("no time matches this expression")
