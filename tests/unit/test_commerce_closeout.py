# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Commerce close-out, pure parts: held stock, stock at payment, loyalty on re-price, refund bookkeeping,
the email outbox wiring, the stock-short email and the 1,000-product Shopify read.

The Postgres paths are in ``tests/integration/test_commerce_closeout_postgres.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.commerce import hosted_refunds, merchant_notifications, order_notifications
from core.commerce import payment_handoff as ph
from core.commerce.ucp import catalog_recheck as recheck
from core.commerce.ucp.items import CatalogItem
from core.commerce.ucp.quantity import QuantityUnit

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _item(item_id: str = "v1", *, inventory: int | None = 3, scale: int = 0) -> CatalogItem:
    return CatalogItem(
        id=item_id,
        title="Filter coffee 250 g",
        price_minor_units=42000,
        currency="INR",
        inventory=inventory,
        image_url=None,
        quantity_unit=QuantityUnit(scale=scale),
    )


def _line(item_id: str = "v1", quantity: int = 2) -> dict[str, Any]:
    return {"id": "li_1", "item": {"id": item_id, "title": "Filter coffee 250 g", "price": 42000}, "quantity": quantity}


# --- loyalty on re-price -----------------------------------------------------------------------------


def test_loyalty_is_no_longer_kept_blindly_from_the_stored_checkout() -> None:
    assert "loyalty" not in recheck.KEPT_MEMBERS


def test_the_recomputed_loyalty_replaces_the_stored_forecast() -> None:
    stored = {"loyalty": {"rewards": {"forecast": 100}}, "handoff": {"mode": "x"}}
    fresh = {"loyalty": {"rewards": {"forecast": 140}}, "totals": []}
    merged = recheck.merged_state(fresh, stored, loyalty_recomputed=True)
    assert merged["loyalty"] == {"rewards": {"forecast": 140}} and merged["handoff"] == {"mode": "x"}


def test_without_shopify_reads_the_stored_loyalty_is_kept_rather_than_erased() -> None:
    stored = {"loyalty": {"rewards": {"forecast": 100}}}
    # The engine sets loyalty to None when it cannot read the membership.
    merged = recheck.merged_state({"loyalty": None}, stored, loyalty_recomputed=False)
    assert merged["loyalty"] == {"rewards": {"forecast": 100}}
    assert "loyalty" not in recheck.merged_state({}, {}, loyalty_recomputed=False)


# --- held stock --------------------------------------------------------------------------------------


def test_stock_held_by_other_payable_links_refuses_and_says_when_it_frees() -> None:
    held = {"v1": [(1, NOW + timedelta(minutes=20)), (1, NOW + timedelta(minutes=5))]}
    messages = recheck.held_stock_changes([_line(quantity=2)], {"v1": _item(inventory=3)}, held)
    assert [m["code"] for m in messages] == ["out_of_stock"]
    content = messages[0]["content"]
    # 3 in stock, 2 held: 1 can be bought now; the earliest hold frees enough at 10:05 UTC.
    assert "Only 1 of Filter coffee 250 g can be bought now: 2 are held by another buyer's pending payment" in content
    assert "until about 10:05 UTC" in content and messages[0]["path"] == "$.line_items[0]"


def test_enough_stock_after_the_holds_or_untracked_stock_is_never_refused() -> None:
    held = {"v1": [(1, NOW + timedelta(minutes=20))]}
    assert recheck.held_stock_changes([_line(quantity=2)], {"v1": _item(inventory=3)}, held) == []
    assert recheck.held_stock_changes([_line(quantity=2)], {"v1": _item(inventory=None)}, held) == []
    assert recheck.held_stock_changes([_line(quantity=2)], {"v1": _item(inventory=3)}, {}) == []


def test_held_stock_counts_whole_units_of_a_scaled_quantity() -> None:
    # 15 tenths of a metre held is 2 whole units; 4 in stock leaves 2, and the cart needs 3 (25 tenths).
    held = {"v1": [(15, NOW + timedelta(minutes=10))]}
    messages = recheck.held_stock_changes([_line(quantity=25)], {"v1": _item(inventory=4, scale=1)}, held)
    assert "Only 2 of" in messages[0]["content"]


def _record(status: str, expires: datetime) -> dict[str, Any]:
    return {"status": status, "expires_at": ph.iso(expires)}


