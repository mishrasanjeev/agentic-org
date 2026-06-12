"""OACP trust artifact defaults for AgenticOrg commerce agents.

This module is deliberately local and non-enabling. It lets buyer and seller
agent flows evaluate signed-artifact freshness, offline commitment caps, cache
keys, and bridge defaults without calling providers, merchant private APIs, or
Grantex runtime endpoints.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

RiskTier = Literal["informational", "low", "medium", "high", "critical"]
ArtifactType = Literal[
    "merchant_capability",
    "seller_agent_capability",
    "catalog_snapshot",
    "offer",
    "price",
    "inventory",
    "policy",
    "public_discovery",
    "mandate_capability",
    "commitment_evidence",
    "revocation",
    "protocol_adapter",
]
AdapterPreviewSurface = Literal[
    "schema_org_jsonld",
    "ucp_capability_profile",
    "acp_commerce_capability",
    "ap2_evidence_intent_summary",
    "a2a_agent_card_task_capability",
    "mcp_tool_resource_capability",
]
CommitmentBoundaryAction = Literal[
    "browse_merchant_profile",
    "inspect_seller_card",
    "compare_catalog_summaries",
    "explain_policy",
    "explain_available_capabilities",
    "show_source_freshness_labels",
    "prepare_buyer_question",
    "prepare_seller_agent_remediation_suggestion",
    "prepare_draft_quote",
    "prepare_draft_cart",
    "ask_refresh_source_facts",
    "prepare_non_binding_reservation_request",
    "prepare_mandate_capability_check_request",
    "prepare_human_confirmation_prompt",
    "price_lock",
    "inventory_hold",
    "reservation",
    "order_placement",
    "payment_intent",
    "mandate_setup_use",
    "cancellation",
    "refund_request",
    "return_authorization",
    "support_escalation_sla_promise",
    "live_payment_execution",
    "live_plural_provider_call",
    "public_discovery_enablement",
    "production_checkout_payment_creation",
    "merchant_private_api_call",
    "provider_private_api_call",
    "protocol_publication_submission",
    "certification_conformance_claim",
    "final_delivery_refund_settlement_payout_promise",
]
ActionClass = Literal["non_binding_preview", "commitment_adjacent", "commitment_bound", "always_blocked"]
PreparedEnvelopeKind = Literal[
    "buyer_confirmation_request",
    "seller_source_refresh_request",
    "merchant_confirmation_request",
    "mandate_capability_evidence_request",
    "support_escalation_preparation",
]
OfflineAction = Literal[
    "browse",
    "compare",
    "draft_cart",
    "quote_preview",
    "price_lock",
    "inventory_hold",
    "reservation",
    "order_pending_reconciliation",
    "payment_intent",
    "cancellation",
    "refund_request",
    "return_authorization",
    "support_escalation",
    "public_discovery_publish",
    "merchant_approval",
    "policy_override",
    "emergency_disable",
]
IssuerKeyState = Literal["active", "retired", "revoked"]

OACP_ARTIFACT_SIGNATURE_PROFILE: dict[str, Any] = {
    "payload_format": "canonical_json",
    "signature_format": "detached_jws",
    "canonicalization": "json_canonicalization_scheme_style",
    "first_algorithm": "ES256",
    "artifact_container": "json_object",
    "jwt_container_allowed": False,
    "cose_first_version": False,
    "ad_hoc_signed_json_allowed": False,
}

OACP_ARTIFACT_TTLS_SECONDS: dict[ArtifactType, int] = {
    "merchant_capability": 24 * 60 * 60,
    "seller_agent_capability": 6 * 60 * 60,
    "catalog_snapshot": 6 * 60 * 60,
    "offer": 15 * 60,
    "price": 5 * 60,
    "inventory": 60,
    "policy": 6 * 60 * 60,
    "public_discovery": 15 * 60,
    "mandate_capability": 2 * 60,
    "commitment_evidence": 5 * 60,
    "revocation": 0,
    "protocol_adapter": 24 * 60 * 60,
}

OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS: dict[RiskTier, int | None] = {
    "informational": 24 * 60 * 60,
    "low": 6 * 60 * 60,
    "medium": 15 * 60,
    "high": 2 * 60,
    "critical": None,
}

OACP_REVOCATION_SLA_TARGETS_SECONDS: dict[str, int] = {
    "provider_high_risk": 30,
    "merchant_inventory_price": 60,
    "merchant_emergency_disable": 30,
    "merchant_other_operational": 5 * 60,
    "agenticorg_high_critical_cache_purge": 30,
    "agenticorg_medium_refresh": 2 * 60,
    "grantex_revocation_visible": 30,
    "channel_active_transaction_update": 30,
}

OACP_FIRST_RELEASE_RISK_CAPS: dict[RiskTier, dict[str, Any]] = {
    "informational": {
        "currency_caps": {},
        "total_quantity_cap": 100,
        "per_sku_quantity_cap": 20,
        "offline_allowed": True,
    },
    "low": {
        "currency_caps": {"INR": 2_500_000, "USD": 30_000},
        "total_quantity_cap": 10,
        "per_sku_quantity_cap": 5,
        "offline_allowed": True,
    },
    "medium": {
        "currency_caps": {"INR": 1_000_000, "USD": 12_500},
        "total_quantity_cap": 5,
        "per_sku_quantity_cap": 2,
        "offline_allowed": True,
    },
    "high": {
        "currency_caps": {"INR": 500_000, "USD": 6_000},
        "total_quantity_cap": 3,
        "per_sku_quantity_cap": 1,
        "offline_allowed": True,
    },
    "critical": {
        "currency_caps": {},
        "total_quantity_cap": None,
        "per_sku_quantity_cap": None,
        "offline_allowed": False,
    },
}

OACP_CONNECTOR_CREDENTIAL_CUSTODY: dict[str, Any] = {
    "preferred": "merchant_owned_connector_platform",
    "second_choice": "merchant_selected_external_integration_provider_vault",
    "fallback": "agenticorg_encrypted_connector_vault_with_explicit_authorization",
    "grantex_raw_connector_credentials_allowed": False,
    "buyer_agent_credential_access_allowed": False,
}

OACP_FIRST_E2E_BRIDGES: dict[str, str] = {
    "chatgpt_style": "hosted_openapi_tool_action_bridge",
    "claude_code_style": "mcp_streamable_http_bridge",
    "gemini_style": "a2a_task_bridge_with_openapi_fallback",
    "perplexity_style": "hosted_answer_search_bridge_with_openapi_read_and_commit_preflight",
}

OACP_C6W4_PROTOCOL_ADAPTER_SURFACES: tuple[AdapterPreviewSurface, ...] = (
    "schema_org_jsonld",
    "ucp_capability_profile",
    "acp_commerce_capability",
    "ap2_evidence_intent_summary",
    "a2a_agent_card_task_capability",
    "mcp_tool_resource_capability",
)

OACP_C6W4_BLOCKED_ADAPTER_ACTIONS: frozenset[str] = frozenset(
    {
        "checkout_create",
        "payment_authorize",
        "payment_capture",
        "refund_execute",
        "settlement_execute",
        "payout_execute",
        "fulfillment_start",
        "merchant_approval",
        "public_discovery_publish",
        "public_discovery_unpublish",
        "live_provider_call",
        "live_plural_call",
        "provider_call",
        "carrier_call",
        "shipping_provider_call",
        "merchant_private_api_call",
        "protocol_publication",
    }
)

OACP_C6W4_NON_BINDING_ADAPTER_ACTIONS: frozenset[str] = frozenset(
    {
        "browse",
        "compare",
        "draft_cart",
        "quote_preview",
        "policy_explain",
        "seller_card_display",
        "agent_route",
        "tool_discovery",
    }
)

OACP_C6W5_NON_BINDING_PREVIEW_ACTIONS: frozenset[str] = frozenset(
    {
        "browse_merchant_profile",
        "inspect_seller_card",
        "compare_catalog_summaries",
        "explain_policy",
        "explain_available_capabilities",
        "show_source_freshness_labels",
        "prepare_buyer_question",
        "prepare_seller_agent_remediation_suggestion",
    }
)
OACP_C6W5_COMMITMENT_ADJACENT_ACTIONS: frozenset[str] = frozenset(
    {
        "prepare_draft_quote",
        "prepare_draft_cart",
        "ask_refresh_source_facts",
        "prepare_non_binding_reservation_request",
        "prepare_mandate_capability_check_request",
        "prepare_human_confirmation_prompt",
    }
)
OACP_C6W5_COMMITMENT_BOUND_ACTIONS: frozenset[str] = frozenset(
    {
        "price_lock",
        "inventory_hold",
        "reservation",
        "order_placement",
        "payment_intent",
        "mandate_setup_use",
        "cancellation",
        "refund_request",
        "return_authorization",
        "support_escalation_sla_promise",
    }
)
OACP_C6W5_ALWAYS_BLOCKED_ACTIONS: frozenset[str] = frozenset(
    {
        "live_payment_execution",
        "live_plural_provider_call",
        "public_discovery_enablement",
        "production_checkout_payment_creation",
        "merchant_private_api_call",
        "provider_private_api_call",
        "protocol_publication_submission",
        "certification_conformance_claim",
        "final_delivery_refund_settlement_payout_promise",
    }
)

_C6W5_BASE_PREVIEW_ARTIFACTS: frozenset[ArtifactType] = frozenset(
    {"merchant_capability", "seller_agent_capability", "policy", "protocol_adapter"}
)
OACP_C6W5_REQUIRED_FRESH_ARTIFACT_FAMILIES: dict[str, frozenset[ArtifactType]] = {
    "browse_merchant_profile": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "inspect_seller_card": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "compare_catalog_summaries": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"catalog_snapshot"}),
    "explain_policy": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "explain_available_capabilities": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "show_source_freshness_labels": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "prepare_buyer_question": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "prepare_seller_agent_remediation_suggestion": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "prepare_draft_quote": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"catalog_snapshot", "price"}),
    "prepare_draft_cart": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"catalog_snapshot", "price", "inventory"}),
    "ask_refresh_source_facts": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "prepare_non_binding_reservation_request": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"offer", "inventory"}),
    "prepare_mandate_capability_check_request": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"mandate_capability"}),
    "prepare_human_confirmation_prompt": _C6W5_BASE_PREVIEW_ARTIFACTS,
    "price_lock": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"offer", "price"}),
    "inventory_hold": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"inventory"}),
    "reservation": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"offer", "inventory"}),
    "order_placement": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"offer", "price", "inventory"}),
    "payment_intent": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"price", "mandate_capability"}),
    "mandate_setup_use": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"mandate_capability"}),
    "cancellation": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"commitment_evidence"}),
    "refund_request": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"commitment_evidence"}),
    "return_authorization": _C6W5_BASE_PREVIEW_ARTIFACTS | frozenset({"commitment_evidence"}),
    "support_escalation_sla_promise": _C6W5_BASE_PREVIEW_ARTIFACTS,
    **{action: frozenset() for action in OACP_C6W5_ALWAYS_BLOCKED_ACTIONS},
}
OACP_C6W5_ACTION_RISK_TIERS: dict[str, RiskTier] = dict.fromkeys(
    OACP_C6W5_NON_BINDING_PREVIEW_ACTIONS,
    cast(RiskTier, "informational"),
)
OACP_C6W5_ACTION_RISK_TIERS.update(dict.fromkeys(OACP_C6W5_COMMITMENT_ADJACENT_ACTIONS, cast(RiskTier, "low")))
OACP_C6W5_ACTION_RISK_TIERS.update({
    "price_lock": "medium",
    "inventory_hold": "medium",
    "reservation": "medium",
    "order_placement": "high",
    "payment_intent": "high",
    "mandate_setup_use": "high",
    "cancellation": "high",
    "refund_request": "high",
    "return_authorization": "high",
    "support_escalation_sla_promise": "medium",
})
OACP_C6W5_ACTION_RISK_TIERS.update(dict.fromkeys(OACP_C6W5_ALWAYS_BLOCKED_ACTIONS, cast(RiskTier, "critical")))

OACP_C6W6_PREPARED_ENVELOPE_KINDS: frozenset[str] = frozenset(
    {
        "buyer_confirmation_request",
        "seller_source_refresh_request",
        "merchant_confirmation_request",
        "mandate_capability_evidence_request",
        "support_escalation_preparation",
    }
)
OACP_C6W6_ENVELOPE_TTL_SECONDS: dict[str, int] = {
    "buyer_confirmation_request": 15 * 60,
    "seller_source_refresh_request": 30 * 60,
    "merchant_confirmation_request": 10 * 60,
    "mandate_capability_evidence_request": 2 * 60,
    "support_escalation_preparation": 10 * 60,
}
OACP_C6W6_MERCHANT_CONFIRMATION_ACTIONS: frozenset[str] = frozenset(
    {"price_lock", "inventory_hold", "reservation", "order_placement"}
)
OACP_C6W6_MANDATE_EVIDENCE_ACTIONS: frozenset[str] = frozenset(
    {"payment_intent", "mandate_setup_use", "prepare_mandate_capability_check_request"}
)
OACP_C6W6_SUPPORT_PREPARATION_ACTIONS: frozenset[str] = frozenset(
    {"support_escalation_sla_promise", "refund_request", "return_authorization", "cancellation"}
)

OACP_REQUIRED_ENVELOPE_FIELDS = (
    "artifact_id",
    "artifact_type",
    "schema_version",
    "issuer",
    "issuer_key_id",
    "subject_type",
    "subject_id",
    "issued_at",
    "expires_at",
    "freshness_class",
    "revocation_status_url",
    "policy_version",
    "evidence_refs",
    "payload_hash",
    "signature_alg",
    "signature",
    "safety",
)

OACP_REQUIRED_SAFETY_FIELDS = (
    "public_safe",
    "contains_private_data",
    "allowed_agent_uses",
    "forbidden_agent_uses",
    "commitment_allowed",
    "offline_commitment_allowed",
    "requires_online_confirmation",
    "requires_provider_direct_verification",
    "requires_merchant_system_confirmation",
    "stale_behavior",
    "refusal_code_if_invalid",
)

OACP_ARTIFACT_SCHEMA_DESCRIPTORS: dict[ArtifactType, dict[str, Any]] = {
    "merchant_capability": {
        "required_payload_fields": (
            "merchant_display_name",
            "merchant_category",
            "supported_countries",
            "supported_currencies",
            "commerce_status",
            "public_discovery_state",
            "source_evidence_refs",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": False, "buyer_agent_id": False},
        "source_observed_at": True,
    },
    "seller_agent_capability": {
        "required_payload_fields": (
            "seller_agent_id",
            "merchant_id",
            "agent_runtime",
            "supported_channels",
            "supported_tasks",
            "forbidden_tasks",
            "public_claim_limits",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": False},
        "source_observed_at": True,
    },
    "catalog_snapshot": {
        "required_payload_fields": (
            "catalog_snapshot_id",
            "product_refs",
            "category_refs",
            "source_system_refs",
            "private_field_redaction_status",
            "sample_count",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": False},
        "source_observed_at": True,
    },
    "offer": {
        "required_payload_fields": (
            "offer_id",
            "product_id",
            "variant_id",
            "currency",
            "offer_valid_from",
            "offer_valid_until",
            "price_lock_allowed",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": True},
        "source_observed_at": True,
    },
    "price": {
        "required_payload_fields": (
            "product_id",
            "variant_id",
            "amount_minor_units",
            "currency",
            "price_source",
            "price_valid_until",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": True},
        "source_observed_at": True,
    },
    "inventory": {
        "required_payload_fields": (
            "product_id",
            "variant_id",
            "availability_state",
            "quantity_bucket",
            "hold_allowed",
            "stale_inventory_refusal",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": True},
        "source_observed_at": True,
    },
    "policy": {
        "required_payload_fields": (
            "return_policy_summary",
            "warranty_policy_summary",
            "fulfillment_policy_summary",
            "cancellation_policy_summary",
            "support_policy_summary",
            "jurisdiction_scope",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": False},
        "source_observed_at": True,
    },
    "public_discovery": {
        "required_payload_fields": (
            "discovery_state",
            "allowed_surfaces",
            "blocked_surfaces",
            "display_name",
            "public_description",
            "allowed_protocol_adapters",
            "publish_offline_allowed",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": False},
        "source_observed_at": True,
    },
    "mandate_capability": {
        "required_payload_fields": (
            "provider_key",
            "rail_type",
            "jurisdiction",
            "mandate_capability_ref",
            "buyer_agent_id",
            "merchant_scope",
            "max_amount",
            "currency",
            "verification_mode",
            "provider_direct_verification_required",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": True},
        "source_observed_at": True,
    },
    "commitment_evidence": {
        "required_payload_fields": (
            "commitment_id",
            "commitment_type",
            "buyer_agent_id",
            "seller_agent_id",
            "merchant_id",
            "artifact_refs_used",
            "policy_decision",
            "offline_commitment_mode",
            "forbidden_execution_claims",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": True},
        "source_observed_at": True,
    },
    "revocation": {
        "required_payload_fields": (
            "revocation_list_id",
            "revoked_artifact_ids",
            "revoked_subject_ids",
            "reason_codes",
            "effective_at",
            "emergency_disable",
        ),
        "scope": {"tenant_id": True, "merchant_id": False, "seller_agent_id": False, "buyer_agent_id": False},
        "source_observed_at": False,
    },
    "protocol_adapter": {
        "required_payload_fields": (
            "adapter_type",
            "adapter_version",
            "referenced_artifact_ids",
            "referenced_artifact_expires_at",
            "generated_from_artifact_hashes",
            "public_claim_limits",
        ),
        "scope": {"tenant_id": True, "merchant_id": True, "seller_agent_id": True, "buyer_agent_id": False},
        "source_observed_at": True,
    },
}

OACP_COMMITMENT_EVIDENCE_FORBIDDEN_TYPES = frozenset(
    {"payment_capture", "refund_execution", "settlement", "payout", "fulfillment_start", "merchant_approval"}
)

OACP_FINAL_COMMITMENT_REQUIRED_ARTIFACTS: dict[OfflineAction, frozenset[ArtifactType]] = {
    "price_lock": frozenset({"merchant_capability", "seller_agent_capability", "offer", "price", "policy"}),
    "inventory_hold": frozenset({"merchant_capability", "seller_agent_capability", "inventory", "policy"}),
    "reservation": frozenset({"merchant_capability", "seller_agent_capability", "offer", "inventory", "policy"}),
    "order_pending_reconciliation": frozenset(
        {"merchant_capability", "seller_agent_capability", "offer", "price", "inventory", "policy"}
    ),
    "payment_intent": frozenset(
        {"merchant_capability", "seller_agent_capability", "price", "policy", "mandate_capability"}
    ),
    "cancellation": frozenset({"merchant_capability", "seller_agent_capability", "policy", "commitment_evidence"}),
    "refund_request": frozenset({"merchant_capability", "seller_agent_capability", "policy", "commitment_evidence"}),
    "return_authorization": frozenset(
        {"merchant_capability", "seller_agent_capability", "policy", "commitment_evidence"}
    ),
    "support_escalation": frozenset({"merchant_capability", "seller_agent_capability", "policy"}),
}

ACTION_RISK_TIERS: dict[OfflineAction, RiskTier] = {
    "browse": "informational",
    "compare": "informational",
    "draft_cart": "low",
    "quote_preview": "low",
    "price_lock": "medium",
    "inventory_hold": "medium",
    "reservation": "medium",
    "order_pending_reconciliation": "high",
    "payment_intent": "high",
    "cancellation": "high",
    "refund_request": "high",
    "return_authorization": "high",
    "support_escalation": "medium",
    "public_discovery_publish": "critical",
    "merchant_approval": "critical",
    "policy_override": "critical",
    "emergency_disable": "critical",
}

MERCHANT_CONFIRMATION_ACTIONS: frozenset[OfflineAction] = frozenset(
    {
        "price_lock",
        "inventory_hold",
        "reservation",
        "order_pending_reconciliation",
        "cancellation",
        "refund_request",
        "return_authorization",
        "support_escalation",
    }
)
PROVIDER_VERIFICATION_ACTIONS: frozenset[OfflineAction] = frozenset({"payment_intent"})

FORBIDDEN_ARTIFACT_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "bank_account",
        "card_number",
        "checkout_payment_enabled",
        "credential",
        "credentials",
        "customer_identifier",
        "live_payment_enabled",
        "live_provider_enabled",
        "merchant_private_api_key",
        "merchant_private_api_url",
        "password",
        "private_key",
        "production_allowlist",
        "public_discovery_enabled",
        "raw_connector_payload",
        "raw_jwt",
        "raw_provider_payload",
        "secret",
        "token",
        "webhook_secret",
    }
)


@dataclass(frozen=True)
class OfflineCommitmentInput:
    action: OfflineAction
    artifacts_valid: bool
    artifacts_scoped_to_all_four: bool
    artifacts_allow_offline_commitment: bool
    effective_artifact_age_seconds: int
    effective_artifact_ttl_seconds: int
    revocation_snapshot_age_seconds: int
    merchant_confirmation: bool
    provider_verification: bool
    currency: str | None = None
    amount_minor_units: int | None = None
    total_quantity: int | None = None
    max_quantity_per_sku: int | None = None


@dataclass(frozen=True)
class OacpIssuerKeyMetadata:
    issuer: str
    issuer_key_id: str
    algorithm: str
    state: IssuerKeyState
    not_before: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class OacpArtifactScope:
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str


@dataclass(frozen=True)
class OacpRevocationSnapshot:
    observed_at: str
    age_seconds: int
    revoked_artifact_ids: frozenset[str] = field(default_factory=frozenset)
    revoked_subject_ids: frozenset[str] = field(default_factory=frozenset)


DetachedJwsVerifier = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class OacpArtifactVerificationInput:
    envelope: dict[str, Any]
    payload: Any
    issuer_keys: list[OacpIssuerKeyMetadata]
    now_iso: str
    revocation_snapshot: OacpRevocationSnapshot
    expected_scope: OacpArtifactScope
    risk_tier: RiskTier
    verify_detached_jws: DetachedJwsVerifier


@dataclass(frozen=True)
class OacpCachedArtifact:
    cache_key: str
    envelope: dict[str, Any]
    payload: Any
    verified_at: str


def canonicalize_oacp_payload(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_oacp_payload(payload: Any) -> str:
    return hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()


C6W3_BASE_ISSUED_AT = "2026-06-11T00:00:00.000Z"
C6W3_BASE_SCOPE = {
    "tenant_id": "cten_C6W3",
    "merchant_id": "mch_C6W3",
    "seller_agent_id": "seller_C6W3",
    "buyer_agent_id": "buyer_C6W3",
}


def _iso_after_base(seconds: int) -> str:
    base = datetime.fromisoformat(C6W3_BASE_ISSUED_AT.replace("Z", "+00:00")).astimezone(UTC)
    return datetime.fromtimestamp(base.timestamp() + seconds, tz=UTC).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _fixture_ttl_seconds(artifact_type: ArtifactType) -> int:
    if artifact_type == "revocation":
        return 0
    if artifact_type == "mandate_capability":
        return 120
    return min(OACP_ARTIFACT_TTLS_SECONDS[artifact_type], 300)


def _fixture_safety(artifact_type: ArtifactType) -> dict[str, Any]:
    safety: dict[str, Any] = {
        "public_safe": True,
        "contains_private_data": False,
        "allowed_agent_uses": ["browse", "compare"],
        "forbidden_agent_uses": ["payment_capture", "refund_execution", "settlement", "payout"],
        "commitment_allowed": False,
        "offline_commitment_allowed": False,
        "requires_online_confirmation": True,
        "requires_provider_direct_verification": False,
        "requires_merchant_system_confirmation": False,
        "stale_behavior": "non_binding_only",
        "refusal_code_if_invalid": "artifact_invalid",
    }
    if artifact_type in {"offer", "price", "inventory"}:
        safety.update(
            {
                "allowed_agent_uses": ["quote_preview", "price_lock", "inventory_hold"],
                "commitment_allowed": True,
                "requires_merchant_system_confirmation": True,
                "stale_behavior": "refuse_final_commitment",
            }
        )
    if artifact_type == "mandate_capability":
        safety.update(
            {
                "allowed_agent_uses": ["payment_intent"],
                "commitment_allowed": True,
                "requires_provider_direct_verification": True,
                "stale_behavior": "refuse_final_commitment",
            }
        )
    if artifact_type == "public_discovery":
        safety.update(
            {
                "allowed_agent_uses": ["discovery_display"],
                "forbidden_agent_uses": ["public_discovery_publish", "public_discovery_unpublish"],
            }
        )
    if artifact_type in {"seller_agent_capability", "protocol_adapter"}:
        safety.update({"forbidden_agent_uses": ["order_creation", "payment_intent", "merchant_approval"]})
    return safety


def _fixture_payload(artifact_type: ArtifactType) -> dict[str, Any]:
    if artifact_type == "merchant_capability":
        return {
            "merchant_display_name": "Synthetic Merchant C6W3",
            "merchant_category": "test_category",
            "supported_countries": ["IN"],
            "supported_currencies": ["INR"],
            "commerce_status": "internal_review_only",
            "public_discovery_state": "disabled",
            "source_evidence_refs": ["evidence_C6W3_merchant"],
        }
    if artifact_type == "seller_agent_capability":
        return {
            "seller_agent_id": C6W3_BASE_SCOPE["seller_agent_id"],
            "merchant_id": C6W3_BASE_SCOPE["merchant_id"],
            "agent_runtime": "agenticorg_internal",
            "supported_channels": ["internal_test"],
            "supported_tasks": ["answer_policy_question", "prepare_quote_preview"],
            "forbidden_tasks": ["payment_capture", "merchant_approval"],
            "public_claim_limits": ["no_payment_authority", "no_public_launch_claim"],
        }
    if artifact_type == "catalog_snapshot":
        return {
            "catalog_snapshot_id": "catalog_C6W3",
            "product_refs": ["prod_C6W3_1"],
            "category_refs": ["cat_C6W3_test"],
            "source_system_refs": ["source_ref_C6W3_redacted"],
            "private_field_redaction_status": "redacted",
            "sample_count": 1,
        }
    if artifact_type == "offer":
        return {
            "offer_id": "offer_C6W3",
            "product_id": "prod_C6W3_1",
            "variant_id": "variant_C6W3_1",
            "currency": "INR",
            "offer_valid_from": C6W3_BASE_ISSUED_AT,
            "offer_valid_until": _iso_after_base(300),
            "price_lock_allowed": True,
        }
    if artifact_type == "price":
        return {
            "product_id": "prod_C6W3_1",
            "variant_id": "variant_C6W3_1",
            "amount_minor_units": 99_900,
            "currency": "INR",
            "price_source": "redacted_source_snapshot",
            "price_valid_until": _iso_after_base(300),
        }
    if artifact_type == "inventory":
        return {
            "product_id": "prod_C6W3_1",
            "variant_id": "variant_C6W3_1",
            "availability_state": "available",
            "quantity_bucket": "low",
            "hold_allowed": True,
            "stale_inventory_refusal": "inventory_stale",
        }
    if artifact_type == "policy":
        return {
            "return_policy_summary": "Synthetic 7 day return window for internal tests.",
            "warranty_policy_summary": "Synthetic limited warranty summary.",
            "fulfillment_policy_summary": "Synthetic dispatch estimate.",
            "cancellation_policy_summary": "Synthetic cancellation before dispatch.",
            "support_policy_summary": "Synthetic support queue only.",
            "jurisdiction_scope": ["IN"],
        }
    if artifact_type == "public_discovery":
        return {
            "discovery_state": "disabled",
            "allowed_surfaces": [],
            "blocked_surfaces": ["public_search"],
            "display_name": "Synthetic Merchant C6W3",
            "public_description": "Synthetic internal-only merchant description.",
            "allowed_protocol_adapters": [],
            "publish_offline_allowed": False,
        }
    if artifact_type == "mandate_capability":
        return {
            "provider_key": "provider_stub_c6w3",
            "rail_type": "sandbox_reference_only",
            "jurisdiction": "IN",
            "mandate_capability_ref": "mandate_ref_C6W3_hash_only",
            "buyer_agent_id": C6W3_BASE_SCOPE["buyer_agent_id"],
            "merchant_scope": [C6W3_BASE_SCOPE["merchant_id"]],
            "max_amount": 500_000,
            "currency": "INR",
            "verification_mode": "provider_direct_verification_required",
            "provider_direct_verification_required": True,
        }
    if artifact_type == "commitment_evidence":
        return {
            "commitment_id": "commitment_C6W3",
            "commitment_type": "price_lock",
            "buyer_agent_id": C6W3_BASE_SCOPE["buyer_agent_id"],
            "seller_agent_id": C6W3_BASE_SCOPE["seller_agent_id"],
            "merchant_id": C6W3_BASE_SCOPE["merchant_id"],
            "artifact_refs_used": ["price_C6W3"],
            "policy_decision": "allowed_internal_only",
            "offline_commitment_mode": False,
            "forbidden_execution_claims": [],
        }
    if artifact_type == "revocation":
        return {
            "revocation_list_id": "revocation_C6W3",
            "revoked_artifact_ids": [],
            "revoked_subject_ids": [],
            "reason_codes": [],
            "effective_at": C6W3_BASE_ISSUED_AT,
            "emergency_disable": False,
        }
    return {
        "adapter_type": "schema_org_preview",
        "adapter_version": "adapter_C6W3",
        "referenced_artifact_ids": ["merchant_capability_C6W3", "price_C6W3"],
        "referenced_artifact_expires_at": [_iso_after_base(300)],
        "generated_from_artifact_hashes": ["hash_C6W3_redacted"],
        "public_claim_limits": ["internal_preview_only", "not_public_ready"],
    }


def make_c6w3_oacp_fixture(artifact_type: ArtifactType) -> dict[str, Any]:
    descriptor = OACP_ARTIFACT_SCHEMA_DESCRIPTORS[artifact_type]
    payload = _fixture_payload(artifact_type)
    envelope: dict[str, Any] = {
        "artifact_id": f"{artifact_type}_C6W3",
        "artifact_type": artifact_type,
        "schema_version": "oacp.internal.v1",
        "issuer": "grantex",
        "issuer_key_id": "kid_C6W3_stub",
        "subject_type": artifact_type,
        "subject_id": f"subject_{artifact_type}_C6W3",
        "tenant_id": C6W3_BASE_SCOPE["tenant_id"],
        "issued_at": C6W3_BASE_ISSUED_AT,
        "expires_at": _iso_after_base(_fixture_ttl_seconds(artifact_type)),
        "freshness_class": "fresh",
        "revocation_status_url": f"https://grantex.example.invalid/oacp/revocations/{artifact_type}_C6W3",
        "policy_version": "policy_C6W3",
        "evidence_refs": [f"evidence_{artifact_type}_C6W3"],
        "payload_hash": hash_oacp_payload(payload),
        "signature_alg": "ES256",
        "signature": f"eyJhbGciOiJFUzI1NiJ9..sig_{artifact_type}_C6W3",
        "safety": _fixture_safety(artifact_type),
    }
    scope = cast(dict[str, bool], descriptor["scope"])
    for field_name, required in scope.items():
        if required:
            envelope[field_name] = C6W3_BASE_SCOPE[field_name]
    if descriptor["source_observed_at"]:
        envelope["source_observed_at"] = C6W3_BASE_ISSUED_AT
    return {"envelope": envelope, "payload": payload}


OACP_C6W3_VALID_ARTIFACT_FIXTURES: dict[ArtifactType, dict[str, Any]] = {
    artifact_type: make_c6w3_oacp_fixture(artifact_type)
    for artifact_type in OACP_ARTIFACT_SCHEMA_DESCRIPTORS
}


def build_oacp_artifact_cache_key(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str,
    buyer_agent_id: str,
    artifact_type: ArtifactType,
    artifact_id: str,
    schema_version: str,
    policy_version: str,
) -> str:
    return ":".join(
        [
            tenant_id,
            merchant_id,
            seller_agent_id,
            buyer_agent_id,
            artifact_type,
            artifact_id,
            schema_version,
            policy_version,
        ]
    )


def _normalize_key(key: str) -> str:
    output: list[str] = []
    for index, char in enumerate(key):
        if char.isupper() and index > 0:
            output.append("_")
        output.append(char.lower() if char not in "- " else "_")
    return "".join(output)


def assert_no_forbidden_oacp_artifact_fields(value: Any) -> None:
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                if _normalize_key(str(key)) in FORBIDDEN_ARTIFACT_KEYS:
                    raise ValueError(f"OACP artifact cannot contain private or enabling field: {key}")
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _artifact_refusal(artifact_id: str | None, code: str, message: str) -> dict[str, Any]:
    return {
        "valid": False,
        "status": "refused",
        "artifact_id": artifact_id,
        "refusal_code": code,
        "message": message,
    }


def _string_field(envelope: dict[str, Any], key: str) -> str | None:
    value = envelope.get(key)
    return value if isinstance(value, str) else None


def _is_detached_jws(signature: str | None) -> bool:
    if signature is None or signature == "detached_jws_required_before_publication":
        return False
    parts = signature.split(".")
    return len(parts) == 3 and bool(parts[0]) and parts[1] == "" and bool(parts[2])


def _find_issuer_key(
    envelope: dict[str, Any],
    issuer_keys: list[OacpIssuerKeyMetadata],
) -> OacpIssuerKeyMetadata | None:
    issuer = _string_field(envelope, "issuer")
    issuer_key_id = _string_field(envelope, "issuer_key_id")
    signature_alg = _string_field(envelope, "signature_alg")
    for key in issuer_keys:
        if key.issuer == issuer and key.issuer_key_id == issuer_key_id and key.algorithm == signature_alg:
            return key
    return None


def _scope_matches(envelope: dict[str, Any], expected_scope: OacpArtifactScope) -> bool:
    return (
        envelope.get("tenant_id") == expected_scope.tenant_id
        and envelope.get("merchant_id") == expected_scope.merchant_id
        and envelope.get("seller_agent_id") == expected_scope.seller_agent_id
        and envelope.get("buyer_agent_id") == expected_scope.buyer_agent_id
    )


def _missing_fields(record: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field_name for field_name in fields if record.get(field_name) is None]


def validate_oacp_artifact_family(
    *,
    envelope: dict[str, Any],
    payload: Any,
    now_iso: str | None = None,
) -> dict[str, Any]:
    artifact_id = _string_field(envelope, "artifact_id")
    artifact_type_raw = _string_field(envelope, "artifact_type")
    if artifact_type_raw not in OACP_ARTIFACT_SCHEMA_DESCRIPTORS:
        return _artifact_refusal(artifact_id, "unknown_artifact_type", "Unknown OACP artifact type.")

    artifact_type = cast(ArtifactType, artifact_type_raw)
    descriptor = OACP_ARTIFACT_SCHEMA_DESCRIPTORS[artifact_type]
    missing_envelope = _missing_fields(envelope, OACP_REQUIRED_ENVELOPE_FIELDS)
    if missing_envelope:
        return _artifact_refusal(artifact_id, "envelope_field_missing", f"Missing envelope fields: {missing_envelope}")

    scope = cast(dict[str, bool], descriptor["scope"])
    for field_name, required in scope.items():
        if required and envelope.get(field_name) is None:
            return _artifact_refusal(artifact_id, "scope_field_missing", f"{field_name} is required.")
    if descriptor["source_observed_at"] and envelope.get("source_observed_at") is None:
        return _artifact_refusal(artifact_id, "envelope_field_missing", "source_observed_at is required.")

    safety = envelope.get("safety")
    if not isinstance(safety, dict):
        return _artifact_refusal(artifact_id, "safety_field_missing", "Artifact safety metadata is missing.")
    missing_safety = _missing_fields(safety, OACP_REQUIRED_SAFETY_FIELDS)
    if missing_safety:
        return _artifact_refusal(artifact_id, "safety_field_missing", f"Missing safety fields: {missing_safety}")
    if safety.get("public_safe") is not True or safety.get("contains_private_data") is not False:
        return _artifact_refusal(artifact_id, "safety_policy_violation", "Artifact safety metadata is not public-safe.")

    if not isinstance(payload, dict):
        return _artifact_refusal(artifact_id, "payload_must_be_object", "Artifact payload must be an object.")
    try:
        assert_no_forbidden_oacp_artifact_fields(payload)
    except ValueError:
        return _artifact_refusal(
            artifact_id,
            "private_or_forbidden_payload_field",
            "Payload contains private, raw, credential, or enabling fields.",
        )

    required_payload_fields = cast(tuple[str, ...], descriptor["required_payload_fields"])
    missing_payload = _missing_fields(payload, required_payload_fields)
    if missing_payload:
        return _artifact_refusal(artifact_id, "payload_field_missing", f"Missing payload fields: {missing_payload}")

    if _string_field(envelope, "signature_alg") != OACP_ARTIFACT_SIGNATURE_PROFILE["first_algorithm"]:
        return _artifact_refusal(artifact_id, "signature_algorithm_unsupported", "Signature algorithm is unsupported.")
    if not _is_detached_jws(_string_field(envelope, "signature")):
        return _artifact_refusal(artifact_id, "signature_missing_or_placeholder", "Detached JWS signature is missing.")
    if hash_oacp_payload(payload) != _string_field(envelope, "payload_hash"):
        return _artifact_refusal(artifact_id, "payload_hash_mismatch", "Payload hash does not match artifact.")

    issued_at = _parse_iso(_string_field(envelope, "issued_at"))
    expires_at = _parse_iso(_string_field(envelope, "expires_at"))
    if issued_at is None or expires_at is None:
        return _artifact_refusal(artifact_id, "envelope_field_missing", "Artifact timestamps are invalid.")
    ttl_seconds = int((expires_at - issued_at).total_seconds())
    if ttl_seconds < 0 or ttl_seconds > OACP_ARTIFACT_TTLS_SECONDS[artifact_type]:
        return _artifact_refusal(artifact_id, "artifact_ttl_exceeds_default", "Artifact TTL exceeds pinned default.")

    now = _parse_iso(now_iso)
    not_before = _parse_iso(_string_field(envelope, "not_before"))
    if now is not None:
        if not_before is not None and now < not_before:
            return _artifact_refusal(artifact_id, "artifact_not_yet_valid", "Artifact is not yet valid.")
        if now > expires_at:
            return _artifact_refusal(artifact_id, "artifact_expired_or_stale", "Artifact has expired.")

    if artifact_type == "protocol_adapter":
        refs = payload.get("referenced_artifact_expires_at")
        ref_dates = [_parse_iso(value) for value in refs if isinstance(value, str)] if isinstance(refs, list) else []
        valid_ref_dates = [value for value in ref_dates if value is not None]
        if not valid_ref_dates or expires_at > min(valid_ref_dates):
            return _artifact_refusal(
                artifact_id,
                "protocol_adapter_outlives_references",
                "Protocol adapter outlives referenced artifacts.",
            )

    if (
        artifact_type == "mandate_capability"
        and (
            payload.get("provider_direct_verification_required") is not True
            or safety.get("requires_provider_direct_verification") is not True
        )
    ):
        return _artifact_refusal(
            artifact_id,
            "mandate_provider_verification_required",
            "Mandate capability requires direct provider verification.",
        )

    if (
        artifact_type == "public_discovery"
        and (
            payload.get("publish_offline_allowed") is not False
            or safety.get("offline_commitment_allowed") is not False
        )
    ):
        return _artifact_refusal(
            artifact_id,
            "public_discovery_offline_change_forbidden",
            "Public discovery publish or unpublish is not allowed offline.",
        )

    if artifact_type == "commitment_evidence":
        forbidden_claims = payload.get("forbidden_execution_claims")
        if (
            payload.get("commitment_type") in OACP_COMMITMENT_EVIDENCE_FORBIDDEN_TYPES
            or (isinstance(forbidden_claims, list) and len(forbidden_claims) > 0)
        ):
            return _artifact_refusal(
                artifact_id,
                "commitment_evidence_forbidden_implication",
                "Commitment evidence cannot imply execution authority.",
            )

    return {
        "valid": True,
        "status": "valid",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "required_payload_fields": list(required_payload_fields),
    }


def evaluate_required_artifacts_for_final_commitment(
    action: OfflineAction,
    available_artifact_types: set[ArtifactType],
) -> dict[str, Any]:
    required = OACP_FINAL_COMMITMENT_REQUIRED_ARTIFACTS.get(action)
    if required is None:
        return {"allowed": True, "status": "allowed", "missing_artifact_types": []}
    missing = sorted(required - available_artifact_types)
    if missing:
        return {
            "allowed": False,
            "status": "refused",
            "refusal_code": "required_artifact_missing",
            "missing_artifact_types": missing,
        }
    return {"allowed": True, "status": "allowed", "missing_artifact_types": []}


def evaluate_agenticorg_artifact_runtime_use(
    *,
    artifact_type: ArtifactType,
    action: OfflineAction,
    provider_verification: bool = False,
) -> dict[str, Any]:
    if artifact_type == "public_discovery" and action == "public_discovery_publish":
        return {
            "allowed": False,
            "status": "refused",
            "refusal_code": "public_discovery_offline_change_forbidden",
        }
    if artifact_type == "mandate_capability" and action == "payment_intent" and not provider_verification:
        return {
            "allowed": False,
            "status": "refused",
            "refusal_code": "provider_verification_required",
        }
    if artifact_type in {"seller_agent_capability", "protocol_adapter"} and action not in {
        "browse",
        "compare",
        "draft_cart",
        "quote_preview",
    }:
        return {
            "allowed": False,
            "status": "refused",
            "refusal_code": "artifact_not_commerce_authority",
        }
    return {"allowed": True, "status": "allowed"}


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _adapter_preview_refusal(code: str, message: str) -> dict[str, Any]:
    return {
        "allowed": False,
        "status": "refused",
        "refusal_code": code,
        "message": message,
    }


def evaluate_agenticorg_oacp_adapter_preview_use(
    *,
    preview: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    """Evaluate local use of a Grantex adapter preview without granting authority."""

    if preview.get("generated") is not True or preview.get("status") != "preview_only":
        return _adapter_preview_refusal("adapter_preview_invalid", "Adapter preview is not valid.")
    if preview.get("surface") not in OACP_C6W4_PROTOCOL_ADAPTER_SURFACES:
        return _adapter_preview_refusal("adapter_surface_unknown", "Adapter preview surface is unsupported.")

    safety_flags = (
        preview.get("non_authoritative_for_transaction") is True
        and preview.get("no_checkout_payment_enablement") is True
        and preview.get("no_live_provider_enablement") is True
        and preview.get("no_public_discovery_enablement") is True
    )
    if not safety_flags:
        return _adapter_preview_refusal(
            "adapter_preview_not_buyer_safe",
            "Adapter preview is missing required safety flags.",
        )

    source_ids = _string_list(preview.get("source_artifact_ids"))
    source_families = _string_list(preview.get("source_artifact_families"))
    if (
        not source_ids
        or not source_families
        or preview.get("source_authority") != "grantex_canonical_oacp_artifact_authority"
    ):
        return _adapter_preview_refusal("adapter_source_missing", "Adapter preview must cite Grantex source artifacts.")

    unsupported = set(_string_list(preview.get("unsupported_capabilities")))
    blocked = OACP_C6W4_BLOCKED_ADAPTER_ACTIONS | unsupported
    if action in blocked or action not in OACP_C6W4_NON_BINDING_ADAPTER_ACTIONS:
        return _adapter_preview_refusal(
            "adapter_not_transaction_authority",
            "Adapter previews can route or display sourced facts, not approve commerce actions.",
        )

    return {
        "allowed": True,
        "status": "allowed",
        "action": action,
        "source_artifact_ids": source_ids,
        "source_artifact_families": source_families,
        "freshness_tier": preview.get("freshness_tier"),
        "unsupported_capabilities": sorted(blocked),
        "commerce_facts_invented": False,
    }


def summarize_oacp_adapter_preview_for_buyer(preview: dict[str, Any]) -> dict[str, Any]:
    """Return channel-safe wording for buyer/seller surfaces."""

    evaluation = evaluate_agenticorg_oacp_adapter_preview_use(preview=preview, action="browse")
    if not evaluation["allowed"]:
        return evaluation

    return {
        "allowed": True,
        "status": "allowed",
        "wording": "Preview facts are sourced from Grantex OACP artifacts and are not purchase approval.",
        "source_artifact_ids": evaluation["source_artifact_ids"],
        "source_artifact_families": evaluation["source_artifact_families"],
        "freshness_tier": evaluation["freshness_tier"],
        "unsupported_capabilities": evaluation["unsupported_capabilities"],
        "non_authoritative_for_transaction": True,
        "commerce_facts_invented": False,
    }


def _c6w5_action_class(action: str) -> ActionClass:
    if action in OACP_C6W5_NON_BINDING_PREVIEW_ACTIONS:
        return "non_binding_preview"
    if action in OACP_C6W5_COMMITMENT_ADJACENT_ACTIONS:
        return "commitment_adjacent"
    if action in OACP_C6W5_COMMITMENT_BOUND_ACTIONS:
        return "commitment_bound"
    return "always_blocked"


def _c6w5_blocked_capabilities(preview: dict[str, Any]) -> list[str]:
    blocked = set(OACP_C6W4_BLOCKED_ADAPTER_ACTIONS) | set(OACP_C6W5_ALWAYS_BLOCKED_ACTIONS)
    blocked.update(_string_list(preview.get("blocked_capabilities")))
    blocked.update(_string_list(preview.get("unsupported_capabilities")))
    return sorted(blocked)


def _artifact_type_from_cached(artifact: dict[str, Any]) -> ArtifactType | None:
    envelope = artifact.get("envelope")
    if not isinstance(envelope, dict):
        return None
    artifact_type = envelope.get("artifact_type")
    return cast(ArtifactType, artifact_type) if artifact_type in OACP_ARTIFACT_TTLS_SECONDS else None


def _c6w5_freshness_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_freshness: dict[str, str] = {}
    expires: list[datetime] = []
    freshness_values: set[str] = set()
    for artifact in artifacts:
        envelope = artifact.get("envelope")
        if not isinstance(envelope, dict):
            continue
        artifact_id = _string_field(envelope, "artifact_id")
        freshness = _string_field(envelope, "freshness_class")
        expires_at = _parse_iso(_string_field(envelope, "expires_at"))
        if artifact_id is not None and freshness is not None:
            artifact_freshness[artifact_id] = freshness
            freshness_values.add(freshness)
        if expires_at is not None:
            expires.append(expires_at)
    tier = next(iter(freshness_values)) if len(freshness_values) == 1 else "mixed"
    earliest = min(expires).isoformat(timespec="milliseconds").replace("+00:00", "Z") if expires else None
    return {
        "freshness_tier": tier,
        "artifact_freshness": artifact_freshness,
        "earliest_expires_at": earliest,
    }


def _c6w5_decision(
    *,
    action: str,
    action_class: ActionClass,
    allowed_to_preview: bool,
    allowed_to_prepare: bool,
    reason: str | None,
    required: frozenset[ArtifactType],
    source_artifacts: list[dict[str, Any]],
    risk_tier: RiskTier,
    offline_mode_status: str,
    buyer_safe_message: str,
    blocked_capabilities: list[str],
) -> dict[str, Any]:
    source_ids: list[str] = []
    source_families: list[str] = []
    for artifact in source_artifacts:
        envelope = artifact.get("envelope")
        if not isinstance(envelope, dict):
            continue
        artifact_id = _string_field(envelope, "artifact_id")
        artifact_type = _string_field(envelope, "artifact_type")
        if artifact_id is not None:
            source_ids.append(artifact_id)
        if artifact_type is not None:
            source_families.append(artifact_type)
    return {
        "action": action,
        "action_class": action_class,
        "allowed_to_preview": allowed_to_preview,
        "allowed_to_prepare": allowed_to_prepare,
        "allowed_to_execute": False,
        "refusal_or_escalation_reason": reason,
        "required_fresh_artifact_families": sorted(required),
        "source_artifact_ids": source_ids,
        "source_artifact_families": source_families,
        "source_authority": "grantex_canonical_oacp_artifact_authority",
        "freshness_summary": _c6w5_freshness_summary(source_artifacts),
        "risk_tier": risk_tier,
        "offline_mode_status": offline_mode_status,
        "buyer_safe_message": buyer_safe_message,
        "blocked_capabilities": blocked_capabilities,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
    }


def _c6w5_requires_risk_context(action: str, action_class: ActionClass) -> bool:
    return action_class not in {"non_binding_preview", "always_blocked"} and action not in {
        "ask_refresh_source_facts",
        "prepare_human_confirmation_prompt",
        "prepare_mandate_capability_check_request",
    }


def _c6w5_risk_context_reason(
    *,
    risk_tier: RiskTier,
    currency: str | None,
    amount_minor_units: int | None,
    total_quantity: int | None,
    max_quantity_per_sku: int | None,
) -> str | None:
    if currency not in {"INR", "USD"} or amount_minor_units is None or amount_minor_units < 0:
        return "risk_context_missing_or_ambiguous"
    if total_quantity is None or total_quantity <= 0:
        return "risk_context_missing_or_ambiguous"
    cap = OACP_FIRST_RELEASE_RISK_CAPS[risk_tier]
    amount_cap = cap["currency_caps"].get(currency)
    if amount_cap is None or amount_minor_units > amount_cap:
        return "risk_cap_exceeded"
    total_cap = cap["total_quantity_cap"]
    per_sku_cap = cap["per_sku_quantity_cap"]
    if total_cap is not None and total_quantity > total_cap:
        return "quantity_cap_exceeded"
    if max_quantity_per_sku is not None and per_sku_cap is not None and max_quantity_per_sku > per_sku_cap:
        return "quantity_cap_exceeded"
    return None


def evaluate_agenticorg_c6w5_commitment_boundary(
    *,
    action: str,
    cached_artifacts: list[dict[str, Any]],
    adapter_preview: dict[str, Any],
    now_iso: str,
    grantex_available: bool,
    revocation_snapshot_age_seconds: int | None = None,
    currency: str | None = None,
    amount_minor_units: int | None = None,
    total_quantity: int | None = None,
    max_quantity_per_sku: int | None = None,
) -> dict[str, Any]:
    """Resolve C6W5 preview/prepare boundaries from local artifacts only."""

    action_class = _c6w5_action_class(action)
    risk_tier = OACP_C6W5_ACTION_RISK_TIERS.get(action, "critical")
    required = OACP_C6W5_REQUIRED_FRESH_ARTIFACT_FAMILIES.get(action, frozenset())
    blocked_capabilities = _c6w5_blocked_capabilities(adapter_preview)
    source_artifacts = [artifact for artifact in cached_artifacts if _artifact_type_from_cached(artifact) is not None]
    by_type = {_artifact_type_from_cached(artifact): artifact for artifact in source_artifacts}
    offline_mode_status = (
        "online_policy_available"
        if grantex_available
        else "offline_prepared_not_executed"
        if action_class == "commitment_bound"
        else "offline_cache_valid"
    )

    if action_class == "always_blocked":
        return _c6w5_decision(
            action=action,
            action_class=action_class,
            allowed_to_preview=False,
            allowed_to_prepare=False,
            reason="blocked_in_c6w5",
            required=required,
            source_artifacts=source_artifacts,
            risk_tier=risk_tier,
            offline_mode_status="offline_blocked",
            buyer_safe_message="This C6W5 action is blocked and cannot be prepared or executed from adapter previews.",
            blocked_capabilities=blocked_capabilities,
        )

    preview_check = evaluate_agenticorg_oacp_adapter_preview_use(preview=adapter_preview, action="browse")
    preview_expires_at = _parse_iso(_string_field(adapter_preview, "expires_at"))
    now = _parse_iso(now_iso)
    if (
        not preview_check["allowed"]
        or preview_expires_at is None
        or now is None
        or now > preview_expires_at
    ):
        return _c6w5_decision(
            action=action,
            action_class=action_class,
            allowed_to_preview=False,
            allowed_to_prepare=False,
            reason="adapter_preview_missing_safety_or_freshness",
            required=required,
            source_artifacts=source_artifacts,
            risk_tier=risk_tier,
            offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
            buyer_safe_message="Adapter previews cannot override missing safety flags or expired metadata.",
            blocked_capabilities=blocked_capabilities,
        )

    missing = sorted(required - {artifact_type for artifact_type in by_type if artifact_type is not None})
    if missing:
        return _c6w5_decision(
            action=action,
            action_class=action_class,
            allowed_to_preview=action_class == "non_binding_preview",
            allowed_to_prepare=False,
            reason=f"required_artifact_missing:{','.join(missing)}",
            required=required,
            source_artifacts=source_artifacts,
            risk_tier=risk_tier,
            offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
            buyer_safe_message="Required source artifacts are missing, so the action can only remain non-executing.",
            blocked_capabilities=blocked_capabilities,
        )

    required_artifacts = [by_type[artifact_type] for artifact_type in required if artifact_type in by_type]
    for artifact in required_artifacts:
        envelope = artifact["envelope"]
        payload = artifact["payload"]
        validation = validate_oacp_artifact_family(envelope=envelope, payload=payload, now_iso=now_iso)
        if not validation["valid"]:
            return _c6w5_decision(
                action=action,
                action_class=action_class,
                allowed_to_preview=False,
                allowed_to_prepare=False,
                reason=str(validation["refusal_code"]),
                required=required,
                source_artifacts=required_artifacts,
                risk_tier=risk_tier,
                offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
                buyer_safe_message="A required OACP artifact is invalid, stale, revoked, or ambiguous.",
                blocked_capabilities=blocked_capabilities,
            )
        freshness = _string_field(envelope, "freshness_class")
        if freshness in {"stale", "unknown"} or (action_class != "non_binding_preview" and freshness != "fresh"):
            return _c6w5_decision(
                action=action,
                action_class=action_class,
                allowed_to_preview=action_class == "non_binding_preview",
                allowed_to_prepare=False,
                reason="artifact_freshness_missing_stale_or_ambiguous",
                required=required,
                source_artifacts=required_artifacts,
                risk_tier=risk_tier,
                offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
                buyer_safe_message="Source facts must be fresh enough before a commitment-bound request is prepared.",
                blocked_capabilities=blocked_capabilities,
            )

    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[risk_tier]
    if action_class != "non_binding_preview" and (
        revocation_snapshot_age_seconds is None
        or max_revocation_age is None
        or revocation_snapshot_age_seconds > max_revocation_age
    ):
        return _c6w5_decision(
            action=action,
            action_class=action_class,
            allowed_to_preview=True,
            allowed_to_prepare=False,
            reason="revocation_snapshot_missing_or_stale",
            required=required,
            source_artifacts=required_artifacts,
            risk_tier=risk_tier,
            offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
            buyer_safe_message="The local revocation posture is too stale for a commitment-bound preparation.",
            blocked_capabilities=blocked_capabilities,
        )

    if _c6w5_requires_risk_context(action, action_class):
        risk_reason = _c6w5_risk_context_reason(
            risk_tier=risk_tier,
            currency=currency,
            amount_minor_units=amount_minor_units,
            total_quantity=total_quantity,
            max_quantity_per_sku=max_quantity_per_sku,
        )
        if risk_reason is not None:
            return _c6w5_decision(
                action=action,
                action_class=action_class,
                allowed_to_preview=True,
                allowed_to_prepare=False,
                reason=risk_reason,
                required=required,
                source_artifacts=required_artifacts,
                risk_tier=risk_tier,
                offline_mode_status="online_policy_available" if grantex_available else "offline_blocked",
                buyer_safe_message=(
                    "Amount, currency, or quantity is missing or outside the conservative C6W5 risk caps."
                ),
                blocked_capabilities=blocked_capabilities,
            )

    allowed_to_prepare = action_class != "non_binding_preview"
    if action_class == "non_binding_preview":
        message = "Preview can continue from sourced OACP artifacts; this is not purchase approval."
        reason = None
    elif action_class == "commitment_adjacent":
        message = (
            "Prepared request only; no checkout, payment, provider call, or merchant private API call is executed."
        )
        reason = None
    else:
        message = "Prepared, not executed. C6W5 does not grant transaction authority from adapter previews."
        reason = "prepared_not_executed_c6w5"
    return _c6w5_decision(
        action=action,
        action_class=action_class,
        allowed_to_preview=True,
        allowed_to_prepare=allowed_to_prepare,
        reason=reason,
        required=required,
        source_artifacts=required_artifacts,
        risk_tier=risk_tier,
        offline_mode_status=offline_mode_status,
        buyer_safe_message=message,
        blocked_capabilities=blocked_capabilities,
    )


_C6W6_PRIVATE_VALUE_MARKERS = (
    "http://",
    "https://",
    "postgres://",
    "redis://",
    "mongodb://",
    "private_key",
    "raw_jwt",
    "access_token",
    "api_key",
    "password",
    "secret",
    "credential",
    "allowlist",
)


def _c6w6_decision_id(decision: dict[str, Any]) -> str:
    payload = {
        "action": decision.get("action"),
        "action_class": decision.get("action_class"),
        "source_artifact_ids": decision.get("source_artifact_ids"),
        "required_fresh_artifact_families": decision.get("required_fresh_artifact_families"),
        "freshness_summary": decision.get("freshness_summary"),
        "risk_tier": decision.get("risk_tier"),
        "offline_mode_status": decision.get("offline_mode_status"),
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()
    return f"oacp_c6w5_decision_{digest[:20]}"


def _c6w6_redact_evidence_refs(evidence_refs: list[str] | None) -> list[str]:
    redacted: list[str] = []
    for value in evidence_refs or []:
        if not isinstance(value, str) or not value:
            continue
        normalized = value.lower()
        safe_value = (
            "redacted_private_evidence_ref"
            if any(marker in normalized for marker in _C6W6_PRIVATE_VALUE_MARKERS)
            else value
        )
        if safe_value not in redacted:
            redacted.append(safe_value)
    return redacted[:20]


def _c6w6_ttl(kind: str, created_at: str, decision: dict[str, Any]) -> dict[str, Any] | None:
    created = _parse_iso(created_at)
    if created is None:
        return None
    default_ttl = OACP_C6W6_ENVELOPE_TTL_SECONDS[kind]
    freshness = decision.get("freshness_summary")
    earliest_source_expiry = None
    if isinstance(freshness, dict):
        earliest_source_expiry = _parse_iso(_string_field(freshness, "earliest_expires_at"))
    default_expiry = created.timestamp() + default_ttl
    expires_timestamp = (
        default_expiry
        if earliest_source_expiry is None
        else min(default_expiry, earliest_source_expiry.timestamp())
    )
    if expires_timestamp <= created.timestamp():
        return None
    expires_at = datetime.fromtimestamp(expires_timestamp, tz=UTC).isoformat(timespec="milliseconds")
    return {
        "expires_at": expires_at.replace("+00:00", "Z"),
        "max_ttl_seconds": int(expires_timestamp - created.timestamp()),
    }


def _c6w6_kind_matches_decision(kind: str, decision: dict[str, Any]) -> bool:
    action = str(decision.get("action"))
    action_class = str(decision.get("action_class"))
    allowed_to_prepare = decision.get("allowed_to_prepare") is True
    if kind == "seller_source_refresh_request":
        return action_class != "always_blocked"
    if kind == "buyer_confirmation_request":
        return allowed_to_prepare
    if kind == "merchant_confirmation_request":
        return allowed_to_prepare and action in OACP_C6W6_MERCHANT_CONFIRMATION_ACTIONS
    if kind == "mandate_capability_evidence_request":
        return allowed_to_prepare and action in OACP_C6W6_MANDATE_EVIDENCE_ACTIONS
    return allowed_to_prepare and action in OACP_C6W6_SUPPORT_PREPARATION_ACTIONS


def _c6w6_risk_context_missing(
    *,
    kind: str,
    decision: dict[str, Any],
    currency: str | None,
    amount_minor_units: int | None,
    total_quantity: int | None,
) -> bool:
    if kind == "seller_source_refresh_request" or decision.get("action_class") != "commitment_bound":
        return False
    return (
        currency not in {"INR", "USD"}
        or amount_minor_units is None
        or amount_minor_units < 0
        or total_quantity is None
        or total_quantity <= 0
    )


def _c6w6_next_human_step(kind: str, decision: dict[str, Any]) -> str:
    action = str(decision.get("action"))
    if kind == "buyer_confirmation_request":
        return f"Review sourced {action} preparation and confirm whether a non-executing request should be sent."
    if kind == "seller_source_refresh_request":
        return (
            "Ask the seller agent or source owner to refresh stale or missing source facts "
            "before preparation continues."
        )
    if kind == "merchant_confirmation_request":
        return f"Ask the merchant source owner to confirm {action} facts before any execution path exists."
    if kind == "mandate_capability_evidence_request":
        return "Ask for provider-owned mandate capability evidence to be supplied as cached evidence only."
    return (
        "Prepare a support escalation note without promising SLA, refund, return, replacement, "
        "or settlement outcome."
    )


def _c6w6_next_system_step_label(kind: str) -> str:
    return {
        "buyer_confirmation_request": "local_human_confirmation_handoff",
        "seller_source_refresh_request": "seller_source_refresh_handoff_label",
        "merchant_confirmation_request": "merchant_source_confirmation_handoff_label",
        "mandate_capability_evidence_request": "mandate_evidence_preparation_handoff_label",
        "support_escalation_preparation": "support_escalation_preparation_handoff_label",
    }[kind]


def _c6w6_seller_safe_message(kind: str, decision: dict[str, Any]) -> str:
    if kind == "seller_source_refresh_request":
        families = ", ".join(_string_list(decision.get("required_fresh_artifact_families"))) or "source"
        return f"Refresh requested for {families} facts; do not include private credentials or raw payloads."
    if kind == "merchant_confirmation_request":
        return (
            "Merchant confirmation is prepared as evidence-only text; no order, hold, checkout, "
            "or payment is created."
        )
    if kind == "mandate_capability_evidence_request":
        return "Mandate capability evidence is requested as cached evidence only; no provider rail is called."
    if kind == "support_escalation_preparation":
        return (
            "Support escalation is non-binding and must not promise SLA, refund, return, replacement, "
            "settlement, or payout."
        )
    return (
        "Buyer confirmation is local and non-executing; seller cards and adapter previews "
        "are not transaction authority."
    )


def _c6w6_envelope_id(kind: str, created_at: str, decision_id: str, requested_action: str) -> str:
    payload = {
        "kind": kind,
        "created_at": created_at,
        "decision_id": decision_id,
        "requested_action": requested_action,
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()
    return f"oacp_c6w6_envelope_{digest[:20]}"


def _c6w6_envelope(
    *,
    kind: str,
    status: str,
    created_at: str,
    decision_id: str,
    decision: dict[str, Any],
    expires_at: str,
    max_ttl_seconds: int,
    redacted_evidence_refs: list[str],
    unsupported_capabilities: list[str],
) -> dict[str, Any]:
    action = str(decision.get("action"))
    return {
        "envelope_id": _c6w6_envelope_id(kind, created_at, decision_id, action),
        "envelope_kind": kind,
        "envelope_status": status,
        "created_at": created_at,
        "expires_at": expires_at,
        "max_ttl_seconds": max_ttl_seconds,
        "source_resolver_decision_id": decision_id,
        "action_class": decision.get("action_class"),
        "requested_action": action,
        "risk_tier": decision.get("risk_tier"),
        "offline_mode_status": decision.get("offline_mode_status"),
        "allowed_to_preview": status == "prepared_only" and decision.get("allowed_to_preview") is True,
        "allowed_to_prepare": status == "prepared_only" and decision.get("allowed_to_prepare") is True,
        "allowed_to_execute": False,
        "prepared_only": True,
        "source_artifact_ids": _string_list(decision.get("source_artifact_ids")),
        "source_artifact_families": _string_list(decision.get("source_artifact_families")),
        "source_authority": decision.get("source_authority"),
        "required_fresh_artifact_families": _string_list(decision.get("required_fresh_artifact_families")),
        "freshness_summary": decision.get("freshness_summary"),
        "blocked_capabilities": _string_list(decision.get("blocked_capabilities")),
        "unsupported_capabilities": unsupported_capabilities,
        "buyer_safe_message": str(decision.get("buyer_safe_message")),
        "seller_safe_message": _c6w6_seller_safe_message(kind, decision),
        "next_human_step": _c6w6_next_human_step(kind, decision),
        "next_system_step_label": _c6w6_next_system_step_label(kind),
        "redacted_evidence_refs": redacted_evidence_refs,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
    }


def _c6w6_refusal(refusal_code: str, message: str, blocked_envelope: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "generated": False,
        "status": "blocked",
        "refusal_code": refusal_code,
        "message": message,
    }
    if blocked_envelope is not None:
        result["blocked_envelope"] = blocked_envelope
    return result


def prepare_agenticorg_c6w6_commitment_envelope(
    *,
    envelope_kind: PreparedEnvelopeKind,
    resolver_decision: dict[str, Any] | None,
    created_at: str,
    source_resolver_decision_id: str | None = None,
    evidence_refs: list[str] | None = None,
    unsupported_capabilities: list[str] | None = None,
    currency: str | None = None,
    amount_minor_units: int | None = None,
    total_quantity: int | None = None,
    max_quantity_per_sku: int | None = None,
) -> dict[str, Any]:
    """Prepare a C6W6 local handoff envelope from a C6W5 decision."""

    del max_quantity_per_sku
    if resolver_decision is None:
        return _c6w6_refusal(
            "resolver_decision_missing",
            "C6W6 requires a C6W5 resolver decision before preparing an envelope.",
        )

    decision_id = source_resolver_decision_id or _c6w6_decision_id(resolver_decision)
    ttl = _c6w6_ttl(envelope_kind, created_at, resolver_decision)
    redacted_refs = _c6w6_redact_evidence_refs(evidence_refs)
    unsupported = sorted(set(unsupported_capabilities or []))
    blocked_envelope = None
    if ttl is not None:
        blocked_envelope = _c6w6_envelope(
            kind=envelope_kind,
            status="blocked",
            created_at=created_at,
            decision_id=decision_id,
            decision=resolver_decision,
            expires_at=str(ttl["expires_at"]),
            max_ttl_seconds=int(ttl["max_ttl_seconds"]),
            redacted_evidence_refs=redacted_refs,
            unsupported_capabilities=unsupported,
        )

    if resolver_decision.get("allowed_to_execute") is not False:
        return _c6w6_refusal(
            "resolver_decision_allows_execution",
            "C6W6 cannot prepare envelopes from executable decisions.",
            blocked_envelope,
        )
    if not _string_list(resolver_decision.get("source_artifact_ids")) or not _string_list(
        resolver_decision.get("source_artifact_families")
    ):
        return _c6w6_refusal(
            "source_artifacts_missing",
            "C6W6 requires source artifact references for prepared handoff envelopes.",
            blocked_envelope,
        )
    if resolver_decision.get("action_class") == "always_blocked":
        return _c6w6_refusal(
            "action_blocked_in_c6w6",
            "Always-blocked actions cannot produce prepared C6W6 envelopes.",
            blocked_envelope,
        )
    freshness = resolver_decision.get("freshness_summary")
    if (
        not isinstance(freshness, dict)
        or freshness.get("earliest_expires_at") is None
        or freshness.get("freshness_tier") in {"stale", "unknown"}
        or ttl is None
    ):
        return _c6w6_refusal(
            "source_freshness_missing_or_stale",
            "Prepared envelopes require source freshness and TTL metadata.",
            blocked_envelope,
        )
    if not _c6w6_kind_matches_decision(envelope_kind, resolver_decision):
        return _c6w6_refusal(
            "envelope_kind_action_mismatch",
            "Envelope kind does not match the C6W5 action class or requested action.",
            blocked_envelope,
        )
    if _c6w6_risk_context_missing(
        kind=envelope_kind,
        decision=resolver_decision,
        currency=currency,
        amount_minor_units=amount_minor_units,
        total_quantity=total_quantity,
    ):
        return _c6w6_refusal(
            "risk_context_missing_or_ambiguous",
            "Commitment-bound envelopes require amount, currency, and quantity context.",
            blocked_envelope,
        )

    envelope = _c6w6_envelope(
        kind=envelope_kind,
        status="prepared_only",
        created_at=created_at,
        decision_id=decision_id,
        decision=resolver_decision,
        expires_at=str(ttl["expires_at"]),
        max_ttl_seconds=int(ttl["max_ttl_seconds"]),
        redacted_evidence_refs=redacted_refs,
        unsupported_capabilities=unsupported,
    )
    try:
        assert_no_forbidden_oacp_artifact_fields(envelope)
    except ValueError:
        return _c6w6_refusal(
            "private_or_forbidden_envelope_field",
            "Prepared envelope contains forbidden private or enabling fields.",
            blocked_envelope,
        )
    return {"generated": True, "status": "prepared_only", "envelope": envelope}


def verify_oacp_artifact(input_data: OacpArtifactVerificationInput) -> dict[str, Any]:
    envelope = input_data.envelope
    artifact_id = _string_field(envelope, "artifact_id")
    artifact_type = _string_field(envelope, "artifact_type")
    subject_id = _string_field(envelope, "subject_id")

    if artifact_id is None or artifact_type is None or subject_id is None:
        return _artifact_refusal(artifact_id, "artifact_missing_or_invalid", "Artifact envelope is incomplete.")
    if artifact_type not in OACP_ARTIFACT_TTLS_SECONDS:
        return _artifact_refusal(artifact_id, "artifact_missing_or_invalid", "Artifact type is unsupported.")

    safety = envelope.get("safety")
    if (
        not isinstance(safety, dict)
        or safety.get("public_safe") is not True
        or safety.get("contains_private_data") is True
    ):
        return _artifact_refusal(
            artifact_id,
            "artifact_missing_or_invalid",
            "Artifact safety metadata is not public-safe.",
        )

    try:
        assert_no_forbidden_oacp_artifact_fields(input_data.payload)
    except ValueError:
        return _artifact_refusal(
            artifact_id,
            "artifact_missing_or_invalid",
            "Payload contains private or enabling fields.",
        )

    now = _parse_iso(input_data.now_iso)
    issued_at = _parse_iso(_string_field(envelope, "issued_at"))
    expires_at = _parse_iso(_string_field(envelope, "expires_at"))
    not_before = _parse_iso(_string_field(envelope, "not_before"))
    if now is None or issued_at is None or expires_at is None:
        return _artifact_refusal(artifact_id, "artifact_missing_or_invalid", "Artifact contains invalid timestamps.")
    if not_before is not None and now < not_before:
        return _artifact_refusal(artifact_id, "artifact_not_yet_valid", "Artifact is not yet valid.")
    if now > expires_at:
        return _artifact_refusal(artifact_id, "artifact_expired_or_stale", "Artifact has expired.")

    max_ttl_seconds = OACP_ARTIFACT_TTLS_SECONDS[cast(ArtifactType, artifact_type)]
    actual_ttl_seconds = int((expires_at - issued_at).total_seconds())
    if actual_ttl_seconds < 0 or actual_ttl_seconds > max_ttl_seconds:
        return _artifact_refusal(
            artifact_id,
            "artifact_ttl_exceeds_default",
            "Artifact TTL exceeds the pinned default.",
        )

    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[input_data.risk_tier]
    if max_revocation_age is None or input_data.revocation_snapshot.age_seconds > max_revocation_age:
        return _artifact_refusal(artifact_id, "revocation_snapshot_stale", "Revocation snapshot is too stale.")
    if (
        artifact_id in input_data.revocation_snapshot.revoked_artifact_ids
        or subject_id in input_data.revocation_snapshot.revoked_subject_ids
    ):
        return _artifact_refusal(artifact_id, "artifact_revoked", "Artifact or subject is revoked.")

    if _string_field(envelope, "signature_alg") != OACP_ARTIFACT_SIGNATURE_PROFILE["first_algorithm"]:
        return _artifact_refusal(artifact_id, "signature_algorithm_unsupported", "Signature algorithm is unsupported.")
    signature = _string_field(envelope, "signature")
    if not _is_detached_jws(signature):
        return _artifact_refusal(artifact_id, "signature_missing_or_placeholder", "Detached JWS signature is missing.")

    payload_hash = hash_oacp_payload(input_data.payload)
    if payload_hash != _string_field(envelope, "payload_hash"):
        return _artifact_refusal(artifact_id, "payload_hash_mismatch", "Payload hash does not match artifact.")

    issuer_key = _find_issuer_key(envelope, input_data.issuer_keys)
    if issuer_key is None:
        return _artifact_refusal(artifact_id, "issuer_key_untrusted", "Issuer key is not trusted.")
    key_not_before = _parse_iso(issuer_key.not_before)
    key_expires_at = _parse_iso(issuer_key.expires_at)
    if (
        issuer_key.state != "active"
        or (key_not_before is not None and now < key_not_before)
        or (key_expires_at is not None and now > key_expires_at)
    ):
        return _artifact_refusal(artifact_id, "issuer_key_inactive", "Issuer key is not active.")

    if not _scope_matches(envelope, input_data.expected_scope):
        return _artifact_refusal(artifact_id, "artifact_scope_mismatch", "Artifact is outside expected scope.")

    schema_version = _string_field(envelope, "schema_version")
    policy_version = _string_field(envelope, "policy_version")
    if schema_version is None or policy_version is None:
        return _artifact_refusal(
            artifact_id,
            "artifact_missing_or_invalid",
            "Artifact schema or policy version is missing.",
        )

    signature_ok = input_data.verify_detached_jws(
        {
            "canonical_payload": canonicalize_oacp_payload(input_data.payload),
            "payload_hash": payload_hash,
            "artifact_id": artifact_id,
            "issuer": _string_field(envelope, "issuer"),
            "issuer_key_id": _string_field(envelope, "issuer_key_id"),
            "signature_alg": _string_field(envelope, "signature_alg"),
            "signature": signature,
        }
    )
    if not signature_ok:
        return _artifact_refusal(artifact_id, "detached_jws_verification_failed", "Detached JWS verification failed.")

    cache_key = build_oacp_artifact_cache_key(
        tenant_id=input_data.expected_scope.tenant_id,
        merchant_id=input_data.expected_scope.merchant_id,
        seller_agent_id=input_data.expected_scope.seller_agent_id,
        buyer_agent_id=input_data.expected_scope.buyer_agent_id,
        artifact_type=cast(ArtifactType, artifact_type),
        artifact_id=artifact_id,
        schema_version=schema_version,
        policy_version=policy_version,
    )
    return {
        "valid": True,
        "status": "valid",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "cache_key": cache_key,
        "payload_hash": payload_hash,
        "expires_at": _string_field(envelope, "expires_at"),
    }


class OacpArtifactCache:
    def __init__(self) -> None:
        self._items: dict[str, OacpCachedArtifact] = {}

    def put_verified(self, input_data: OacpArtifactVerificationInput) -> dict[str, Any]:
        result = verify_oacp_artifact(input_data)
        if not result["valid"]:
            return result

        cache_key = str(result["cache_key"])
        self._items[cache_key] = OacpCachedArtifact(
            cache_key=cache_key,
            envelope=input_data.envelope,
            payload=input_data.payload,
            verified_at=input_data.now_iso,
        )
        return result

    def get_verified(
        self,
        *,
        scope: OacpArtifactScope,
        artifact_type: ArtifactType,
        artifact_id: str,
        schema_version: str,
        policy_version: str,
        issuer_keys: list[OacpIssuerKeyMetadata],
        now_iso: str,
        revocation_snapshot: OacpRevocationSnapshot,
        risk_tier: RiskTier,
        verify_detached_jws: DetachedJwsVerifier,
    ) -> dict[str, Any]:
        cache_key = build_oacp_artifact_cache_key(
            tenant_id=scope.tenant_id,
            merchant_id=scope.merchant_id,
            seller_agent_id=scope.seller_agent_id,
            buyer_agent_id=scope.buyer_agent_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            schema_version=schema_version,
            policy_version=policy_version,
        )
        cached = self._items.get(cache_key)
        if cached is None:
            return _artifact_refusal(artifact_id, "artifact_missing_or_invalid", "Artifact is not present in cache.")

        return verify_oacp_artifact(
            OacpArtifactVerificationInput(
                envelope=cached.envelope,
                payload=cached.payload,
                issuer_keys=issuer_keys,
                now_iso=now_iso,
                revocation_snapshot=revocation_snapshot,
                expected_scope=scope,
                risk_tier=risk_tier,
                verify_detached_jws=verify_detached_jws,
            )
        )


def risk_tier_for_oacp_offline_action(action: OfflineAction) -> RiskTier:
    return ACTION_RISK_TIERS.get(action, "critical")


def _refusal(action: OfflineAction, tier: RiskTier, code: str, message: str) -> dict[str, Any]:
    return {
        "allowed": False,
        "status": "refused",
        "action": action,
        "tier": tier,
        "refusal_code": code,
        "message": message,
    }


def evaluate_oacp_offline_commitment(input_data: OfflineCommitmentInput) -> dict[str, Any]:
    tier = risk_tier_for_oacp_offline_action(input_data.action)
    cap = OACP_FIRST_RELEASE_RISK_CAPS[tier]

    if not cap["offline_allowed"]:
        return _refusal(
            input_data.action,
            tier,
            "critical_action_offline_refused",
            "Critical actions are not allowed offline.",
        )
    if not input_data.artifacts_valid:
        return _refusal(input_data.action, tier, "artifact_missing_or_invalid", "Required artifacts are invalid.")
    if not input_data.artifacts_scoped_to_all_four:
        return _refusal(
            input_data.action,
            tier,
            "artifact_scope_mismatch",
            "Artifacts must be scoped to all four cache dimensions.",
        )
    if not input_data.artifacts_allow_offline_commitment:
        return _refusal(
            input_data.action,
            tier,
            "offline_commitment_not_permitted",
            "Artifacts do not permit offline commitment.",
        )
    if input_data.effective_artifact_age_seconds > input_data.effective_artifact_ttl_seconds:
        return _refusal(input_data.action, tier, "artifact_expired_or_stale", "The effective artifact TTL has expired.")

    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[tier]
    if max_revocation_age is None or input_data.revocation_snapshot_age_seconds > max_revocation_age:
        return _refusal(input_data.action, tier, "revocation_snapshot_stale", "The revocation snapshot is stale.")

    if input_data.amount_minor_units is not None:
        currency_caps = cap["currency_caps"]
        amount_cap = currency_caps.get(input_data.currency or "")
        if amount_cap is None:
            return _refusal(
                input_data.action,
                tier,
                "currency_cap_unavailable",
                "No approved cap exists for this currency.",
            )
        if input_data.amount_minor_units > amount_cap:
            return _refusal(
                input_data.action,
                tier,
                "risk_cap_exceeded",
                "The requested amount exceeds the offline cap.",
            )

    total_cap = cap["total_quantity_cap"]
    per_sku_cap = cap["per_sku_quantity_cap"]
    if total_cap is not None and input_data.total_quantity is not None and input_data.total_quantity > total_cap:
        return _refusal(
            input_data.action,
            tier,
            "quantity_cap_exceeded",
            "The requested quantity exceeds the offline cap.",
        )
    if (
        per_sku_cap is not None
        and input_data.max_quantity_per_sku is not None
        and input_data.max_quantity_per_sku > per_sku_cap
    ):
        return _refusal(
            input_data.action,
            tier,
            "quantity_cap_exceeded",
            "The requested per-SKU quantity exceeds the offline cap.",
        )

    if input_data.action in MERCHANT_CONFIRMATION_ACTIONS and not input_data.merchant_confirmation:
        return _refusal(
            input_data.action,
            tier,
            "merchant_confirmation_required",
            "Merchant/source confirmation is required.",
        )
    if input_data.action in PROVIDER_VERIFICATION_ACTIONS and not input_data.provider_verification:
        return _refusal(input_data.action, tier, "provider_verification_required", "Provider verification is required.")

    evidence_required = ["grantex_reconciliation"]
    if input_data.action in MERCHANT_CONFIRMATION_ACTIONS:
        evidence_required.insert(0, "merchant_confirmation")
    if input_data.action in PROVIDER_VERIFICATION_ACTIONS:
        evidence_required.insert(0, "provider_verification")

    return {
        "allowed": True,
        "status": "allowed",
        "action": input_data.action,
        "tier": tier,
        "evidence_required": evidence_required,
    }
