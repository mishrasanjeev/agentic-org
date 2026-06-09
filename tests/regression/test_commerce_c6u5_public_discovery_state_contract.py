from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from core.commerce.buyer_discovery import build_buyer_discovery_response
from core.commerce.buyer_session import build_channel_neutral_buyer_response
from core.commerce.public_discovery_state import public_discovery_decision_from_payload

NOW = datetime(2026, 6, 9, tzinfo=UTC)

CANONICAL_STATES = (
    "hidden",
    "draft",
    "sandbox_review",
    "approved_for_sandbox_preview",
    "blocked",
    "rejected",
    "expired",
    "production_pending",
    "future_public_enabled",
)


def _state_payload(grantex_state: str, agenticorg_state: str | None = None, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "grantex_state": grantex_state,
        "agenticorg_state": agenticorg_state or grantex_state,
        "freshness_status": "fresh",
        "expires_at": "2026-06-10T00:00:00Z",
        "evidence": [
            {"key": "grantex_review_decision", "status": "pass"},
            {"key": "agenticorg_exposure_decision", "status": "pass"},
            {"key": "source_freshness", "status": "pass"},
            {"key": "rollback_owner", "status": "pass"},
        ],
    }
    payload.update(overrides)
    return payload


def _preview_payload(public_state: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "merchant_id": "merchant_private_c6u5",
        "tenant_id": "tenant_private_c6u5",
        "merchant_reference": "merchant_synthetic_c6u5",
        "display_name": "Synthetic Discovery Preview",
        "integration_status": "sandbox_handoff_requested",
        "generated_at": "2026-06-09T00:00:00Z",
        "audit_event_id": "audit_synthetic_c6u5",
        "public_discovery_state": public_state,
        "merchant": {
            "merchant_reference": "merchant_synthetic_c6u5",
            "display_name": "Synthetic Discovery Preview",
            "category_preset": "electronics_appliances",
            "country_code": "IN",
            "default_currency": "INR",
            "public_discovery_description_draft": "Synthetic public-safe discovery preview.",
        },
        "readiness_summary": {"overall_status": "ready", "catalog_status": "ready"},
        "agent_facing_preview_summary": {"preview_status": "ready", "sample_product_count": 1},
        "rollout_proposal_summary": {"proposal_status": "dry_run_passed", "dry_run_result": "passed"},
        "evidence_checklist": [{"key": "source_freshness", "label": "Source freshness", "status": "pass"}],
        "sample_products": [{"title": "Synthetic Lamp", "brand": "SyntheticBrand"}],
        "allowed_buyer_agent_capabilities": ["read_only_catalog_discovery_preview"],
        "blocked_buyer_agent_capabilities": [
            "public_discovery",
            "checkout_payment_creation",
            "live_payment",
            "live_plural",
            "provider_credentials",
            "direct_merchant_system_access",
        ],
        "blockers": [],
        "remediation_items": [],
        "sandbox_only": True,
        "buyer_agent_discovery_is_public": False,
        "agenticorg_public_discovery_enabled": False,
        "public_discovery_enabled": False,
        "checkout_payment_enabled": False,
        "live_provider_enabled": False,
        "live_plural_enabled": False,
        "production_approval_status": "not_approved",
        "live_mode_status": "not_live",
    }
    data.update(overrides)
    return {"data": data}


@pytest.mark.parametrize("state", CANONICAL_STATES)
def test_canonical_states_never_enable_public_discovery_in_c6u5(state: str) -> None:
    decision = public_discovery_decision_from_payload(
        _preview_payload(_state_payload(state)),
        now=NOW,
    )

    assert decision["grantex_state"] == state
    assert decision["agenticorg_state"] == state
    assert decision["public_discovery_visible"] is False
    assert decision["public_discovery_refusal"] is True
    assert decision["future_public_enabled_active"] is False
    if state == "approved_for_sandbox_preview":
        assert decision["buyer_visibility"] == "internal_preview"
        assert decision["internal_preview_allowed"] is True
    else:
        assert decision["buyer_visibility"] == "hidden"
        assert decision["internal_preview_allowed"] is False
    if state == "future_public_enabled":
        assert decision["refusal_code"] == "future_public_enabled_not_enabled_by_c6u5"