def test_a_checkout_holds_its_items_while_a_link_can_be_paid_and_has_not_lapsed() -> None:
    later = NOW + timedelta(minutes=25)
    state = {"handoff_orders": {"a": _record("issued", NOW + timedelta(minutes=5)), "b": _record("issued", later)}}
    assert ph.held_until(state, NOW) == later
    assert ph.held_until({"handoff_orders": {"a": _record("issued", NOW - timedelta(minutes=1))}}, NOW) is None
    assert ph.held_until({"handoff_orders": {"a": _record("paid", later)}}, NOW) is None
    assert ph.held_until({"handoff_orders": {"a": _record("failed", later)}}, NOW) is None
    creating = {"handoff": {"mode": ph.MODE_PLURAL_HOSTED, "status": "creating"}}
    assert ph.held_until(creating, NOW) == NOW + ph.PLURAL_LINK_TTL
    assert ph.held_until({"handoff": {"mode": ph.MODE_SHOPIFY_CART, "status": "creating"}}, NOW) is None
    assert ph.held_until({}, NOW) is None


# --- stock at payment ------------------------------------------------------------------------------


class _Catalog:
    def __init__(self, items: dict[str, CatalogItem]) -> None:
        self.items = items

    async def lookup(self, _merchant: Any, ids: list[str]) -> dict[str, CatalogItem]:
        return {i: self.items[i] for i in ids if i in self.items}


@pytest.mark.asyncio
async def test_the_lines_stock_no_longer_covers_at_payment_are_named() -> None:
    state = {"currency": "INR", "line_items": [_line("v1", 2), {**_line("v2", 1), "id": "li_2"}, _line("gone", 1)]}
    short = await recheck.stock_short_lines(_Catalog({"v1": _item("v1", inventory=1), "v2": _item("v2")}), None, state)
    # A removed product is not counted as short: its stock is unknown, and the order still ships what it can.
    assert short == [
        {"line_item_id": "li_1", "item_id": "v1", "title": "Filter coffee 250 g", "quantity": 2, "in_stock": 1}
    ]
    assert await recheck.stock_short_lines(_Catalog({}), None, {"line_items": []}) == []


@pytest.mark.asyncio
async def test_a_catalog_that_cannot_be_read_at_payment_never_blocks_the_order(monkeypatch) -> None:
    from core.commerce.ucp import handlers

    class _Broken:
        async def lookup(self, *_a: Any) -> dict[str, CatalogItem]:
            raise RuntimeError("catalog down")

    monkeypatch.setattr(handlers, "payment_catalog_for", lambda _m: _Broken())
    assert await handlers._stock_short_at_payment(None, {"line_items": [_line()]}) == []  # type: ignore[arg-type]


# --- refunds ---------------------------------------------------------------------------------------


def test_refunds_recorded_on_an_order_count_completed_and_pending_only() -> None:
    snapshot = {
        "adjustments": [
            {"type": "refund", "status": "completed", "totals": [{"type": "total", "amount": -1000}]},
            {"type": "refund", "status": "pending", "totals": [{"type": "total", "amount": -500}]},
            {"type": "refund", "status": "failed", "totals": [{"type": "total", "amount": -700}]},
            {"type": "cancellation", "status": "completed", "totals": [{"type": "total", "amount": -300}]},
        ]
    }
    assert hosted_refunds.recorded_refunds(snapshot) == 1500
    assert hosted_refunds.recorded_refunds(None) == 0


def test_the_refund_credential_is_the_one_the_paid_link_was_issued_under() -> None:
    state = {
        "handoff_orders": {
            "aoh-1": {"provider_order_id": "v1-a", "credential_ref": "commerce_plural_old"},
            "aoh-2": {"provider_order_id": "v1-b", "credential_ref": "commerce_plural_new"},
        },
        "handoff": {"credential_ref": "commerce_plural_current"},
    }
    assert hosted_refunds.credential_ref_for_order(state, "v1-b") == "commerce_plural_new"
    assert hosted_refunds.credential_ref_for_order(state, "v1-x") == "commerce_plural_old"
    assert hosted_refunds.credential_ref_for_order({"handoff": {"credential_ref": "c"}}, "v1") == "c"
    assert hosted_refunds.credential_ref_for_order({}, "v1") is None


