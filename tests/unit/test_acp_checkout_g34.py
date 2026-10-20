# SPDX-License-Identifier: Apache-2.0
"""G34/G38: ACP requests translate onto the UCP state engine and render as schema-valid ACP CheckoutSessions;
headers, statuses, capability declarations and the request schema behave as ACP 2026-04-17 specifies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core.commerce.acp import capabilities as caps
from core.commerce.acp import checkout as acp
from core.commerce.acp import headers as hdr
from core.commerce.payment_handlers import plural
from core.commerce.ucp import checkout as engine
from core.commerce.ucp.completion import VENDOR_MEMBER
from tests import acp_fixtures as af
from tests import ucp_txn_fixtures as uf

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000e1"))


async def _session(body: dict, *, previous: dict | None = None, delegate: dict | None = None, vendor=None, now=None):
    """Translate, evaluate on the shared engine, and wrap the state in a row-like object."""
    merchant = af.merchant(TENANT)
    runtime = af.runtime(TENANT, resolved=merchant).ucp
    translate = acp.make_translator(vendor=vendor or {}, delegate_state=delegate)
    ucp_body, kept = translate(previous, dict(body))
    negotiated = acp.acp_negotiated(runtime, merchant, af.PLATFORM)
    deps = engine.EngineDeps(
        catalog=runtime.catalog_for(merchant), rates_source=runtime.rates_for(merchant), shopify_reads=None
    )
    moment = now or datetime.now(UTC)
    evaluation = await engine.evaluate(
        resource="checkout",
        merchant=merchant,
        negotiated=negotiated,
        body=ucp_body,
        previous=previous,
        deps=deps,
        now=moment,
    )
    state = {**evaluation.state, "protocol": "acp", **kept}
    row = SimpleNamespace(
        id="chk_test",
        status=evaluation.status,
        state=state,
        order_id=None,
        created_at=moment,
        updated_at=moment,
        expires_at=moment + timedelta(hours=6),
    )
    return merchant, runtime, ucp_body, row


def _render(merchant, runtime, row, **kw) -> dict:
    return acp.render_session(
        row=row,
        merchant=merchant,
        handlers=runtime.registry.payment_handlers(merchant, acp.ENGINE_RELEASE),
        now=datetime.now(UTC),
        **kw,
    )


@pytest.mark.asyncio
async def test_items_have_no_quantity_so_a_repeated_id_is_one_more_unit() -> None:
    body = af.create_body(af.TOTE, af.TOTE, af.TOTE)
    merchant, runtime, ucp_body, row = await _session(body)
    assert ucp_body["line_items"] == [{"item": {"id": af.TOTE}, "quantity": 3}]
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert rendered["line_items"][0]["quantity"] == 3
    assert rendered["line_items"][0]["totals"][-1]["amount"] == 7500
    assert "quantity" not in af.SCHEMA["$defs"]["Item"]["properties"]


@pytest.mark.asyncio
async def test_address_and_selected_option_drive_shipping_and_totals() -> None:
    body = af.create_body(**af.select_shipping("ship-std"))
    merchant, runtime, ucp_body, row = await _session(body)
    method = ucp_body["fulfillment"]["methods"][0]
    assert method["destinations"][0]["address_country"] == "IN" and method["groups"] == [
        {"selected_option_id": "ship-std"}
    ]
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert {o["id"] for o in rendered["fulfillment_options"]} == {"ship-std", "ship-exp"}
    assert rendered["selected_fulfillment_options"][0]["option_id"] == "ship-std"
    assert [t["type"] for t in rendered["totals"]] == ["subtotal", "fulfillment", "total"]
    assert rendered["totals"][-1]["amount"] == 2500 + 5000
    # No passport: the delegate-payment allowance is missing, so the session is not ready for payment.
    assert rendered["status"] == "not_ready_for_payment"
    codes = {(m["type"], m.get("code"), m.get("param")) for m in rendered["messages"]}
    assert ("error", "missing", "$.agenticorg_commerce") in codes


@pytest.mark.asyncio
async def test_ready_for_payment_with_an_issued_allowance() -> None:
    delegate = {"status": "issued", **af.allowance_answer("chk_test")}
    body = af.create_body(**af.select_shipping())
    merchant, runtime, _ucp_body, row = await _session(body, delegate=delegate)
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert rendered["status"] == "ready_for_payment", rendered["messages"]
    handler = rendered["capabilities"]["payment"]["handlers"][0]
    assert handler["name"] == plural.HANDLER_NAME and handler["requires_delegate_payment"] is True
    assert handler["config_schema"] == plural.HANDLER_SCHEMA
    assert handler["instrument_schemas"] == [plural.INSTRUMENT_SCHEMA]
    assert handler["config"]["delegate_payment"]["allowance"]["checkout_session_id"] == "chk_test"
    assert set(handler["config"]["delegate_payment"]) == {"allowance", "risk_signals", "metadata"}


@pytest.mark.asyncio
async def test_missing_address_and_unknown_item_are_acp_message_errors() -> None:
    body = af.create_body("not-a-variant", address=False)
    merchant, runtime, _b, row = await _session(body)
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert rendered["status"] == "not_ready_for_payment"
    codes = {(m.get("code"), m.get("param")) for m in rendered["messages"] if m["type"] == "error"}
    assert ("not_found", "$.line_items[0]") in codes
    assert ("missing", "$.fulfillment_details") in codes


@pytest.mark.asyncio
async def test_currency_mismatch_blocks_payment() -> None:
    body = af.create_body(**af.select_shipping())
    body["currency"] = "usd"
    delegate = {"status": "issued", **af.allowance_answer("chk_test")}
    merchant, runtime, _b, row = await _session(body, delegate=delegate)
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert rendered["status"] == "not_ready_for_payment"
    assert ("invalid", "$.currency") in {(m.get("code"), m.get("param")) for m in rendered["messages"]}
    assert rendered["currency"] == "inr"


@pytest.mark.asyncio
async def test_unit_amount_differing_from_the_price_is_a_price_change_warning() -> None:
    body = af.create_body()
    body["line_items"] = [{"id": af.TOTE, "unit_amount": 1999}]
    merchant, runtime, _b, row = await _session(body)
    rendered = _render(merchant, runtime, row)
    af.assert_session(rendered)
    assert {"type": "warning", "code": "price_change"}.items() <= next(
        m for m in rendered["messages"] if m["type"] == "warning"
    ).items()


@pytest.mark.asyncio
async def test_partial_update_merges_with_the_stored_input_and_keeps_line_ids() -> None:
    merchant, runtime, _b, row = await _session(af.create_body())
    first_line = row.state["line_items"][0]["id"]
    update = {"line_items": [{"id": af.TOTE}, {"id": af.TOTE}], **af.select_shipping("ship-exp")}
    _m, _r, ucp_body, updated = await _session(update, previous=row.state)
    assert ucp_body["line_items"] == [{"item": {"id": af.TOTE}, "quantity": 2, "id": first_line}]
    # The address from create is still there: only the members sent were replaced.
    assert ucp_body["fulfillment"]["methods"][0]["groups"] == [{"selected_option_id": "ship-exp"}]
    assert updated.state["acp"]["input"]["buyer"]["email"] == "buyer@example.com"
    rendered = _render(merchant, runtime, updated)
    af.assert_session(rendered)
    assert rendered["line_items"][0]["id"] == first_line and rendered["line_items"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_vendor_extension_maps_to_the_ucp_vendor_member_and_is_declared_only_when_negotiated() -> None:
    vendor = {"decision_id": "dec_1", "buyer_authority": {"consent_ref": "consent-1"}, "passport_jwt": af.PASSPORT}
    body = af.create_body(vendor=vendor)
    body.pop(caps.VENDOR_FIELD)
    merchant, runtime, ucp_body, row = await _session(body, vendor=vendor)
    assert ucp_body[VENDOR_MEMBER] == {"decision_id": "dec_1", "buyer_authority": {"consent_ref": "consent-1"}}
    rendered = _render(merchant, runtime, row, vendor={"decision": {"decision_id": "dec_9", "outcome": "allow"}})
    assert rendered["capabilities"]["extensions"] == [caps.SERVED_EXTENSIONS[caps.VENDOR_EXTENSION]]
    assert rendered[caps.VENDOR_FIELD] == {"decision": {"decision_id": "dec_9", "outcome": "allow"}}
    af.assert_session(rendered)
    plain = af.create_body()
    merchant, runtime, _b, row = await _session(plain)
    rendered = _render(merchant, runtime, row, vendor={"decision": {"decision_id": "dec_9", "outcome": "allow"}})
    assert "extensions" not in rendered["capabilities"] and caps.VENDOR_FIELD not in rendered


def test_extension_negotiation_declares_only_what_is_served() -> None:
    assert caps.active_extensions(["discount", "ai.agenticorg.commerce"]) == ["ai.agenticorg.commerce"]
    assert caps.active_extensions(["ai.agenticorg.commerce@2026-10-20"]) == ["ai.agenticorg.commerce"]
    assert caps.active_extensions(["ai.agenticorg.commerce@2025-01-01"]) == []
    assert caps.active_extensions(["discount", "discount@2026-01-27"]) == []
    assert caps.requested_extensions({"extensions": ["b", "a", 3]}) == ["a", "b"]
    declaration = caps.SERVED_EXTENSIONS[caps.VENDOR_EXTENSION]
    assert af.errors("ExtensionDeclaration", declaration) == []


@pytest.mark.asyncio
async def test_expired_and_terminal_statuses() -> None:
    merchant, runtime, _b, row = await _session(af.create_body())
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert _render(merchant, runtime, row)["status"] == "expired"
    row.status = "canceled"
    assert _render(merchant, runtime, row)["status"] == "canceled"
    row.status = "completed"
    row.order_id = "ord_1"
    row.state["order_permalink_url"] = "https://shop.merchant.example/orders/ord_1"
    done = _render(merchant, runtime, row)
    assert done["order"] == {
        "id": "ord_1",
        "checkout_session_id": "chk_test",
        "permalink_url": "https://shop.merchant.example/orders/ord_1",
        "status": "confirmed",
    }
    af.assert_session(done)


def test_complete_request_translation() -> None:
    body = af.complete_body(vendor={"decide_only": True})
    vendor = body.pop(caps.VENDOR_FIELD)
    out = acp.complete_body(body, vendor)
    assert out["payment"]["instruments"] == [
        {
            "id": "pi_1",
            "handler_id": "plural_tokenized",
            "type": "card",
            "credential": {"type": "plural_pine_token", "token": "tok_example_1"},
        }
    ]
    assert out["signals"] == {"ip_address": "192.0.2.10", "user_agent": "Nimbus Shopper 2.4"}
    assert out[VENDOR_MEMBER] == {"decide_only": True}
    assert acp.complete_body({"payment_data": {"purchase_order_number": "PO-1"}}, {})["payment"] == {"instruments": []}


def test_acp_complete_carries_the_mandate_request_to_the_ucp_complete() -> None:
    """ACP Complete runs the UCP Complete (run_operation), so a mandate request reaches the same D1 path."""
    request = {"passport_jwt": af.PASSPORT, "presence": "human_not_present", "description": "A tote bag"}
    body = af.complete_body(vendor={"mandate_request": request, "passport_jwt": af.PASSPORT})
    vendor = acp._vendor(body)
    out = acp.complete_body(body, vendor)
    assert out[VENDOR_MEMBER] == {"mandate_request": request}
    assert acp.OPERATIONS["complete"] == "complete_checkout"
    with pytest.raises(acp._RefusalError):
        acp._vendor(af.complete_body(vendor={"mandate": request}))


@pytest.mark.parametrize(
    ("definition", "body", "param"),
    [
        (
            "CheckoutSessionCreateRequest",
            {"line_items": [{"id": "x", "quantity": 2}], "currency": "inr", "capabilities": {}},
            "$.line_items[0]",
        ),
        ("CheckoutSessionCreateRequest", {"line_items": [], "currency": "inr", "capabilities": {}}, "$.line_items"),
        ("CheckoutSessionCreateRequest", {"line_items": [{"id": "x"}], "capabilities": {}}, "$"),
        (
            "CheckoutSessionCreateRequest",
            {"items": [{"id": "x", "quantity": 1}], "currency": "inr", "capabilities": {}},
            "$",
        ),
        ("CheckoutSessionCompleteRequest", {}, "$"),
        ("CheckoutSessionUpdateRequest", {"buyer": {"first_name": "A"}}, "$.buyer"),
    ],
)
def test_requests_are_validated_against_the_acp_schema(definition, body, param) -> None:
    found = acp.request_problem(definition, body)
    assert found is not None and found[1] == param


def test_create_request_fixture_is_schema_valid() -> None:
    assert acp.request_problem("CheckoutSessionUpdateRequest", af.select_shipping()) is None
    assert acp.request_problem("CheckoutSessionCreateRequest", af.create_body()) is None
    # A selection is an update member only; create renders the options to choose from.
    assert acp.request_problem("CheckoutSessionCreateRequest", af.create_body(**af.select_shipping())) is not None
    assert acp.request_problem("CheckoutSessionCompleteRequest", af.complete_body()) is None


def test_canonical_body_ignores_key_order_but_keeps_arrays_and_nulls() -> None:
    assert hdr.canonical_body(b'{"b":1,"a":[1,2]}') == hdr.canonical_body(b'{"a":[1,2],"b":1}')
    assert hdr.canonical_body(b'{"a":[1,2]}') != hdr.canonical_body(b'{"a":[2,1]}')
    assert hdr.canonical_body(b'{"a":null}') != hdr.canonical_body(b"{}")


class _Verifier:
    def __init__(self, accept: bool) -> None:
        self.accept = accept
        self.calls = 0

    def verify(self, **_kw) -> bool:
        self.calls += 1
        return self.accept


def test_signature_log_mode_never_refuses_and_enforce_mode_needs_a_verifier() -> None:
    now = datetime.now(UTC)
    stamp = now.isoformat().replace("+00:00", "Z")
    base = {"platform": af.PLATFORM, "method": "POST", "path": "/acp/checkout_sessions", "body": b"{}", "now": now}
    assert hdr.check_signature(headers={}, mode="log", **base) == hdr.SignatureCheck("absent", False)
    assert hdr.check_signature(headers={}, mode="enforce", **base).refuse
    assert hdr.check_signature(headers={"Signature": "abc", "Timestamp": stamp}, mode="enforce", **base) == (
        hdr.SignatureCheck("no_verifier", True)
    )
    accept = _Verifier(True)
    hdr.register_signature_verifier(af.PLATFORM, accept)
    try:
        ok = hdr.check_signature(headers={"Signature": "abc", "Timestamp": stamp}, mode="enforce", **base)
        assert ok == hdr.SignatureCheck("verified", False)
        stale = (now - timedelta(seconds=301)).isoformat()
        assert hdr.check_signature(headers={"Signature": "abc", "Timestamp": stale}, mode="enforce", **base).refuse
        hdr.register_signature_verifier(af.PLATFORM, _Verifier(False))
        assert hdr.check_signature(headers={"Signature": "abc", "Timestamp": stamp}, mode="log", **base) == (
            hdr.SignatureCheck("invalid", False)
        )
        assert hdr.check_signature(headers={"Signature": "abc", "Timestamp": stamp}, mode="enforce", **base).refuse
    finally:
        hdr.unregister_signature_verifier(af.PLATFORM)
    assert accept.calls == 1


def test_handler_without_acp_projection_is_not_offered() -> None:
    from core.commerce.ucp.capabilities import PaymentHandlerEntry

    other = PaymentHandlerEntry(
        name="com.example.other",
        id="other",
        version="2026-10-20",
        schema="https://x.example/s.json",
        spec=None,
        available_instruments=(),
    )
    assert caps.acp_payment_handler(other, None) is None
    assert caps.response_capabilities([other], [], None) == {"payment": {"handlers": []}}


def test_merchant_must_opt_in() -> None:
    assert acp.merchant_accepts_acp(af.merchant(TENANT)) is True
    assert acp.merchant_accepts_acp(af.merchant(TENANT, acp_enabled=False)) is False
    assert acp.merchant_accepts_acp(uf.merchant(TENANT)) is False
