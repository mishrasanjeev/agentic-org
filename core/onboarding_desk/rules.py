# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Whether a lead may be contacted on a channel now, and why not.

The desk contacts only people who agreed to be contacted on that channel, on
no suppression list, inside the hours the law allows where they are, and within
an attempt limit. The defaults below are floors a partner may narrow and never
widen; they are a conservative reading to be confirmed with counsel, not legal
advice:

- United States: calls and texts between 08:00 and 21:00 in the recipient's
  local time (the federal telephone-solicitation hours).
- Canada: telemarketing between 09:00 and 21:30 on weekdays and 10:00 and
  18:00 on weekends, recipient's local time.
- Florida, Oklahoma and Maryland: 08:00 to 20:00 and at most three attempts on
  the same subject in 24 hours, the stricter state rules.
- An AI or prerecorded voice call needs prior express written consent, so a
  voice consent counts only when it was captured in writing.
- Where a state or province spans several time zones, only the hours that are
  allowed in every one of them are used.

Email is not bound to hours; it still needs consent and no suppression. The
web channel is never initiated by the desk: an applicant opens it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

CHANNELS: tuple[str, ...] = ("sms", "email", "whatsapp", "voice", "web")
OUTREACH_CHANNELS: tuple[str, ...] = ("sms", "email", "whatsapp", "voice")
TIMED_CHANNELS: frozenset[str] = frozenset({"sms", "whatsapp", "voice"})
COUNTRIES: tuple[str, ...] = ("US", "CA")
PURPOSE = "onboarding"

# Region code -> the IANA zones in use there. Several zones mean the window is
# the intersection: a contact is allowed only when it is allowed in all of them.
US_REGIONS: dict[str, tuple[str, ...]] = {
    "AL": ("America/Chicago",),
    "AK": ("America/Anchorage", "America/Adak"),
    "AZ": ("America/Phoenix", "America/Denver"),
    "AR": ("America/Chicago",),
    "CA": ("America/Los_Angeles",),
    "CO": ("America/Denver",),
    "CT": ("America/New_York",),
    "DE": ("America/New_York",),
    "DC": ("America/New_York",),
    "FL": ("America/New_York", "America/Chicago"),
    "GA": ("America/New_York",),
    "HI": ("Pacific/Honolulu",),
    "ID": ("America/Boise", "America/Los_Angeles"),
    "IL": ("America/Chicago",),
    "IN": ("America/Indiana/Indianapolis", "America/Chicago"),
    "IA": ("America/Chicago",),
    "KS": ("America/Chicago", "America/Denver"),
    "KY": ("America/New_York", "America/Chicago"),
    "LA": ("America/Chicago",),
    "ME": ("America/New_York",),
    "MD": ("America/New_York",),
    "MA": ("America/New_York",),
    "MI": ("America/Detroit", "America/Menominee"),
    "MN": ("America/Chicago",),
    "MS": ("America/Chicago",),
    "MO": ("America/Chicago",),
    "MT": ("America/Denver",),
    "NE": ("America/Chicago", "America/Denver"),
    "NV": ("America/Los_Angeles",),
    "NH": ("America/New_York",),
    "NJ": ("America/New_York",),
    "NM": ("America/Denver",),
    "NY": ("America/New_York",),
    "NC": ("America/New_York",),
    "ND": ("America/Chicago", "America/Denver"),
    "OH": ("America/New_York",),
    "OK": ("America/Chicago",),
    "OR": ("America/Los_Angeles", "America/Boise"),
    "PA": ("America/New_York",),
    "RI": ("America/New_York",),
    "SC": ("America/New_York",),
    "SD": ("America/Chicago", "America/Denver"),
    "TN": ("America/New_York", "America/Chicago"),
    "TX": ("America/Chicago", "America/Denver"),
    "UT": ("America/Denver",),
    "VT": ("America/New_York",),
    "VA": ("America/New_York",),
    "WA": ("America/Los_Angeles",),
    "WV": ("America/New_York",),
    "WI": ("America/Chicago",),
    "WY": ("America/Denver",),
}
CA_REGIONS: dict[str, tuple[str, ...]] = {
    "AB": ("America/Edmonton",),
    "BC": ("America/Vancouver", "America/Edmonton"),
    "MB": ("America/Winnipeg",),
    "NB": ("America/Moncton",),
    "NL": ("America/St_Johns", "America/Goose_Bay"),
    "NS": ("America/Halifax",),
    "NT": ("America/Yellowknife",),
    "NU": ("America/Iqaluit", "America/Rankin_Inlet", "America/Cambridge_Bay"),
    "ON": ("America/Toronto", "America/Winnipeg"),
    "PE": ("America/Halifax",),
    "QC": ("America/Toronto", "America/Halifax"),
    "SK": ("America/Regina",),
    "YT": ("America/Whitehorse",),
}
REGIONS: dict[str, dict[str, tuple[str, ...]]] = {"US": US_REGIONS, "CA": CA_REGIONS}
FRENCH_DEFAULT_REGIONS: frozenset[tuple[str, str]] = frozenset({("CA", "QC")})