def test_refund_references_are_deterministic_and_plural_safe() -> None:
    from core.commerce.executors.plural import refund_reference

    first = refund_reference("ord_1", "req-1")
    assert first == refund_reference("ord_1", "req-1") != refund_reference("ord_1", "req-2")
    assert first.startswith("aor-") and len(first) <= 50
    assert hosted_refunds.adjustment_id_for(first) == hosted_refunds.adjustment_id_for(first)
    assert hosted_refunds.adjustment_id_for(first).startswith("adj_")
    unmatched = hosted_refunds.unmatched_reference(str(uuid.UUID(int=1)), 1)
    assert unmatched.startswith("aou-") and len(unmatched) <= 50
    assert unmatched != hosted_refunds.unmatched_reference(str(uuid.UUID(int=1)), 2)


def test_the_executor_uses_the_request_reference_when_given() -> None:
    from core.commerce.executors.plural import RefundRequest

    fields = RefundRequest.__dataclass_fields__
    assert "reference" in fields and fields["reference"].default is None


def test_an_order_adjustment_with_a_fixed_id_is_added_once() -> None:
    from core.commerce.ucp.orders import apply_adjustment

    snapshot: dict[str, Any] = {"line_items": [], "adjustments": []}
    entry = apply_adjustment(
        snapshot,
        {"type": "refund", "status": "completed", "totals": [{"type": "total", "amount": -1}]},
        now=NOW,
        entry_id="adj_0123456789abcdef",
    )
    assert entry["id"] == "adj_0123456789abcdef" and snapshot["adjustments"] == [entry]


def test_an_unmatched_payment_view_never_carries_a_credential() -> None:
    from types import SimpleNamespace

    row = SimpleNamespace(
        id=uuid.UUID(int=7),
        merchant_id="tea-corner",
        checkout_session_id="chk_1",
        order_id=None,
        reason="paid_after_cancel",
        amount_minor_units=88900,
        currency="INR",
        provider_order_id="v1-hosted-9",
        details={},
        refund_status="none",
        refund_error=None,
        refunded_at=None,
        created_at=NOW,
        credential_ref="commerce_plural_secretref",
    )
    view = hosted_refunds.unmatched_view(row)
    assert view["refundable"] is True and view["amount"] == "Rs 889.00"
    assert "credential" not in json.dumps(view) and "commerce_plural_secretref" not in json.dumps(view)
    assert (
        hosted_refunds.unmatched_view(SimpleNamespace(**{**row.__dict__, "refund_status": "refunded"}))["refundable"]
        is False
    )
    assert (
        hosted_refunds.unmatched_view(SimpleNamespace(**{**row.__dict__, "amount_minor_units": None}))["refundable"]
        is False
    )


# --- emails --------------------------------------------------------------------------------------------


def test_the_stock_short_email_names_the_order_the_short_items_and_what_to_do() -> None:
    subject, body = order_notifications.stock_short_email(
        merchant_name="Kaveri <Coffee>",
        order_id="ord_9",
        lines=[{"title": "Filter <coffee>", "quantity": 2, "in_stock": 1}],
        amount_minor_units=84000,
        currency="INR",
        provider_order_id="v1-hosted-9",
        buyer={"email": "asha@example.com"},
        surface="Telegram",
    )
    assert subject == "Action needed: order ord_9 was paid but stock is short"
    assert "Kaveri &lt;Coffee&gt;" in body and "Filter &lt;coffee&gt;: ordered 2, in stock 1" in body
    assert "Ship what you can and refund the rest, or refund the order" in body
    assert "/dashboard/orders" in body and "Plural dashboard" in body


def test_the_orders_page_link_is_absolute_when_the_console_address_is_set(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "ui_base_url", "https://app.merchant.example/")
    assert order_notifications.orders_page_url() == "https://app.merchant.example/dashboard/orders"
    monkeypatch.setattr(settings, "ui_base_url", "")
    assert order_notifications.orders_page_url() == "/dashboard/orders"


def test_the_outbox_backs_off_and_knows_its_kinds() -> None:
    row = uuid.UUID(int=3)
    delays = [merchant_notifications.retry_delay(n, row).total_seconds() for n in range(1, 12)]
    assert 48 <= delays[0] <= 72 and delays[1] > delays[0] and delays[2] > delays[1]
    assert max(delays) <= merchant_notifications.MAX_DELAY_SECONDS * 1.2
    assert merchant_notifications.KINDS == ("order", "payment_attention")


