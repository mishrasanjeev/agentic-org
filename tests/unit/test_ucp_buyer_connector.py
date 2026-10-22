# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The ucp_buyer connector: profile discovery, request shapes, error mapping, secrets and governance."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog

from connectors.commerce.ucp_buyer import PAYMENT_NOTE, UcpBuyerConnector
from core.governance.action_policy import ActionContext, ActionMode, ActionRisk, classify_action, evaluate_action

PROFILE_URL = "https://merchant.example/.well-known/ucp"
ENDPOINT = "https://merchant.example/ucp"
AGENT_PROFILE = "https://platform.example/.well-known/ucp"
API_KEY = "ao_sk_test_placeholder_platform_key"
TENANT_ID = "11111111-1111-1111-1111-111111111111"
COMPANY_ID = "22222222-2222-2222-2222-222222222222"

Handler = Callable[[httpx.Request], httpx.Response]


def _profile(
    *,
    endpoint: str = ENDPOINT,
    version: str = "2026-04-08",
    transport: str = "rest",
    capabilities: tuple[str, ...] = (
        "dev.ucp.shopping.catalog.search",
        "dev.ucp.shopping.catalog.lookup",
        "dev.ucp.shopping.checkout",
    ),
) -> dict[str, Any]:
    return {
        "ucp": {
            "version": "2026-04-08",
            "services": {
                "dev.ucp.shopping": [
                    {"version": version, "transport": transport, "endpoint": endpoint, "schema": "https://ucp.dev/x"}
                ]
            },
            "capabilities": {name: [{"version": "2026-04-08"}] for name in capabilities},
            "payment_handlers": {},
        }
    }


SEARCH_RESPONSE: dict[str, Any] = {
    "ucp": {"version": "2026-04-08", "capabilities": {"dev.ucp.shopping.catalog.search": [{"version": "2026-04-08"}]}},
    "products": [
        {
            "id": "prod_1",
            "title": "Masala Chai Blend",
            "description": {"plain": "Synthetic demo product. Spiced black tea."},
            "price_range": {"min": {"amount": 34900, "currency": "INR"}, "max": {"amount": 34900, "currency": "INR"}},
            "media": [{"type": "image", "url": "https://merchant.example/img.png"}],
            "variants": [
                {
                    "id": "var_1",
                    "sku": "NTH-MASALA-250",
                    "title": "250 g",
                    "description": {"plain": "Masala Chai Blend - 250 g"},
                    "price": {"amount": 34900, "currency": "INR"},
                    "availability": {"available": True, "status": "in_stock"},
                }
            ],
        }
    ],
    "messages": [],
    "pagination": {"has_next_page": False, "total_count": 1},
}

CHECKOUT_RESPONSE: dict[str, Any] = {
    "ucp": {"version": "2026-04-08", "capabilities": {"dev.ucp.shopping.checkout": [{"version": "2026-04-08"}]}},
    "id": "chk_0123456789abcdef",
    "status": "ready_for_complete",
    "currency": "INR",
    "line_items": [
        {
            "id": "li_1",
            "item": {"id": "var_1", "title": "Masala Chai Blend - 250 g", "price": 34900},
            "quantity": 2,
            "totals": [{"type": "subtotal", "amount": 69800}, {"type": "total", "amount": 69800}],
        }
    ],
    "totals": [{"type": "subtotal", "amount": 69800}, {"type": "total", "amount": 69800}],
    "messages": [],
    "continue_url": "https://merchant.example/checkout/chk_0123456789abcdef",
    "expires_at": "2026-10-22T18:00:00Z",
    "links": [],
    "ai.agenticorg.commerce": {"decision": {"decision_id": "dec_1"}},
}


