from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from core.commerce.buyer_session import build_channel_neutral_buyer_response
from core.commerce.sales_guardrails import validate_payment_action
from core.commerce.session_authority import session_authority_from_payload

NOW = datetime(2026, 6, 9, 0, 0, tzinfo=UTC)


def _authority(**overrides: Any) -> dict[str, Any]:
    authority: dict[str, Any] = {
        "consent": {"status": "granted", "expires_at": "2099-06-09T00:10:00Z"},
        "passport": {
            "status": "valid",
            "expires_at": "2099-06-09T00:10:00Z",
            "merchant_id": "merchant_synthetic_c6u6",
            "agent_id": "agent_synthetic_c6u6",
            "subject": "buyer_synthetic_c6u6",
        },
        "session": {
            "status": "active",
            "id": "buyer_session_synthetic_c6u6",
            "authority_checked_at": "2099-06-08T23:59:00Z",
        },
        "merchant": {"status": "enabled"},
        "agent": {"status": "trusted"},
        "policy": {"decision": "allow"},
        "evidence": [
            {"key": "consent_check", "status": "pass"},
            {"key": "passport_status_check", "status": "pass"},
            {"key": "policy_decision", "status": "pass"},
        ],
    }
    authority.update(overrides)
    return authority


def _preview_payload(authority: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": {
            "integration_status": "sandbox_handoff_requested",
            "merchant_reference": "merchant_ref_synthetic_c6u6",
            "sandbox_only": True,
            "buyer_agent_discovery_is_public": False,
            "agenticorg_public_discovery_enabled": False,
            "public_discovery_enabled": False,
            "checkout_payment_enabled": False,
            "live_provider_enabled": False,
            "live_plural_enabled": False,
            "production_approval_status": "not_approved",
            "live_mode_status": "not_live",
            "session_authority": authority,
            "public_discovery_state": {
                "grantex_state": "hidden",
                "agenticorg_state": "hidden",
            },
        }
    }


def _decision(authority: dict[str, Any]) -> dict[str, Any]:
    return session_authority_from_payload(
        {"data": {"session_authority": authority}},
        expected_merchant_id="merchant_synthetic_c6u6",
        expected_agent_id="agent_synthetic_c6u6",
        expected_buyer_id="buyer_synthetic_c6u6",
        expected_session_id="buyer_session_synthetic_c6u6",
        now=NOW,
    )


@pytest.mark.parametrize(
    ("label", "authority", "refusal_code"),
    [
        ("missing consent", {"consent": {}}, "consent_missing"),
        ("missing passport", {"passport": {}}, "passport_missing"),
        ("expired passport", {"passport": {"status": "expired"}}, "passport_expired"),
        ("revoked passport", {"passport": {"status": "revoked"}}, "passport_revoked"),
        ("revoked passport marker", {"passport": {"status": "valid", "revoked": True}}, "passport_revoked"),
        ("disabled merchant", {"merchant": {"status": "disabled"}}, "merchant_disabled"),
        ("disabled agent", {"agent": {"status": "disabled"}}, "agent_disabled"),
        ("policy denial", {"policy": {"decision": "deny"}}, "policy_denied"),
        (
            "stale authority",
            {
                "session": {
                    "status": "active",
                    "id": "buyer_session_synthetic_c6u6",
                    "authority_checked_at": "2026-06-08T23:45:00Z",
                }
            },
            "authority_stale",
        ),
        (
            "malformed authority timestamp",
            {
                "session": {
                    "status": "active",
                    "id": "buyer_session_synthetic_c6u6",
                    "authority_checked_at": "not-a-date",
                }
            },
            "authority_stale",
        ),
        (
            "malformed passport expiry",
            {"passport": {"status": "valid", "expires_at": "not-a-date"}},
            "passport_expired",
        ),
        (
            "mismatched merchant",
            {"passport": {"status": "valid", "merchant_id": "merchant_other"}},
            "merchant_mismatch",
        ),
        ("mismatched agent", {"passport": {"status": "valid", "agent_id": "agent_other"}}, "agent_mismatch"),
        ("mismatched buyer", {"passport": {"status": "valid", "subject": "buyer_other"}}, "buyer_mismatch"),
        (
            "mismatched session",
            {
                "session": {
                    "status": "active",
                    "id": "session_other",
                    "authority_checked_at": "2026-06-08T23:59:00Z",
                }
            },
            "session_mismatch",
        ),
        ("ambiguous consent", {"consent": {"status": "unknown"}}, "consent_ambiguous"),
    ],
)
def test_authority_state_fails_closed(label: str, authority: dict[str, Any], refusal_code: str) -> None:
    result = _decision(_authority(**authority))

    assert result["authority_valid"] is False, label
    assert result["protected_action_allowed"] is False
    assert result["checkout_payment_enabled"] is False
    assert result["live_provider_enabled"] is False
    assert result["public_discovery_enabled"] is False
    assert refusal_code in result["blockers"]


def test_fresh_authority_is_buyer_safe_but_not_checkout_permission() -> None:
    result = _decision(_authority())

    assert result["authority_valid"] is True
    assert result["protected_action_allowed"] is False
    assert result["refusal_code"] == "checkout_payment_not_enabled_by_c6u6"
    assert result["checkout_payment_enabled"] is False