@pytest.mark.asyncio
async def test_an_unknown_outbox_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        await merchant_notifications.enqueue(
            None, tenant_id=uuid.UUID(int=1), merchant_id="m", kind="other", payload_ref="x"
        )


@pytest.mark.asyncio
async def test_a_payment_attention_row_sends_the_payment_email(monkeypatch) -> None:
    calls: list[tuple[Any, ...]] = []

    async def unmatched(tenant_id, checkout, **kwargs):
        calls.append((tenant_id, checkout, kwargs))
        return 1

    async def order(tenant_id, order_id):
        calls.append((tenant_id, order_id))
        return 2

    monkeypatch.setattr(order_notifications, "notify_merchant_of_unmatched_payment", unmatched)
    monkeypatch.setattr(order_notifications, "notify_merchant_of_order", order)
    details = {
        "checkout_session_id": "chk_1",
        "result": "out_of_stock_at_payment",
        "amount_minor_units": 100,
        "currency": "INR",
        "provider_order_id": "v1-h",
        "order_id": "ord_1",
        "lines": [{"title": "Tea"}],
    }
    assert await merchant_notifications.send_notification("t1", "payment_attention", "pid", details) == 1
    assert await merchant_notifications.send_notification("t1", "order", "ord_1", {}) == 2
    assert calls[0] == (
        "t1",
        "chk_1",
        {
            "result": "out_of_stock_at_payment",
            "amount_minor_units": 100,
            "currency": "INR",
            "provider_order_id": "v1-h",
            "order_id": "ord_1",
            "lines": [{"title": "Tea"}],
        },
    )
    assert calls[1] == ("t1", "ord_1")


def test_the_outbox_sweep_is_registered_on_the_beat_and_the_maintenance_queue() -> None:
    from core.tasks import merchant_notification_tasks as tasks
    from core.tasks.celery_app import app

    sweep = "core.tasks.merchant_notification_tasks.sweep_merchant_notifications"
    deliver = "core.tasks.merchant_notification_tasks.deliver_merchant_notifications"
    assert tasks.sweep_merchant_notifications.name == sweep and tasks.deliver_merchant_notifications.name == deliver
    assert sweep in app.tasks and deliver in app.tasks
    entry = app.conf.beat_schedule["sweep-merchant-notifications"]
    assert entry["task"] == sweep and entry["schedule"] == 60.0 and entry["options"]["queue"] == "maintenance"
    assert app.conf.task_routes[deliver] == {"queue": "delivery"}
    assert "core.tasks.merchant_notification_tasks" in app.conf.include


def test_the_outbox_tasks_run_the_sweep_and_one_tenant(monkeypatch) -> None:
    import asyncio

    from core.tasks import merchant_notification_tasks as tasks

    seen: list[Any] = []

    async def sweep() -> dict[str, int]:
        seen.append("sweep")
        return {"sent": 1, "retried": 0, "dead": 0}

    async def deliver(tenant_id: Any) -> merchant_notifications.DeliveryReport:
        seen.append(tenant_id)
        return merchant_notifications.DeliveryReport(sent=2)

    monkeypatch.setattr(merchant_notifications, "sweep", sweep)
    monkeypatch.setattr(merchant_notifications, "deliver_tenant", deliver)
    monkeypatch.setattr(tasks, "run_async", asyncio.run)
    tenant = "00000000-0000-0000-0000-0000000000d1"
    assert tasks.sweep_merchant_notifications() == {"sent": 1, "retried": 0, "dead": 0}
    assert tasks.deliver_merchant_notifications(tenant) == {"sent": 2, "retried": 0, "dead": 0}
    assert seen == ["sweep", uuid.UUID(tenant)]


# --- the Shopify catalog read limit -------------------------------------------------------------------


def test_one_named_limit_of_1000_products_serves_every_catalog_read() -> None:
    from core.commerce import c6z_runtime_vertical as c6z

    assert c6z.SHOPIFY_CATALOG_READ_LIMIT == 1000 == c6z.SHOPIFY_CATALOG_PAGE_SIZE * c6z.SHOPIFY_CATALOG_MAX_PAGES
    for path in ("api/v1/commerce_runtime.py", "api/v1/commerce_merchant_setup.py"):
        source = (REPO / path).read_text(encoding="utf-8")
        assert "max_pages=10" not in source and "page_size=50," not in source, path
        assert "SHOPIFY_CATALOG_MAX_PAGES" in source


