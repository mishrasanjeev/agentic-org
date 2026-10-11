# SPDX-License-Identifier: Apache-2.0
"""The two calendars of spend intelligence, and the one clock tests patch.

A record has an **event date**, its instant in the reporting zone
(``spend_reporting_timezone``, ``Asia/Kolkata`` by default): FX, rollups,
coverage and the Gate 1 attribution month use it. It also has a **billing
date**, its instant in the zone the provider closes its billing day in
(``spend_provider_billing_timezones_json``, then ``PROVIDER_BILLING_TZ_DEFAULTS``,
then UTC): rate cards, commitments, tiers and reconciliation use it. In-house
providers and platform storage bill in the reporting zone.

Nothing here reads the wall clock except ``now_utc``; every service takes
``now`` from its caller and falls back to ``now_utc`` only at a route or a task.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.config import settings
from core.spend import vocab
from core.spend.errors import SpendError

_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def now_utc() -> datetime:
    """The current instant, in UTC."""
    return datetime.now(UTC)


def reporting_zone() -> ZoneInfo:
    """The zone spend is reported in."""
    return ZoneInfo((settings.spend_reporting_timezone or "Asia/Kolkata").strip())


def _configured_billing_zones() -> dict[str, str]:
    raw = (settings.spend_provider_billing_timezones_json or "").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("spend_provider_billing_timezones_json must be a JSON object")
    return {str(key).strip().lower(): str(value).strip() for key, value in data.items()}


def billing_zone(provider: str) -> ZoneInfo:
    """The zone ``provider`` closes its billing day and month in."""
    name = (provider or "").strip().lower()
    if name in vocab.IN_HOUSE_PROVIDERS or name == vocab.STORAGE_PROVIDER:
        return reporting_zone()
    configured = _configured_billing_zones().get(name)
    if configured:
        return ZoneInfo(configured)
    default = vocab.PROVIDER_BILLING_TZ_DEFAULTS.get(name)
    return ZoneInfo(default) if default else ZoneInfo("UTC")


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def event_date_of(ts: datetime) -> date:
    """The reporting date of an instant (a naive instant is read as UTC)."""
    return _aware(ts).astimezone(reporting_zone()).date()


def billing_date_of(provider: str, ts: datetime) -> date:
    """The provider's billing date of an instant (a naive instant is read as UTC)."""
    return _aware(ts).astimezone(billing_zone(provider)).date()


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` of ``day`` in ``zone``, as UTC instants."""
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def next_month(day: date) -> date:
    """The first day of the month after ``day``'s month."""
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def days_in_month(day: date) -> int:
    """Days in ``day``'s month (29 in a leap February)."""
    return calendar.monthrange(day.year, day.month)[1]


def month_bounds(period: str, zone: ZoneInfo) -> tuple[date, date, datetime, datetime]:
    """``"YYYY-MM"`` -> (first day, first day of the next month, and both as UTC instants in ``zone``)."""
    match = _PERIOD_RE.fullmatch(str(period or "").strip())
    if not match:
        raise SpendError(422, "invalid_period", "a period is a month YYYY-MM")
    try:
        first = date(int(match.group(1)), int(match.group(2)), 1)
        following = next_month(first)
        return first, following, day_bounds(first, zone)[0], day_bounds(following, zone)[0]
    except (ValueError, OverflowError):
        # Year 0000, or a month whose bounds fall outside the calendar (9999-12): a bad request, not a 500.
        raise SpendError(422, "invalid_period", "a period is a month YYYY-MM within the calendar") from None


def today_in(zone: ZoneInfo, now: datetime) -> date:
    """The date ``now`` falls on in ``zone``."""
    return _aware(now).astimezone(zone).date()
