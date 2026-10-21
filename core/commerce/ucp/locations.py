# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""UCP Location (``dev.ucp.common.location.search`` / ``.lookup``, 2026-08-25 only).

Locations are the merchant's enabled offline POS stores from the merchant
commerce config. A store may carry a ``location`` detail block (validated by
:func:`normalize_location_detail` when the config is saved)::

    {"timezone": "Asia/Kolkata",
     "address": {"street_address": ..., "address_locality": ..., "address_region": ...,
                 "postal_code": ..., "address_country": "IN"},
     "geo": {"latitude": 12.97, "longitude": 77.59},
     "hours": [{"day": "monday", "opens": "10:00", "closes": "20:00"}],
     "exception_hours": [{"valid_from": "2026-12-25", "valid_through": "2026-12-25", "title": "Holiday"}]}

Schedule semantics (2026-08-25 common/location, Operating Hours): values are
local civil date and time in the location's IANA ``timezone``; an interval is
open at ``opens`` and closed at ``closes``; ``closes`` earlier than ``opens``
continues into the next local date; ``00:00``-``23:59`` is the whole local date;
equal ``opens`` and ``closes`` is invalid. Evaluation converts the instant to
local date, weekday and time (zoneinfo), so DST gap labels match no instant and
both fold instants get the same result. An exception replaces the regular
schedule on every covered date, takes authority at local midnight over an
interval carried in from the day before, and a full closure (no ``opens`` /
``closes``) stands alone for its bounds; intersecting non-identical exception
ranges are invalid.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_ADDRESS_FIELDS: tuple[str, ...] = (
    "street_address",
    "extended_address",
    "address_locality",
    "address_region",
    "postal_code",
    "address_country",
)
MAX_HOURS = 50
MAX_EXCEPTIONS = 100
WHOLE_DAY = ("00:00", "23:59")


class LocationConfigError(ValueError):
    """A store's location detail is not a valid UCP schedule or address."""