class _Merchant:
    """A MockTransport merchant: serves the profile and records every other request."""

    def __init__(self, handler: Handler | None = None, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile if profile is not None else _profile()
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET" and request.url.path == "/.well-known/ucp":
            return httpx.Response(200, json=self.profile)
        if self.handler is None:
            return httpx.Response(500, json={"code": "unexpected"})
        return self.handler(request)

    @property
    def api_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/.well-known/ucp"]


async def _connector(
    monkeypatch: pytest.MonkeyPatch,
    merchant: _Merchant,
    **config: Any,
) -> UcpBuyerConnector:
    settings = {
        "merchant_profile_url": PROFILE_URL,
        "agent_profile_url": AGENT_PROFILE,
        "api_key": API_KEY,
        **config,
    }
    connector = UcpBuyerConnector({key: value for key, value in settings.items() if value is not None})
    # The real guarded client (egress check per request, no redirects) over a MockTransport.
    monkeypatch.setattr(connector, "_connector_transport_and_dns", lambda: (httpx.MockTransport(merchant), False))
    await connector.connect()
    return connector


def _json(request: httpx.Request) -> Any:
    return json.loads(request.content)


# -- registration -----------------------------------------------------------------------------


def test_registers_exactly_the_five_buyer_tools_and_no_payment_tool() -> None:
    connector = UcpBuyerConnector({})
    assert set(connector._tool_registry) == {
        "catalog_search",
        "catalog_lookup",
        "checkout_create",
        "checkout_get",
        "checkout_update",
    }
    for name in dir(connector):
        lowered = name.lower()
        assert not any(word in lowered for word in ("complete", "cancel", "pay_", "payment_", "refund")), name
    assert connector.name == "ucp_buyer" and connector.category == "commerce"


def test_connector_is_registered_listed_and_addressable_by_qualified_name() -> None:
    import connectors  # noqa: F401
    from connectors.catalog_meta import get_meta
    from connectors.registry import ConnectorRegistry
    from core.langgraph.tool_adapter import _build_tool_index, is_tool_authorized

    assert ConnectorRegistry.get("ucp_buyer") is UcpBuyerConnector
    assert get_meta("ucp_buyer")["display_name"] == "UCP Buyer"
    assert is_tool_authorized(["ucp_buyer:checkout_create"], "ucp_buyer", "checkout_create")
    assert not is_tool_authorized(["ucp_buyer:checkout_create"], "grantex_commerce", "checkout_create")
    # Bare names keep their first registration (grantex_commerce); ucp_buyer is addressed qualified.
    index = _build_tool_index(include_connector_aliases=True)
    assert index["checkout_create"][0] == "grantex_commerce"
    assert index["ucp_buyer:checkout_create"][0] == "ucp_buyer"


def test_llm_argument_schemas_name_the_tool_parameters() -> None:
    from core.langgraph.tool_adapter import _tool_args_schema

    connector = UcpBuyerConnector({})
    search = _tool_args_schema(connector.catalog_search, "catalog_search").model_json_schema()
    create = _tool_args_schema(connector.checkout_create, "checkout_create").model_json_schema()
    assert set(search["properties"]) == {"query", "currency", "limit", "cursor"}
    assert search["required"] == ["query"]
    assert set(create["properties"]) == {"line_items", "buyer"}


# -- profile ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://merchant.example/.well-known/ucp",
        "https://merchant.example/ucp",
        "https://user:pw@merchant.example/.well-known/ucp",
        "https://merchant.example/.well-known/ucp?x=1",
        "not a url",
        None,
    ],
)
async def test_invalid_merchant_profile_url_is_refused_before_any_request(monkeypatch, url) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant, merchant_profile_url=url)
    result = await connector.catalog_search(query="tea")
    assert result["error"] == "merchant_profile_url_invalid"
    assert merchant.requests == []


@pytest.mark.asyncio
async def test_connect_refuses_a_profile_host_that_is_not_public(monkeypatch) -> None:
    connector = UcpBuyerConnector({"merchant_profile_url": "https://127.0.0.1/.well-known/ucp"})
    monkeypatch.setattr(connector, "_connector_transport_and_dns", lambda: (httpx.MockTransport(_Merchant()), False))
    with pytest.raises(ValueError, match="public"):
        await connector.connect()


