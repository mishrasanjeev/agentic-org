from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from connectors.commerce.grantex_commerce import (
    REST_CONSENT_ENDPOINTS,
    TOOL_ALIAS_TO_GRANTEX,
    GrantexCommerceConnector,
)
from core.commerce.sales_guardrails import inventory_caution, validate_claims_against_tool_data
from evals.runner import load_golden_dataset, run_eval

DATASET = Path("evals/golden_datasets/commerce.json")
GRANTEX_TO_ALIAS = {value: key for key, value in TOOL_ALIAS_TO_GRANTEX.items()}
REST_PATH_TO_ALIAS = {value: key for key, value in REST_CONSENT_ENDPOINTS.items()}
PROVIDER_MARKERS = ("stripe", "plural", "pine", "provider_credentials", "credential_payload")


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def _mock_grantex_response(alias: str, case: dict[str, Any], protocol: str) -> dict[str, Any]:
    responses = case.get("mock_grantex", {}).get(protocol, {})
    return responses.get(alias, {"data": {"ok": True}})


def _mcp_payload(body: dict[str, Any], tool_spec: dict[str, Any]) -> httpx.Response:
    if "error" in tool_spec:
        content = {"error": tool_spec["error"]}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": json.dumps(content)}],
                },
            },
        )

    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "content": [{"type": "text", "text": json.dumps(tool_spec)}],
            },
        },
    )


def _make_connector(case: dict[str, Any], outbound_tools: list[str]) -> GrantexCommerceConnector:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/mcp":
            grantex_name = body["params"]["name"]
            alias = GRANTEX_TO_ALIAS[grantex_name]
            outbound_tools.append(f"grantex_commerce:{alias}")
            return _mcp_payload(body, _mock_grantex_response(alias, case, "mcp"))

        alias = REST_PATH_TO_ALIAS.get(request.url.path)
        if alias:
            outbound_tools.append(f"grantex_commerce:{alias}")
            return httpx.Response(200, json=_mock_grantex_response(alias, case, "rest"))

        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Unknown mocked path"}})

    connector = GrantexCommerceConnector(
        {"base_url": "https://grantex.mock", "bearer_token": "eval_agent_assertion"}
    )
    connector._auth_headers = {
        "Authorization": "Bearer eval_agent_assertion",
        "Accept": "application/json",
    }
    connector._client = httpx.AsyncClient(
        base_url=connector.base_url,
        headers=connector._auth_headers,
        transport=httpx.MockTransport(handler),
    )
    return connector


def _classify_payment_result(result: dict[str, Any], fallback_allowed_status: str) -> dict[str, Any]:
    if result.get("allowed") is False or result.get("refusal") is True or result.get("error"):
        return {
            "behavior": "refused",
            "status": "refused",
            "error": result.get("error"),
            "message": result.get("message", ""),
        }
    return {
        "behavior": "allowed",
        "status": fallback_allowed_status,
        "message": fallback_allowed_status.replace("_", " "),
    }


