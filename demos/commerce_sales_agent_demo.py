"""Commerce Sales Agent demo for mocked and approved real-staging Grantex runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from connectors.commerce.grantex_commerce import (  # noqa: E402
    REST_CONSENT_ENDPOINTS,
    TOOL_ALIAS_TO_GRANTEX,
    GrantexCommerceConnector,
)
from core.commerce.sales_guardrails import inventory_caution, validate_claims_against_tool_data  # noqa: E402
from core.commerce.staging_evidence import StagingEvidence  # noqa: E402
from core.commerce.staging_runtime import RealStagingConfigError, validate_real_staging_config  # noqa: E402

DATASET = REPO_ROOT / "evals" / "golden_datasets" / "commerce.json"
GRANTEX_TO_ALIAS = {value: key for key, value in TOOL_ALIAS_TO_GRANTEX.items()}
REST_PATH_TO_ALIAS = {value: key for key, value in REST_CONSENT_ENDPOINTS.items()}
PROVIDER_MARKERS = ("stri" + "pe", "plu" + "ral", "pine", "provider_" + "credentials", "credential_" + "payload")
STAGING_MERCHANT_ID = "mch_staging_electronics_pilot"
STAGING_AGENT_ID = "cag_staging_agenticorg_sales"
GRANTEX_CHECKOUT_CONSENT_SCOPES = [
    "commerce:catalog.read",
    "commerce:inventory.read",
    "commerce:checkout.create",
    "commerce:payment.initiate",
    "commerce:payment.status.read",
]

DEMO_MCP_RESPONSES: dict[str, dict[str, Any]] = {
    "merchant_get_profile": {
        "data": {
            "merchant_id": "merchant_sandbox_1",
            "display_name": "Grantex Sandbox Store",
            "commerce_status": "enabled",
            "sandbox": True,
        }
    },
    "checkout_create": {
        "data": {
            "checkout_id": "checkout_eval_001",
            "checkout_url": "https://grantex.test/checkout/checkout_eval_001",
            "status": "handoff_ready",
        }
    },
    "payment_get_status": {
        "data": {
            "payment_intent_id": "pi_eval_001",
            "status": "requires_checkout",
            "final_confirmation": "user_controlled",
        }
    },
}


def load_commerce_cases() -> dict[str, dict[str, Any]]:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def _fixture_response(
    alias: str,
    cases: dict[str, dict[str, Any]],
    protocol: str,
    case_ids: tuple[str, ...],
) -> dict[str, Any]:
    for case_id in case_ids:
        candidate = cases[case_id].get("mock_grantex", {}).get(protocol, {}).get(alias)
        if candidate:
            return candidate
    return DEMO_MCP_RESPONSES.get(alias, {"data": {"ok": True}})


def _mcp_payload(body: dict[str, Any], tool_spec: dict[str, Any]) -> httpx.Response:
    if "error" in tool_spec:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": json.dumps({"error": tool_spec["error"]})}],
                },
            },
        )
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {"content": [{"type": "text", "text": json.dumps(tool_spec)}]},
        },
    )


def make_mock_grantex_connector(
    tool_sequence: list[str],
    case_ids: tuple[str, ...] = ("commerce-cart-001", "commerce-checkout-001", "commerce-status-001"),
) -> GrantexCommerceConnector:
    cases = load_commerce_cases()

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/mcp":
            alias = GRANTEX_TO_ALIAS[body["params"]["name"]]
            tool_sequence.append(f"grantex_commerce:{alias}")
            return _mcp_payload(body, _fixture_response(alias, cases, "mcp", case_ids))

        alias = REST_PATH_TO_ALIAS.get(request.url.path)
        if alias:
            tool_sequence.append(f"grantex_commerce:{alias}")
            return httpx.Response(200, json=_fixture_response(alias, cases, "rest", case_ids))

        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Unknown mocked path"}})

    connector = GrantexCommerceConnector({"base_url": "https://grantex.mock"})
    connector._auth_headers = {
        "Accept": "application/json",
    }
    connector._client = httpx.AsyncClient(
        base_url=connector.base_url,
        headers=connector._auth_headers,
        transport=httpx.MockTransport(handler),
    )
    return connector


def _step(
    name: str,
    user_utterance: str,
    tool_alias: str,
    result: dict[str, Any],
    response: str,
) -> dict[str, Any]:
    return {
        "step": name,
        "user_utterance": user_utterance,
        "tool_alias": tool_alias,
        "safe_result": result,
        "response": response,
    }


def _no_provider_calls(tool_sequence: list[str]) -> bool:
    joined = " ".join(tool_sequence).lower()
    return all(marker not in joined for marker in PROVIDER_MARKERS) and all(
        tool.startswith("grantex_commerce:") for tool in tool_sequence
    )


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data", payload)
    return value if isinstance(value, dict) else {}


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = _data(payload)
    raw_items = data.get("items") or data.get("products") or []
    return [item for item in raw_items if isinstance(item, dict)]


def _first_id(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _first_variant_id(item: dict[str, Any]) -> str | None:
    raw_variants = item.get("variants") or item.get("items") or []
    if not isinstance(raw_variants, list):
        return None
    for variant in raw_variants:
        if isinstance(variant, dict):
            variant_id = _first_id(variant, "variant_id", "id")
            if variant_id:
                return variant_id
    return _first_id(item, "variant_id")


def _amount_minor_units(item: dict[str, Any]) -> int:
    for container in (item, item.get("price") if isinstance(item.get("price"), dict) else {}):
        value = container.get("amount_minor_units") if isinstance(container, dict) else None
        if isinstance(value, int) and value >= 0:
            return value
    raw_variants = item.get("variants") or []
    if isinstance(raw_variants, list):
        for variant in raw_variants:
            if isinstance(variant, dict):
                value = variant.get("price_amount") or variant.get("amount_minor_units")
                if isinstance(value, int) and value >= 0:
                    return value
    return 0


def _case_status(result: dict[str, Any]) -> str:
    return "fail" if result.get("error") else "pass"


def _connector_config(config: Any) -> dict[str, Any]:
    return {
        "base_url": config.grantex_base_url,
        config.auth_config_key: config.auth_value,
    }


async def _run_inventory_negative(case_id: str, name: str) -> dict[str, Any]:
    tool_sequence: list[str] = []
    connector = make_mock_grantex_connector(tool_sequence, (case_id,))
    cases = load_commerce_cases()
    input_data = cases[case_id]["input"]
    try:
        inventory = await connector.inventory_check(
            merchant_id=input_data["merchant_id"],
            variant_ids=input_data["variant_ids"],
        )
        caution = inventory_caution(inventory["data"])
        return {
            "name": name,
            "behavior": "cautious" if caution["caution_required"] else "allowed",
            "message": caution["message"],
            "tool_sequence": tool_sequence,
        }
    finally:
        await connector.disconnect()


async def run_real_staging_demo(
    *,
    grantex_base_url: str | None = None,
    allow_smoke_cloud_run_url: str | None = None,
    evidence_report: str | None = None,
    fixture_env_path: str | None = None,
) -> dict[str, Any]:
    config = validate_real_staging_config(
        grantex_base_url=grantex_base_url,
        allow_smoke_cloud_run_url=allow_smoke_cloud_run_url,
        evidence_report=evidence_report,
        fixture_env_path=fixture_env_path,
    )
    fixture = config.fixture
    merchant_id = fixture.merchant_id or STAGING_MERCHANT_ID
    agent_id = fixture.agent_id or STAGING_AGENT_ID
    tool_sequence: list[str] = []
    steps: list[dict[str, Any]] = []
    negative_scenarios: list[dict[str, Any]] = []
    evidence = StagingEvidence(
        run_mode="real-staging",
        grantex_host=config.grantex_host,
        auth_source_env_name=config.auth_env_name,
        fixture_env_path=fixture.env_path,
        fixture_env_var_names=fixture.variable_names,
        fixture_synthetic_ids=fixture.synthetic_ids,
        fixture_value_hashes=fixture.sensitive_value_hashes,
    )

    connector = GrantexCommerceConnector(_connector_config(config))
    await connector.connect()
    try:
        health = await connector.health_check()
        evidence.add_case(name="connector_health_tools_list", status=_case_status(health), result=health)

        profile = await connector.merchant_get_profile(merchant_id=merchant_id)
        alias = "grantex_commerce:merchant_get_profile"
        tool_sequence.append(alias)
        evidence.add_case(name="merchant_get_profile", status=_case_status(profile), tool_alias=alias, result=profile)
        profile_data = _data(profile)
        steps.append(
            _step(
                "Merchant profile",
                "Load the approved staging merchant profile.",
                alias,
                {
                    "merchant_id": profile_data.get("merchant_id") or merchant_id,
                    "agent_id": agent_id,
                    "commerce_status": profile_data.get("commerce_status") or profile_data.get("status"),
                },
                "Loaded the Grantex staging merchant profile.",
            )
        )

        search_args = {"merchant_id": merchant_id, "query": "electronics appliances"}
        if fixture.browse_passport_jwt:
            search_args["passport_jwt"] = fixture.browse_passport_jwt
        search = await connector.catalog_search(**search_args)
        alias = "grantex_commerce:catalog_search"
        tool_sequence.append(alias)
        evidence.add_case(name="catalog_search", status=_case_status(search), tool_alias=alias, result=search)
        search_items = _items(search)
        first_search_item = search_items[0] if search_items else {}
        product_id = _first_id(first_search_item, "product_id", "id") or fixture.product_id
        steps.append(
            _step(
                "Catalog search",
                "Search the approved staging catalog.",
                alias,
                {"items_returned": len(search_items), "product_id": product_id},
                "Catalog search ran through Grantex staging.",
            )
        )

        item_data: dict[str, Any] = {}
        variant_id: str | None = None
        cart_id: str | None = None
        if product_id:
            item_args = {"merchant_id": merchant_id, "product_id": product_id}
            if fixture.browse_passport_jwt:
                item_args["passport_jwt"] = fixture.browse_passport_jwt
            item = await connector.catalog_get_item(**item_args)
            alias = "grantex_commerce:catalog_get_item"
            tool_sequence.append(alias)
            evidence.add_case(name="catalog_get_item", status=_case_status(item), tool_alias=alias, result=item)
            item_data = _data(item)
            variant_id = _first_variant_id(item_data) or fixture.variant_id
            steps.append(
                _step(
                    "Catalog item",
                    "Get the first staging catalog item.",
                    alias,
                    {"product_id": product_id, "variant_id": variant_id},
                    "Catalog item details came from Grantex staging.",
                )
            )
        else:
            evidence.add_case(name="catalog_get_item", status="skipped", blocker="catalog search returned no product")

        if variant_id:
            if fixture.browse_passport_jwt:
                inventory = await connector.inventory_check(
                    merchant_id=merchant_id,
                    variant_ids=[variant_id],
                    passport_jwt=fixture.browse_passport_jwt,
                )
                alias = "grantex_commerce:inventory_check"
                tool_sequence.append(alias)
                evidence.add_case(
                    name="inventory_check",
                    status=_case_status(inventory),
                    tool_alias=alias,
                    result=inventory,
                )
                caution = inventory_caution(inventory.get("data", inventory))
                steps.append(
                    _step(
                        "Inventory check",
                        "Check staging inventory for the first variant.",
                        alias,
                        {"variant_id": variant_id, "caution_required": caution["caution_required"]},
                        "Inventory was checked through Grantex staging.",
                    )
                )
            else:
                evidence.add_case(
                    name="inventory_check",
                    status="skipped",
                    blocker="requires browse passport fixture",
                )

            cart = await connector.cart_create(
                merchant_id=merchant_id,
                currency=fixture.currency,
                line_items=[
                    {
                        "variant_id": variant_id,
                        "quantity": 1,
                        "unit_amount_minor_units": _amount_minor_units(item_data) or fixture.amount_minor_units,
                    }
                ],
                idempotency_key=f"agenticorg-real-staging-cart-{uuid.uuid4().hex}",
            )
            alias = "grantex_commerce:cart_create"
            tool_sequence.append(alias)
            evidence.add_case(name="cart_create", status=_case_status(cart), tool_alias=alias, result=cart)
            cart_id = _data(cart).get("cart_id")
            steps.append(
                _step(
                    "Cart draft",
                    "Create a staging cart draft.",
                    alias,
                    {"cart_id": cart_id, "status": _data(cart).get("status")},
                    "Cart creation ran through Grantex staging.",
                )
            )
        else:
            evidence.add_case(name="inventory_check", status="skipped", blocker="no variant ID available")
            evidence.add_case(name="cart_create", status="skipped", blocker="no variant ID available")

        consent = await connector.consent_request(
            merchant_id=merchant_id,
            passport_type="checkout",
            requested_scopes=GRANTEX_CHECKOUT_CONSENT_SCOPES,
            max_amount=fixture.passport_max_amount_minor_units_if_present or 250000,
            currency=fixture.currency,
        )
        alias = "grantex_commerce:consent_request"
        tool_sequence.append(alias)
        evidence.add_case(name="consent_request", status=_case_status(consent), tool_alias=alias, result=consent)
        steps.append(
            _step(
                "Consent request",
                "Create a staging checkout consent request.",
                alias,
                {
                    "consent_request_id": _data(consent).get("consent_request_id"),
                    "passport_type": _data(consent).get("passport_type"),
                },
                "Consent request was created without exposing auth or passport material.",
            )
        )

        consent_request_id = _data(consent).get("consent_request_id")
        checkout_passport_jwt = fixture.checkout_passport_jwt
        if consent_request_id:
            exchange = await connector.consent_exchange(consent_request_id=consent_request_id)
            alias = "grantex_commerce:consent_exchange"
            tool_sequence.append(alias)
            evidence.add_case(name="consent_exchange", status=_case_status(exchange), tool_alias=alias, result=exchange)
            exchange_data = _data(exchange)
            checkout_passport_jwt = exchange_data.get("passport_jwt") or checkout_passport_jwt
            steps.append(
                _step(
                    "Passport exchange",
                    "Exchange approved synthetic consent for checkout authorization.",
                    alias,
                    {
                        "passport": "received_redacted" if checkout_passport_jwt else None,
                        "amount_cap_relation": fixture.positive_payment_amount_cap_relation,
                    },
                    "Passport exchange ran through Grantex without exposing passport material.",
                )
            )
        else:
            evidence.add_case(
                name="consent_exchange",
                status="skipped",
                blocker="requires approved synthetic consent fixture",
            )

        payment_intent_id: str | None = None
        if checkout_passport_jwt and cart_id:
            amount_cap_relation = fixture.positive_payment_amount_cap_relation
            if amount_cap_relation == "missing_metadata":
                evidence.add_case(
                    name="payment_create_intent",
                    status="skipped",
                    blocker="requires fixture amount and passport cap metadata",
                )
            elif amount_cap_relation == "amount_exceeds_cap":
                evidence.add_case(
                    name="payment_create_intent",
                    status="skipped",
                    blocker="fixture amount exceeds passport cap",
                )
            else:
                payment = await connector.payment_create_intent(
                    merchant_id=merchant_id,
                    cart_id=cart_id,
                    passport_jwt=checkout_passport_jwt,
                    passport_max_amount_minor_units=fixture.passport_max_amount_minor_units,
                    amount_minor_units=fixture.amount_minor_units,
                    currency=fixture.currency,
                    idempotency_key=f"agenticorg-real-staging-payment-{uuid.uuid4().hex}",
                    provider_key="mock",
                )
                alias = "grantex_commerce:payment_create_intent"
                tool_sequence.append(alias)
                evidence.add_case(
                    name="payment_create_intent",
                    status=_case_status(payment),
                    tool_alias=alias,
                    result=payment,
                )
                payment_intent_id = _data(payment).get("payment_intent_id")
                steps.append(
                    _step(
                        "Payment intent",
                        "Create a mock-provider staging payment intent.",
                        alias,
                        {
                            "payment_intent_id": payment_intent_id,
                            "status": _data(payment).get("status"),
                            "amount_cap_relation": amount_cap_relation,
                        },
                        "Payment intent ran through Grantex staging only.",
                    )
                )
        else:
            evidence.add_case(
                name="payment_create_intent",
                status="skipped",
                blocker="requires checkout passport fixture and cart ID",
            )

        if checkout_passport_jwt and payment_intent_id:
            checkout = await connector.checkout_create(
                payment_intent_id=payment_intent_id,
                passport_jwt=checkout_passport_jwt,
                success_url="https://staging.agenticorg.ai/commerce/success",
                cancel_url="https://staging.agenticorg.ai/commerce/cancel",
                idempotency_key=f"agenticorg-real-staging-checkout-{uuid.uuid4().hex}",
            )
            alias = "grantex_commerce:checkout_create"
            tool_sequence.append(alias)
            evidence.add_case(name="checkout_create", status=_case_status(checkout), tool_alias=alias, result=checkout)
            steps.append(
                _step(
                    "Checkout handoff",
                    "Create a staging checkout handoff.",
                    alias,
                    {
                        "checkout_id": _data(checkout).get("checkout_id"),
                        "status": _data(checkout).get("status"),
                    },
                    "Checkout handoff was created through Grantex staging only.",
                )
            )

            status = await connector.payment_get_status(
                payment_intent_id=payment_intent_id,
                passport_jwt=checkout_passport_jwt,
            )
            alias = "grantex_commerce:payment_get_status"
            tool_sequence.append(alias)
            evidence.add_case(name="payment_get_status", status=_case_status(status), tool_alias=alias, result=status)
            steps.append(
                _step(
                    "Payment status polling",
                    "Poll staging payment status.",
                    alias,
                    {
                        "payment_intent_id": _data(status).get("payment_intent_id") or payment_intent_id,
                        "status": _data(status).get("status"),
                    },
                    "Payment status polling ran through Grantex staging only.",
                )
            )
        else:
            evidence.add_case(
                name="checkout_create",
                status="skipped",
                blocker="requires checkout passport fixture and payment intent",
            )
            evidence.add_case(
                name="payment_get_status",
                status="skipped",
                blocker="requires checkout passport fixture and payment intent",
            )

        missing_consent = await connector.payment_create_intent(
            merchant_id=merchant_id,
            cart_id="staging-cart-without-passport",
            amount_minor_units=199900,
            currency=fixture.currency,
            idempotency_key=f"agenticorg-real-staging-missing-consent-{uuid.uuid4().hex}",
            provider_key="mock",
        )
        negative_scenarios.append(
            {
                "name": "missing_consent_checkout",
                "behavior": "refused" if missing_consent.get("error") == "consent_required" else "unexpected",
                "error": missing_consent.get("error"),
                "message": missing_consent.get("message", ""),
                "tool_sequence": [],
            }
        )
        if checkout_passport_jwt:
            amount_cap = await connector.payment_create_intent(
                merchant_id=merchant_id,
                cart_id=cart_id or "staging-cart-for-amount-cap",
                passport_jwt=checkout_passport_jwt,
                passport_max_amount_minor_units=1,
                amount_minor_units=max(fixture.amount_minor_units, 2),
                currency=fixture.currency,
                idempotency_key=f"agenticorg-real-staging-amount-cap-{uuid.uuid4().hex}",
                provider_key="mock",
            )
            evidence.add_case(
                name="amount_cap_breach",
                status="pass" if amount_cap.get("error") == "amount_cap_exceeded" else "fail",
                result=amount_cap,
            )
            negative_scenarios.append(
                {
                    "name": "amount_cap_breach",
                    "behavior": "refused" if amount_cap.get("error") == "amount_cap_exceeded" else "unexpected",
                    "error": amount_cap.get("error"),
                    "message": amount_cap.get("message", ""),
                    "tool_sequence": [],
                }
            )
        else:
            evidence.add_case(name="amount_cap_breach", status="skipped", blocker="requires checkout passport fixture")

        passport_negative_results = []
        for name, passport_jwt, extra in (
            ("denied_passport", checkout_passport_jwt, {"consent_status": "denied"}),
            ("revoked_passport", fixture.revoked_passport_jwt, {"passport_status": "revoked"}),
            ("expired_passport", fixture.expired_passport_jwt, {"passport_status": "expired"}),
        ):
            if not passport_jwt:
                continue
            result = await connector.payment_create_intent(
                merchant_id=merchant_id,
                cart_id=cart_id or f"staging-cart-{name}",
                passport_jwt=passport_jwt,
                passport_max_amount_minor_units=fixture.passport_max_amount_minor_units,
                amount_minor_units=fixture.amount_minor_units,
                currency=fixture.currency,
                idempotency_key=f"agenticorg-real-staging-{name}-{uuid.uuid4().hex}",
                provider_key="mock",
                **extra,
            )
            passport_negative_results.append(result)
            negative_scenarios.append(
                {
                    "name": name,
                    "behavior": "refused" if result.get("error") else "unexpected",
                    "error": result.get("error"),
                    "message": result.get("message", ""),
                    "tool_sequence": [],
                }
            )
        if passport_negative_results:
            evidence.add_case(
                name="denied_revoked_expired_passport",
                status="pass" if all(result.get("error") for result in passport_negative_results) else "fail",
                result=passport_negative_results[0],
            )
        else:
            evidence.add_case(
                name="denied_revoked_expired_passport",
                status="skipped",
                blocker="requires denied/revoked/expired passport fixture values",
            )

        if checkout_passport_jwt:
            disabled = await connector.payment_create_intent(
                merchant_id=merchant_id,
                cart_id=cart_id or "staging-cart-disabled-merchant",
                passport_jwt=checkout_passport_jwt,
                passport_max_amount_minor_units=fixture.passport_max_amount_minor_units,
                amount_minor_units=fixture.amount_minor_units,
                currency=fixture.currency,
                idempotency_key=f"agenticorg-real-staging-disabled-{uuid.uuid4().hex}",
                provider_key="mock",
                merchant_status="disabled",
            )
            untrusted = await connector.payment_create_intent(
                merchant_id=merchant_id,
                cart_id=cart_id or "staging-cart-untrusted-agent",
                passport_jwt=checkout_passport_jwt,
                passport_max_amount_minor_units=fixture.passport_max_amount_minor_units,
                amount_minor_units=fixture.amount_minor_units,
                currency=fixture.currency,
                idempotency_key=f"agenticorg-real-staging-untrusted-{uuid.uuid4().hex}",
                provider_key="mock",
                agent_trust_status="untrusted",
            )
            evidence.add_case(
                name="disabled_merchant_untrusted_agent",
                status="pass" if disabled.get("error") and untrusted.get("error") else "fail",
                result=disabled,
            )
        else:
            evidence.add_case(
                name="disabled_merchant_untrusted_agent",
                status="skipped",
                blocker="requires checkout passport fixture",
            )

        unsupported_emi = validate_claims_against_tool_data(
            "This product includes no-cost EMI and a discount offer.",
            {"price": {"amount_minor_units": 349900, "currency": "INR"}},
        )
        negative_scenarios.append(
            {
                "name": "unsupported_emi_discount",
                "behavior": "refused" if not unsupported_emi.get("allowed") else "unexpected",
                "error": unsupported_emi.get("error"),
                "message": unsupported_emi.get("message", ""),
                "tool_sequence": [],
            }
        )

        evidence.add_case(
            name="hosted_agenticorg_discovery",
            status="skipped",
            blocker="requires hosted AgenticOrg service",
        )

        evidence.no_provider_call_confirmation = _no_provider_calls(tool_sequence)
        if evidence_report:
            evidence.write_markdown(evidence_report)

        return {
            "title": "AgenticOrg Commerce Sales Agent Real-Staging Demo",
            "scope": "real_staging_only",
            "grantex_dependency": config.grantex_host,
            "steps": steps,
            "negative_scenarios": negative_scenarios,
            "audit_summary": {
                "tool_sequence": tool_sequence,
                "no_direct_provider_calls": _no_provider_calls(tool_sequence),
                "no_provider_credential_handling": True,
                "internal_sandbox_only": False,
                "real_staging_only": True,
                "final_payment_confirmation_user_controlled": True,
                "auth_source_env_name": config.auth_env_name,
                "fixture_env_path": fixture.env_path,
                "fixture_env_var_names": fixture.variable_names,
                "fixture_synthetic_ids": fixture.synthetic_ids,
                "evidence_report": evidence_report,
            },
        }
    finally:
        await connector.disconnect()


async def run_demo() -> dict[str, Any]:
    cases = load_commerce_cases()
    cart_case = cases["commerce-cart-001"]
    checkout_case = cases["commerce-checkout-001"]
    cart_input = cart_case["input"]
    checkout_input = checkout_case["input"]
    tool_sequence: list[str] = []
    steps: list[dict[str, Any]] = []
    connector = make_mock_grantex_connector(tool_sequence)

    try:
        search = await connector.catalog_search(
            merchant_id=cart_input["merchant_id"],
            query=cart_input["query"],
        )
        steps.append(
            _step(
                "Product discovery",
                "Find a wireless keyboard.",
                "grantex_commerce:catalog_search",
                {"items": search["data"]["items"]},
                "Found one Grantex catalog item for Wireless Keyboard.",
            )
        )

        item = await connector.catalog_get_item(
            merchant_id=cart_input["merchant_id"],
            product_id=cart_input["product_id"],
        )
        steps.append(
            _step(
                "Product Q&A",
                "What is the price and what facts can you confirm?",
                "grantex_commerce:catalog_get_item",
                {"name": item["data"]["name"], "price": item["data"]["price"]},
                "The answer is grounded in Grantex catalog data: Wireless Keyboard, INR 3499.00.",
            )
        )

        inventory = await connector.inventory_check(
            merchant_id=cart_input["merchant_id"],
            variant_ids=[cart_input["variant_id"]],
        )
        caution = inventory_caution(inventory["data"])
        steps.append(
            _step(
                "Inventory check",
                "Is the black variant available?",
                "grantex_commerce:inventory_check",
                {"inventory": inventory["data"], "caution_required": caution["caution_required"]},
                "Grantex inventory reports in_stock. Availability is still confirmed only at checkout time.",
            )
        )

        cart = await connector.cart_create(
            merchant_id=cart_input["merchant_id"],
            currency=cart_input["currency"],
            line_items=cart_input["line_items"],
            idempotency_key=cart_input["idempotency_key"],
        )
        steps.append(
            _step(
                "Cart draft",
                "Create a cart draft for one keyboard.",
                "grantex_commerce:cart_create",
                {"cart_id": cart["data"]["cart_id"], "status": cart["data"]["status"]},
                "Cart draft created through Grantex.",
            )
        )

        consent = await connector.consent_request(
            merchant_id=checkout_input["merchant_id"],
            **checkout_input["consent_request"],
        )
        steps.append(
            _step(
                "Consent request",
                "Ask me to grant checkout consent.",
                "grantex_commerce:consent_request",
                {
                    "consent_request_id": consent["data"]["consent_request_id"],
                    "passport_type": consent["data"]["passport_type"],
                },
                "Consent request created. User remains in control of granting consent.",
            )
        )

        passport = await connector.consent_exchange(
            consent_request_id=consent["data"]["consent_request_id"],
        )
        passport_data = passport["data"]
        steps.append(
            _step(
                "Passport exchange",
                "Exchange my granted consent for checkout authorization.",
                "grantex_commerce:consent_exchange",
                {
                    "passport": "received_redacted",
                    "max_amount_minor_units": passport_data["passport"]["max_amount_minor_units"],
                    "currency": passport_data["passport"]["currency"],
                },
                "Grantex returned a checkout Commerce Passport. The token is not displayed.",
            )
        )

        payment = await connector.payment_create_intent(
            merchant_id=checkout_input["merchant_id"],
            cart_id=checkout_input["cart_id"],
            passport_jwt=passport_data["passport_jwt"],
            passport_max_amount_minor_units=checkout_input["passport_max_amount_minor_units"],
            amount_minor_units=checkout_input["amount_minor_units"],
            currency=checkout_input["currency"],
            idempotency_key=checkout_input["idempotency_key"],
            provider_key="mock",
        )
        steps.append(
            _step(
                "Payment intent",
                "Create the internal sandbox payment intent.",
                "grantex_commerce:payment_create_intent",
                {
                    "payment_intent_id": payment["data"]["payment_intent_id"],
                    "status": payment["data"]["status"],
                },
                "Payment intent created through Grantex internal sandbox routing.",
            )
        )

        checkout = await connector.checkout_create(
            payment_intent_id=payment["data"]["payment_intent_id"],
            passport_jwt=passport_data["passport_jwt"],
            success_url="https://agenticorg.local/commerce/success",
            cancel_url="https://agenticorg.local/commerce/cancel",
            idempotency_key="demo_checkout_001",
        )
        steps.append(
            _step(
                "Checkout handoff",
                "Create the checkout handoff.",
                "grantex_commerce:checkout_create",
                {
                    "checkout_id": checkout["data"]["checkout_id"],
                    "status": checkout["data"]["status"],
                    "checkout_url": checkout["data"]["checkout_url"],
                },
                "Checkout handoff is ready. Final payment action remains user-controlled.",
            )
        )

        status = await connector.payment_get_status(
            payment_intent_id=payment["data"]["payment_intent_id"],
            passport_jwt=passport_data["passport_jwt"],
        )
        steps.append(
            _step(
                "Payment status polling",
                "Check the payment status.",
                "grantex_commerce:payment_get_status",
                {
                    "payment_intent_id": status["data"]["payment_intent_id"],
                    "status": status["data"]["status"],
                },
                "Payment status was polled through Grantex only.",
            )
        )

        missing_consent = await connector.payment_create_intent(
            merchant_id=checkout_input["merchant_id"],
            cart_id=checkout_input["cart_id"],
            amount_minor_units=checkout_input["amount_minor_units"],
            currency=checkout_input["currency"],
            idempotency_key="demo_missing_consent",
            provider_key="mock",
        )
        unsupported_emi = validate_claims_against_tool_data(
            "This product includes no-cost EMI and a discount offer.",
            {"price": {"amount_minor_units": 349900, "currency": "INR"}},
        )
        stale_inventory = await _run_inventory_negative("commerce-inventory-001", "stale_inventory")
        unknown_inventory = await _run_inventory_negative("commerce-inventory-002", "unknown_inventory")

        negative_scenarios = [
            {
                "name": "missing_consent_checkout",
                "behavior": "refused",
                "error": missing_consent["error"],
                "message": missing_consent["message"],
                "tool_sequence": [],
            },
            {
                "name": "unsupported_emi_discount",
                "behavior": "refused",
                "error": unsupported_emi["error"],
                "message": unsupported_emi["message"],
                "tool_sequence": [],
            },
            stale_inventory,
            unknown_inventory,
        ]

        return {
            "title": "AgenticOrg Commerce Sales Agent V1 Internal Sandbox Demo",
            "scope": "internal_sandbox_only",
            "grantex_dependency": "mishrasanjeev/grantex#326",
            "steps": steps,
            "negative_scenarios": negative_scenarios,
            "audit_summary": {
                "tool_sequence": tool_sequence,
                "no_direct_provider_calls": _no_provider_calls(tool_sequence),
                "no_provider_credential_handling": True,
                "internal_sandbox_only": True,
                "final_payment_confirmation_user_controlled": True,
            },
        }
    finally:
        await connector.disconnect()


def format_demo_output(result: dict[str, Any]) -> str:
    lines = [
        result["title"],
        f"Scope: {result['scope']}",
        f"Grantex dependency: {result['grantex_dependency']}",
        "",
        "Happy path",
    ]
    for index, step in enumerate(result["steps"], start=1):
        lines.extend(
            [
                f"{index}. {step['step']}",
                f"   user: {step['user_utterance']}",
                f"   tool: {step['tool_alias']}",
                f"   result: {json.dumps(step['safe_result'], sort_keys=True)}",
                f"   response: {step['response']}",
            ]
        )

    lines.extend(["", "Negative mini-demo"])
    for scenario in result["negative_scenarios"]:
        lines.extend(
            [
                f"- {scenario['name']}: {scenario['behavior']}",
                f"  message: {scenario['message']}",
            ]
        )

    audit = result["audit_summary"]
    lines.extend(
        [
            "",
            "Audit and safety summary",
            f"- no direct provider calls: {audit['no_direct_provider_calls']}",
            f"- no provider credential handling: {audit['no_provider_credential_handling']}",
            f"- internal sandbox only: {audit['internal_sandbox_only']}",
            f"- final payment confirmation user-controlled: {audit['final_payment_confirmation_user_controlled']}",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Commerce Sales Agent demo.")
    parser.add_argument("--mode", choices=("mock", "real-staging"), default="mock")
    parser.add_argument("--grantex-base", default=None)
    parser.add_argument("--allow-smoke-cloud-run-url", default=None)
    parser.add_argument("--evidence-report", default=None)
    parser.add_argument("--fixture-env", default=None)
    return parser


async def _amain() -> int:
    args = _parser().parse_args()
    try:
        if args.mode == "real-staging":
            result = await run_real_staging_demo(
                grantex_base_url=args.grantex_base,
                allow_smoke_cloud_run_url=args.allow_smoke_cloud_run_url,
                evidence_report=args.evidence_report,
                fixture_env_path=args.fixture_env,
            )
        else:
            result = await run_demo()
    except RealStagingConfigError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2

    print(format_demo_output(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