@pytest.mark.asyncio
async def test_endpoint_on_another_origin_is_refused_and_the_key_is_never_sent(monkeypatch) -> None:
    merchant = _Merchant(profile=_profile(endpoint="https://elsewhere.example/ucp"))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])
    assert result["error"] == "merchant_endpoint_origin_mismatch"
    assert [str(r.url) for r in merchant.requests] == [PROFILE_URL]
    assert all("x-api-key" not in r.headers for r in merchant.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        _profile(version="2026-08-25"),
        _profile(transport="mcp"),
        _profile(endpoint="http://merchant.example/ucp"),
        {"ucp": {"services": {}}},
        {"not": "a profile"},
    ],
)
async def test_profile_without_the_2026_04_08_rest_service_is_refused(monkeypatch, profile) -> None:
    merchant = _Merchant(profile=profile)
    connector = await _connector(monkeypatch, merchant)
    result = await connector.catalog_search(query="tea")
    assert result["error"] in {"merchant_rest_service_missing", "merchant_profile_invalid"}
    assert merchant.api_calls == []


@pytest.mark.asyncio
async def test_a_capability_the_merchant_does_not_offer_is_not_called(monkeypatch) -> None:
    merchant = _Merchant(profile=_profile(capabilities=("dev.ucp.shopping.catalog.search",)))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])
    assert result["error"] == "capability_not_offered"
    assert merchant.api_calls == []
    assert all("x-api-key" not in r.headers for r in merchant.requests)