def test_cached_revoked_authority_refuses_before_payment_guardrail_allows_action() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "merchant_id": "merchant_synthetic_c6u6",
            "agent_id": "agent_synthetic_c6u6",
            "buyer_id": "buyer_synthetic_c6u6",
            "buyer_session_id": "buyer_session_synthetic_c6u6",
            "passport_jwt": "passport.synthetic.raw.value",
            "amount_minor_units": 1000,
            "currency": "INR",
            "provider_key": "mock",
            "session_authority": _authority(passport={"status": "revoked"}),
        },
    )
    serialized = json.dumps(result, sort_keys=True).lower()

    assert result["allowed"] is False
    assert result["error"] == "passport_revoked"
    assert "passport.synthetic.raw.value" not in serialized
    assert "jwt" not in serialized
    assert "raw_payload" not in serialized


def test_fresh_cached_authority_still_does_not_enable_payment() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "merchant_id": "merchant_synthetic_c6u6",
            "agent_id": "agent_synthetic_c6u6",
            "buyer_id": "buyer_synthetic_c6u6",
            "buyer_session_id": "buyer_session_synthetic_c6u6",
            "passport_jwt": "passport.synthetic.raw.value",
            "amount_minor_units": 1000,
            "currency": "INR",
            "provider_key": "mock",
            "session_authority": _authority(),
        },
    )

    assert result["allowed"] is False
    assert result["error"] == "checkout_payment_not_enabled_by_c6u6"
    assert result["details"]["authority"]["authority_valid"] is True
    assert result["details"]["authority"]["protected_action_allowed"] is False


def test_missing_authority_summary_refuses_payment_even_with_passport_string() -> None:
    result = validate_payment_action(
        "payment_create_intent",
        {
            "merchant_id": "merchant_synthetic_c6u6",
            "agent_id": "agent_synthetic_c6u6",
            "buyer_id": "buyer_synthetic_c6u6",
            "buyer_session_id": "buyer_session_synthetic_c6u6",
            "passport_jwt": "passport.synthetic.raw.value",
            "amount_minor_units": 1000,
            "currency": "INR",
            "provider_key": "mock",
        },
    )
    serialized = json.dumps(result, sort_keys=True).lower()

    assert result["allowed"] is False
    assert result["error"] == "consent_missing"
    assert "passport.synthetic.raw.value" not in serialized
    assert result["details"]["authority"]["authority_valid"] is False


def test_ambiguous_cached_authority_refuses_payment_action() -> None:
    result = validate_payment_action(
        "payment_get_status",
        {
            "payment_intent_id": "pi_synthetic_c6u6",
            "passport_jwt": "passport.synthetic.raw.value",
            "session_authority": "not-a-structured-authority",
        },
    )

    assert result["allowed"] is False
    assert result["error"] == "authority_ambiguous"


def test_buyer_session_projects_redacted_authority_and_keeps_public_discovery_hidden() -> None:
    response = build_channel_neutral_buyer_response(
        _preview_payload(
            _authority(
                evidence=[
                    {"key": "consent_check", "status": "pass"},
                    {"key": "passport_jwt", "status": "pass"},
                    {"key": "https://merchant-private.internal/authority", "status": "pass"},
                    {"key": "policy_decision", "status": "pass"},
                ],
                raw_payload={"token": "synthetic-secret"},
                private_url="https://merchant-private.internal/authority",
            )
        ),
        request_text="Show merchant preview.",
        channel="web_chat",
    )
    authority = response["evidence_summary"]["session_authority"]
    serialized = json.dumps(response, sort_keys=True).lower()

    assert response["status"] == "preview_only"
    assert response["evidence_summary"]["public_discovery_state"]["public_discovery_visible"] is False
    assert authority["authority_valid"] is True
    assert authority["protected_action_allowed"] is False
    assert "passport_jwt" not in authority["evidence_keys"]
    assert "merchant-private.internal" not in serialized
    assert "synthetic-secret" not in serialized
    assert "raw_payload" not in serialized


@pytest.mark.parametrize(
    ("request_text", "refusal_code"),
    [
        ("Create checkout and payment now.", "checkout_payment_not_enabled"),
        ("Use live Plural provider routing.", "live_provider_not_enabled"),
        ("Call the merchant private API.", "merchant_private_api_not_allowed"),
    ],
)
def test_checkout_live_provider_and_private_api_remain_blocked(request_text: str, refusal_code: str) -> None:
    response = build_channel_neutral_buyer_response(
        _preview_payload(_authority()),
        request_text=request_text,
        channel="future_channel",
    )
    serialized = json.dumps(response, sort_keys=True).lower()

    assert response["status"] == "refused"
    assert response["refusal_code"] == refusal_code
    assert response["evidence_summary"]["grantex_call_status"] == "not_attempted"
    assert "provider call" not in serialized
    assert "merchant-private" not in serialized
    assert "checkout_payment_enabled\": true" not in serialized
    assert "live_provider_enabled\": true" not in serialized
    assert "live_plural_enabled\": true" not in serialized