def _parse_hhmm(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _HHMM_RE.fullmatch(value):
        raise LocationConfigError(f"{name} must be HH:MM (24-hour)")
    return value


def _parse_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise LocationConfigError(f"{name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LocationConfigError(f"{name} must be YYYY-MM-DD") from exc


def _text(value: Any, limit: int = 200) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    return text[:limit] or None


def normalize_location_detail(detail: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validate and normalize one store's ``location`` block; raises :class:`LocationConfigError`."""
    if detail is None:
        return None
    if not isinstance(detail, Mapping):
        raise LocationConfigError("location must be an object")
    result: dict[str, Any] = {}
    timezone = detail.get("timezone")
    hours = detail.get("hours")
    exceptions = detail.get("exception_hours")
    if timezone is not None:
        if not isinstance(timezone, str):
            raise LocationConfigError("timezone must be an IANA identifier")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise LocationConfigError("timezone must be an IANA identifier") from exc
        result["timezone"] = timezone
    if (hours is not None or exceptions is not None) and "timezone" not in result:
        raise LocationConfigError("timezone is required with hours or exception_hours")
    address = detail.get("address")
    if address is not None:
        if not isinstance(address, Mapping):
            raise LocationConfigError("address must be an object")
        clean = {field: text for field in _ADDRESS_FIELDS if (text := _text(address.get(field)))}
        if clean:
            result["address"] = clean
    geo = detail.get("geo")
    if geo is not None:
        if not isinstance(geo, Mapping):
            raise LocationConfigError("geo must be an object")
        latitude, longitude = geo.get("latitude"), geo.get("longitude")
        if (
            not isinstance(latitude, int | float)
            or isinstance(latitude, bool)
            or not isinstance(longitude, int | float)
            or isinstance(longitude, bool)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise LocationConfigError("geo needs latitude in [-90, 90] and longitude in [-180, 180]")
        result["geo"] = {"latitude": float(latitude), "longitude": float(longitude)}
    if hours is not None:
        result["hours"] = _normalize_hours(hours)
    if exceptions is not None:
        result["exception_hours"] = _normalize_exceptions(exceptions)
    return result


def _normalize_interval(item: Mapping[str, Any], name: str) -> tuple[str, str]:
    opens = _parse_hhmm(item.get("opens"), f"{name}.opens")
    closes = _parse_hhmm(item.get("closes"), f"{name}.closes")
    if opens == closes:
        raise LocationConfigError(f"{name}: opens and closes must differ")
    return opens, closes


def _normalize_hours(hours: Any) -> list[dict[str, str]]:
    if not isinstance(hours, Sequence) or isinstance(hours, str) or len(hours) > MAX_HOURS:
        raise LocationConfigError(f"hours must be a list of at most {MAX_HOURS} entries")
    result = []
    for index, item in enumerate(hours):
        if not isinstance(item, Mapping) or item.get("day") not in DAYS:
            raise LocationConfigError(f"hours[{index}].day must be a lowercase English weekday")
        opens, closes = _normalize_interval(item, f"hours[{index}]")
        result.append({"day": item["day"], "opens": opens, "closes": closes})
    return result


def _normalize_exceptions(exceptions: Any) -> list[dict[str, str]]:
    if not isinstance(exceptions, Sequence) or isinstance(exceptions, str) or len(exceptions) > MAX_EXCEPTIONS:
        raise LocationConfigError(f"exception_hours must be a list of at most {MAX_EXCEPTIONS} entries")
    result: list[dict[str, str]] = []
    for index, item in enumerate(exceptions):
        if not isinstance(item, Mapping):
            raise LocationConfigError(f"exception_hours[{index}] must be an object")
        start = _parse_date(item.get("valid_from"), f"exception_hours[{index}].valid_from")
        end = _parse_date(item.get("valid_through"), f"exception_hours[{index}].valid_through")
        if start > end:
            raise LocationConfigError(f"exception_hours[{index}]: valid_from is after valid_through")
        entry: dict[str, str] = {"valid_from": start.isoformat(), "valid_through": end.isoformat()}
        has_opens, has_closes = item.get("opens") is not None, item.get("closes") is not None
        if has_opens != has_closes:
            raise LocationConfigError(f"exception_hours[{index}]: opens and closes go together")
        if has_opens:
            entry["opens"], entry["closes"] = _normalize_interval(item, f"exception_hours[{index}]")
        title = _text(item.get("title"), 120)
        if title:
            entry["title"] = title
        result.append(entry)
    _check_exception_ranges(result)
    return result


def _check_exception_ranges(exceptions: list[dict[str, str]]) -> None:
    by_bounds: dict[tuple[str, str], list[dict[str, str]]] = {}
    for item in exceptions:
        by_bounds.setdefault((item["valid_from"], item["valid_through"]), []).append(item)
    for bounds, items in by_bounds.items():
        if len(items) > 1 and any("opens" not in item for item in items):
            raise LocationConfigError(f"a full closure stands alone for its bounds {bounds[0]}..{bounds[1]}")
    ranges = sorted(by_bounds)
    for (first_start, first_end), (second_start, second_end) in zip(ranges, ranges[1:], strict=False):
        if second_start <= first_end:
            raise LocationConfigError(
                f"exception ranges {first_start}..{first_end} and {second_start}..{second_end} intersect"
            )


def _exception_entries_for(location: Mapping[str, Any], day: date) -> list[Mapping[str, Any]] | None:
    """The exception entries governing ``day`` (None when no exception covers it)."""
    covering = [
        item
        for item in location.get("exception_hours") or ()
        if item["valid_from"] <= day.isoformat() <= item["valid_through"]
    ]
    return covering or None


def _intervals_starting(location: Mapping[str, Any], day: date) -> list[tuple[str, str]]:
    exceptions = _exception_entries_for(location, day)
    if exceptions is not None:
        return [(item["opens"], item["closes"]) for item in exceptions if "opens" in item]
    weekday = DAYS[day.weekday()]
    return [(item["opens"], item["closes"]) for item in location.get("hours") or () if item["day"] == weekday]


def is_open_at(location: Mapping[str, Any], instant: datetime) -> bool | None:
    """Whether ``location`` is open at the aware ``instant``; None when its schedule is unknown."""
    timezone = location.get("timezone")
    if not timezone or ("hours" not in location and "exception_hours" not in location):
        return None
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    local = instant.astimezone(ZoneInfo(timezone))
    today = local.date()
    clock = local.time().replace(second=0, microsecond=0, fold=0)
    now_minutes = clock.hour * 60 + clock.minute
    if "hours" not in location and _exception_entries_for(location, today) is None:
        return None
    for opens, closes in _intervals_starting(location, today):
        if (opens, closes) == WHOLE_DAY:
            return True
        start, end = _minutes(opens), _minutes(closes)
        if (start < end and start <= now_minutes < end) or (end < start and now_minutes >= start):
            return True
    if _exception_entries_for(location, today) is None:
        # An interval that began yesterday carries into today unless an exception governs today.
        for opens, closes in _intervals_starting(location, today - timedelta(days=1)):
            if (opens, closes) == WHOLE_DAY:
                continue
            start, end = _minutes(opens), _minutes(closes)
            if end < start and now_minutes < end:
                return True
    return False


def _minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def store_locations(offline_pos_stores: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """UCP ``location`` objects for the enabled POS stores, ordered by id."""
    locations = []
    for store in offline_pos_stores:
        if not isinstance(store, Mapping) or store.get("enabled") is False:
            continue
        store_id = _text(store.get("store_id"), 160)
        if not store_id:
            continue
        raw_detail = store.get("location")
        detail: Mapping[str, Any] = raw_detail if isinstance(raw_detail, Mapping) else {}
        location: dict[str, Any] = {"id": store_id, "name": _text(store.get("display_name"), 200) or store_id}
        address = dict(detail.get("address") or {})
        if "address_locality" not in address and _text(store.get("city")):
            address["address_locality"] = _text(store.get("city"))
        if "address_country" not in address and _text(store.get("country_code"), 3):
            address["address_country"] = _text(store.get("country_code"), 3)
        if address:
            location["address"] = address
        for field in ("geo", "hours", "exception_hours", "timezone"):
            if field in detail:
                location[field] = detail[field]
        locations.append(location)
    return sorted(locations, key=lambda item: item["id"])


def search_locations(
    locations: Sequence[Mapping[str, Any]],
    *,
    query: str | None,
    open_at: datetime | None,
    amenities: Sequence[str] | None,
) -> list[Mapping[str, Any]]:
    """Locations matching every supplied predicate (AND); an unknown schedule never matches ``open_at``."""
    tokens = [token for token in re.split(r"\s+", (query or "").lower()) if token]
    results = []
    for location in locations:
        if amenities:
            present = set((location.get("amenities") or {}).keys())
            if not set(amenities) <= present:
                continue
        if tokens:
            haystack = " ".join(
                [str(location.get("name") or "")] + [str(value) for value in (location.get("address") or {}).values()]
            ).lower()
            if not all(token in haystack for token in tokens):
                continue
        if open_at is not None and is_open_at(location, open_at) is not True:
            continue
        results.append(location)
    return results


def lookup_locations(
    locations: Sequence[Mapping[str, Any]],
    ids: Sequence[str],
) -> tuple[list[dict[str, Any]], list[int]]:
    """Locations for ``ids`` with their ``inputs``, and the positions of ids that matched nothing."""
    by_id = {location["id"]: location for location in locations}
    selected: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    missing: list[int] = []
    for index, request_id in enumerate(ids):
        if request_id not in by_id:
            missing.append(index)
            continue
        if request_id not in selected:
            selected[request_id] = []
            order.append(request_id)
        selected[request_id].append({"id": request_id})
    return [{**by_id[location_id], "inputs": selected[location_id]} for location_id in order], missing


def parse_open_at(value: str) -> datetime:
    """An RFC 3339 instant with ``Z`` or a numeric offset (location_filter.json ``hours.open_at``)."""
    if not re.search(r"(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$", value):
        raise ValueError("open_at needs Z or a numeric offset")
    parsed = datetime.fromisoformat(value.replace("z", "Z").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("open_at needs an offset")
    return parsed