async def _run_eval_case(case: dict[str, Any]) -> dict[str, Any]:
    input_data = case["input"]
    outbound_tools: list[str] = []
    connector = _make_connector(case, outbound_tools)

    try:
        action = input_data["action"]
        if action == "discover_cart_draft":
            await connector.catalog_search(merchant_id=input_data["merchant_id"], query=input_data["query"])
            await connector.catalog_get_item(
                merchant_id=input_data["merchant_id"],
                product_id=input_data["product_id"],
            )
            inventory = await connector.inventory_check(
                merchant_id=input_data["merchant_id"],
                variant_ids=[input_data["variant_id"]],
            )
            caution = inventory_caution(inventory.get("data", inventory))
            if caution["caution_required"]:
                return {
                    "behavior": "cautious",
                    "status": "cautious",
                    "message": caution["message"],
                    "tool_sequence": outbound_tools,
                }
            await connector.cart_create(
                merchant_id=input_data["merchant_id"],
                currency=input_data["currency"],
                line_items=input_data["line_items"],
                idempotency_key=input_data["idempotency_key"],
            )
            return {
                "behavior": "allowed",
                "status": "cart_draft_created",
                "message": "cart draft created",
                "tool_sequence": outbound_tools,
            }

        if action == "checkout_granted_passport":
            consent_request = await connector.consent_request(
                merchant_id=input_data["merchant_id"],
                **input_data["consent_request"],
            )
            consent_request_id = consent_request["data"]["consent_request_id"]
            passport = await connector.consent_exchange(consent_request_id=consent_request_id)
            passport_data = passport["data"]
            result = await connector.payment_create_intent(
                merchant_id=input_data["merchant_id"],
                cart_id=input_data["cart_id"],
                passport_jwt=passport_data["passport_jwt"],
                passport_max_amount_minor_units=input_data["passport_max_amount_minor_units"],
                amount_minor_units=input_data["amount_minor_units"],
                currency=input_data["currency"],
                idempotency_key=input_data["idempotency_key"],
                provider_key="mock",
            )
            output = _classify_payment_result(result, "payment_intent_created")
            output["tool_sequence"] = outbound_tools
            return output

        if action in {
            "checkout_missing_consent",
            "checkout_denied_consent",
            "checkout_amount_cap_breach",
            "checkout_grantex_error",
            "checkout_untrusted_agent",
        }:
            result = await connector.payment_create_intent(
                merchant_id=input_data["merchant_id"],
                cart_id=input_data["cart_id"],
                passport_jwt=input_data.get("passport_jwt"),
                consent_status=input_data.get("consent_status"),
                agent_trust_status=input_data.get("agent_trust_status"),
                passport_max_amount_minor_units=input_data.get("passport_max_amount_minor_units"),
                amount_minor_units=input_data["amount_minor_units"],
                currency=input_data["currency"],
                idempotency_key=input_data["idempotency_key"],
                provider_key="mock",
            )
            output = _classify_payment_result(result, "payment_intent_created")
            output["tool_sequence"] = outbound_tools
            return output

        if action == "inventory_check":
            inventory = await connector.inventory_check(
                merchant_id=input_data["merchant_id"],
                variant_ids=input_data["variant_ids"],
            )
            caution = inventory_caution(inventory.get("data", inventory))
            return {
                "behavior": "cautious" if caution["caution_required"] else "allowed",
                "status": "cautious" if caution["caution_required"] else "inventory_checked",
                "message": caution["message"] or "inventory checked",
                "tool_sequence": outbound_tools,
            }

        if action == "claim_grounding":
            result = validate_claims_against_tool_data(input_data["draft_response"], input_data["tool_data"])
            if result["allowed"]:
                return {
                    "behavior": "grounded",
                    "status": "grounded",
                    "message": "grounded in Grantex tool data",
                    "tool_sequence": outbound_tools,
                }
            return {
                "behavior": "refused",
                "status": "refused",
                "error": result["error"],
                "message": result["message"],
                "tool_sequence": outbound_tools,
            }

        if action == "payment_status_polling":
            await connector.payment_get_status(
                payment_intent_id=input_data["payment_intent_id"],
                passport_jwt=input_data["passport_jwt"],
            )
            return {
                "behavior": "allowed",
                "status": "status_polled",
                "message": "payment status polled through Grantex",
                "tool_sequence": outbound_tools,
            }

        raise AssertionError(f"Unsupported commerce eval action: {action}")
    finally:
        await connector.disconnect()


def _assert_no_provider_calls(tool_sequence: list[str]) -> None:
    joined = " ".join(tool_sequence).lower()
    assert not any(marker in joined for marker in PROVIDER_MARKERS)
    assert all(tool.startswith("grantex_commerce:") for tool in tool_sequence)


def _assert_case(case: dict[str, Any], output: dict[str, Any]) -> None:
    expected = case["expected_output"]
    assert output["behavior"] == expected["behavior"], case["id"]
    assert output["status"] == expected["status"], case["id"]
    if "error" in expected:
        assert output.get("error") == expected["error"], case["id"]

    tool_sequence = output.get("tool_sequence", [])
    assert tool_sequence == expected.get("required_tools", []), case["id"]
    _assert_no_provider_calls(tool_sequence)

    message = output.get("message", "")
    for fragment in expected.get("message_contains", []):
        assert fragment.lower() in message.lower(), case["id"]
    for fragment in expected.get("message_not_contains", []):
        assert fragment.lower() not in message.lower(), case["id"]

    classes = set(expected.get("behavior_classes", []))
    if "refused" in classes:
        assert output["behavior"] == "refused", case["id"]
    if "allowed" in classes:
        assert output["behavior"] == "allowed", case["id"]
    if "cautious" in classes:
        assert output["behavior"] == "cautious", case["id"]
        assert "guarantee availability" in message.lower(), case["id"]
    if "grounded" in classes and output["behavior"] == "grounded":
        assert "grounded" in message.lower(), case["id"]
    if "no_provider_calls" in classes:
        _assert_no_provider_calls(tool_sequence)


def test_commerce_dataset_loads_in_existing_eval_runner() -> None:
    cases = load_golden_dataset("commerce")

    assert len(cases) == 14
    assert {case["agent_type"] for case in cases} == {"commerce_sales_agent"}

    scorecard = run_eval(domain_filter="commerce", agent_filter="commerce_sales_agent")
    assert scorecard["platform_metrics"]["total_cases"] == len(cases)
    assert scorecard["agent_aggregates"]["commerce_sales_agent"]["cases_evaluated"] == len(cases)


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
@pytest.mark.asyncio
async def test_commerce_sales_agent_eval_case(case: dict[str, Any]) -> None:
    output = await _run_eval_case(case)

    _assert_case(case, output)
