# SPDX-License-Identifier: Apache-2.0
"""G27: order webhook subscriptions refuse bad input before any write, every delivery is signed (or not sent),
transport failures are classified for retry, and the Celery kick never blocks or breaks the request."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from core.cases.push import SigningKey
from core.commerce.acp.orders import SIGNATURE_HEADER, verify_merchant_signature
from core.commerce.ucp import webhooks
from core.commerce.ucp.httpsig import SigningKeyUnavailableError, verify_request_signature
from core.tasks import ucp_order_webhook_tasks as tasks
from tests import ucp_txn_fixtures as uf

TENANT = uuid.UUID("00000000-0000-4000-8000-0000000000c7")
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
SHARED = "-".join(("shared", "secret", "placeholder"))


# --- signer and renderers -------------------------------------------------------------------------


def test_merchant_signer_can_be_replaced_and_restored() -> None:
    original = webhooks.merchant_signer()
    replacement = uf.signer()
    try:
        webhooks.set_merchant_signer(replacement)
        assert webhooks.merchant_signer() is replacement
        webhooks.set_merchant_signer(None)
        assert webhooks.merchant_signer() is None
    finally:
        webhooks.set_merchant_signer(original)


def test_payload_renderers_are_pluggable_for_known_schemes_only() -> None:
    original = webhooks._renderers["acp_hmac"]
    try:
        webhooks.register_payload_renderer("acp_hmac", lambda event: {"type": "custom", "id": event.order_id})
        assert webhooks._renderers["acp_hmac"](SimpleNamespace(order_id="ord_1")) == {"type": "custom", "id": "ord_1"}
    finally:
        webhooks.register_payload_renderer("acp_hmac", original)
    with pytest.raises(ValueError, match="unknown scheme"):
        webhooks.register_payload_renderer("smtp", lambda event: {})


# --- subscriptions: refused before any database work -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheme", "url", "platform", "code"),
    [
        ("email", "https://receiver.example/orders", None, "scheme_invalid"),
        ("acp_hmac", "https://receiver.example/orders", None, "platform_required"),
        ("acp_hmac", "https://receiver.example/orders", "ucp:https://platform.example", "platform_required"),
        ("ucp", "https://receiver.example/orders", webhooks.acp_platform_id(uuid.uuid4()), "platform_invalid"),
        ("ucp", "ftp://receiver.example/orders", None, "webhook_url_invalid"),
        ("ucp", "https://192.0.2.10/orders", None, "webhook_url_invalid"),
        ("ucp", "https://localhost/orders", None, "webhook_url_invalid"),
    ],
)
async def test_create_subscription_refuses_invalid_input(scheme, url, platform, code) -> None:
    with pytest.raises(webhooks.WebhookSubscriptionError) as caught:
        await webhooks.create_subscription(
            None, tenant_id=TENANT, merchant_id="m1", url=url, scheme=scheme, now=NOW, platform=platform
        )
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_platform_subscription_with_an_undeliverable_url_is_skipped() -> None:
    row = await webhooks.ensure_platform_subscription(
        None,
        tenant_id=TENANT,
        merchant_id="m1",
        webhook_url="https://127.0.0.1/hooks",
        platform_profile_url=uf.PLATFORM_PROFILE,
        now=NOW,
    )
    assert row is None


@pytest.mark.asyncio
async def test_only_acp_hmac_subscriptions_rotate_keys() -> None:
    row = SimpleNamespace(scheme="ucp")
    with pytest.raises(webhooks.WebhookSubscriptionError) as caught:
        await webhooks.rotate_subscription_key(None, row, now=NOW)
    assert caught.value.code == "scheme_invalid"


def test_subscription_view_exposes_no_key_material() -> None:
    row = SimpleNamespace(
        id=uuid.UUID(int=1),
        merchant_id="m1",
        url="https://receiver.example/orders",
        scheme="acp_hmac",
        source="merchant",
        platform_profile_url=webhooks.acp_platform_id(uuid.UUID(int=2)),
        enabled=True,
        active_key_id="k_1",
        signing_keys_encrypted={"_encrypted": "ciphertext"},
    )
    view = webhooks.subscription_view(row)
    assert view == {
        "id": str(uuid.UUID(int=1)),
        "merchant_id": "m1",
        "url": "https://receiver.example/orders",
        "scheme": "acp_hmac",
        "source": "merchant",
        "platform_profile_url": f"acp:api_key:{uuid.UUID(int=2)}",
        "enabled": True,
        "active_key_id": "k_1",
    }


def test_platform_bound_subscriptions_receive_only_their_platform_orders() -> None:
    platform = webhooks.acp_platform_id(uuid.UUID(int=5))
    merchant_ucp = SimpleNamespace(source="merchant", scheme="ucp", platform_profile_url=None)
    platform_ucp = SimpleNamespace(source="platform_profile", scheme="ucp", platform_profile_url=uf.PLATFORM_PROFILE)
    acp_bound = SimpleNamespace(source="merchant", scheme="acp_hmac", platform_profile_url=platform)
    acp_unbound = SimpleNamespace(source="merchant", scheme="acp_hmac", platform_profile_url=None)
    assert webhooks.receives_order(merchant_ucp, None) and webhooks.receives_order(merchant_ucp, platform)
    assert webhooks.receives_order(platform_ucp, uf.PLATFORM_PROFILE)
    assert not webhooks.receives_order(platform_ucp, platform) and not webhooks.receives_order(platform_ucp, None)
    assert webhooks.receives_order(acp_bound, platform) and not webhooks.receives_order(acp_bound, uf.PLATFORM_PROFILE)
    assert not webhooks.receives_order(acp_unbound, None)


def test_webhook_id_is_stable_per_subscription_order_and_revision() -> None:
    sub = uuid.UUID(int=9)
    assert webhooks.webhook_id_for(sub, "ord_1", 1) == webhooks.webhook_id_for(sub, "ord_1", 1)
    assert webhooks.webhook_id_for(sub, "ord_1", 2) != webhooks.webhook_id_for(sub, "ord_1", 1)
    assert webhooks.webhook_id_for(uuid.UUID(int=10), "ord_1", 1) != webhooks.webhook_id_for(sub, "ord_1", 1)


def test_default_transport_is_the_pinned_async_transport() -> None:
    from core.security.egress import PinnedDnsAsyncHTTPTransport

    assert isinstance(webhooks._default_transport(), PinnedDnsAsyncHTTPTransport)


# --- sending one delivery ----------------------------------------------------------------------------


def _item(scheme: str = "ucp", keys: list[SigningKey] | None = None) -> dict:
    return {
        "id": uuid.uuid4(),
        "webhook_id": str(uuid.UUID(int=77)),
        "event_type": "order.created",
        "payload": {"id": "ord_1", "status": "processing"},
        "attempts": 1,
        "url": "https://receiver.example/orders",
        "scheme": scheme,
        "keys": keys or [],
        "merchant_id": "m1",
        "seller_agent_id": "default",
        "business_profile": f"https://{uf.HOST}/.well-known/ucp",
        "event_timestamp": int(NOW.timestamp()) - 30,
    }


async def _send(dispatcher: webhooks.OrderWebhookDispatcher, item: dict, handler) -> tuple[str, int | None]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await dispatcher._send(client, item, TENANT)


def _dispatcher(signer=None) -> webhooks.OrderWebhookDispatcher:
    return webhooks.OrderWebhookDispatcher(clock=lambda: NOW, signer=lambda: signer)


@pytest.mark.asyncio
async def test_ucp_delivery_is_signed_with_stable_event_headers() -> None:
    signing = uf.signer()
    sent: list[httpx.Request] = []

    def _ok(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    assert await _send(_dispatcher(signing), _item(), _ok) == ("", 204)
    request = sent[0]
    assert request.headers["webhook-id"] == str(uuid.UUID(int=77))
    assert request.headers["webhook-timestamp"] == str(int(NOW.timestamp()) - 30)
    assert request.headers["ucp-agent"] == f'profile="https://{uf.HOST}/.well-known/ucp"'
    assert verify_request_signature(
        method="POST",
        url=str(request.url),
        headers=dict(request.headers),
        body=request.content,
        public_key=signing._key.public_key(),
    )


@pytest.mark.asyncio
async def test_acp_delivery_carries_a_merchant_signature_under_the_active_key() -> None:
    key = SigningKey(key_id="k_active", secret=SHARED, created_at=NOW.isoformat())
    sent: list[httpx.Request] = []

    def _ok(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    assert await _send(_dispatcher(), _item("acp_hmac", [key]), _ok) == ("", 200)
    request = sent[0]
    assert request.headers["request-id"] == str(uuid.UUID(int=77))
    assert request.headers["timestamp"] == "2026-10-20T09:00:00Z"
    assert verify_merchant_signature(
        request.headers[SIGNATURE_HEADER], request.content, SHARED, now=int(NOW.timestamp())
    )


class _NoKeySigner:
    async def key_id(self, *_args) -> str | None:
        return None

    async def sign(self, *_args) -> bytes:  # pragma: no cover - never reached without a key id
        raise SigningKeyUnavailableError("m1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatcher", "item"),
    [
        (_dispatcher(None), _item()),
        (_dispatcher(_NoKeySigner()), _item()),
        (_dispatcher(), _item("acp_hmac", [])),
    ],
    ids=["no-signer", "no-merchant-key", "no-shared-key"],
)
async def test_nothing_unsigned_is_ever_sent(dispatcher, item) -> None:
    sent: list[httpx.Request] = []
    result = await _send(dispatcher, item, lambda request: sent.append(request) or httpx.Response(200))
    assert result == ("signing_key_unavailable", None)
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "transport_ConnectError"),
        (httpx.RemoteProtocolError("bad framing"), "transport_RemoteProtocolError"),
    ],
)
async def test_transport_failures_are_retryable_errors_without_a_status(raised, expected) -> None:
    def _fail(_request: httpx.Request) -> httpx.Response:
        raise raised

    assert await _send(_dispatcher(uf.signer()), _item(), _fail) == (expected, None)


@pytest.mark.asyncio
async def test_non_2xx_answers_name_the_status() -> None:
    assert await _send(_dispatcher(uf.signer()), _item(), lambda _r: httpx.Response(410)) == ("http_410", 410)


@pytest.mark.asyncio
async def test_dispatch_all_sums_the_tenant_reports() -> None:
    reports = {
        uuid.UUID(int=1): webhooks.DispatchReport(delivered=2, retried=1),
        uuid.UUID(int=2): webhooks.DispatchReport(dead_lettered=3),
    }

    async def _tenants() -> list[uuid.UUID]:
        return list(reports)

    dispatcher = webhooks.OrderWebhookDispatcher(tenant_ids=_tenants)
    seen: list[uuid.UUID] = []

    async def _dispatch_tenant(tenant_id: uuid.UUID) -> webhooks.DispatchReport:
        seen.append(tenant_id)
        return reports[tenant_id]

    dispatcher.dispatch_tenant = _dispatch_tenant  # type: ignore[method-assign]
    total = await dispatcher.dispatch_all()
    assert (total.delivered, total.retried, total.dead_lettered) == (2, 1, 3)
    assert seen == list(reports)


# --- the Celery kick ---------------------------------------------------------------------------------


def test_send_kick_queues_the_tenant_dispatch(monkeypatch) -> None:
    queued: list[str] = []
    monkeypatch.setattr(tasks.dispatch_order_webhooks, "delay", lambda tenant: queued.append(tenant))
    webhooks._send_kick(TENANT)
    assert queued == [str(TENANT)]


def test_broker_failure_on_kick_is_swallowed(monkeypatch) -> None:
    def _down(_tenant):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.dispatch_order_webhooks, "delay", _down)
    webhooks._send_kick(TENANT)  # the periodic sweep delivers instead


def test_kick_without_a_running_loop_sends_inline(monkeypatch) -> None:
    kicked: list[uuid.UUID] = []
    monkeypatch.setattr(webhooks, "_send_kick", kicked.append)
    webhooks.kick_dispatch(TENANT)
    assert kicked == [TENANT]


@pytest.mark.asyncio
async def test_kick_inside_the_event_loop_runs_off_the_loop(monkeypatch) -> None:
    import threading

    threads: list[int] = []
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _record(_tenant: uuid.UUID) -> None:
        threads.append(threading.get_ident())
        loop.call_soon_threadsafe(done.set)

    monkeypatch.setattr(webhooks, "_send_kick", _record)
    webhooks.kick_dispatch(TENANT)
    await asyncio.wait_for(done.wait(), timeout=5)
    assert threads and threads[0] != threading.get_ident()


# --- Celery entry points ---------------------------------------------------------------------------------


class _FakeDispatcher:
    calls: list[object] = []

    async def dispatch_tenant(self, tenant_id: uuid.UUID) -> webhooks.DispatchReport:
        self.calls.append(tenant_id)
        return webhooks.DispatchReport(delivered=1)

    async def dispatch_all(self) -> webhooks.DispatchReport:
        self.calls.append("all")
        return webhooks.DispatchReport(retried=2, dead_lettered=1)


def test_dispatch_task_delivers_one_tenant(monkeypatch) -> None:
    _FakeDispatcher.calls = []
    monkeypatch.setattr(webhooks, "OrderWebhookDispatcher", _FakeDispatcher)
    assert tasks.dispatch_order_webhooks(str(TENANT)) == {"delivered": 1, "retried": 0, "dead_lettered": 0}
    assert tasks.dispatch_order_webhooks() == {"delivered": 0, "retried": 2, "dead_lettered": 1}
    assert _FakeDispatcher.calls == [TENANT, "all"]


def test_sweep_dispatches_every_tenant_and_prunes_idempotency_records(monkeypatch) -> None:
    from core.commerce.ucp import idempotency
    from core.config import settings

    _FakeDispatcher.calls = []
    monkeypatch.setattr(webhooks, "OrderWebhookDispatcher", _FakeDispatcher)

    async def _prune() -> int:
        return 4

    monkeypatch.setattr(idempotency, "prune_expired", _prune)
    monkeypatch.setattr(settings, "ucp_checkout_enabled", True)
    assert tasks.sweep_order_webhooks() == {
        "delivered": 0,
        "retried": 2,
        "dead_lettered": 1,
        "idempotency_pruned": 4,
    }
    assert _FakeDispatcher.calls == ["all"]