@pytest.mark.asyncio
async def test_profile_is_fetched_once_and_without_credentials(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(200, json=SEARCH_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    await connector.catalog_search(query="tea")
    await connector.catalog_search(query="chai")
    profile_requests = [r for r in merchant.requests if r.url.path == "/.well-known/ucp"]
    assert len(profile_requests) == 1
    assert "x-api-key" not in profile_requests[0].headers


# -- catalog ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_search_request_shape_and_compact_result(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(200, json=SEARCH_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.catalog_search(query=" tea ", currency="inr", limit=2)

    [request] = merchant.api_calls
    assert request.method == "POST" and str(request.url) == f"{ENDPOINT}/catalog/search"
    assert _json(request) == {"query": "tea", "context": {"currency": "INR"}, "pagination": {"limit": 2}}
    assert "x-api-key" not in request.headers
    assert "idempotency-key" not in request.headers
    assert request.headers["ucp-agent"] == f'profile="{AGENT_PROFILE}"'
    uuid.UUID(request.headers["request-id"])

    assert result["products"] == [
        {
            "id": "prod_1",
            "title": "Masala Chai Blend",
            "description": "Synthetic demo product. Spiced black tea.",
            "variants": [
                {
                    "id": "var_1",
                    "sku": "NTH-MASALA-250",
                    "title": "250 g",
                    "price": {"amount": 34900, "currency": "INR"},
                    "availability": "in_stock",
                }
            ],
        }
    ]
    assert result["pagination"] == {"has_next_page": False, "total_count": 1}
    assert result["messages"] == []


@pytest.mark.asyncio
async def test_catalog_search_works_without_api_key_or_agent_profile(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(200, json=SEARCH_RESPONSE))
    connector = await _connector(monkeypatch, merchant, api_key=None, agent_profile_url=None)
    result = await connector.catalog_search(query="tea")
    assert result["products"][0]["title"] == "Masala Chai Blend"
    [request] = merchant.api_calls
    assert "ucp-agent" not in request.headers and "x-api-key" not in request.headers


@pytest.mark.asyncio
async def test_catalog_unavailable_message_is_kept(monkeypatch) -> None:
    body = {
        "ucp": {"version": "2026-04-08"},
        "products": [],
        "messages": [
            {
                "type": "error",
                "code": "catalog_unavailable",
                "content": "The catalog is temporarily unavailable.",
                "severity": "unrecoverable",
            }
        ],
        "pagination": {"has_next_page": False},
    }
    merchant = _Merchant(lambda _r: httpx.Response(200, json=body))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.catalog_search(query="tea")
    assert result["products"] == []
    assert result["messages"][0]["code"] == "catalog_unavailable"
    assert "error" not in result


@pytest.mark.asyncio
async def test_catalog_lookup_request_shape(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(200, json=SEARCH_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.catalog_lookup(ids=["var_1", "NTH-MASALA-250"])
    [request] = merchant.api_calls
    assert request.method == "POST" and str(request.url) == f"{ENDPOINT}/catalog/lookup"
    assert _json(request) == {"ids": ["var_1", "NTH-MASALA-250"]}
    assert "x-api-key" not in request.headers
    assert result["products"][0]["variants"][0]["id"] == "var_1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "kwargs"),
    [
        ("catalog_search", {"query": ""}),
        ("catalog_search", {"query": "tea", "currency": "rupees"}),
        ("catalog_search", {"query": "tea", "limit": 0}),
        ("catalog_lookup", {"ids": []}),
        ("catalog_lookup", {"ids": [""]}),
        ("checkout_create", {"line_items": []}),
        ("checkout_create", {"line_items": [{"item_id": "var_1", "quantity": 0}]}),
        ("checkout_create", {"line_items": [{"quantity": 1}]}),
        ("checkout_create", {"line_items": [{"item_id": "var_1", "quantity": True}]}),
    ],
)
async def test_invalid_arguments_are_refused_before_any_request(monkeypatch, call, kwargs) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant)
    result = await getattr(connector, call)(**kwargs)
    assert result["error"] == "invalid_request"
    assert merchant.requests == []


# -- checkout ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_create_request_shape_headers_and_result(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(201, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_create(
        line_items=[{"item_id": "var_1", "quantity": 2}], buyer={"email": "buyer@example.com"}
    )

    [request] = merchant.api_calls
    assert request.method == "POST" and str(request.url) == f"{ENDPOINT}/checkout-sessions"
    assert _json(request) == {
        "line_items": [{"item": {"id": "var_1"}, "quantity": 2}],
        "buyer": {"email": "buyer@example.com"},
    }
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["ucp-agent"] == f'profile="{AGENT_PROFILE}"'
    uuid.UUID(request.headers["idempotency-key"])
    uuid.UUID(request.headers["request-id"])
    assert "authorization" not in request.headers

    assert result["id"] == "chk_0123456789abcdef"
    assert result["status"] == "ready_for_complete"
    assert result["line_items"][0]["item"]["id"] == "var_1" and result["line_items"][0]["quantity"] == 2
    assert result["totals"][-1] == {"type": "total", "amount": 69800}
    assert result["messages"] == []
    assert result["continue_url"].startswith("https://merchant.example/")
    assert result["payment"] == "not_available"
    assert PAYMENT_NOTE in result["note"]
    assert "ai.agenticorg.commerce" not in result and "links" not in result


@pytest.mark.asyncio
async def test_checkout_create_accepts_the_ucp_line_item_shape(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(201, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    await connector.checkout_create(line_items=[{"item": {"id": "var_1"}, "quantity": 1}])
    assert _json(merchant.api_calls[0]) == {"line_items": [{"item": {"id": "var_1"}, "quantity": 1}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"agent_profile_url": None}, "agent_profile_url_invalid"),
        ({"agent_profile_url": 'https://platform.example/"x'}, "agent_profile_url_invalid"),
        ({"api_key": None}, "api_key_missing"),
    ],
)
async def test_checkout_needs_an_agent_profile_and_an_api_key(monkeypatch, config, error) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant, **config)
    result = await connector.checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])
    assert result["error"] == error
    assert merchant.requests == []


