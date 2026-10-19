# SPDX-License-Identifier: Apache-2.0
"""UCP Location: store locations, timezone-aware regular and exception hours across DST (G28)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from core.commerce.oacp_merchant_config import MerchantCommerceConfigError, normalize_merchant_commerce_config
from core.commerce.ucp import v2026_08_25
from core.commerce.ucp.locations import (
    LocationConfigError,
    is_open_at,
    lookup_locations,
    normalize_location_detail,
    parse_open_at,
    search_locations,
    store_locations,
)
from tests.ucp_fixtures import store_with_hours
from tests.ucp_schema_validation import R25_LOCATION_LOOKUP, R25_LOCATION_SEARCH, assert_ucp_valid

NEW_YORK = {
    "id": "loc_ny",
    "name": "Example New York",
    "timezone": "America/New_York",
    "hours": [
        {"day": "sunday", "opens": "01:00", "closes": "04:00"},
        {"day": "saturday", "opens": "22:00", "closes": "02:00"},
        {"day": "monday", "opens": "09:00", "closes": "17:00"},
    ],
}


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_spring_forward_gap_is_evaluated_on_local_clock() -> None:
    # 2026-03-08 02:00 EST -> 03:00 EDT. Sunday 01:00-04:00 local.
    assert is_open_at(NEW_YORK, _utc(2026, 3, 8, 5, 30)) is True  # 00:30 EST, carried over from Saturday 22-02
    assert is_open_at(NEW_YORK, _utc(2026, 3, 8, 6, 30)) is True  # 01:30 EST
    assert is_open_at(NEW_YORK, _utc(2026, 3, 8, 7, 30)) is True  # 03:30 EDT (02:xx labels do not exist)
    assert is_open_at(NEW_YORK, _utc(2026, 3, 8, 8, 0)) is False  # 04:00 EDT: closed at closes


def test_fall_back_fold_instants_get_the_same_result() -> None:
    # 2026-11-01 02:00 EDT -> 01:00 EST: 01:30 local happens twice; both are open (01:00-04:00).
    first = _utc(2026, 11, 1, 5, 30)  # 01:30 EDT
    second = _utc(2026, 11, 1, 6, 30)  # 01:30 EST
    assert is_open_at(NEW_YORK, first) is True
    assert is_open_at(NEW_YORK, second) is True
    assert is_open_at(NEW_YORK, _utc(2026, 11, 1, 9, 0)) is False  # 04:00 EST


def test_overnight_interval_carries_into_the_next_day_and_closes_at_closes() -> None:
    assert is_open_at(NEW_YORK, _utc(2026, 10, 24, 3, 0)) is False  # Fri 23:00 EDT
    assert is_open_at(NEW_YORK, _utc(2026, 10, 25, 3, 0)) is True  # Sat 23:00 EDT
    assert is_open_at(NEW_YORK, _utc(2026, 10, 25, 5, 59)) is True  # Sun 01:59 EDT (both intervals)
    assert is_open_at(NEW_YORK, _utc(2026, 10, 26, 13, 0)) is True  # Mon 09:00 EDT opens
    assert is_open_at(NEW_YORK, _utc(2026, 10, 26, 21, 0)) is False  # Mon 17:00 EDT closed


def test_exception_replaces_regular_hours_and_takes_authority_at_midnight() -> None:
    location = {
        **NEW_YORK,
        "exception_hours": [
            {"valid_from": "2026-10-25", "valid_through": "2026-10-25", "title": "Closed"},
            {"valid_from": "2026-10-26", "valid_through": "2026-10-27", "opens": "12:00", "closes": "14:00"},
            {"valid_from": "2026-10-26", "valid_through": "2026-10-27", "opens": "16:00", "closes": "18:00"},
        ],
    }
    # Saturday 22:00-02:00 would carry into Sunday, but Sunday is a full closure.
    assert is_open_at(location, _utc(2026, 10, 25, 3, 0)) is True  # Sat 23:00 EDT
    assert is_open_at(location, _utc(2026, 10, 25, 4, 30)) is False  # Sun 00:30 EDT
    assert is_open_at(location, _utc(2026, 10, 26, 14, 0)) is False  # Mon 10:00 EDT (regular hours replaced)
    assert is_open_at(location, _utc(2026, 10, 26, 16, 30)) is True  # Mon 12:30 split shift 1
    assert is_open_at(location, _utc(2026, 10, 27, 21, 0)) is True  # Tue 17:00 split shift 2


def test_whole_day_and_unknown_schedules() -> None:
    whole_day = {
        "id": "x",
        "name": "x",
        "timezone": "Asia/Kolkata",
        "hours": [{"day": "monday", "opens": "00:00", "closes": "23:59"}],
    }
    assert (
        is_open_at(whole_day, datetime(2026, 10, 19, 23, 59, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        is True
    )
    assert is_open_at({"id": "y", "name": "y"}, _utc(2026, 10, 19, 10, 0)) is None
    only_exceptions = {
        "id": "z",
        "name": "z",
        "timezone": "UTC",
        "exception_hours": [{"valid_from": "2026-10-19", "valid_through": "2026-10-19"}],
    }
    assert is_open_at(only_exceptions, _utc(2026, 10, 19, 10, 0)) is False
    assert is_open_at(only_exceptions, _utc(2026, 10, 20, 10, 0)) is None
    with pytest.raises(ValueError):
        is_open_at(NEW_YORK, datetime(2026, 10, 19, 10, 0))  # noqa: DTZ001


@pytest.mark.parametrize(
    "detail",
    [
        {"hours": [{"day": "monday", "opens": "09:00", "closes": "17:00"}]},
        {"timezone": "Mars/Olympus", "hours": []},
        {"timezone": "UTC", "hours": [{"day": "Monday", "opens": "09:00", "closes": "17:00"}]},
        {"timezone": "UTC", "hours": [{"day": "monday", "opens": "09:00", "closes": "09:00"}]},
        {"timezone": "UTC", "hours": [{"day": "monday", "opens": "9:00", "closes": "17:00"}]},
        {"timezone": "UTC", "exception_hours": [{"valid_from": "2026-12-26", "valid_through": "2026-12-24"}]},
        {
            "timezone": "UTC",
            "exception_hours": [{"valid_from": "2026-12-24", "valid_through": "2026-12-24", "opens": "10:00"}],
        },
        {
            "timezone": "UTC",
            "exception_hours": [
                {"valid_from": "2026-12-24", "valid_through": "2026-12-26"},
                {"valid_from": "2026-12-25", "valid_through": "2026-12-25"},
            ],
        },
        {
            "timezone": "UTC",
            "exception_hours": [
                {"valid_from": "2026-12-24", "valid_through": "2026-12-24"},
                {"valid_from": "2026-12-24", "valid_through": "2026-12-24", "opens": "10:00", "closes": "12:00"},
            ],
        },
        {"geo": {"latitude": 91, "longitude": 0}},
    ],
)
def test_invalid_schedules_are_refused(detail) -> None:
    with pytest.raises(LocationConfigError):
        normalize_location_detail(detail)


def test_pos_store_location_is_validated_in_merchant_config() -> None:
    base = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "merchant_id": "merchant_1",
        "seller_agent_id": "seller_agent_1",
        "merchant_display_name": "Merchant Example",
        "public_brand_profile": {},
        "commerce_categories": ["general"],
        "source_connectors": [
            {"connector_type": "shopify", "mode": "read_only", "credential_custody": "agenticorg_vault"}
        ],
        "buyer_channels": {"web": True},
        "payment_providers": [],
        "public_publishing": {},
        "source_freshness_policy": None,
        "provider_policy": None,
    }
    config = normalize_merchant_commerce_config(**base, offline_pos_stores=[store_with_hours()])
    assert config["offline_pos_stores"][0]["location"]["timezone"] == "Asia/Kolkata"
    without = normalize_merchant_commerce_config(**base, offline_pos_stores=[{"store_id": "s1", "city": "Pune"}])
    assert "location" not in without["offline_pos_stores"][0]
    with pytest.raises(MerchantCommerceConfigError):
        normalize_merchant_commerce_config(
            **base, offline_pos_stores=[{"store_id": "s1", "location": {"timezone": "Nowhere/Else", "hours": []}}]
        )


def test_search_and_lookup_render_schema_valid_location_responses() -> None:
    stores = [
        store_with_hours(),
        {"store_id": "store_mumbai", "display_name": "Mumbai", "city": "Mumbai", "country_code": "IN"},
        {"store_id": "off", "enabled": False},
    ]
    locations = store_locations(stores)
    assert [item["id"] for item in locations] == ["store_mumbai", "store_pune_1"]
    pune = locations[1]
    assert pune["address"]["address_locality"] == "Pune" and pune["timezone"] == "Asia/Kolkata"

    open_at = parse_open_at("2026-10-19T12:00:00+05:30")  # Monday noon in Pune
    matches = search_locations(locations, query="pune", open_at=open_at, amenities=None)
    assert [item["id"] for item in matches] == ["store_pune_1"]
    festival = parse_open_at("2026-10-21T06:30:00Z")  # Wednesday noon, festival closure
    assert search_locations(locations, query=None, open_at=festival, amenities=None) == []
    assert search_locations(locations, query=None, open_at=None, amenities=["dev.ucp.amenity.wi_fi"]) == []

    body = v2026_08_25.render_location_response(
        capability="dev.ucp.common.location.search",
        locations=[dict(item) for item in matches],
        messages=[],
        pagination={"has_next_page": False},
    )
    assert_ucp_valid(body, R25_LOCATION_SEARCH)
    found, missing = lookup_locations(locations, ["store_pune_1", "nope", "store_pune_1"])
    assert missing == [1]
    assert found[0]["inputs"] == [{"id": "store_pune_1"}, {"id": "store_pune_1"}]
    body = v2026_08_25.render_location_response(
        capability="dev.ucp.common.location.lookup",
        locations=found,
        messages=[{"type": "info", "code": "not_found", "content": "No location matches requested identifier 2."}],
        pagination=None,
    )
    assert_ucp_valid(body, R25_LOCATION_LOOKUP)
    with pytest.raises(ValueError):
        parse_open_at("2026-10-19T12:00:00")