# (start, end) per weekday; Monday is 0.
Window = tuple[time, time]
_US_DAY: Window = (time(8, 0), time(21, 0))
_STRICT_US_DAY: Window = (time(8, 0), time(20, 0))
_CA_WEEKDAY: Window = (time(9, 0), time(21, 30))
_CA_WEEKEND: Window = (time(10, 0), time(18, 0))
STRICT_US_STATES: frozenset[str] = frozenset({"FL", "OK", "MD"})
STRICT_ATTEMPTS_PER_DAY = 3
DEFAULT_ATTEMPTS_PER_DAY = 3
MAX_REMINDERS = 3


def floor_windows(country: str, region: str) -> dict[int, Window]:
    """The legal floor of permitted hours for a region, per weekday."""
    if country == "US":
        day = _STRICT_US_DAY if region in STRICT_US_STATES else _US_DAY
        return dict.fromkeys(range(7), day)
    if country == "CA":
        return {d: (_CA_WEEKDAY if d < 5 else _CA_WEEKEND) for d in range(7)}
    return {}


def floor_attempts(country: str, region: str) -> int:
    if country == "US" and region in STRICT_US_STATES:
        return STRICT_ATTEMPTS_PER_DAY
    return DEFAULT_ATTEMPTS_PER_DAY


def zones_for(country: str, region: str) -> tuple[str, ...]:
    return REGIONS.get(country, {}).get(region, ())


def default_locale(country: str, region: str) -> str:
    return "fr" if (country, region) in FRENCH_DEFAULT_REGIONS else "en"


@dataclass(frozen=True)
class Policy:
    """A partner's contact policy: it can only narrow the legal floor."""

    channels: tuple[str, ...] = OUTREACH_CHANNELS
    start: time | None = None  # a later start than the floor
    end: time | None = None  # an earlier end than the floor
    attempts_per_day: int | None = None
    max_reminders: int = MAX_REMINDERS
    min_hours_between_reminders: int = 24
    weekends: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": list(self.channels),
            "start": self.start.strftime("%H:%M") if self.start else None,
            "end": self.end.strftime("%H:%M") if self.end else None,
            "attempts_per_day": self.attempts_per_day,
            "max_reminders": self.max_reminders,
            "min_hours_between_reminders": self.min_hours_between_reminders,
            "weekends": self.weekends,
        }


def _hhmm(value: Any, name: str) -> time | None:
    if value in (None, ""):
        return None
    try:
        hours, minutes = str(value).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be HH:MM") from exc


def policy_from(raw: dict[str, Any] | None) -> Policy:
    """A policy from stored or submitted settings; ``ValueError`` names the field that is wrong."""
    raw = dict(raw or {})
    channels = raw.get("channels", list(OUTREACH_CHANNELS))
    if not isinstance(channels, list) or any(c not in OUTREACH_CHANNELS for c in channels):
        raise ValueError(f"channels must be a subset of {list(OUTREACH_CHANNELS)}")
    start, end = _hhmm(raw.get("start"), "start"), _hhmm(raw.get("end"), "end")
    if start and end and start >= end:
        raise ValueError("start must be before end")
    attempts = raw.get("attempts_per_day")
    if attempts is not None and (not isinstance(attempts, int) or not 1 <= attempts <= DEFAULT_ATTEMPTS_PER_DAY):
        raise ValueError(f"attempts_per_day must be between 1 and {DEFAULT_ATTEMPTS_PER_DAY}")
    reminders = raw.get("max_reminders", MAX_REMINDERS)
    if not isinstance(reminders, int) or not 0 <= reminders <= MAX_REMINDERS:
        raise ValueError(f"max_reminders must be between 0 and {MAX_REMINDERS}")
    gap = raw.get("min_hours_between_reminders", 24)
    if not isinstance(gap, int) or not 4 <= gap <= 24 * 14:
        raise ValueError("min_hours_between_reminders must be between 4 and 336")
    weekends = raw.get("weekends", True)
    if not isinstance(weekends, bool):
        raise ValueError("weekends must be true or false")
    return Policy(tuple(channels), start, end, attempts, reminders, gap, weekends)


