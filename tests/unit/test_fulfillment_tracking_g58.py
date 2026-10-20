# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""G58: Shopify fulfillment payloads normalise to UCP order fulfillment events; replays are idempotent."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from core.commerce.fulfillment_tracking import (
    STATUS_MAP,
    TrackingNormalisationError,
    normalise_carrier,
    normalise_shopify_fulfillment,
)
from core.commerce.ucp.orders import DuplicateOrderEventError, apply_fulfillment_event

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ORDER = {
    "line_items": [
        {"id": "li_1", "item": {"id": "TOTE-1"}, "quantity": {"original": 2, "total": 2, "fulfilled": 0}},
        {"id": "li_2", "item": {"id": "FABRIC-1"}, "quantity": {"original": 1, "total": 1, "fulfilled": 0}},
    ],
    "fulfillment": {"expectations": [], "events": []},
}
FULFILLMENT = {
    "id": 4501,
    "status": "success",
    "tracking_company": "  Acme   Express ",
    "tracking_numbers": ["AX123"],
    "tracking_urls": ["https://track.carrier.example/AX123"],
    "created_at": "2026-10-20T08:00:00+05:30",
    "line_items": [{"id": 1, "sku": "TOTE-1", "variant_id": 991, "quantity": 2}],
}


def test_created_fulfillment_is_shipped_with_tracking_and_normalised_carrier() -> None:
    result = normalise_shopify_fulfillment(FULFILLMENT, ORDER, carrier_aliases={"acme express": "Acme Express Ltd"})
    assert result is not None
    assert result.event == {
        "type": "shipped",
        "line_items": [{"id": "li_1", "quantity": 2}],
        "tracking_number": "AX123",
        "tracking_url": "https://track.carrier.example/AX123",
        "carrier": "Acme Express Ltd",
        "occurred_at": "2026-10-20T02:30:00Z",
    }
    assert result.event_id.startswith("evt_")


@pytest.mark.parametrize(("shopify", "ucp"), sorted(STATUS_MAP.items()))
def test_every_shopify_shipment_status_maps_to_a_ucp_event_type(shopify: str, ucp: str) -> None:
    event = {"id": 77, "fulfillment_id": 4501, "status": shopify, "happened_at": "2026-10-21T10:00:00Z"}
    result = normalise_shopify_fulfillment(event, ORDER)
    assert result is not None and result.event["type"] == ucp
    # A fulfillment event payload without line items covers the order's open lines.
    assert result.event["line_items"] == [{"id": "li_1", "quantity": 2}, {"id": "li_2", "quantity": 1}]


def test_cancelled_and_unknown_statuses() -> None:
    cancelled = normalise_shopify_fulfillment({**FULFILLMENT, "status": "cancelled"}, ORDER)
    assert cancelled is not None and cancelled.event["type"] == "canceled"
    assert normalise_shopify_fulfillment({**FULFILLMENT, "status": "pending"}, ORDER) is None
    assert normalise_shopify_fulfillment({"id": 1, "fulfillment_id": 2, "status": "teleported"}, ORDER) is None


def test_carrier_pick_up_is_in_transit_so_a_shipment_is_not_counted_twice() -> None:
    snapshot = copy.deepcopy(ORDER)
    shipped = normalise_shopify_fulfillment(FULFILLMENT, snapshot)
    apply_fulfillment_event(snapshot, shipped.event, now=NOW, event_id=shipped.event_id)
    picked = normalise_shopify_fulfillment({"id": 9, "fulfillment_id": 4501, "status": "carrier_picked_up"}, snapshot)
    apply_fulfillment_event(snapshot, picked.event, now=NOW, event_id=picked.event_id)
    assert snapshot["line_items"][0]["quantity"]["fulfilled"] == 2
    assert snapshot["line_items"][1]["quantity"]["fulfilled"] == 0  # in_transit does not fulfil
    assert [e["type"] for e in snapshot["fulfillment"]["events"]] == ["shipped", "in_transit"]
    assert snapshot["fulfillment"]["events"][0]["occurred_at"] == "2026-10-20T02:30:00Z"


def test_a_redelivered_webhook_is_a_duplicate() -> None:
    snapshot = copy.deepcopy(ORDER)
    first = normalise_shopify_fulfillment(FULFILLMENT, snapshot)
    again = normalise_shopify_fulfillment(copy.deepcopy(FULFILLMENT), snapshot)
    assert first.event_id == again.event_id
    apply_fulfillment_event(snapshot, first.event, now=NOW, event_id=first.event_id)
    with pytest.raises(DuplicateOrderEventError):
        apply_fulfillment_event(snapshot, again.event, now=NOW, event_id=again.event_id)
    assert len(snapshot["fulfillment"]["events"]) == 1


def test_unmatched_lines_are_refused_never_guessed() -> None:
    with pytest.raises(TrackingNormalisationError):
        normalise_shopify_fulfillment({**FULFILLMENT, "line_items": [{"sku": "UNKNOWN", "quantity": 1}]}, ORDER)
    with pytest.raises(TrackingNormalisationError):
        normalise_shopify_fulfillment({"status": "success"}, ORDER)


def test_insecure_tracking_url_is_dropped_and_carrier_kept_as_sent_without_alias() -> None:
    result = normalise_shopify_fulfillment({**FULFILLMENT, "tracking_urls": ["http://track.example/1"]}, ORDER)
    assert "tracking_url" not in result.event
    assert result.event["carrier"] == "Acme Express"
    assert normalise_carrier("   ") is None


def test_variant_gid_matches_an_order_line() -> None:
    order = copy.deepcopy(ORDER)
    order["line_items"][0]["item"]["id"] = "991"
    payload = {**FULFILLMENT, "line_items": [{"variant_id": "gid://shopify/ProductVariant/991", "quantity": 1}]}
    assert normalise_shopify_fulfillment(payload, order).event["line_items"] == [{"id": "li_1", "quantity": 1}]