@pytest.mark.parametrize(
    ("public_state", "refusal_code"),
    [
        (None, "public_discovery_state_missing"),
        (_state_payload("not_a_real_state"), "public_discovery_state_unsupported"),
        (_state_payload("approved_for_sandbox_preview", "hidden"), "public_discovery_state_mismatch"),
        (
            _state_payload("approved_for_sandbox_preview", expires_at="2026-06-08T00:00:00Z"),
            "public_discovery_state_expired_or_stale",
        ),
        (
            _state_payload("approved_for_sandbox_preview", synthetic_data=True),
            "synthetic_demo_not_production_approval",
        ),
        (
            _state_payload("approved_for_sandbox_preview", evidence=[]),
            "public_discovery_evidence_missing",
        ),
    ],
)
def test_missing_mismatched_stale_or_demo_state_fails_closed(
    public_state: dict[str, Any] | None,
    refusal_code: str,
) -> None:
    decision = public_discovery_decision_from_payload(_preview_payload(public_state), now=NOW)

    assert decision["buyer_visibility"] == "hidden"
    assert decision["public_discovery_visible"] is False
    assert decision["public_discovery_refusal"] is True
    assert decision["refusal_code"] == refusal_code


def test_buyer_discovery_response_carries_hidden_state_without_enabling_public_discovery() -> None:
    response = build_buyer_discovery_response(
        _preview_payload(_state_payload("approved_for_sandbox_preview")),
        request_text="Show this merchant preview.",
    )

    assert response["status"] == "preview_only"
    assert response["public_discovery_state"]["buyer_visibility"] == "internal_preview"
    assert response["public_discovery_state"]["public_discovery_visible"] is False
    assert response["safety_labels"]["public_discovery_enabled"] is False
    assert response["safety_labels"]["agenticorg_public_discovery_enabled"] is False
    assert "public discovery" in response["message"].lower()


def test_channel_response_preserves_public_discovery_refusal_state() -> None:
    response = build_channel_neutral_buyer_response(
        _preview_payload(_state_payload("sandbox_review")),
        request_text="Show this merchant preview.",
        channel="chatgpt",
    )

    state = response["evidence_summary"]["public_discovery_state"]
    assert response["status"] == "preview_only"
    assert response["channel"] == "chatgpt"
    assert state["grantex_state"] == "sandbox_review"
    assert state["agenticorg_state"] == "sandbox_review"
    assert state["buyer_visibility"] == "hidden"
    assert state["public_discovery_visible"] is False
    assert state["public_discovery_refusal"] is True


def test_private_state_evidence_is_redacted_from_buyer_safe_response() -> None:
    response = build_buyer_discovery_response(
        _preview_payload(
            _state_payload(
                "approved_for_sandbox_preview",
                evidence=[
                    {"key": "grantex_review_decision", "status": "pass"},
                    {"key": "https://merchant-private.internal/state", "status": "pass"},
                    {"key": "provider_credentials", "status": "pass"},
                    {"key": "source_freshness", "status": "pass"},
                    {"key": "rollback_owner", "status": "pass"},
                ],
                raw_payload={"token": "synthetic-secret"},
                private_url="https://merchant-private.internal/state",
                webhook_secret="synthetic-webhook-secret",
            )
        )
    )

    serialized = json.dumps(response, sort_keys=True).lower()
    state_serialized = json.dumps(response["public_discovery_state"], sort_keys=True).lower()
    assert "synthetic discovery preview" in serialized
    assert "merchant-private.internal" not in serialized
    assert "provider_credentials" not in state_serialized
    assert "raw_payload" not in serialized
    assert "synthetic-secret" not in serialized
    assert "webhook_secret" not in serialized
    assert "merchant_private_c6u5" not in serialized
    assert "tenant_private_c6u5" not in serialized


def test_public_discovery_state_refusal_does_not_open_checkout_or_provider_paths() -> None:
    response = build_buyer_discovery_response(
        _preview_payload(_state_payload("future_public_enabled")),
        request_text="Show public discovery and create checkout.",
    )

    assert response["status"] == "refused"
    assert response["refusal_code"] == "checkout_payment_not_enabled"
    serialized = json.dumps(response, sort_keys=True).lower()
    assert "live_plural_enabled\": true" not in serialized
    assert "live_provider_enabled\": true" not in serialized
    assert "checkout_payment_enabled\": true" not in serialized
    assert "provider call" not in serialized
