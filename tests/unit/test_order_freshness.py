# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The cart at Complete still matches the shop's catalog; listed Shopify shops are re-read; unmatched payments.

The Postgres paths (re-pricing at Complete, the unmatched-payment email being queued once) are in
``tests/integration/test_payment_handoff_postgres.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.commerce import catalog_autosync, order_notifications
from core.commerce.acp import errors as acp_errors
from core.commerce.network_telegram import SYSTEM_PROMPT
from core.commerce.ucp import catalog_recheck as recheck
from core.commerce.ucp.items import CatalogItem
from core.commerce.ucp.quantity import QuantityUnit


def _item(item_id: str = "v1", *, price: int = 42000, currency: str = "INR", inventory: int | None = 10) -> CatalogItem:
    return CatalogItem(
        id=item_id,
        title="Filter coffee 250 g",
        price_minor_units=price,
        currency=currency,
        inventory=inventory,
        image_url=None,
        quantity_unit=QuantityUnit(),
    )


def _line(item_id: str = "v1", *, price: int = 42000, quantity: int = 2) -> dict[str, Any]:
    return {"id": "li_1", "item": {"id": item_id, "title": "Filter coffee 250 g", "price": price}, "quantity": quantity}


def test_a_cart_that_matches_the_catalog_has_no_changes() -> None:
    assert recheck.changes([_line()], {"v1": _item()}, "INR") == []


def test_a_new_price_a_new_currency_a_sold_out_and_a_removed_item_are_each_named() -> None:
    lines = [_line("v1"), _line("v2"), _line("v3"), _line("v4")]
    found = {
        "v1": _item("v1", price=45000),
        "v2": _item("v2", currency="USD"),
        "v3": _item("v3", inventory=1),
    }
    out = recheck.changes(lines, found, "INR")
    assert [(m["code"], m["path"]) for m in out] == [
        ("price_changed", "$.line_items[0]"),
        ("price_changed", "$.line_items[1]"),
        ("out_of_stock", "$.line_items[2]"),
        ("item_unavailable", "$.line_items[3]"),
    ]
    assert all(m["type"] == "error" and m["severity"] == "recoverable" for m in out)
    assert "Rs 450.00" in out[0]["content"] and "USD 420.00" in out[1]["content"]
    assert "Only 1 of Filter coffee 250 g" in out[2]["content"]


def test_unknown_stock_is_never_a_change() -> None:
    assert recheck.changes([_line(quantity=500)], {"v1": _item(inventory=None)}, "INR") == []


def test_the_rebuilt_update_keeps_items_buyer_context_and_fulfillment_choices_by_id() -> None:
    state = {
        "line_items": [{**_line(), "parent_id": "li_0"}],
        "buyer": {"email": "buyer@example.com"},
        "context": {"address_country": "IN"},
        "fulfillment": {
            "methods": [
                {
                    "id": "fm_1",
                    "type": "shipping",
                    "line_item_ids": ["li_1"],
                    "destinations": [{"id": "dest_1"}],
                    "selected_destination_id": "dest_1",
                }
            ]
        },
    }
    assert recheck.rebuild_body(state) == {
        "line_items": [{"id": "li_1", "item": {"id": "v1"}, "quantity": 2, "parent_id": "li_0"}],
        "buyer": {"email": "buyer@example.com"},
        "context": {"address_country": "IN"},
        "fulfillment": {"methods": [{"id": "fm_1", "type": "shipping", "line_item_ids": ["li_1"]}]},
    }


def test_the_merged_state_keeps_hand_offs_platform_and_consent_from_the_stored_checkout() -> None:
    stored = {
        "totals": [{"type": "total", "amount": 1}],
        "platform": {"surface": "telegram"},
        "handoff_refs": ["ref_1"],
        "consent": {"marketing": True},
        "payment": {"instruments": [{"id": "i1"}]},
    }
    fresh = {"totals": [{"type": "total", "amount": 2}], "consent": None, "payment": {"instruments": []}}
    merged = recheck.merged_state(fresh, stored)
    assert merged["totals"] == [{"type": "total", "amount": 2}]
    assert merged["platform"] == {"surface": "telegram"} and merged["handoff_refs"] == ["ref_1"]
    assert merged["consent"] == {"marketing": True} and merged["payment"] == {"instruments": [{"id": "i1"}]}


def test_acp_buyers_get_a_conflict_for_a_price_change() -> None:
    assert acp_errors.MESSAGE_ERROR_MAP["price_changed"] == "conflict"


def test_the_shopping_bot_asks_the_buyer_before_paying_a_new_price() -> None:
    assert "says a price changed, tell the buyer the new summary.total" in " ".join(SYSTEM_PROMPT.split())