@pytest.mark.asyncio
async def test_checkout_get_and_update_request_shapes(monkeypatch) -> None:
    merchant = _Merchant(lambda _r: httpx.Response(200, json=CHECKOUT_RESPONSE))
    connector = await _connector(monkeypatch, merchant)
    got = await connector.checkout_get(checkout_id="chk_0123456789abcdef")
    updated = await connector.checkout_update(
        checkout_id="chk_0123456789abcdef",
        checkout={
            "line_items": [{"id": "li_1", "item": {"id": "var_1"}, "quantity": 3}],
            "buyer": {"email": "buyer@example.com"},
        },
    )
    get_request, put_request = merchant.api_calls
    assert get_request.method == "GET"
    assert str(get_request.url) == f"{ENDPOINT}/checkout-sessions/chk_0123456789abcdef"
    assert get_request.headers["x-api-key"] == API_KEY and "idempotency-key" not in get_request.headers
    assert put_request.method == "PUT"
    assert str(put_request.url) == f"{ENDPOINT}/checkout-sessions/chk_0123456789abcdef"
    assert _json(put_request) == {
        "id": "chk_0123456789abcdef",
        "line_items": [{"item": {"id": "var_1"}, "quantity": 3, "id": "li_1"}],
        "buyer": {"email": "buyer@example.com"},
    }
    assert put_request.headers["x-api-key"] == API_KEY
    assert put_request.headers["ucp-agent"] == f'profile="{AGENT_PROFILE}"'
    uuid.UUID(put_request.headers["idempotency-key"])
    assert got["status"] == updated["status"] == "ready_for_complete"


@pytest.mark.asyncio
@pytest.mark.parametrize("member", ["payment", "ap2", "ai.agenticorg.commerce"])
async def test_update_refuses_payment_members(monkeypatch, member) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_update(
        checkout_id="chk_1",
        checkout={"line_items": [{"item_id": "var_1", "quantity": 1}], member: {"instruments": []}},
    )
    assert result["error"] == "payment_not_available"
    assert merchant.requests == []


@pytest.mark.asyncio
async def test_update_refuses_unknown_members_and_a_mismatched_id(monkeypatch) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant)
    unknown = await connector.checkout_update(
        checkout_id="chk_1", checkout={"line_items": [{"item_id": "v", "quantity": 1}], "discounts": {}}
    )
    mismatch = await connector.checkout_update(
        checkout_id="chk_1", checkout={"id": "chk_2", "line_items": [{"item_id": "v", "quantity": 1}]}
    )
    assert unknown["error"] == mismatch["error"] == "invalid_request"
    assert merchant.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("checkout_id", ["chk_1/complete", "../complete", "chk_1?x=1", "", "chk 1"])
async def test_checkout_id_cannot_reach_another_operation(monkeypatch, checkout_id) -> None:
    merchant = _Merchant()
    connector = await _connector(monkeypatch, merchant)
    assert (await connector.checkout_get(checkout_id=checkout_id))["error"] == "invalid_request"
    result = await connector.checkout_update(
        checkout_id=checkout_id, checkout={"line_items": [{"item_id": "v", "quantity": 1}]}
    )
    assert result["error"] == "invalid_request"
    assert merchant.requests == []


# -- errors -----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error", "retryable"),
    [
        (400, "invalid_request", False),
        (401, "merchant_auth_rejected", False),
        (403, "merchant_auth_rejected", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (422, "negotiation_failed", False),
        (424, "negotiation_failed", False),
        (429, "rate_limited", True),
        (500, "merchant_error", True),
        (503, "merchant_error", True),
        (302, "merchant_redirect_refused", False),
    ],
)
async def test_http_errors_map_to_clear_error_dicts(monkeypatch, status, error, retryable) -> None:
    body = {"code": "invalid_state", "content": "The checkout is completed; it cannot be updated."}
    merchant = _Merchant(lambda _r: httpx.Response(status, json=body, headers={"Location": "https://x.example/"}))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_get(checkout_id="chk_1")
    assert result["error"] == error
    assert result["retryable"] is retryable
    assert result["http_status"] == status
    assert result["merchant_code"] == "invalid_state"
    assert len(merchant.api_calls) == 1  # a redirect is never followed


