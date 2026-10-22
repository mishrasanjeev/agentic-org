# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The ucp_buyer sandbox checkout exception: DRAFT only at operator-listed hosts, fail closed elsewhere."""

from __future__ import annotations

import httpx
import pytest

from connectors.commerce.ucp_buyer import SANDBOX_HOSTS_ENV, UcpBuyerConnector
from core.governance.action_policy import ActionContext, ActionMode, ActionRisk, classify_action, evaluate_action
from tests.unit.test_ucp_buyer_connector import (
    API_KEY,
    CHECKOUT_RESPONSE,
    COMPANY_ID,
    ENDPOINT,
    TENANT_ID,
    _connector,
    _json,
    _Merchant,
    _profile,
)

PRODUCTION = ActionContext(tenant_id=TENANT_ID, company_id=COMPANY_ID, domain="commerce", runtime_env="production")


# -- governance ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["sandbox_checkout_create", "sandbox_checkout_update"])
def test_sandbox_tools_class_as_draft_by_qualified_name_only(tool: str) -> None:
    assert classify_action(f"ucp_buyer:{tool}", domain="commerce") is ActionRisk.DRAFT


def test_the_exception_leaves_every_other_checkout_class_unchanged() -> None:
    assert classify_action("ucp_buyer:checkout_create", domain="commerce") is ActionRisk.CUSTOMER_WRITE
    assert classify_action("ucp_buyer:checkout_update", domain="commerce") is ActionRisk.CUSTOMER_WRITE
    assert classify_action("grantex_commerce:checkout_create", domain="commerce") is ActionRisk.MONEY
    assert classify_action("checkout_create", domain="commerce") is ActionRisk.MONEY
    # A bare or foreign-connector spelling of the sandbox name gains nothing.
    assert classify_action("sandbox_checkout_create", domain="commerce") is not ActionRisk.DRAFT
    assert classify_action("grantex_commerce:sandbox_checkout_create", domain="commerce") is not ActionRisk.DRAFT


@pytest.mark.asyncio
async def test_production_dispatches_the_sandbox_tool_and_still_contains_the_ordinary_one() -> None:
    sandbox = await evaluate_action("ucp_buyer:sandbox_checkout_create", context=PRODUCTION)
    ordinary = await evaluate_action("ucp_buyer:checkout_create", context=PRODUCTION)
    assert sandbox.mode is ActionMode.DRAFT and sandbox.dispatch_allowed and sandbox.strict_runtime
    assert ordinary.mode is ActionMode.SHADOW and not ordinary.dispatch_allowed


# -- connector ----------------------------------------------------------------------------------


def test_the_sandbox_tools_are_registered_and_there_is_still_no_payment_tool() -> None:
    tools = set(UcpBuyerConnector({})._tool_registry)
    assert {"sandbox_checkout_create", "sandbox_checkout_update"} <= tools
    assert not any(word in name for name in tools for word in ("complete", "cancel", "pay", "refund"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowlist",
    [None, "", "other.example", "shop.merchant.example", "merchant.example.evil", "*.example"],
)
async def test_sandbox_checkout_is_refused_before_any_request_unless_the_host_is_listed_exactly(
    monkeypatch, allowlist
) -> None:
    if allowlist is None:
        monkeypatch.delenv(SANDBOX_HOSTS_ENV, raising=False)
    else:
        monkeypatch.setenv(SANDBOX_HOSTS_ENV, allowlist)
    merchant = _Merchant(lambda _r: httpx.Response(201, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)

    created = await connector.sandbox_checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])
    updated = await connector.sandbox_checkout_update("chk_0123456789abcdef", {"line_items": []})

    for result in (created, updated):
        assert result["error"] == "sandbox_host_not_allowed"
        assert API_KEY not in str(result)
    assert merchant.requests == [], "no profile fetch and no checkout call"


@pytest.mark.asyncio
@pytest.mark.parametrize("allowlist", ["merchant.example", " other.example , MERCHANT.EXAMPLE. "])
async def test_a_listed_host_gets_exactly_the_ordinary_checkout_request(monkeypatch, allowlist) -> None:
    monkeypatch.setenv(SANDBOX_HOSTS_ENV, allowlist)
    merchant = _Merchant(lambda _r: httpx.Response(201, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)

    result = await connector.sandbox_checkout_create(line_items=[{"item_id": "var_1", "quantity": 2}])

    [request] = merchant.api_calls
    assert request.method == "POST" and str(request.url) == f"{ENDPOINT}/checkout-sessions"
    assert _json(request) == {"line_items": [{"item": {"id": "var_1"}, "quantity": 2}]}
    assert request.headers["x-api-key"] == API_KEY
    assert result["id"] == "chk_0123456789abcdef" and result["payment"] == "not_available"


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["payment", "ap2", "ai.agenticorg.commerce"])
async def test_sandbox_update_still_refuses_payment_members(monkeypatch, member) -> None:
    monkeypatch.setenv(SANDBOX_HOSTS_ENV, "merchant.example")
    merchant = _Merchant(lambda _r: httpx.Response(200, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)

    result = await connector.sandbox_checkout_update(
        "chk_0123456789abcdef", {"line_items": [{"item_id": "var_1", "quantity": 1}], member: {}}
    )

    assert result["error"] == "payment_not_available"
    assert merchant.api_calls == []


@pytest.mark.asyncio
async def test_a_listed_host_still_needs_the_same_origin_rest_endpoint(monkeypatch) -> None:
    monkeypatch.setenv(SANDBOX_HOSTS_ENV, "merchant.example")
    merchant = _Merchant(
        lambda _r: httpx.Response(201, json=CHECKOUT_RESPONSE),
        profile=_profile(endpoint="https://elsewhere.example/ucp"),
    )
    connector = await _connector(monkeypatch, merchant)

    result = await connector.sandbox_checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])

    assert "error" in result
    assert merchant.api_calls == [], "the key never leaves for another origin"
