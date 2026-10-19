# SPDX-License-Identifier: Apache-2.0
"""G27: order entity, full-snapshot order events, RFC 9421 webhook signing and the ACP-style HMAC variant."""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from core.cases.push import SigningKey, verify_signature
from core.commerce.ucp import httpsig, orders, webhooks
from core.commerce.ucp.renderers import render_order
from tests import ucp_schema
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a7"))
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _state() -> dict:
    return {
        "currency": "INR",
        "line_items": [
            {
                "id": "li_1",
                "item": {"id": uf.SKU, "title": "Canvas Tote", "price": 2500},
                "quantity": 2,
                "totals": [{"type": "subtotal", "amount": 5000}, {"type": "total", "amount": 5000}],
            },
        ],
        "totals": [
            {"type": "subtotal", "amount": 5000},
            {"type": "fulfillment", "amount": 5000},
            {"type": "total", "amount": 10000},
        ],
        "fulfillment": {
            "methods": [
                {
                    "id": "fm_1",
                    "type": "shipping",
                    "line_item_ids": ["li_1"],
                    "selected_destination_id": "d1",
                    "destinations": [
                        {
                            "id": "d1",
                            "kind": "shipping_address",
                            "address": {"address_country": "IN", "postal_code": "411001"},
                        }
                    ],
                    "groups": [],
                }
            ]
        },
        "attribution": {"campaign_id": "spring"},
    }


def _snapshot() -> dict:
    return orders.build_order_snapshot(
        order_id="ord_1", checkout_id="chk_1", merchant=uf.merchant(TENANT), state=_state(), accepted_term=None
    )


@pytest.mark.parametrize("version", ["2026-04-08", "2026-08-25"])
def test_order_entity_is_schema_valid_and_complete(version: str) -> None:
    snapshot = _snapshot()
    body = render_order(
        snapshot=snapshot, version=version, capabilities={"dev.ucp.shopping.order": [{"version": version}]}
    )
    ucp_schema.assert_valid(version, "shopping/order.json", body)
    assert body["line_items"][0]["status"] == "processing"
    assert body["fulfillment"]["expectations"][0]["destination"]["postal_code"] == "411001"
    assert body["attribution"] == {"campaign_id": "spring"}
    assert body["permalink_url"] == f"https://{uf.HOST}/ucp/orders/ord_1"


def test_fulfillment_events_and_adjustments_drive_line_status() -> None:
    snapshot = _snapshot()
    orders.apply_fulfillment_event(
        snapshot, {"type": "shipped", "line_items": [{"id": "li_1", "quantity": 1}]}, now=NOW
    )
    assert snapshot["line_items"][0]["status"] == "partial"
    orders.apply_fulfillment_event(
        snapshot, {"type": "in_transit", "line_items": [{"id": "li_1", "quantity": 1}]}, now=NOW
    )
    assert snapshot["line_items"][0]["quantity"]["fulfilled"] == 1
    orders.apply_adjustment(
        snapshot,
        {
            "type": "cancellation",
            "status": "completed",
            "line_items": [{"id": "li_1", "quantity": -1}],
            "totals": [{"type": "total", "amount": -2500}],
        },
        now=NOW,
    )
    assert snapshot["line_items"][0]["quantity"] == {"original": 2, "total": 1, "fulfilled": 1}
    assert snapshot["line_items"][0]["status"] == "fulfilled"
    assert len(snapshot["fulfillment"]["events"]) == 2 and len(snapshot["adjustments"]) == 1
    ucp_schema.assert_valid(
        "2026-08-25", "shopping/order.json", render_order(snapshot=snapshot, version="2026-08-25", capabilities={})
    )
    with pytest.raises(orders.OrderUpdateError):
        orders.apply_fulfillment_event(
            snapshot, {"type": "shipped", "line_items": [{"id": "nope", "quantity": 1}]}, now=NOW
        )


def test_content_digest_and_signature_base_follow_rfc_9421() -> None:
    body = b'{"id":"ord_1"}'
    assert httpsig.content_digest(body) == "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"
    headers = {
        "Content-Type": "application/json",
        "Content-Digest": httpsig.content_digest(body),
        "UCP-Agent": 'profile="https://shop.merchant.example/.well-known/ucp"',
    }
    components = httpsig.covered_components(
        url="https://platform.example:8443/hooks?x=1", headers=headers, has_body=True
    )
    assert components == ["@method", "@authority", "@path", "@query", "content-digest", "content-type", "ucp-agent"]
    params = '("@method" "@authority");created=1;keyid="k"'
    base = httpsig.signature_base(
        method="post",
        url="https://platform.example:8443/hooks?x=1",
        headers=headers,
        components=["@method", "@authority"],
        params=params,
    )
    assert base == b'"@method": POST\n"@authority": platform.example:8443\n"@signature-params": ' + params.encode()


