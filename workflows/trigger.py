"""Workflow trigger types including schedule (cron) support."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Lightweight cron expression matcher
# ---------------------------------------------------------------------------
# Supports standard 5-field cron: minute hour day-of-month month day-of-week
# Each field accepts: *, integers, ranges (1-5), step values (*/10), and
# comma-separated lists (1,3,5).  Day-of-week: 0=Sun..6=Sat (7 also = Sun).
# ---------------------------------------------------------------------------

_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3,
    "thu": 4, "fri": 5, "sat": 6,
}

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _replace_names(field: str, mapping: dict[str, int]) -> str:
    """Replace three-letter name tokens with their numeric equivalents."""
    for name, num in mapping.items():
        field = re.sub(rf"\b{name}\b", str(num), field, flags=re.IGNORECASE)
    return field


def _field_matches(field_expr: str, value: int, min_val: int, max_val: int) -> bool:
    """Return True if *value* matches the cron *field_expr*."""
    for part in field_expr.split(","):
        part = part.strip()
        if part == "*":
            return True

        # Step: */N or M-N/S
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if range_part == "*":
                r_start, r_end = min_val, max_val
            elif "-" in range_part:
                r_start, r_end = (int(x) for x in range_part.split("-", 1))
            else:
                r_start, r_end = int(range_part), max_val
            if value in range(r_start, r_end + 1, step):
                return True
            continue

        # Range: M-N
        if "-" in part:
            r_start, r_end = (int(x) for x in part.split("-", 1))
            if r_start <= value <= r_end:
                return True
            continue

        # Literal
        if int(part) == value:
            return True

    return False


def cron_matches(expression: str, dt: datetime | None = None) -> bool:
    """Check whether *dt* (default: now UTC) matches a 5-field cron *expression*.

    Fields: minute  hour  day-of-month  month  day-of-week
    """
    if dt is None:
        dt = datetime.now(UTC)

    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields, got {len(fields)}: '{expression}'"
        )

    minute_f, hour_f, dom_f, month_f, dow_f = fields

    # Normalise name tokens.
    month_f = _replace_names(month_f, _MONTH_NAMES)
    dow_f = _replace_names(dow_f, _DOW_NAMES)

    # day-of-week: normalise 7 -> 0 (both represent Sunday).
    dt.weekday()  # Python: Monday=0 .. Sunday=6
    # Cron convention: Sunday=0, Monday=1 .. Saturday=6
    cron_dow = (dt.isoweekday() % 7)  # isoweekday: Mon=1..Sun=7 -> Sun=0

    return (
        _field_matches(minute_f, dt.minute, 0, 59)
        and _field_matches(hour_f, dt.hour, 0, 23)
        and _field_matches(dom_f, dt.day, 1, 31)
        and _field_matches(month_f, dt.month, 1, 12)
        and _field_matches(dow_f, cron_dow, 0, 6)
    )


# ---------------------------------------------------------------------------
# Trigger class
# ---------------------------------------------------------------------------

class WorkflowTrigger:
    """Determine whether an incoming event should start a workflow.

    Supported trigger types:
      - manual        — always matches
      - webhook       — always matches (webhook routing handled upstream)
      - email_received — matches when subject contains configured keywords
      - api_event     — matches on event_type equality
      - schedule      — matches when current (or supplied) time satisfies a cron expression
    """

    def __init__(self, trigger_type: str, config: dict[str, Any] | None = None):
        self.trigger_type = trigger_type
        self.config = config or {}

    def matches(self, event: dict[str, Any]) -> bool:
        if self.trigger_type == "manual":
            return True

        if self.trigger_type == "webhook":
            return True

        if self.trigger_type == "email_received":
            subject = event.get("subject", "")
            filters = self.config.get("filter", {}).get("subject_contains", [])
            return any(f.lower() in subject.lower() for f in filters)

        if self.trigger_type == "api_event":
            return event.get("event_type") == self.config.get("event_type")

        if self.trigger_type == "schedule":
            return self._matches_schedule(event)

        return False

    # ------------------------------------------------------------------
    # Schedule matching
    # ------------------------------------------------------------------

    def _matches_schedule(self, event: dict[str, Any]) -> bool:
        """Evaluate schedule trigger.

        Config expects::

            {
                "cron": "0 9 * * 1-5",       # 5-field cron expression
                "timezone": "UTC"              # optional, defaults to UTC
            }

        The event may optionally carry ``check_time`` (ISO-8601 string) to
        override the current time — useful for testing and back-fill checks.
        """
        cron_expr = self.config.get("cron")
        if not cron_expr:
            return False

        check_time_raw = event.get("check_time")
        if check_time_raw:
            try:
                check_time = datetime.fromisoformat(check_time_raw)
                if check_time.tzinfo is None:
                    check_time = check_time.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                check_time = datetime.now(UTC)
        else:
            check_time = datetime.now(UTC)

        try:
            return cron_matches(cron_expr, check_time)
        except ValueError:
            return False