def _effective_window(policy: Policy, floor: Window, weekday: int) -> Window | None:
    if not policy.weekends and weekday >= 5:
        return None
    start = max(floor[0], policy.start) if policy.start else floor[0]
    end = min(floor[1], policy.end) if policy.end else floor[1]
    return (start, end) if start < end else None


def within_hours(country: str, region: str, policy: Policy, now: datetime) -> bool:
    """Whether ``now`` is inside the permitted window in every zone of the region."""
    zones = zones_for(country, region)
    floors = floor_windows(country, region)
    if not zones or not floors:
        return False
    for zone in zones:
        local = now.astimezone(ZoneInfo(zone))
        window = _effective_window(policy, floors[local.weekday()], local.weekday())
        if window is None or not (window[0] <= local.time() < window[1]):
            return False
    return True


def next_allowed(country: str, region: str, policy: Policy, now: datetime) -> datetime | None:
    """The first moment, in 15-minute steps over the next eight days, when contact is allowed."""
    if not zones_for(country, region):
        return None
    probe = now.astimezone(UTC).replace(second=0, microsecond=0)
    probe += timedelta(minutes=(15 - probe.minute % 15) % 15)
    for _ in range(8 * 24 * 4):
        if within_hours(country, region, policy, probe):
            return probe
        probe += timedelta(minutes=15)
    return None


@dataclass(frozen=True)
class ConsentView:
    channel: str
    granted: bool
    written: bool = False
    expires_at: datetime | None = None


@dataclass(frozen=True)
class LeadView:
    country: str
    region: str
    opted_out: bool = False
    suppressed_channels: frozenset[str] = frozenset()  # "all" suppresses every channel
    has_email: bool = False
    has_phone: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    next_allowed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "next_allowed_at": self.next_allowed_at.isoformat() if self.next_allowed_at else None,
            **({"details": self.details} if self.details else {}),
        }


def decide(
    lead: LeadView,
    channel: str,
    consents: list[ConsentView],
    *,
    policy: Policy,
    now: datetime,
    attempts_last_24h: int = 0,
    reminders_sent: int = 0,
    is_reminder: bool = False,
    last_attempt_at: datetime | None = None,
) -> Decision:
    """Whether the desk may contact ``lead`` on ``channel`` at ``now``; the first rule that refuses wins."""
    if channel not in CHANNELS:
        return Decision(False, "channel_unknown")
    if channel == "web":
        return Decision(False, "channel_not_initiated", details={"note": "the applicant opens the web channel"})
    if lead.country not in COUNTRIES:
        return Decision(False, "country_not_supported")
    if not zones_for(lead.country, lead.region):
        return Decision(False, "region_required")
    if channel not in policy.channels:
        return Decision(False, "channel_not_allowed")
    if lead.opted_out or "all" in lead.suppressed_channels or channel in lead.suppressed_channels:
        return Decision(False, "opted_out")
    if channel == "email" and not lead.has_email:
        return Decision(False, "no_email")
    if channel in TIMED_CHANNELS and not lead.has_phone:
        return Decision(False, "no_phone")
    current = [c for c in consents if c.channel == channel]
    if not current or not any(c.granted for c in current):
        return Decision(False, "no_consent")
    live = [c for c in current if c.granted and (c.expires_at is None or c.expires_at > now)]
    if not live:
        return Decision(False, "consent_expired")
    if channel == "voice" and not any(c.written for c in live):
        return Decision(False, "voice_needs_written_consent")
    if is_reminder:
        if reminders_sent >= policy.max_reminders:
            return Decision(False, "reminder_limit")
        if last_attempt_at and now - last_attempt_at < timedelta(hours=policy.min_hours_between_reminders):
            return Decision(
                False,
                "reminder_too_soon",
                last_attempt_at + timedelta(hours=policy.min_hours_between_reminders),
            )
    if channel in TIMED_CHANNELS:
        limit = min(
            policy.attempts_per_day or floor_attempts(lead.country, lead.region),
            floor_attempts(lead.country, lead.region),
        )
        if attempts_last_24h >= limit:
            return Decision(False, "attempt_limit", details={"limit": limit})
        if not within_hours(lead.country, lead.region, policy, now):
            return Decision(False, "outside_hours", next_allowed(lead.country, lead.region, policy, now))
    return Decision(True, "allowed")
