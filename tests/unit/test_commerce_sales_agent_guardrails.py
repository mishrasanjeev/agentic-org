from __future__ import annotations

from core.commerce.sales_guardrails import (
    GRANTEX_COMMERCE_DEFAULT_TOOLS,
    inventory_caution,
    validate_claims_against_tool_data,
    validate_payment_action,
)


def test_missing_consent_refuses_checkout_payment() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {"amount_minor_units": 1000, "currency": "INR", "provider_key": "mock"},
    )

    assert result["allowed"] is False
    assert result["error"] == "consent_required"


def test_denied_consent_refuses_checkout_payment() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "consent_status": "denied",
            "passport_jwt": "passport.jwt.value",
            "amount_minor_units": 1000,
            "currency": "INR",
            "provider_key": "mock",
        },
    )

    assert result["allowed"] is False
    assert result["error"] == "consent_denied"


def test_amount_cap_breach_refuses_checkout_payment() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "passport_jwt": "passport.jwt.value",
            "amount_minor_units": 5000,
            "passport_max_amount_minor_units": 4000,
            "currency": "INR",
            "provider_key": "mock",
        },
    )

    assert result["allowed"] is False
    assert result["error"] == "amount_cap_exceeded"


def test_stale_unknown_inventory_requires_cautious_response() -> None:
    stale = inventory_caution({"items": [{"variant_id": "v1", "availability_status": "unknown", "stale": True}]})
    missing = inventory_caution({})

    assert stale["caution_required"] is True
    assert "Do not guarantee availability" in stale["message"]
    assert missing["caution_required"] is True


def test_disabled_merchant_policy_denial_and_untrusted_agent_refuse() -> None:
    merchant = validate_payment_action(
        "checkout_create",
        {"passport_jwt": "passport.jwt.value", "merchant_status": "disabled"},
    )
    policy = validate_payment_action(
        "checkout_create",
        {"passport_jwt": "passport.jwt.value", "policy_decision": "deny"},
    )
    agent = validate_payment_action(
        "checkout_create",
        {"passport_jwt": "passport.jwt.value", "agent_trust_status": "untrusted"},
    )

    assert merchant["error"] == "merchant_disabled"
    assert policy["error"] == "policy_denied"
    assert agent["error"] == "agent_not_trusted"


def test_emergency_disable_and_revoked_passport_refuse() -> None:
    emergency = validate_payment_action(
        "checkout_create",
        {"passport_jwt": "passport.jwt.value", "emergency_disabled": True},
    )
    revoked = validate_payment_action(
        "checkout_create",
        {"passport_jwt": "passport.jwt.value", "passport_status": "revoked"},
    )

    assert emergency["error"] == "merchant_emergency_disabled"
    assert revoked["error"] == "passport_revoked"


def test_live_provider_key_is_blocked() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "passport_jwt": "passport.jwt.value",
            "amount_minor_units": 1000,
            "currency": "INR",
            "provider_key": "plural",
        },
    )

    assert result["allowed"] is False
    assert result["error"] == "live_provider_blocked"


def test_unsupported_emi_discount_offer_warranty_claims_refuse() -> None:
    result = validate_claims_against_tool_data(
        "This product includes no-cost EMI, a discount offer, and warranty.",
        {"price": {"amount_minor_units": 1000, "currency": "INR"}},
    )

    assert result["allowed"] is False
    assert result["error"] == "unsupported_commerce_claim"
    assert {"emi", "discount", "offer", "warranty"}.issubset(set(result["details"]["unsupported_claims"]))


def test_tool_supported_commerce_claims_are_allowed() -> None:
    result = validate_claims_against_tool_data(
        "This product includes a warranty and return policy.",
        {
            "warranty": {"summary": "One year"},
            "return_policy": {"summary": "Seven days"},
        },
    )

    assert result["allowed"] is True


def test_default_tools_are_grantex_only_aliases() -> None:
    assert GRANTEX_COMMERCE_DEFAULT_TOOLS
    assert all(tool.startswith("grantex_commerce:") for tool in GRANTEX_COMMERCE_DEFAULT_TOOLS)
    assert all("." not in tool.split(":", 1)[1] for tool in GRANTEX_COMMERCE_DEFAULT_TOOLS)