def test_the_sync_route_defaults_to_the_limit() -> None:
    from api.v1.commerce_runtime import ShopifySyncRequest

    body = ShopifySyncRequest(packet_id="p")
    assert body.page_size * body.max_pages == 1000
    with pytest.raises(ValueError):
        ShopifySyncRequest(packet_id="p", max_pages=21)


def _client(handler: Any) -> Any:
    from core.commerce import c6z_runtime_vertical as c6z

    return c6z.ShopifyAdminGraphQLClient(
        c6z.ShopifyCredentials(
            shop_domain="ghee-shop.myshopify.com", admin_access_token="shpat_redacted_fixture", api_version="2026-04"
        ),
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


def _products_handler(total: int):
    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        start = int(variables["after"] or 0)
        end = min(start + variables["first"], total)
        nodes = [{"id": f"gid://shopify/Product/{i}"} for i in range(start, end)]
        return httpx.Response(
            200,
            json={
                "data": {
                    "products": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": end < total, "endCursor": str(end)},
                    }
                }
            },
        )

    return handler


@pytest.mark.asyncio
async def test_a_store_with_more_than_1000_products_reads_1000_and_says_so() -> None:
    from core.commerce import c6z_runtime_vertical as c6z

    client = _client(_products_handler(1200))
    products = await client.fetch_products(
        page_size=c6z.SHOPIFY_CATALOG_PAGE_SIZE, max_pages=c6z.SHOPIFY_CATALOG_MAX_PAGES
    )
    assert len(products) == 1000 and client.read_limit_reached is True
    small = _client(_products_handler(1000))
    assert len(await small.fetch_products()) == 1000 and small.read_limit_reached is False
    with pytest.raises(c6z.C6ZRuntimeValidationError):
        await small.fetch_products(max_pages=21)


def test_the_evidence_of_1000_products_is_built_and_hashed_for_the_ledger() -> None:
    from core.commerce.c6z_runtime_vertical import build_seller_onboarding_packet, build_shopify_connector_evidence
    from core.commerce.ledger import commerce_payload_hash

    packet = build_seller_onboarding_packet(
        tenant_id="11111111-1111-1111-1111-111111111111",
        merchant_id="merchant_1",
        seller_agent_id="seller_agent_1",
        merchant_display_name="Demo Store",
        public_brand_profile={"display_name": "Demo Store"},
        commerce_categories=["apparel"],
        requested_grantex_authority_scope={"artifact_families": ["catalog_snapshot"]},
        artifact_cache_scope={"merchant_id": "merchant_1", "seller_agent_id": "seller_agent_1"},
        source_freshness_policy={"max_age_seconds": 900},
        connector_metadata={"shop_domain": "demo.myshopify.com", "credential_ref": "env"},
    )
    stamp = NOW.isoformat().replace("+00:00", "Z")
    products = [
        {
            "id": f"gid://shopify/Product/{i}",
            "title": f"Canvas Tote {i}",
            "descriptionHtml": "Heavy canvas tote",
            "vendor": "Demo Brand",
            "productType": "Bags",
            "status": "ACTIVE",
            "updatedAt": stamp,
            "variants": {
                "nodes": [
                    {
                        "id": f"gid://shopify/ProductVariant/{i}",
                        "sku": f"TOTE-{i}",
                        "title": "Default",
                        "price": "1299.00",
                        "inventoryQuantity": 7,
                        "updatedAt": stamp,
                        "selectedOptions": [],
                        "inventoryItem": {"id": f"gid://shopify/InventoryItem/{i}"},
                    }
                ]
            },
        }
        for i in range(1000)
    ]
    evidence = build_shopify_connector_evidence(
        packet=packet, products=products, synced_at=stamp, source_observed_at=stamp, currency="INR"
    )
    assert evidence["product_count"] == 1000 and evidence["variant_count"] == 1000
    digest = commerce_payload_hash(evidence)
    assert len(digest) == 64 and digest == commerce_payload_hash(json.loads(json.dumps(evidence)))