@pytest.mark.asyncio
async def test_signed_request_verifies_and_tampering_fails() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    signer = httpsig.LocalEs256Signer(key, "kid-1")
    body = b'{"id":"ord_1"}'
    headers = await httpsig.sign_request(
        method="POST",
        url=uf.WEBHOOK_URL,
        headers={"Content-Type": "application/json", "UCP-Agent": 'profile="https://x.example/.well-known/ucp"'},
        body=body,
        signer=signer,
        tenant_id=TENANT,
        merchant_id="m",
        seller_agent_id="s",
        created=1760000000,
    )
    assert 'keyid="kid-1"' in headers["Signature-Input"] and "alg=" not in headers["Signature-Input"]
    raw = base64.b64decode(headers["Signature"].split(":")[1])
    assert len(raw) == 64  # raw r||s, not DER
    assert httpsig.verify_request_signature(
        method="POST", url=uf.WEBHOOK_URL, headers=headers, body=body, public_key=key.public_key()
    )
    assert not httpsig.verify_request_signature(
        method="POST", url=uf.WEBHOOK_URL, headers=headers, body=b'{"id":"ord_2"}', public_key=key.public_key()
    )

    class _NoKey:
        async def key_id(self, *_a):
            return None

        async def sign(self, *_a):
            raise AssertionError("must not sign")

    with pytest.raises(httpsig.SigningKeyUnavailableError):
        await httpsig.sign_request(
            method="POST",
            url=uf.WEBHOOK_URL,
            headers={},
            body=body,
            signer=_NoKey(),
            tenant_id=TENANT,
            merchant_id="m",
            seller_agent_id="s",
            created=1,
        )


@pytest.mark.asyncio
async def test_detached_jws_for_ap2_merchant_authorization() -> None:
    from core.commerce.oacp_jws import b64url_encode, canonicalize_bytes

    key = ec.generate_private_key(ec.SECP256R1())
    signer = httpsig.LocalEs256Signer(key, "kid-ap2")
    checkout = {"id": "chk_1", "totals": [{"type": "total", "amount": 1}]}
    jws = await httpsig.detached_jws(checkout, signer=signer, tenant_id=TENANT, merchant_id="m", seller_agent_id="s")
    header, empty, signature = jws.split(".")
    assert empty == ""
    signing_input = f"{header}.{b64url_encode(canonicalize_bytes(checkout))}".encode()
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    assert httpsig.verify_es256(key.public_key(), signing_input, raw)
    ucp_schema.assert_valid(
        "2026-08-25", "common/payment_ap2_mandate.json", jws, pointer="/$defs/merchant_authorization"
    )


def test_webhook_identity_is_stable_per_subscription_and_revision() -> None:
    sub = uuid.uuid4()
    assert webhooks.webhook_id_for(sub, "ord_1", 1) == webhooks.webhook_id_for(sub, "ord_1", 1)
    assert webhooks.webhook_id_for(sub, "ord_1", 1) != webhooks.webhook_id_for(sub, "ord_1", 2)


def test_acp_hmac_variant_uses_the_case_push_scheme_and_pluggable_payload() -> None:
    from core.cases.push import sign

    event = orders.OrderEvent(
        event_type=orders.ORDER_CREATED,
        tenant_id=TENANT,
        merchant_id="m",
        order_id="ord_1",
        revision=1,
        snapshot=_snapshot(),
        ucp_version="2026-04-08",
    )
    payload = webhooks._renderers["acp_hmac"](event)
    assert payload["type"] == "order.created" and payload["order"]["id"] == "ord_1"
    body = b"{}"
    key = SigningKey("k1", "s" * 32)
    headers = sign(body, event_id="e1", event_type="order.created", timestamp=100, keys=[key])
    assert verify_signature(headers, body, keys={"k1": "s" * 32}, now=100) == ""
    webhooks.register_payload_renderer("acp_hmac", lambda e: {"acp": e.order_id})
    try:
        assert webhooks._renderers["acp_hmac"](event) == {"acp": "ord_1"}
    finally:
        webhooks.register_payload_renderer("acp_hmac", webhooks._default_acp_payload)
    with pytest.raises(ValueError):
        webhooks.register_payload_renderer("smtp", lambda e: {})


@pytest.mark.asyncio
async def test_order_event_listeners_receive_events_in_the_transaction() -> None:
    seen = []

    async def _listener(session, event):
        seen.append((session, event.event_type, event.revision))

    orders.register_order_event_listener(_listener)
    try:
        event = orders.OrderEvent(
            event_type=orders.ORDER_UPDATED,
            tenant_id=TENANT,
            merchant_id="m",
            order_id="ord_1",
            revision=3,
            snapshot=_snapshot(),
            ucp_version="2026-04-08",
        )
        await orders.emit_order_event("db-session", event)
    finally:
        orders.unregister_order_event_listener(_listener)
    assert seen == [("db-session", "order.updated", 3)]