# --- the scheduled catalog sync -----------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def exists(self, key: str) -> int:
        return int(key in self.keys)

    async def delete(self, key: str) -> int:
        return int(self.keys.pop(key, None) is not None)


@pytest.fixture(autouse=True)
def no_redis(monkeypatch) -> None:
    async def none() -> None:
        return None

    monkeypatch.setattr(catalog_autosync, "_redis", none)


def _listings(count: int) -> list[Any]:
    return [
        SimpleNamespace(tenant_id=f"t{i}", merchant_id=f"m{i}", seller_agent_id=f"s{i}", slug=f"shop-{i}")
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_the_sync_is_off_while_the_network_is_off_or_the_setting_is_zero(monkeypatch) -> None:
    from core.commerce import network as net
    from core.config import settings

    monkeypatch.setattr(net, "network_enabled", lambda: False)
    assert await catalog_autosync.sync_listed_catalogs() == {"enabled": False}
    monkeypatch.setattr(net, "network_enabled", lambda: True)
    monkeypatch.setattr(settings, "network_catalog_sync_minutes", 0)
    assert await catalog_autosync.sync_listed_catalogs() == {"enabled": False}


@pytest.mark.asyncio
async def test_the_sync_reads_due_shops_caps_the_reads_and_survives_a_failing_shop(monkeypatch) -> None:
    from api.v1 import commerce_runtime
    from core.commerce import network as net
    from core.config import settings

    listings = _listings(60)
    asked: list[tuple[str, int]] = []

    async def listed_rows() -> list[Any]:
        return listings

    async def resync(*, tenant_id: str, merchant_id: str, seller_agent_id: str, max_age_seconds: int) -> str:
        asked.append((merchant_id, max_age_seconds))
        index = int(merchant_id[1:])
        if index == 3:
            raise RuntimeError("shop down")
        return "fresh" if index % 2 else "synced"

    monkeypatch.setattr(net, "network_enabled", lambda: True)
    monkeypatch.setattr(settings, "network_catalog_sync_minutes", 60)
    monkeypatch.setattr(net, "listed_rows", listed_rows)
    monkeypatch.setattr(commerce_runtime, "resync_shopify_scope", resync)
    monkeypatch.setattr(catalog_autosync.random, "shuffle", lambda items: None)
    result = await catalog_autosync.sync_listed_catalogs()

    assert all(age == 3600 for _m, age in asked)
    assert result["failed"] == 1 and result["synced"] + result["failed"] == catalog_autosync.SYNC_MAX_SHOPS
    assert result["shops"] == 60 and result["left_for_next_run"] == 60 - len(asked)
    # Fresh shops cost no Shopify read and do not count toward the cap.
    assert result["fresh"] == len(asked) - catalog_autosync.SYNC_MAX_SHOPS


@pytest.mark.asyncio
async def test_the_sync_order_is_shuffled_on_every_run(monkeypatch) -> None:
    from api.v1 import commerce_runtime
    from core.commerce import network as net

    listings = _listings(3)
    asked: list[str] = []

    async def listed_rows() -> list[Any]:
        return listings

    async def resync(*, merchant_id: str, **_: Any) -> str:
        asked.append(merchant_id)
        return "fresh"

    monkeypatch.setattr(net, "network_enabled", lambda: True)
    monkeypatch.setattr(net, "listed_rows", listed_rows)
    monkeypatch.setattr(commerce_runtime, "resync_shopify_scope", resync)
    monkeypatch.setattr(catalog_autosync.random, "shuffle", lambda items: items.reverse())
    await catalog_autosync.sync_listed_catalogs()
    assert asked == ["m2", "m1", "m0"]
    assert [entry.merchant_id for entry in listings] == ["m0", "m1", "m2"]


def test_the_sync_runs_on_the_beat_every_ten_minutes() -> None:
    from core.tasks.celery_app import app

    entry = app.conf.beat_schedule["sync-network-shop-catalogs"]
    assert entry["task"] == "core.tasks.network_tasks.sync_shop_catalogs" and entry["schedule"] == 600.0


# --- a payment no order matches -----------------------------------------------------------------


def test_the_unmatched_payment_email_names_the_plural_order_the_reason_and_what_to_do() -> None:
    subject, body = order_notifications.unmatched_payment_email(
        merchant_name="Kaveri <Coffee>",
        checkout_id="chk_1",
        result="paid_after_cancel",
        amount_minor_units=88900,
        currency="INR",
        provider_order_id="v1-hosted-9",
        buyer={"email": "asha@example.com", "phone_number": "+91 90000 00000"},
        surface="Telegram",
    )
    assert subject == "Action needed: a payment of Rs 889.00 has no order"
    assert "Kaveri &lt;Coffee&gt;" in body and "<Coffee>" not in body
    assert "the checkout had been canceled before the payment arrived" in body
    assert "v1-hosted-9" in body and "chk_1" in body and "asha@example.com" in body
    assert "refund it, or contact the buyer" in body


def test_every_unmatched_result_has_a_plain_reason() -> None:
    from core.commerce.ucp.handlers import UNMATCHED_RESULTS

    assert set(UNMATCHED_RESULTS) == set(order_notifications.UNMATCHED_REASONS)


def test_the_unmatched_payment_kick_queues_the_task_with_the_details(monkeypatch) -> None:
    from core.tasks import payment_handoff_tasks

    queued: list[tuple[Any, ...]] = []
    monkeypatch.setattr(payment_handoff_tasks.notify_unmatched_payment, "delay", lambda *a: queued.append(a))
    order_notifications.kick_unmatched_payment_notification(
        "t1", "chk_1", result="amount_mismatch", amount_minor_units=100, currency="INR", provider_order_id="v1-h"
    )
    assert queued == [
        (
            "t1",
            "chk_1",
            {"result": "amount_mismatch", "amount_minor_units": 100, "currency": "INR", "provider_order_id": "v1-h"},
        )
    ]


@pytest.mark.asyncio
async def test_a_failed_shop_backs_off_for_an_hour_and_a_held_lock_skips_the_run(monkeypatch) -> None:
    from api.v1 import commerce_runtime
    from core.commerce import network as net

    redis = FakeRedis()
    listings = _listings(2)
    asked: list[str] = []

    async def fake_redis() -> FakeRedis:
        return redis

    async def listed_rows() -> list[Any]:
        return listings

    async def resync(*, merchant_id: str, **_: Any) -> str:
        asked.append(merchant_id)
        return "failed" if merchant_id == "m0" else "synced"

    monkeypatch.setattr(catalog_autosync, "_redis", fake_redis)
    monkeypatch.setattr(net, "network_enabled", lambda: True)
    monkeypatch.setattr(net, "listed_rows", listed_rows)
    monkeypatch.setattr(commerce_runtime, "resync_shopify_scope", resync)
    first = await catalog_autosync.sync_listed_catalogs()
    assert first["failed"] == 1 and first["synced"] == 1
    assert catalog_autosync.RUN_LOCK_KEY not in redis.keys
    second = await catalog_autosync.sync_listed_catalogs()
    assert second["backing_off"] == 1 and asked.count("m0") == 1

    redis.keys[catalog_autosync.RUN_LOCK_KEY] = "1"
    assert await catalog_autosync.sync_listed_catalogs() == {"enabled": True, "skipped": "another_run"}


@pytest.mark.asyncio
async def test_a_redis_that_fails_never_stops_the_sync(monkeypatch) -> None:
    from api.v1 import commerce_runtime
    from core.commerce import network as net

    class Broken:
        def __getattr__(self, _name: str) -> Any:
            async def refused(*_a: Any, **_k: Any) -> None:
                raise ConnectionError("refused")

            return refused

    async def broken() -> Broken:
        return Broken()

    async def listed_rows() -> list[Any]:
        return _listings(1)

    async def resync(**_: Any) -> str:
        return "failed"

    monkeypatch.setattr(catalog_autosync, "_redis", broken)
    monkeypatch.setattr(net, "network_enabled", lambda: True)
    monkeypatch.setattr(net, "listed_rows", listed_rows)
    monkeypatch.setattr(commerce_runtime, "resync_shopify_scope", resync)
    assert (await catalog_autosync.sync_listed_catalogs())["failed"] == 1


def test_only_each_sources_newest_snapshot_is_read() -> None:
    from core.commerce.ucp.items import newest_per_source

    rows = [
        SimpleNamespace(evidence_id="s3", source_system="shopify"),
        SimpleNamespace(evidence_id="u2", source_system="catalog_upload"),
        SimpleNamespace(evidence_id="s2", source_system="shopify"),
        SimpleNamespace(evidence_id="u1", source_system="catalog_upload"),
    ]
    assert [r.evidence_id for r in newest_per_source(rows)] == ["s3", "u2"]


def test_a_change_the_engine_already_names_is_not_repeated() -> None:
    found = [
        {"type": "error", "code": "out_of_stock", "path": "$.line_items[0]", "content": "a"},
        {"type": "error", "code": "price_changed", "path": "$.line_items[1]", "content": "b"},
    ]
    kept = [
        {"type": "error", "code": "out_of_stock", "path": "$.line_items[0]", "content": "Only 1 in stock."},
        {"type": "error", "code": "missing", "path": "$.line_items[1]", "content": "c"},
    ]
    assert [m["code"] for m in recheck.not_repeated(found, kept)] == ["price_changed"]
    only = recheck.not_repeated(found[:1], kept)
    assert [(m["type"], m["code"]) for m in only] == [("info", "catalog_changed")]