@pytest.mark.asyncio
async def test_a_business_error_response_is_returned_with_its_messages(monkeypatch) -> None:
    body = {
        "ucp": {"version": "2026-04-08", "status": "error"},
        "messages": [
            {
                "type": "error",
                "code": "capabilities_incompatible",
                "content": "The platform profile does not declare dev.ucp.shopping.checkout.",
                "severity": "unrecoverable",
            }
        ],
    }
    merchant = _Merchant(lambda _r: httpx.Response(200, json=body))
    connector = await _connector(monkeypatch, merchant)
    result = await connector.checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}])
    assert result["error"] == "merchant_refused"
    assert result["merchant_code"] == "capabilities_incompatible"
    assert result["messages"][0]["code"] == "capabilities_incompatible"


@pytest.mark.asyncio
async def test_transport_failures_and_oversized_bodies_are_mapped(monkeypatch) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connector = await _connector(monkeypatch, _Merchant(unreachable))
    result = await connector.catalog_search(query="tea")
    assert result["error"] == "merchant_unreachable" and result["retryable"] is True

    huge = _Merchant(lambda _r: httpx.Response(200, content=b"x" * (1024 * 1024 + 10)))
    connector = await _connector(monkeypatch, huge)
    assert (await connector.catalog_search(query="tea"))["error"] == "merchant_response_too_large"

    bad = _Merchant(lambda _r: httpx.Response(200, text="<html>"))
    connector = await _connector(monkeypatch, bad)
    assert (await connector.catalog_search(query="tea"))["error"] == "invalid_merchant_response"


@pytest.mark.asyncio
async def test_api_key_never_appears_in_results_or_logs(monkeypatch, caplog) -> None:
    echo = {"code": "unauthorized", "content": f"key {API_KEY} is not valid"}
    merchant = _Merchant(lambda _r: httpx.Response(401, json=echo))
    connector = await _connector(monkeypatch, merchant)
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as logs:
        results = [
            await connector.checkout_create(line_items=[{"item_id": "var_1", "quantity": 1}]),
            await connector.checkout_get(checkout_id="chk_1"),
            await connector.health_check(),
        ]
    assert API_KEY not in json.dumps(results)
    assert "[redacted]" in json.dumps(results[0])
    assert API_KEY not in json.dumps(logs, default=str)
    assert API_KEY not in caplog.text
    assert API_KEY not in repr(connector._auth_headers) and API_KEY not in repr(connector._client.headers)


# -- governance -------------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["ucp_buyer:{}", "ucp_buyer.{}", "ucp_buyer__{}"])
def test_ucp_buyer_checkout_writes_are_customer_writes_not_money(spelling: str) -> None:
    for tool in ("checkout_create", "checkout_update"):
        assert classify_action(spelling.format(tool), domain="commerce") is ActionRisk.CUSTOMER_WRITE
    for tool in ("catalog_search", "catalog_lookup", "checkout_get"):
        assert classify_action(spelling.format(tool), domain="commerce") is ActionRisk.READ


def test_grantex_commerce_and_bare_checkout_create_keep_the_money_class() -> None:
    assert classify_action("grantex_commerce:checkout_create", domain="commerce") is ActionRisk.MONEY
    assert classify_action("checkout_create", domain="commerce") is ActionRisk.MONEY
    assert classify_action("grantex_commerce:catalog_search", domain="commerce") is ActionRisk.READ


@pytest.mark.asyncio
async def test_strict_runtime_contains_ucp_buyer_checkout_as_a_customer_write() -> None:
    context = ActionContext(tenant_id=TENANT_ID, company_id=COMPANY_ID, domain="commerce", runtime_env="production")
    create = await evaluate_action("ucp_buyer:checkout_create", context=context)
    search = await evaluate_action("ucp_buyer:catalog_search", context=context)
    assert create.mode is ActionMode.SHADOW and not create.dispatch_allowed
    assert create.risk is ActionRisk.CUSTOMER_WRITE
    assert create.required_feature_flag == "safety.live_capability.commerce.customer_write"
    assert search.mode is ActionMode.READ_ONLY and search.dispatch_allowed
