"""OACP trust artifact defaults for AgenticOrg commerce agents.

This module is deliberately local and non-enabling. It lets buyer and seller
agent flows evaluate signed-artifact freshness, offline commitment caps, cache
keys, and bridge defaults without calling providers, merchant private APIs, or
Grantex runtime endpoints.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
ResponseEvidenceKind = Literal[
    "buyer_confirmation_response",
    "seller_source_refresh_response",
    "merchant_confirmation_response",
    "mandate_capability_evidence_response",
    "support_escalation_response",
]
ReconciliationStatus = Literal[
    "accepted_for_preparation",
    "rejected",
    "needs_source_refresh",
    "needs_human_review",
    "expired",
    "stale",
    "mismatched",
    "blocked",
]
EligibilityPacketKind = Literal[
    "execution_handoff_eligibility_packet",
    "audit_trail_preparation_packet",
    "missing_evidence_packet",
    "blocked_execution_packet",
    "manual_review_packet",
]
EligibilityStatus = Literal[
    "eligible_for_future_handoff",
    "missing_evidence",
    "needs_human_review",
    "blocked",
    "stale",
    "expired",
    "mismatched",
    "unsupported",
]
DryRunVerificationKind = Literal[
    "execution_controller_handoff_dry_run",
    "audit_readiness_verification",
    "missing_contract_requirement",
    "blocked_handoff_verification",
    "manual_review_required_verification",
]
DryRunVerificationStatus = Literal[
    "dry_run_accepted_for_future_controller",
    "missing_contract_requirement",
    "needs_human_review",
    "blocked",
    "stale",
    "expired",
    "mismatched",
    "unsupported",
    "unsafe",
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
PersistentCacheScopeKind = Literal["buyer_agent", "seller_agent", "tenant", "merchant"]
PersistentCacheActionIntent = Literal["non_binding_preview", "prepare_only", "final_commitment"]
PersistentCacheEvaluationStatus = Literal[
    "usable_for_non_binding_cache",
    "prepared_only_for_commitment_boundary",
    "blocked",
    "stale",
    "expired",
    "revoked",
    "mismatched",
    "unsafe",
    "unsupported",
]
OacpCacheMaintenanceOutcome = Literal[
    "keep_usable",
    "refresh_recommended",
    "refresh_required_before_commitment",
    "evict_expired",
    "purge_revoked",
    "quarantine_ambiguous_revocation",
    "quarantine_scope_mismatch",
    "quarantine_private_or_raw_ref",
    "source_refresh_needed",
    "human_review_required",
    "blocked_unsafe",
]
OacpCacheMaintenanceReportKind = Literal[
    "cache_maintenance_dry_run_report",
    "operator_review_packet",
    "blocked_cache_action_report",
    "stale_or_revoked_artifact_summary",
    "source_refresh_request_preview",
]
OacpCacheOperatorDecisionKind = Literal[
    "approve_future_refresh_request",
    "approve_future_eviction_request",
    "approve_future_quarantine_request",
    "request_more_evidence",
    "reject_maintenance_action",
    "defer_until_freshness_update",
    "escalate_to_human_support",
    "block_unsafe_action",
]
OacpAuditExportReviewRetentionClass = Literal[
    "short_lived_internal_review",
    "standard_internal_review",
    "legal_hold_candidate",
]

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
OACP_C6W7_RESPONSE_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "buyer_confirmation_response",
        "seller_source_refresh_response",
        "merchant_confirmation_response",
        "mandate_capability_evidence_response",
        "support_escalation_response",
    }
)
OACP_C6W7_RECONCILIATION_STATUSES: frozenset[str] = frozenset(
    {
        "accepted_for_preparation",
        "rejected",
        "needs_source_refresh",
        "needs_human_review",
        "expired",
        "stale",
        "mismatched",
        "blocked",
    }
)
OACP_C6W7_RESPONSE_TTL_SECONDS: dict[str, int] = {
    "buyer_confirmation_response": 15 * 60,
    "seller_source_refresh_response": 30 * 60,
    "merchant_confirmation_response": 10 * 60,
    "mandate_capability_evidence_response": 2 * 60,
    "support_escalation_response": 10 * 60,
}
OACP_C6W7_RESPONSE_KIND_BY_ENVELOPE_KIND: dict[str, str] = {
    "buyer_confirmation_request": "buyer_confirmation_response",
    "seller_source_refresh_request": "seller_source_refresh_response",
    "merchant_confirmation_request": "merchant_confirmation_response",
    "mandate_capability_evidence_request": "mandate_capability_evidence_response",
    "support_escalation_preparation": "support_escalation_response",
}
OACP_C6W8_ELIGIBILITY_PACKET_KINDS: frozenset[str] = frozenset(
    {
        "execution_handoff_eligibility_packet",
        "audit_trail_preparation_packet",
        "missing_evidence_packet",
        "blocked_execution_packet",
        "manual_review_packet",
    }
)
OACP_C6W8_ELIGIBILITY_STATUSES: frozenset[str] = frozenset(
    {
        "eligible_for_future_handoff",
        "missing_evidence",
        "needs_human_review",
        "blocked",
        "stale",
        "expired",
        "mismatched",
        "unsupported",
    }
)
OACP_C6W8_PACKET_TTL_SECONDS: dict[str, int] = {
    "execution_handoff_eligibility_packet": 10 * 60,
    "audit_trail_preparation_packet": 30 * 60,
    "missing_evidence_packet": 10 * 60,
    "blocked_execution_packet": 10 * 60,
    "manual_review_packet": 15 * 60,
}
OACP_C6W9_DRY_RUN_VERIFICATION_KINDS: frozenset[str] = frozenset(
    {
        "execution_controller_handoff_dry_run",
        "audit_readiness_verification",
        "missing_contract_requirement",
        "blocked_handoff_verification",
        "manual_review_required_verification",
    }
)
OACP_C6W9_VERIFIER_STATUSES: frozenset[str] = frozenset(
    {
        "dry_run_accepted_for_future_controller",
        "missing_contract_requirement",
        "needs_human_review",
        "blocked",
        "stale",
        "expired",
        "mismatched",
        "unsupported",
        "unsafe",
    }
)
OACP_C6W9_CONTRACT_CHECKS: tuple[str, ...] = (
    "packet_kind_recognized",
    "eligibility_status_acceptable",
    "reconciliation_lineage_present",
    "envelope_lineage_present",
    "source_artifact_refs_present",
    "evidence_refs_redacted_and_non_private",
    "required_confirmations_present",
    "freshness_ttl_valid",
    "mandate_evidence_valid",
    "action_class_risk_tier_consistent",
    "commitment_risk_context_present",
    "non_enablement_flags_intact",
    "no_executable_url_or_target",
    "no_raw_private_labels_or_payloads",
    "no_publication_certification_readiness_claims",
)
OACP_C6W9_AUDIT_READINESS_CHECKS: tuple[str, ...] = (
    "audit_lineage_refs_present",
    "audit_refs_redacted",
    "decision_lineage_complete",
    "source_refs_carried_forward",
    "evidence_refs_carried_forward",
    "messages_safe_and_non_executing",
)
OACP_C6W9_VERIFICATION_TTL_SECONDS: dict[str, int] = {
    "execution_controller_handoff_dry_run": 5 * 60,
    "audit_readiness_verification": 10 * 60,
    "missing_contract_requirement": 5 * 60,
    "blocked_handoff_verification": 5 * 60,
    "manual_review_required_verification": 10 * 60,
}

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


@dataclass(frozen=True)
class OacpPersistentArtifactCacheRecord:
    cache_record_id: str
    artifact_id: str
    artifact_type: ArtifactType
    authority: str
    issuer: str
    scope_kind: PersistentCacheScopeKind
    tenant_id: str | None
    merchant_id: str | None
    seller_agent_id: str | None
    buyer_agent_id: str | None
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    generated_at: str
    cached_at: str
    expires_at: str
    freshness_status: str
    revocation_snapshot_status: str
    revocation_snapshot_observed_at: str | None
    ttl_policy_seconds: int
    risk_tier: RiskTier
    blocked_capabilities: tuple[str, ...] = field(default_factory=tuple)
    unsupported_capabilities: tuple[str, ...] = field(default_factory=tuple)
    verifier_result_ref: str | None = None
    revocation_snapshot_age_seconds: int | None = None
    allowed_to_execute: bool = False
    non_authoritative_for_transaction: bool = True
    no_checkout_payment_enablement: bool = True
    no_live_provider_enablement: bool = True
    no_public_discovery_enablement: bool = True


@dataclass(frozen=True)
class OacpArtifactCacheRepositoryQuery:
    scope_kind: PersistentCacheScopeKind | None = None
    tenant_id: str | None = None
    merchant_id: str | None = None
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    artifact_type: ArtifactType | None = None
    authority: str | None = None


@dataclass(frozen=True)
class OacpOperatorDecisionRepositoryQuery:
    tenant_id: str | None = None
    merchant_id: str | None = None
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    decision_kind: OacpCacheOperatorDecisionKind | None = None
    review_packet_id: str | None = None
    maintenance_plan_id: str | None = None


@dataclass(frozen=True)
class OacpAuditReviewManifestRepositoryQuery:
    tenant_id: str | None = None
    merchant_id: str | None = None
    seller_agent_id: str | None = None
    buyer_agent_id: str | None = None
    bundle_id: str | None = None
    retention_class: OacpAuditExportReviewRetentionClass | None = None


class OacpArtifactCacheRepositoryPort(Protocol):
    def upsert(self, record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
        """Store or replace a local cache record without external calls."""
        ...

    def get(self, cache_record_id: str) -> OacpPersistentArtifactCacheRecord | None:
        """Read one local cache record by deterministic local id."""
        ...

    def list_for_scope(self, query: OacpArtifactCacheRepositoryQuery) -> tuple[OacpPersistentArtifactCacheRecord, ...]:
        """List local cache records that match a tenant, merchant, seller, or buyer scope."""
        ...

    def evaluate(
        self,
        *,
        cache_record_id: str,
        action_intent: PersistentCacheActionIntent,
        now_iso: str,
        grantex_available: bool,
        expected_scope: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one stored record using the C6X2 fail-closed cache policy."""
        ...


class OacpOperatorDecisionRepositoryPort(Protocol):
    def upsert_decision(self, decision_record: Mapping[str, Any]) -> dict[str, Any]:
        """Store or replace one audit-safe operator decision record without external calls."""
        ...

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        """Read one operator decision record by deterministic decision id."""
        ...

    def list_decisions_for_scope(self, query: OacpOperatorDecisionRepositoryQuery) -> tuple[dict[str, Any], ...]:
        """List local operator decision records for a tenant, merchant, seller, or buyer scope."""
        ...

    def evaluate_decision_for_future_action(self, decision_id: str) -> dict[str, Any]:
        """Evaluate a decision record for a future action without approving execution."""
        ...


class OacpAuditReviewManifestRepositoryPort(Protocol):
    def upsert_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Store or replace one audit-safe review manifest without external calls."""
        ...

    def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        """Read one review manifest by deterministic manifest id."""
        ...

    def list_manifests_for_scope(self, query: OacpAuditReviewManifestRepositoryQuery) -> tuple[dict[str, Any], ...]:
        """List local review manifests for a tenant, merchant, seller, buyer, bundle, or retention scope."""
        ...

    def evaluate_manifest_for_internal_review(self, manifest_id: str) -> dict[str, Any]:
        """Evaluate a review manifest without writing export files or approving execution."""
        ...


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


_C6W7_PRIVATE_VALUE_MARKERS = (
    "http://",
    "https://",
    "postgres://",
    "redis://",
    "mongodb://",
    "private_key",
    "raw_jwt",
    "passport",
    "access_token",
    "api_key",
    "password",
    "secret",
    "credential",
    "allowlist",
    "customer_data",
    "customer_identifier",
    "customer_email",
    "customer_phone",
    "customer_address",
    "raw_provider_payload",
    "raw_connector_payload",
)
_C6W7_FORBIDDEN_EXECUTION_MARKERS = (
    "execute",
    "executed",
    "execution",
    "checkout",
    "payment",
    "order_create",
    "order_created",
    "order_placed",
    "hold_created",
    "refund_created",
    "return_created",
    "shipment",
    "shipping",
    "carrier",
    "provider_call",
    "merchant_private_api",
    "public_discovery_enable",
    "public_discovery_publish",
    "protocol_publication",
    "protocol_submission",
    "certification",
    "conformance",
    "standardization",
    "production_ready",
    "live_provider",
    "live_rail",
    "live_payment",
    "mandate_created",
)


def _c6w7_safe_evidence_refs(evidence_refs: list[str] | None) -> list[str] | None:
    refs: list[str] = []
    for value in evidence_refs or []:
        if not isinstance(value, str) or not value:
            continue
        normalized = value.lower()
        if any(marker in normalized for marker in _C6W7_PRIVATE_VALUE_MARKERS):
            return None
        if value not in refs:
            refs.append(value)
    return refs[:20]


def _c6w7_ttl(response_kind: str, created_at: str, envelope: dict[str, Any]) -> dict[str, Any] | None:
    created = _parse_iso(created_at)
    envelope_expiry = _parse_iso(_string_field(envelope, "expires_at"))
    if created is None or envelope_expiry is None:
        return None
    default_expiry = created.timestamp() + OACP_C6W7_RESPONSE_TTL_SECONDS[response_kind]
    expires_timestamp = min(default_expiry, envelope_expiry.timestamp())
    if expires_timestamp <= created.timestamp():
        return None
    expires_at = datetime.fromtimestamp(expires_timestamp, tz=UTC).isoformat(timespec="milliseconds")
    return {
        "expires_at": expires_at.replace("+00:00", "Z"),
        "max_ttl_seconds": int(expires_timestamp - created.timestamp()),
    }


def _c6w7_risk_context_missing(
    *,
    envelope: dict[str, Any],
    currency: str | None,
    amount_minor_units: int | None,
    total_quantity: int | None,
) -> bool:
    if (
        envelope.get("envelope_kind") == "seller_source_refresh_request"
        or envelope.get("action_class") != "commitment_bound"
    ):
        return False
    return (
        currency not in {"INR", "USD"}
        or amount_minor_units is None
        or amount_minor_units < 0
        or total_quantity is None
        or total_quantity <= 0
    )


def _c6w7_mandate_evidence_stale(
    *,
    response_kind: str,
    response_status: str,
    created_at: str,
    response_evidence_issued_at: str | None,
) -> bool:
    if response_kind != "mandate_capability_evidence_response" or response_status != "accepted_for_preparation":
        return False
    created = _parse_iso(created_at)
    issued = _parse_iso(response_evidence_issued_at)
    if created is None or issued is None or issued > created:
        return True
    return created.timestamp() - issued.timestamp() > OACP_ARTIFACT_TTLS_SECONDS["mandate_capability"]


def _c6w7_response_conflicts_with_envelope(
    *,
    envelope: dict[str, Any],
    response_claimed_envelope_id: str | None,
    response_claimed_action: str | None,
) -> bool:
    if response_claimed_envelope_id is not None and response_claimed_envelope_id != envelope.get("envelope_id"):
        return True
    if response_claimed_action is not None and response_claimed_action != envelope.get("requested_action"):
        return True
    return False


def _c6w7_response_indicates_forbidden_execution(response_flags: list[str] | None) -> bool:
    for value in response_flags or []:
        if not isinstance(value, str):
            continue
        normalized = value.lower()
        if any(marker in normalized for marker in _C6W7_FORBIDDEN_EXECUTION_MARKERS):
            return True
    return False


def _c6w7_next_human_step(status: str, envelope: dict[str, Any]) -> str:
    action = str(envelope.get("requested_action"))
    if status == "accepted_for_preparation":
        return f"Review reconciled {action} evidence before any separate execution handoff exists."
    if status == "rejected":
        return f"Stop {action} preparation and keep source evidence attached for audit."
    if status in {"needs_source_refresh", "stale", "expired"}:
        return "Refresh source artifacts before preparing another envelope."
    if status in {"needs_human_review", "mismatched"}:
        return "Route the response to a human reviewer with source and freshness labels."
    return "Do not proceed; the response is blocked by C6W7 fail-closed policy."


def _c6w7_next_system_step_label(status: str) -> str:
    if status == "accepted_for_preparation":
        return "local_reconciled_preparation_handoff_label"
    if status in {"needs_source_refresh", "stale", "expired"}:
        return "source_refresh_reconciliation_label"
    if status in {"needs_human_review", "mismatched"}:
        return "human_review_reconciliation_label"
    if status == "rejected":
        return "local_rejection_record_label"
    return "blocked_reconciliation_label"


def _c6w7_buyer_safe_message(status: str, envelope: dict[str, Any]) -> str:
    action = str(envelope.get("requested_action"))
    if status == "accepted_for_preparation":
        return (
            f"Response accepted for preparation only for {action}; no order, hold, checkout, "
            "payment, mandate, refund, return, shipment, or provider action occurred."
        )
    if status == "rejected":
        return f"Response rejected {action} preparation; no execution occurred."
    if status in {"needs_source_refresh", "stale", "expired"}:
        return (
            "Source evidence is missing, stale, or expired. A refreshed source artifact is required "
            "before preparation continues."
        )
    if status in {"needs_human_review", "mismatched"}:
        return "The response needs human review because source or envelope evidence is ambiguous."
    return "The response is blocked. C6W7 does not execute or approve live transaction actions."


def _c6w7_seller_safe_message(status: str, envelope: dict[str, Any]) -> str:
    if status == "accepted_for_preparation":
        return (
            f"Evidence for {envelope.get('envelope_kind')} was reconciled locally as prepared-only; "
            "keep merchant systems and provider rails as operational authorities."
        )
    if status == "rejected":
        return "Seller response is recorded as rejected and does not create operational obligations."
    if status in {"needs_source_refresh", "stale", "expired"}:
        return "Seller or source owner must provide refreshed cached evidence without private payloads or credentials."
    if status in {"needs_human_review", "mismatched"}:
        return (
            "Seller response conflicts with local envelope metadata and must be reviewed before another "
            "prepared handoff."
        )
    return "Seller response is blocked and must not be treated as transaction authority."


def _c6w7_reconciliation_id(
    *,
    envelope_id: str,
    response_kind: str,
    response_status: str,
    created_at: str,
    response_evidence_refs: list[str],
) -> str:
    payload = {
        "envelope_id": envelope_id,
        "response_kind": response_kind,
        "response_status": response_status,
        "created_at": created_at,
        "response_evidence_refs": response_evidence_refs,
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()
    return f"oacp_c6w7_reconciliation_{digest[:20]}"


def _c6w7_reconciliation(
    *,
    envelope: dict[str, Any],
    response_kind: str,
    response_status: str,
    created_at: str,
    expires_at: str,
    max_ttl_seconds: int,
    response_evidence_refs: list[str],
) -> dict[str, Any]:
    allowed = response_status == "accepted_for_preparation"
    envelope_id = str(envelope.get("envelope_id"))
    return {
        "reconciliation_id": _c6w7_reconciliation_id(
            envelope_id=envelope_id,
            response_kind=response_kind,
            response_status=response_status,
            created_at=created_at,
            response_evidence_refs=response_evidence_refs,
        ),
        "envelope_id": envelope_id,
        "envelope_kind": envelope.get("envelope_kind"),
        "response_kind": response_kind,
        "response_status": response_status,
        "created_at": created_at,
        "expires_at": expires_at,
        "max_ttl_seconds": max_ttl_seconds,
        "action_class": envelope.get("action_class"),
        "requested_action": envelope.get("requested_action"),
        "risk_tier": envelope.get("risk_tier"),
        "source_artifact_ids": _string_list(envelope.get("source_artifact_ids")),
        "source_artifact_families": _string_list(envelope.get("source_artifact_families")),
        "source_authority": envelope.get("source_authority"),
        "response_evidence_refs": response_evidence_refs,
        "freshness_summary": envelope.get("freshness_summary"),
        "decision_summary": {
            "source_resolver_decision_id": envelope.get("source_resolver_decision_id"),
            "action_class": envelope.get("action_class"),
            "offline_mode_status": envelope.get("offline_mode_status"),
            "allowed_to_prepare_from_envelope": envelope.get("allowed_to_prepare") is True,
            "non_authoritative_for_transaction": True,
        },
        "unsupported_capabilities": _string_list(envelope.get("unsupported_capabilities")),
        "blocked_capabilities": _string_list(envelope.get("blocked_capabilities")),
        "required_next_artifact_families": _string_list(envelope.get("required_fresh_artifact_families")),
        "buyer_safe_message": _c6w7_buyer_safe_message(response_status, envelope),
        "seller_safe_message": _c6w7_seller_safe_message(response_status, envelope),
        "next_human_step": _c6w7_next_human_step(response_status, envelope),
        "next_system_step_label": _c6w7_next_system_step_label(response_status),
        "allowed_to_preview": envelope.get("allowed_to_preview") is True,
        "allowed_to_prepare": allowed and envelope.get("allowed_to_prepare") is True,
        "allowed_to_execute": False,
        "prepared_only": True,
        "reconciled_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
    }


def _c6w7_refusal(
    refusal_code: str,
    message: str,
    blocked_reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reconciled": False,
        "status": "blocked",
        "refusal_code": refusal_code,
        "message": message,
    }
    if blocked_reconciliation is not None:
        result["blocked_reconciliation"] = blocked_reconciliation
    return result


def reconcile_agenticorg_c6w7_prepared_response(
    *,
    envelope: dict[str, Any] | None,
    response_kind: ResponseEvidenceKind,
    response_status: ReconciliationStatus,
    created_at: str,
    response_evidence_refs: list[str] | None = None,
    response_flags: list[str] | None = None,
    response_claimed_envelope_id: str | None = None,
    response_claimed_action: str | None = None,
    response_evidence_issued_at: str | None = None,
    amount_minor_units: int | None = None,
    currency: str | None = None,
    total_quantity: int | None = None,
) -> dict[str, Any]:
    """Reconcile cached C6W7 response evidence against a C6W6 prepared envelope."""

    if envelope is None:
        return _c6w7_refusal(
            "prepared_envelope_missing",
            "C6W7 requires a C6W6 prepared envelope before response reconciliation.",
        )

    evidence_refs = _c6w7_safe_evidence_refs(response_evidence_refs)
    ttl = _c6w7_ttl(response_kind, created_at, envelope)
    blocked_reconciliation = None
    if ttl is not None and evidence_refs:
        blocked_reconciliation = _c6w7_reconciliation(
            envelope=envelope,
            response_kind=response_kind,
            response_status="blocked",
            created_at=created_at,
            expires_at=str(ttl["expires_at"]),
            max_ttl_seconds=int(ttl["max_ttl_seconds"]),
            response_evidence_refs=evidence_refs,
        )

    if envelope.get("allowed_to_execute") is not False:
        return _c6w7_refusal(
            "prepared_envelope_allows_execution",
            "C6W7 refuses executable envelope input.",
            blocked_reconciliation,
        )
    if envelope.get("prepared_only") is not True or envelope.get("envelope_status") != "prepared_only":
        return _c6w7_refusal(
            "prepared_envelope_not_prepared_only",
            "C6W7 reconciles prepared-only envelopes only.",
            blocked_reconciliation,
        )

    freshness = envelope.get("freshness_summary")
    if (
        not _string_list(envelope.get("source_artifact_ids"))
        or not _string_list(envelope.get("source_artifact_families"))
        or not isinstance(freshness, dict)
        or freshness.get("earliest_expires_at") is None
        or freshness.get("freshness_tier") in {"stale", "unknown"}
        or ttl is None
    ):
        return _c6w7_refusal(
            "source_freshness_missing_or_stale",
            "C6W7 requires fresh envelope source metadata and a live TTL.",
            blocked_reconciliation,
        )
    if OACP_C6W7_RESPONSE_KIND_BY_ENVELOPE_KIND.get(str(envelope.get("envelope_kind"))) != response_kind:
        return _c6w7_refusal(
            "response_kind_envelope_mismatch",
            "Response kind does not match the prepared envelope kind.",
            blocked_reconciliation,
        )
    if response_status not in OACP_C6W7_RECONCILIATION_STATUSES:
        return _c6w7_refusal(
            "response_status_attempts_execution",
            "Response status is not an allowed C6W7 fail-closed status.",
            blocked_reconciliation,
        )
    if evidence_refs is None:
        return _c6w7_refusal(
            "private_or_forbidden_response_field",
            "Response evidence refs contain private or enabling fields.",
            blocked_reconciliation,
        )
    if not evidence_refs:
        return _c6w7_refusal(
            "response_evidence_refs_missing",
            "C6W7 requires local cached response evidence refs.",
            blocked_reconciliation,
        )
    if _c6w7_response_indicates_forbidden_execution(response_flags):
        return _c6w7_refusal(
            "response_indicates_forbidden_execution",
            "Response evidence implies forbidden live execution or publication behavior.",
            blocked_reconciliation,
        )
    if _c6w7_risk_context_missing(
        envelope=envelope,
        currency=currency,
        amount_minor_units=amount_minor_units,
        total_quantity=total_quantity,
    ):
        return _c6w7_refusal(
            "risk_context_missing_or_ambiguous",
            "Commitment-bound reconciliation requires amount, currency, and quantity context.",
            blocked_reconciliation,
        )
    if _c6w7_mandate_evidence_stale(
        response_kind=response_kind,
        response_status=response_status,
        created_at=created_at,
        response_evidence_issued_at=response_evidence_issued_at,
    ):
        return _c6w7_refusal(
            "mandate_evidence_stale",
            "Mandate capability evidence is older than the commitment-boundary TTL.",
            blocked_reconciliation,
        )
    if _c6w7_response_conflicts_with_envelope(
        envelope=envelope,
        response_claimed_envelope_id=response_claimed_envelope_id,
        response_claimed_action=response_claimed_action,
    ):
        return _c6w7_refusal(
            "response_conflicts_with_envelope",
            "Response evidence conflicts with C6W6 envelope metadata.",
            blocked_reconciliation,
        )

    reconciliation = _c6w7_reconciliation(
        envelope=envelope,
        response_kind=response_kind,
        response_status=response_status,
        created_at=created_at,
        expires_at=str(ttl["expires_at"]),
        max_ttl_seconds=int(ttl["max_ttl_seconds"]),
        response_evidence_refs=evidence_refs,
    )
    try:
        assert_no_forbidden_oacp_artifact_fields(reconciliation)
    except ValueError:
        return _c6w7_refusal(
            "private_or_forbidden_response_field",
            "Prepared response reconciliation contains private or enabling fields.",
            blocked_reconciliation,
        )
    return {"reconciled": True, "status": response_status, "reconciliation": reconciliation}


_C6W8_PRIVATE_VALUE_MARKERS = _C6W7_PRIVATE_VALUE_MARKERS + (
    "unredacted",
    "private_merchant",
    "private_customer",
    "private_provider",
)
_C6W8_FORBIDDEN_EXECUTION_MARKERS = _C6W7_FORBIDDEN_EXECUTION_MARKERS + (
    "hold_create",
    "refund_execute",
    "return_execute",
    "mandate_create",
)


def _c6w8_safe_refs(values: list[str] | None) -> list[str] | None:
    refs: list[str] = []
    for value in values or []:
        normalized = value.strip()
        if not normalized:
            continue
        lowered = normalized.lower().replace("-", "_")
        if any(marker in lowered for marker in _C6W8_PRIVATE_VALUE_MARKERS):
            return None
        if normalized not in refs:
            refs.append(normalized)
    return refs


def _c6w8_ttl(
    packet_kind: str,
    created_at: str,
    reconciliation: dict[str, Any],
) -> dict[str, Any] | None:
    created = _parse_iso(created_at)
    reconciliation_expiry = _parse_iso(str(reconciliation.get("expires_at")))
    if created is None or reconciliation_expiry is None:
        return None
    default_expiry = datetime.fromtimestamp(
        created.timestamp() + OACP_C6W8_PACKET_TTL_SECONDS[packet_kind],
        tz=UTC,
    )
    expires_at = min(default_expiry, reconciliation_expiry)
    if expires_at <= created:
        return {"expires_at": created_at, "max_ttl_seconds": 0, "expired": True}
    return {
        "expires_at": expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "max_ttl_seconds": int((expires_at - created).total_seconds()),
        "expired": False,
    }


def _c6w8_default_confirmations(reconciliation: dict[str, Any]) -> list[str]:
    response_kind = str(reconciliation.get("response_kind"))
    if response_kind == "buyer_confirmation_response":
        return ["buyer_confirmation"]
    if response_kind == "seller_source_refresh_response":
        return ["seller_source_refresh"]
    if response_kind == "merchant_confirmation_response":
        return ["merchant_source_confirmation"]
    if response_kind == "mandate_capability_evidence_response":
        return ["mandate_capability_evidence"]
    return ["support_owner_review"]


def _c6w8_missing_confirmations(required: list[str], provided: list[str] | None) -> list[str]:
    provided_set = {value.strip() for value in provided or [] if value.strip()}
    return [confirmation for confirmation in required if confirmation not in provided_set]


def _c6w8_risk_context_missing(
    *,
    reconciliation: dict[str, Any],
    currency: str | None,
    amount_minor_units: int | None,
    total_quantity: int | None,
) -> bool:
    action = str(reconciliation.get("requested_action"))
    action_class = cast(ActionClass, str(reconciliation.get("action_class")))
    if not _c6w5_requires_risk_context(action, action_class):
        return False
    return (
        amount_minor_units is None
        or amount_minor_units <= 0
        or currency is None
        or not currency.strip()
        or total_quantity is None
        or total_quantity <= 0
    )


def _c6w8_mandate_evidence_stale(
    *,
    reconciliation: dict[str, Any],
    created_at: str,
    mandate_evidence_issued_at: str | None,
) -> bool:
    response_kind = str(reconciliation.get("response_kind"))
    requested_action = str(reconciliation.get("requested_action"))
    if response_kind != "mandate_capability_evidence_response" and requested_action not in {
        "payment_intent",
        "mandate_setup_use",
        "prepare_mandate_capability_check_request",
    }:
        return False
    if mandate_evidence_issued_at is None:
        return True
    issued = _parse_iso(mandate_evidence_issued_at)
    created = _parse_iso(created_at)
    if issued is None or created is None:
        return True
    return (created - issued).total_seconds() > 120


def _c6w8_packet_indicates_forbidden_execution(packet_flags: list[str] | None) -> bool:
    for value in packet_flags or []:
        normalized = value.lower().replace("-", "_")
        if any(marker in normalized for marker in _C6W8_FORBIDDEN_EXECUTION_MARKERS):
            return True
    return False


def _c6w8_source_freshness_status(
    reconciliation: dict[str, Any],
    ttl: dict[str, Any] | None,
) -> str | None:
    freshness = reconciliation.get("freshness_summary")
    if (
        not _string_list(reconciliation.get("source_artifact_ids"))
        or not _string_list(reconciliation.get("source_artifact_families"))
        or not isinstance(freshness, dict)
        or freshness.get("earliest_expires_at") is None
        or freshness.get("freshness_tier") == "unknown"
    ):
        return "missing_evidence"
    if ttl is None or ttl.get("expired") is True or freshness.get("freshness_tier") == "stale":
        return "expired"
    return None


def _c6w8_status_from_reconciliation(response_status: str) -> str:
    if response_status == "accepted_for_preparation":
        return "eligible_for_future_handoff"
    if response_status == "needs_source_refresh":
        return "missing_evidence"
    if response_status == "needs_human_review":
        return "needs_human_review"
    if response_status in {"expired", "stale", "mismatched"}:
        return response_status
    return "blocked"


def _c6w8_packet_kind_matches_status(packet_kind: str, status: str) -> bool:
    if status == "eligible_for_future_handoff":
        return packet_kind in {"execution_handoff_eligibility_packet", "audit_trail_preparation_packet"}
    if status == "missing_evidence":
        return packet_kind == "missing_evidence_packet"
    if status == "needs_human_review":
        return packet_kind == "manual_review_packet"
    return packet_kind == "blocked_execution_packet"


def _c6w8_reason(status: str, packet_kind: str) -> str:
    if status == "eligible_for_future_handoff":
        return (
            "C6W8 found a prepared-only, reconciled-only request with local evidence refs "
            "and required confirmations for a future controlled handoff packet."
        )
    if status == "missing_evidence":
        return "C6W8 cannot prepare future handoff eligibility because evidence or confirmation material is missing."
    if status == "needs_human_review":
        return "C6W8 requires a human review label before any future handoff packet can be considered."
    if status in {"stale", "expired"}:
        return "C6W8 source or reconciliation freshness is stale or expired; refresh source artifacts first."
    if status == "mismatched":
        return "C6W8 detected a mismatch between reconciliation evidence and the prepared envelope lineage."
    if status == "unsupported":
        return "C6W8 marks this request unsupported for future handoff under the internal non-executing policy."
    return (
        f"{packet_kind} blocks future handoff and does not approve checkout, payment, order, hold, "
        "refund, return, shipping, provider rail, or merchant private API behavior."
    )


def _c6w8_buyer_safe_message(status: str, reconciliation: dict[str, Any]) -> str:
    if status == "eligible_for_future_handoff":
        return (
            f"The {reconciliation.get('requested_action')} request is eligible only for a future controlled "
            "handoff packet. Nothing has been executed."
        )
    if status == "missing_evidence":
        return "More source evidence or confirmation is needed before this request can move beyond preparation."
    if status == "needs_human_review":
        return "A human review is required before another prepared step can be considered."
    if status in {"stale", "expired"}:
        return "The cached source evidence is stale or expired, so the request is not eligible for handoff."
    if status == "mismatched":
        return "The response evidence does not match the prepared request lineage."
    return "The request is blocked from future handoff and has not been executed."


def _c6w8_seller_safe_message(status: str, reconciliation: dict[str, Any]) -> str:
    if status == "eligible_for_future_handoff":
        return (
            f"Carry forward redacted refs for {reconciliation.get('reconciliation_id')}; "
            "merchant systems and provider rails remain operational authorities."
        )
    if status == "missing_evidence":
        return (
            "Provide refreshed source artifacts or confirmation refs only; "
            "do not send private payloads or credentials."
        )
    if status == "needs_human_review":
        return "Route to the named human review owner label before any future prepared handoff."
    if status in {"stale", "expired"}:
        return "Refresh source facts from operational systems before preparing another packet."
    if status == "mismatched":
        return "Reconcile the mismatch against the original envelope and cached source refs."
    return "Do not treat this packet as transaction authority or an execution approval."


def _c6w8_next_human_step(status: str) -> str:
    if status == "eligible_for_future_handoff":
        return (
            "Human owner must verify audit lineage before any future execution-controller slice "
            "can consume this packet."
        )
    if status == "missing_evidence":
        return "Collect missing source evidence or confirmation refs through non-executing channels."
    if status == "needs_human_review":
        return "Assign a human reviewer to inspect the reconciliation and source lineage."
    if status in {"stale", "expired"}:
        return "Ask the source owner to refresh cached artifacts before preparing another packet."
    if status == "mismatched":
        return "Resolve the mismatch before preparing any future handoff packet."
    return "Stop the handoff path and keep buyer and seller messages non-executing."


def _c6w8_next_system_step_label(status: str) -> str:
    if status == "eligible_for_future_handoff":
        return "future_execution_controller_review_label"
    if status == "missing_evidence":
        return "missing_evidence_refresh_label"
    if status == "needs_human_review":
        return "manual_review_packet_label"
    if status in {"stale", "expired"}:
        return "source_refresh_packet_label"
    if status == "mismatched":
        return "lineage_mismatch_review_label"
    if status == "unsupported":
        return "unsupported_handoff_label"
    return "blocked_handoff_label"


def _c6w8_packet_id(
    *,
    packet_kind: str,
    reconciliation_id: str,
    eligibility_status: str,
    created_at: str,
    audit_lineage_refs: list[str],
) -> str:
    payload = {
        "packet_kind": packet_kind,
        "reconciliation_id": reconciliation_id,
        "eligibility_status": eligibility_status,
        "created_at": created_at,
        "audit_lineage_refs": audit_lineage_refs,
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()
    return f"oacp_c6w8_packet_{digest[:20]}"


def _c6w8_packet(
    *,
    packet_kind: str,
    reconciliation: dict[str, Any],
    status: str,
    reason: str,
    missing_requirements: list[str],
    required_confirmations: list[str],
    created_at: str,
    expires_at: str,
    max_ttl_seconds: int,
    response_evidence_refs: list[str],
    audit_lineage_refs: list[str],
) -> dict[str, Any]:
    allowed_for_future_handoff = status == "eligible_for_future_handoff"
    return {
        "packet_id": _c6w8_packet_id(
            packet_kind=packet_kind,
            reconciliation_id=str(reconciliation.get("reconciliation_id")),
            eligibility_status=status,
            created_at=created_at,
            audit_lineage_refs=audit_lineage_refs,
        ),
        "packet_kind": packet_kind,
        "created_at": created_at,
        "expires_at": expires_at,
        "max_ttl_seconds": max_ttl_seconds,
        "reconciliation_id": reconciliation.get("reconciliation_id"),
        "envelope_id": reconciliation.get("envelope_id"),
        "response_kind": reconciliation.get("response_kind"),
        "response_status": reconciliation.get("response_status"),
        "requested_action": reconciliation.get("requested_action"),
        "action_class": reconciliation.get("action_class"),
        "risk_tier": reconciliation.get("risk_tier"),
        "eligibility_status": status,
        "eligibility_reason": reason,
        "missing_requirements": missing_requirements,
        "required_confirmations": required_confirmations,
        "source_artifact_ids": _string_list(reconciliation.get("source_artifact_ids")),
        "source_artifact_families": _string_list(reconciliation.get("source_artifact_families")),
        "response_evidence_refs": response_evidence_refs,
        "audit_lineage_refs": audit_lineage_refs,
        "freshness_summary": reconciliation.get("freshness_summary"),
        "unsupported_capabilities": _string_list(reconciliation.get("unsupported_capabilities")),
        "blocked_capabilities": _string_list(reconciliation.get("blocked_capabilities")),
        "buyer_safe_message": _c6w8_buyer_safe_message(status, reconciliation),
        "seller_safe_message": _c6w8_seller_safe_message(status, reconciliation),
        "next_human_step": _c6w8_next_human_step(status),
        "next_system_step_label": _c6w8_next_system_step_label(status),
        "allowed_to_preview": reconciliation.get("allowed_to_preview") is True,
        "allowed_to_prepare": allowed_for_future_handoff and reconciliation.get("allowed_to_prepare") is True,
        "allowed_for_future_handoff": allowed_for_future_handoff,
        "allowed_to_execute": False,
        "prepared_only": True,
        "reconciled_only": True,
        "eligibility_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
    }


def _c6w8_refusal(
    refusal_code: str,
    message: str,
    blocked_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "prepared": False,
        "status": "blocked",
        "refusal_code": refusal_code,
        "message": message,
    }
    if blocked_packet is not None:
        result["blocked_packet"] = blocked_packet
    return result


def prepare_agenticorg_c6w8_eligibility_packet(
    *,
    packet_kind: EligibilityPacketKind,
    reconciliation: dict[str, Any] | None,
    created_at: str,
    audit_lineage_refs: list[str] | None = None,
    required_confirmations: list[str] | None = None,
    provided_confirmations: list[str] | None = None,
    packet_flags: list[str] | None = None,
    mandate_evidence_issued_at: str | None = None,
    amount_minor_units: int | None = None,
    currency: str | None = None,
    total_quantity: int | None = None,
) -> dict[str, Any]:
    """Prepare a local C6W8 future handoff eligibility packet from C6W7 evidence."""

    if reconciliation is None:
        return _c6w8_refusal(
            "reconciliation_missing",
            "C6W8 requires a C6W7 reconciliation before preparing eligibility packets.",
        )

    response_evidence_refs = _c6w8_safe_refs(_string_list(reconciliation.get("response_evidence_refs")))
    requested_lineage_refs = _c6w8_safe_refs(audit_lineage_refs)
    requested_required_confirmations = (
        None if required_confirmations is None else _c6w8_safe_refs(required_confirmations)
    )
    requested_provided_confirmations = _c6w8_safe_refs(provided_confirmations)
    requested_packet_flags = _c6w8_safe_refs(packet_flags)
    ttl = _c6w8_ttl(packet_kind, created_at, reconciliation)
    default_lineage_refs = [
        str(reconciliation.get("reconciliation_id")),
        str(reconciliation.get("envelope_id")),
        *_string_list(reconciliation.get("source_artifact_ids")),
        *_string_list(reconciliation.get("response_evidence_refs")),
    ]
    audit_refs = None
    if requested_lineage_refs is not None:
        audit_refs = list(dict.fromkeys(requested_lineage_refs if requested_lineage_refs else default_lineage_refs))
    required = list(dict.fromkeys(requested_required_confirmations or _c6w8_default_confirmations(reconciliation)))
    missing_confirmations = _c6w8_missing_confirmations(required, requested_provided_confirmations)
    missing_requirements: list[str] = []
    status = _c6w8_status_from_reconciliation(str(reconciliation.get("response_status")))

    if (
        response_evidence_refs is None
        or audit_refs is None
        or requested_required_confirmations is None and required_confirmations is not None
        or requested_provided_confirmations is None
        or requested_packet_flags is None
    ):
        return _c6w8_refusal(
            "private_or_forbidden_packet_field",
            "C6W8 packet refs contain private, raw, or unredacted fields.",
        )
    if not response_evidence_refs or not audit_refs:
        return _c6w8_refusal(
            "evidence_refs_missing",
            "C6W8 requires redacted response evidence refs and audit lineage refs.",
        )
    if reconciliation.get("allowed_to_execute") is not False:
        return _c6w8_refusal(
            "reconciliation_allows_execution",
            "C6W8 refuses executable reconciliation input.",
        )
    if reconciliation.get("prepared_only") is not True or reconciliation.get("reconciled_only") is not True:
        return _c6w8_refusal(
            "reconciliation_not_prepared_or_reconciled_only",
            "C6W8 accepts prepared-only and reconciled-only input only.",
        )
    if _c6w8_packet_indicates_forbidden_execution(packet_flags):
        return _c6w8_refusal(
            "packet_indicates_forbidden_execution",
            "C6W8 refuses packet flags that imply live execution or publication behavior.",
        )
    if _c6w8_risk_context_missing(
        reconciliation=reconciliation,
        currency=currency,
        amount_minor_units=amount_minor_units,
        total_quantity=total_quantity,
    ):
        return _c6w8_refusal(
            "risk_context_missing_or_ambiguous",
            "C6W8 requires amount, currency, and quantity context for commitment-bound eligibility.",
        )
    if _c6w8_mandate_evidence_stale(
        reconciliation=reconciliation,
        created_at=created_at,
        mandate_evidence_issued_at=mandate_evidence_issued_at,
    ):
        return _c6w8_refusal(
            "mandate_evidence_stale",
            "Mandate capability evidence is missing or stale at the C6W8 commitment boundary.",
        )

    source_status = _c6w8_source_freshness_status(reconciliation, ttl)
    if source_status is not None:
        status = source_status
        missing_requirements.append("fresh_source_artifacts")
    if str(reconciliation.get("requested_action")) in OACP_C6W5_ALWAYS_BLOCKED_ACTIONS:
        status = "blocked"
        missing_requirements.append("requested_action_blocked_by_c6w5")
    if reconciliation.get("risk_tier") == "critical":
        status = "unsupported"
        missing_requirements.append("critical_risk_not_supported_for_prepared_handoff")
    for confirmation in missing_confirmations:
        missing_requirements.append(f"confirmation:{confirmation}")
    if missing_confirmations and status == "eligible_for_future_handoff":
        status = "missing_evidence"

    if not _c6w8_packet_kind_matches_status(packet_kind, status):
        blocked_packet = None
        if ttl is not None:
            blocked_packet = _c6w8_packet(
                packet_kind="blocked_execution_packet",
                reconciliation=reconciliation,
                status="mismatched",
                reason=_c6w8_reason("mismatched", "blocked_execution_packet"),
                missing_requirements=["packet_kind_status_mismatch"],
                required_confirmations=required,
                created_at=created_at,
                expires_at=str(ttl["expires_at"]),
                max_ttl_seconds=int(ttl["max_ttl_seconds"]),
                response_evidence_refs=response_evidence_refs,
                audit_lineage_refs=audit_refs,
            )
        return _c6w8_refusal(
            "packet_kind_status_mismatch",
            "C6W8 packet kind does not match the derived eligibility status.",
            blocked_packet,
        )
    if ttl is None:
        return _c6w8_refusal(
            "private_or_forbidden_packet_field",
            "C6W8 cannot derive a safe TTL from reconciliation metadata.",
        )

    packet = _c6w8_packet(
        packet_kind=packet_kind,
        reconciliation=reconciliation,
        status=status,
        reason=_c6w8_reason(status, packet_kind),
        missing_requirements=missing_requirements,
        required_confirmations=required,
        created_at=created_at,
        expires_at=str(ttl["expires_at"]),
        max_ttl_seconds=int(ttl["max_ttl_seconds"]),
        response_evidence_refs=response_evidence_refs,
        audit_lineage_refs=audit_refs,
    )
    try:
        assert_no_forbidden_oacp_artifact_fields(packet)
    except ValueError:
        return _c6w8_refusal(
            "private_or_forbidden_packet_field",
            "C6W8 eligibility packet contains private or enabling fields.",
        )
    return {"prepared": True, "status": status, "packet": packet}


_C6W9_FORBIDDEN_TARGET_MARKERS = (
    "http://",
    "https://",
    "endpoint",
    "checkout_url",
    "payment_url",
    "order_target",
    "provider_target",
    "merchant_private",
    "live_rail",
    "carrier_target",
    "shipping_target",
)
_C6W9_PUBLICATION_CLAIM_MARKERS = (
    "protocol_publication",
    "protocol_submission",
    "certification",
    "compliance",
    "conformance",
    "standardization",
    "production_ready",
    "execution_ready",
)


def _c6w9_blank_contract_checks(value: bool = False) -> dict[str, bool]:
    return dict.fromkeys(OACP_C6W9_CONTRACT_CHECKS, value)


def _c6w9_blank_audit_checks(value: bool = False) -> dict[str, bool]:
    return dict.fromkeys(OACP_C6W9_AUDIT_READINESS_CHECKS, value)


def _c6w9_safe_refs(values: list[str] | None) -> list[str] | None:
    return _c6w8_safe_refs(values)


def _c6w9_values_safe(values: list[str] | None) -> bool:
    refs = _c6w9_safe_refs(values)
    if refs is None:
        return False
    for value in refs:
        normalized = value.lower().replace("-", "_")
        if any(marker in normalized for marker in _C6W9_FORBIDDEN_TARGET_MARKERS):
            return False
    return True


def _c6w9_values_do_not_claim_publication_or_readiness(values: list[str] | None) -> bool:
    for value in values or []:
        normalized = value.lower().replace("-", "_")
        if any(marker in normalized for marker in _C6W9_PUBLICATION_CLAIM_MARKERS):
            return False
    return True


def _c6w9_ttl(
    verification_kind: str,
    created_at: str,
    packet: dict[str, Any],
) -> dict[str, Any] | None:
    created = _parse_iso(created_at)
    packet_expiry = _parse_iso(_string_field(packet, "expires_at"))
    if created is None or packet_expiry is None:
        return None
    default_expiry = datetime.fromtimestamp(
        created.timestamp() + OACP_C6W9_VERIFICATION_TTL_SECONDS[verification_kind],
        tz=UTC,
    )
    expires_at = min(default_expiry, packet_expiry)
    if expires_at <= created:
        return {"expires_at": created_at, "max_ttl_seconds": 0, "expired": True}
    return {
        "expires_at": expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "max_ttl_seconds": int((expires_at - created).total_seconds()),
        "expired": False,
    }


def _c6w9_provided_confirmations_present(required: list[str], provided: list[str] | None) -> bool:
    if not required:
        return False
    safe_provided = _c6w9_safe_refs(provided)
    if safe_provided is None:
        return False
    provided_set = set(safe_provided)
    return all(confirmation in provided_set for confirmation in required)


def _c6w9_risk_context_missing(
    *,
    packet: dict[str, Any],
    currency: str | None,
    amount_minor_units: int | None,
    total_quantity: int | None,
) -> bool:
    action = str(packet.get("requested_action"))
    action_class = cast(ActionClass, str(packet.get("action_class")))
    if not _c6w5_requires_risk_context(action, action_class):
        return False
    return (
        amount_minor_units is None
        or amount_minor_units <= 0
        or currency is None
        or not currency.strip()
        or total_quantity is None
        or total_quantity <= 0
    )


def _c6w9_mandate_evidence_stale(
    *,
    packet: dict[str, Any],
    created_at: str,
    mandate_evidence_issued_at: str | None,
) -> bool:
    requested_action = str(packet.get("requested_action"))
    if requested_action not in {
        "payment_intent",
        "mandate_setup_use",
        "prepare_mandate_capability_check_request",
    }:
        return False
    if mandate_evidence_issued_at is None:
        return True
    issued = _parse_iso(mandate_evidence_issued_at)
    created = _parse_iso(created_at)
    if issued is None or created is None:
        return True
    return (created - issued).total_seconds() > 120


def _c6w9_action_risk_consistent(packet: dict[str, Any]) -> bool:
    action_class = str(packet.get("action_class"))
    risk_tier = str(packet.get("risk_tier"))
    if action_class == "always_blocked" or risk_tier == "critical":
        return False
    if action_class == "commitment_bound":
        return risk_tier in {"medium", "high"}
    if action_class == "commitment_adjacent":
        return risk_tier in {"low", "medium"}
    return risk_tier in {"informational", "low"}


def _c6w9_freshness_valid(
    *,
    packet: dict[str, Any],
    created_at: str,
    ttl: dict[str, Any] | None,
) -> bool:
    freshness = packet.get("freshness_summary")
    if not isinstance(freshness, dict):
        return False
    earliest_expires_at = freshness.get("earliest_expires_at")
    earliest_expiry = _parse_iso(str(earliest_expires_at)) if earliest_expires_at is not None else None
    created = _parse_iso(created_at)
    return (
        ttl is not None
        and ttl.get("expired") is not True
        and int(packet.get("max_ttl_seconds") or 0) > 0
        and freshness.get("freshness_tier") not in {"stale", "unknown"}
        and earliest_expiry is not None
        and created is not None
        and earliest_expiry > created
    )


def _c6w9_non_enablement_flags_intact(packet: dict[str, Any]) -> bool:
    return (
        packet.get("allowed_to_execute") is False
        and packet.get("prepared_only") is True
        and packet.get("reconciled_only") is True
        and packet.get("eligibility_only") is True
        and packet.get("non_authoritative_for_transaction") is True
        and packet.get("no_checkout_payment_enablement") is True
        and packet.get("no_live_provider_enablement") is True
        and packet.get("no_public_discovery_enablement") is True
    )


def _c6w9_no_executable_target(packet: dict[str, Any], verification_flags: list[str] | None) -> bool:
    return (
        _c6w9_values_safe(verification_flags)
        and _c6w9_values_safe([str(packet.get("next_system_step_label", ""))])
        and _c6w9_values_safe(_string_list(packet.get("audit_lineage_refs")))
        and _c6w9_values_safe(_string_list(packet.get("response_evidence_refs")))
    )


def _c6w9_no_publication_or_readiness_claims(packet: dict[str, Any], verification_flags: list[str] | None) -> bool:
    return (
        _c6w9_values_do_not_claim_publication_or_readiness(verification_flags)
        and _c6w9_values_do_not_claim_publication_or_readiness([str(packet.get("next_system_step_label", ""))])
        and _c6w9_values_do_not_claim_publication_or_readiness(_string_list(packet.get("audit_lineage_refs")))
        and _c6w9_values_do_not_claim_publication_or_readiness(_string_list(packet.get("response_evidence_refs")))
    )


def _c6w9_claimed_lineage_matches(
    *,
    packet: dict[str, Any],
    claimed_packet_id: str | None,
    claimed_reconciliation_id: str | None,
    claimed_envelope_id: str | None,
) -> bool:
    return (
        (claimed_packet_id is None or claimed_packet_id == packet.get("packet_id"))
        and (claimed_reconciliation_id is None or claimed_reconciliation_id == packet.get("reconciliation_id"))
        and (claimed_envelope_id is None or claimed_envelope_id == packet.get("envelope_id"))
    )


def _c6w9_status_from_packet(packet: dict[str, Any]) -> str:
    eligibility_status = str(packet.get("eligibility_status"))
    if eligibility_status == "eligible_for_future_handoff":
        return "dry_run_accepted_for_future_controller"
    if eligibility_status == "missing_evidence":
        return "missing_contract_requirement"
    if eligibility_status == "needs_human_review":
        return "needs_human_review"
    return eligibility_status


def _c6w9_kind_matches_status(verification_kind: str, status: str) -> bool:
    if status == "dry_run_accepted_for_future_controller":
        return verification_kind in {"execution_controller_handoff_dry_run", "audit_readiness_verification"}
    if status == "missing_contract_requirement":
        return verification_kind == "missing_contract_requirement"
    if status == "needs_human_review":
        return verification_kind == "manual_review_required_verification"
    return verification_kind == "blocked_handoff_verification"


def _c6w9_operator_message(status: str) -> str:
    if status == "dry_run_accepted_for_future_controller":
        return (
            "C6W9 dry-run accepted the local packet contract shape for a future controller review. "
            "This is not execution readiness and does not execute."
        )
    if status == "missing_contract_requirement":
        return "C6W9 found missing contract evidence, freshness, risk, or confirmation requirements."
    if status == "needs_human_review":
        return "C6W9 requires human review labels only and does not approve merchant, payment, or rail behavior."
    if status in {"stale", "expired"}:
        return "C6W9 found stale or expired packet lineage; refresh source artifacts before another dry run."
    if status == "mismatched":
        return "C6W9 found mismatched packet, reconciliation, envelope, or lineage references."
    if status == "unsupported":
        return "C6W9 marks this handoff contract unsupported under the internal non-executing policy."
    if status == "unsafe":
        return "C6W9 blocked unsafe private, executable, or publication-oriented packet content."
    return "C6W9 blocks the future handoff path and keeps the request non-executing."


def _c6w9_verification_id(
    *,
    verification_kind: str,
    verification_status: str,
    eligibility_packet_id: str,
    created_at: str,
    audit_lineage_refs: list[str],
) -> str:
    payload = {
        "verification_kind": verification_kind,
        "verification_status": verification_status,
        "eligibility_packet_id": eligibility_packet_id,
        "created_at": created_at,
        "audit_lineage_refs": audit_lineage_refs,
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(payload).encode("utf-8")).hexdigest()
    return f"oacp_c6w9_verification_{digest[:20]}"


def _c6w9_verification(
    *,
    verification_kind: str,
    packet: dict[str, Any],
    status: str,
    created_at: str,
    ttl: dict[str, Any],
    contract_checks: dict[str, bool],
    audit_readiness_checks: dict[str, bool],
    missing_requirements: list[str],
) -> dict[str, Any]:
    allowed_for_future_handoff = (
        status == "dry_run_accepted_for_future_controller" and packet.get("allowed_for_future_handoff") is True
    )
    audit_refs = _string_list(packet.get("audit_lineage_refs"))
    return {
        "verification_id": _c6w9_verification_id(
            verification_kind=verification_kind,
            verification_status=status,
            eligibility_packet_id=str(packet.get("packet_id")),
            created_at=created_at,
            audit_lineage_refs=audit_refs,
        ),
        "verification_kind": verification_kind,
        "verification_status": status,
        "created_at": created_at,
        "expires_at": ttl["expires_at"],
        "max_ttl_seconds": ttl["max_ttl_seconds"],
        "eligibility_packet_id": packet.get("packet_id"),
        "packet_kind": packet.get("packet_kind"),
        "eligibility_status": packet.get("eligibility_status"),
        "reconciliation_id": packet.get("reconciliation_id"),
        "envelope_id": packet.get("envelope_id"),
        "requested_action": packet.get("requested_action"),
        "action_class": packet.get("action_class"),
        "risk_tier": packet.get("risk_tier"),
        "source_artifact_ids": _string_list(packet.get("source_artifact_ids")),
        "source_artifact_families": _string_list(packet.get("source_artifact_families")),
        "response_evidence_refs": _string_list(packet.get("response_evidence_refs")),
        "audit_lineage_refs": audit_refs,
        "required_confirmations": _string_list(packet.get("required_confirmations")),
        "missing_requirements": missing_requirements,
        "freshness_summary": packet.get("freshness_summary"),
        "contract_checks": contract_checks,
        "audit_readiness_checks": audit_readiness_checks,
        "unsupported_capabilities": _string_list(packet.get("unsupported_capabilities")),
        "blocked_capabilities": _string_list(packet.get("blocked_capabilities")),
        "buyer_safe_message": packet.get("buyer_safe_message"),
        "seller_safe_message": packet.get("seller_safe_message"),
        "operator_safe_message": _c6w9_operator_message(status),
        "next_human_step": packet.get("next_human_step"),
        "next_system_step_label": packet.get("next_system_step_label"),
        "allowed_to_preview": packet.get("allowed_to_preview") is True,
        "allowed_to_prepare": allowed_for_future_handoff and packet.get("allowed_to_prepare") is True,
        "allowed_for_future_handoff": allowed_for_future_handoff,
        "allowed_to_execute": False,
        "dry_run_only": True,
        "eligibility_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
    }


def _c6w9_refusal(
    refusal_code: str,
    message: str,
    status: str = "blocked",
    blocked_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "verified": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
    }
    if blocked_verification is not None:
        result["blocked_verification"] = blocked_verification
    return result


def verify_agenticorg_c6w9_execution_handoff_dry_run(
    *,
    verification_kind: DryRunVerificationKind,
    eligibility_packet: dict[str, Any] | None,
    created_at: str,
    provided_confirmations: list[str] | None = None,
    verification_flags: list[str] | None = None,
    claimed_packet_id: str | None = None,
    claimed_reconciliation_id: str | None = None,
    claimed_envelope_id: str | None = None,
    mandate_evidence_issued_at: str | None = None,
    amount_minor_units: int | None = None,
    currency: str | None = None,
    total_quantity: int | None = None,
) -> dict[str, Any]:
    """Dry-run verify a local C6W8 eligibility packet for future controller review only."""

    if eligibility_packet is None:
        return _c6w9_refusal(
            "packet_missing",
            "C6W9 requires a C6W8 eligibility packet before dry-run verification.",
        )

    packet = eligibility_packet
    if _c6w9_safe_refs(provided_confirmations) is None or _c6w9_safe_refs(verification_flags) is None:
        return _c6w9_refusal(
            "private_or_forbidden_verification_field",
            "C6W9 verifier input contains private, raw, or unredacted fields.",
            "unsafe",
        )
    if packet.get("allowed_to_execute") is not False:
        return _c6w9_refusal(
            "packet_allows_execution",
            "C6W9 refuses eligibility packets that allow execution.",
            "unsafe",
        )
    if (
        packet.get("prepared_only") is not True
        or packet.get("reconciled_only") is not True
        or packet.get("eligibility_only") is not True
    ):
        return _c6w9_refusal(
            "packet_not_prepared_reconciled_or_eligibility_only",
            "C6W9 accepts prepared-only, reconciled-only, eligibility-only packets only.",
        )

    ttl = _c6w9_ttl(verification_kind, created_at, packet)
    contract_checks = _c6w9_blank_contract_checks()
    audit_checks = _c6w9_blank_audit_checks()
    source_ids = _string_list(packet.get("source_artifact_ids"))
    source_families = _string_list(packet.get("source_artifact_families"))
    response_refs = _string_list(packet.get("response_evidence_refs"))
    audit_refs = _string_list(packet.get("audit_lineage_refs"))
    required_confirmations = _string_list(packet.get("required_confirmations"))
    refs_safe = _c6w9_safe_refs(response_refs) is not None and _c6w9_safe_refs(audit_refs) is not None
    confirmations_present = _c6w9_provided_confirmations_present(required_confirmations, provided_confirmations)
    freshness_valid = _c6w9_freshness_valid(packet=packet, created_at=created_at, ttl=ttl)
    mandate_evidence_valid = not _c6w9_mandate_evidence_stale(
        packet=packet,
        created_at=created_at,
        mandate_evidence_issued_at=mandate_evidence_issued_at,
    )
    risk_context_present = not _c6w9_risk_context_missing(
        packet=packet,
        currency=currency,
        amount_minor_units=amount_minor_units,
        total_quantity=total_quantity,
    )
    lineage_matches = _c6w9_claimed_lineage_matches(
        packet=packet,
        claimed_packet_id=claimed_packet_id,
        claimed_reconciliation_id=claimed_reconciliation_id,
        claimed_envelope_id=claimed_envelope_id,
    )
    no_executable_target = _c6w9_no_executable_target(packet, verification_flags)
    no_publication_claims = _c6w9_no_publication_or_readiness_claims(packet, verification_flags)
    non_enablement_flags_intact = _c6w9_non_enablement_flags_intact(packet)
    source_refs_present = bool(source_ids and source_families)
    evidence_refs_present = bool(response_refs)
    audit_lineage_present = bool(audit_refs)
    reconciliation_id = _string_field(packet, "reconciliation_id") or ""
    envelope_id = _string_field(packet, "envelope_id") or ""
    decision_lineage_complete = (
        audit_lineage_present
        and reconciliation_id in audit_refs
        and envelope_id in audit_refs
        and lineage_matches
    )
    status = _c6w9_status_from_packet(packet)

    contract_checks["packet_kind_recognized"] = str(packet.get("packet_kind")) in OACP_C6W8_ELIGIBILITY_PACKET_KINDS
    contract_checks["eligibility_status_acceptable"] = packet.get("eligibility_status") == "eligible_for_future_handoff"
    contract_checks["reconciliation_lineage_present"] = bool(reconciliation_id) and lineage_matches
    contract_checks["envelope_lineage_present"] = bool(envelope_id) and lineage_matches
    contract_checks["source_artifact_refs_present"] = source_refs_present
    contract_checks["evidence_refs_redacted_and_non_private"] = refs_safe and evidence_refs_present
    contract_checks["required_confirmations_present"] = confirmations_present
    contract_checks["freshness_ttl_valid"] = freshness_valid
    contract_checks["mandate_evidence_valid"] = mandate_evidence_valid
    contract_checks["action_class_risk_tier_consistent"] = _c6w9_action_risk_consistent(packet)
    contract_checks["commitment_risk_context_present"] = risk_context_present
    contract_checks["non_enablement_flags_intact"] = non_enablement_flags_intact
    contract_checks["no_executable_url_or_target"] = no_executable_target
    contract_checks["no_raw_private_labels_or_payloads"] = refs_safe and no_executable_target
    contract_checks["no_publication_certification_readiness_claims"] = no_publication_claims

    audit_checks["audit_lineage_refs_present"] = audit_lineage_present
    audit_checks["audit_refs_redacted"] = refs_safe
    audit_checks["decision_lineage_complete"] = decision_lineage_complete
    audit_checks["source_refs_carried_forward"] = source_refs_present
    audit_checks["evidence_refs_carried_forward"] = evidence_refs_present
    audit_checks["messages_safe_and_non_executing"] = _c6w9_values_safe(
        [
            str(packet.get("buyer_safe_message", "")),
            str(packet.get("seller_safe_message", "")),
            str(packet.get("next_human_step", "")),
            str(packet.get("next_system_step_label", "")),
        ]
    )

    missing_requirements = set(_string_list(packet.get("missing_requirements")))
    if not contract_checks["reconciliation_lineage_present"] or not contract_checks["envelope_lineage_present"]:
        status = "mismatched"
        missing_requirements.add("complete_packet_reconciliation_envelope_lineage")
    if not decision_lineage_complete:
        status = "mismatched"
        missing_requirements.add("complete_packet_reconciliation_envelope_lineage")
    if not source_refs_present or not evidence_refs_present or not audit_lineage_present:
        status = "missing_contract_requirement"
        missing_requirements.add("source_evidence_and_audit_refs")
    if not confirmations_present:
        status = "missing_contract_requirement"
        for confirmation in required_confirmations:
            missing_requirements.add(f"confirmation:{confirmation}")
    if not risk_context_present:
        status = "missing_contract_requirement"
        missing_requirements.add("amount_currency_quantity_context")
    if not mandate_evidence_valid:
        status = "stale"
        missing_requirements.add("fresh_mandate_capability_evidence")
    if not freshness_valid:
        status = "expired" if ttl is None or ttl.get("expired") is True else "stale"
        missing_requirements.add("fresh_source_artifacts")
    if not contract_checks["action_class_risk_tier_consistent"]:
        status = "unsupported" if packet.get("risk_tier") == "critical" else "mismatched"
        missing_requirements.add("consistent_action_class_and_risk_tier")
    if (
        not contract_checks["non_enablement_flags_intact"]
        or not contract_checks["no_executable_url_or_target"]
        or not contract_checks["no_raw_private_labels_or_payloads"]
        or not contract_checks["no_publication_certification_readiness_claims"]
    ):
        status = "unsafe"
        missing_requirements.add("non_enablement_and_private_target_controls")

    if not non_enablement_flags_intact:
        return _c6w9_refusal(
            "non_enablement_flags_missing",
            "C6W9 refuses packets with missing or false non-enablement flags.",
            "unsafe",
        )
    if not no_executable_target or not no_publication_claims or not refs_safe:
        return _c6w9_refusal(
            "private_or_forbidden_verification_field",
            "C6W9 refuses private refs, executable targets, publication claims, or readiness claims.",
            "unsafe",
        )
    if not _c6w9_kind_matches_status(verification_kind, status):
        blocked_verification = None
        if ttl is not None:
            blocked_verification = _c6w9_verification(
                verification_kind=verification_kind,
                packet=packet,
                status="mismatched",
                created_at=created_at,
                ttl=ttl,
                contract_checks=contract_checks,
                audit_readiness_checks=audit_checks,
                missing_requirements=[*sorted(missing_requirements), "verification_kind_status_mismatch"],
            )
        return _c6w9_refusal(
            "verification_kind_status_mismatch",
            "C6W9 verification kind does not match the derived dry-run status.",
            "blocked",
            blocked_verification,
        )

    verification_ttl = ttl or {"expires_at": created_at, "max_ttl_seconds": 0}
    verification = _c6w9_verification(
        verification_kind=verification_kind,
        packet=packet,
        status=status,
        created_at=created_at,
        ttl=verification_ttl,
        contract_checks=contract_checks,
        audit_readiness_checks=audit_checks,
        missing_requirements=sorted(missing_requirements),
    )
    try:
        assert_no_forbidden_oacp_artifact_fields(verification)
    except ValueError:
        return _c6w9_refusal(
            "private_or_forbidden_verification_field",
            "C6W9 dry-run verification contains private or enabling fields.",
            "unsafe",
        )
    return {"verified": True, "status": status, "verification": verification}


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


C6X2_PRIVATE_REF_MARKERS = (
    "raw_jwt",
    "bearer ",
    "private_key",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "db_url",
    "redis_url",
    "merchant_private_api",
    "raw_provider_payload",
    "raw_connector_payload",
    "production_allowlist",
)
C6X2_ENABLEMENT_REF_MARKERS = (
    "checkout_payment_enabled",
    "live_provider_enabled",
    "public_discovery_enabled",
    "order_created",
    "payment_captured",
    "provider_executed",
    "shipping_created",
    "hold_created",
    "refund_created",
    "return_created",
)
C6X2_ARTIFACTS_NOT_TRANSACTION_AUTHORITY = frozenset(
    {"protocol_adapter", "seller_agent_capability", "public_discovery"}
)


def _c6x2_cache_refusal(
    *,
    status: PersistentCacheEvaluationStatus,
    refusal_code: str,
    message: str,
    record: OacpPersistentArtifactCacheRecord | None = None,
) -> dict[str, Any]:
    return {
        "evaluated": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
        "cache_record_id": None if record is None else record.cache_record_id,
        "artifact_id": None if record is None else record.artifact_id,
        "allowed_to_preview": False,
        "allowed_to_prepare": False,
        "allowed_to_execute": False,
        "prepared_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
        "grantex_runtime_required": False,
    }


def _c6x2_scope_present(record: OacpPersistentArtifactCacheRecord) -> bool:
    if not record.tenant_id:
        return False
    if record.scope_kind == "merchant":
        return bool(record.merchant_id)
    if record.scope_kind == "seller_agent":
        return bool(record.merchant_id and record.seller_agent_id)
    if record.scope_kind == "buyer_agent":
        return bool(record.merchant_id and record.seller_agent_id and record.buyer_agent_id)
    return record.scope_kind == "tenant"


def _c6x2_scope_matches(
    record: OacpPersistentArtifactCacheRecord,
    expected_scope: dict[str, str | None] | None,
) -> bool:
    if expected_scope is None:
        return True
    actual: dict[str, str | None] = {
        "tenant_id": record.tenant_id,
        "merchant_id": record.merchant_id,
        "seller_agent_id": record.seller_agent_id,
        "buyer_agent_id": record.buyer_agent_id,
    }
    return all(value is None or actual.get(key) == value for key, value in expected_scope.items())


def _c6x2_refs_safe(values: tuple[str, ...]) -> bool:
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            return False
        if any(marker in normalized for marker in C6X2_PRIVATE_REF_MARKERS):
            return False
        if any(marker in normalized for marker in C6X2_ENABLEMENT_REF_MARKERS):
            return False
    return True


def _c6x2_non_enablement_flags_intact(record: OacpPersistentArtifactCacheRecord) -> bool:
    return (
        record.allowed_to_execute is False
        and record.non_authoritative_for_transaction is True
        and record.no_checkout_payment_enablement is True
        and record.no_live_provider_enablement is True
        and record.no_public_discovery_enablement is True
    )


def evaluate_oacp_persistent_artifact_cache_record(
    *,
    record: OacpPersistentArtifactCacheRecord | None,
    action_intent: PersistentCacheActionIntent,
    now_iso: str,
    grantex_available: bool,
    expected_scope: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    if record is None:
        return _c6x2_cache_refusal(
            status="blocked",
            refusal_code="cache_record_missing",
            message="C6X2 requires a local persistent OACP artifact cache record.",
        )

    if not record.cache_record_id or not record.artifact_id or not record.authority or not record.issuer:
        return _c6x2_cache_refusal(
            status="blocked",
            refusal_code="cache_record_identity_missing",
            message="C6X2 cache records require local id, artifact id, authority, and issuer.",
            record=record,
        )
    if not _c6x2_scope_present(record):
        return _c6x2_cache_refusal(
            status="blocked",
            refusal_code="cache_scope_missing",
            message="C6X2 cache records require tenant, merchant, seller, or buyer scope as applicable.",
            record=record,
        )
    if not _c6x2_scope_matches(record, expected_scope):
        return _c6x2_cache_refusal(
            status="mismatched",
            refusal_code="cache_scope_mismatch",
            message="C6X2 refuses cache records outside the requesting agent or tenant scope.",
            record=record,
        )
    if not _c6x2_non_enablement_flags_intact(record):
        return _c6x2_cache_refusal(
            status="unsafe",
            refusal_code="non_enablement_flags_missing",
            message="C6X2 refuses cache records with missing or false non-enablement flags.",
            record=record,
        )

    now = _parse_iso(now_iso)
    generated_at = _parse_iso(record.generated_at)
    cached_at = _parse_iso(record.cached_at)
    expires_at = _parse_iso(record.expires_at)
    if now is None or generated_at is None or cached_at is None or expires_at is None:
        return _c6x2_cache_refusal(
            status="stale",
            refusal_code="cache_freshness_missing",
            message="C6X2 cache records require generated, cached, and expiry timestamps.",
            record=record,
        )
    if now >= expires_at:
        return _c6x2_cache_refusal(
            status="expired",
            refusal_code="cache_record_expired",
            message="C6X2 refuses expired persistent OACP artifact cache records.",
            record=record,
        )
    if record.ttl_policy_seconds <= 0 or record.ttl_policy_seconds > OACP_ARTIFACT_TTLS_SECONDS[record.artifact_type]:
        return _c6x2_cache_refusal(
            status="stale",
            refusal_code="cache_ttl_policy_invalid",
            message="C6X2 refuses cache records with missing or excessive TTL policy.",
            record=record,
        )
    if record.freshness_status not in {"fresh", "provisional"}:
        return _c6x2_cache_refusal(
            status="stale",
            refusal_code="cache_freshness_stale",
            message="C6X2 cache records must be fresh or provisional for preview and prepare use.",
            record=record,
        )

    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[record.risk_tier]
    if record.revocation_snapshot_status == "revoked":
        return _c6x2_cache_refusal(
            status="revoked",
            refusal_code="cache_record_revoked",
            message="C6X2 refuses revoked persistent OACP artifact cache records.",
            record=record,
        )
    if (
        max_revocation_age is None
        or record.revocation_snapshot_status != "fresh"
        or record.revocation_snapshot_observed_at is None
        or _parse_iso(record.revocation_snapshot_observed_at) is None
        or record.revocation_snapshot_age_seconds is None
        or record.revocation_snapshot_age_seconds > max_revocation_age
    ):
        return _c6x2_cache_refusal(
            status="stale",
            refusal_code="cache_revocation_snapshot_ambiguous",
            message="C6X2 requires a fresh local revocation snapshot before cached artifact use.",
            record=record,
        )

    refs_to_check = (
        *record.source_refs,
        *record.evidence_refs,
        *(() if record.verifier_result_ref is None else (record.verifier_result_ref,)),
    )
    if not record.source_refs or not record.evidence_refs or not _c6x2_refs_safe(refs_to_check):
        return _c6x2_cache_refusal(
            status="unsafe",
            refusal_code="cache_refs_missing_or_private",
            message="C6X2 refuses missing, private, raw, or enabling cache refs.",
            record=record,
        )

    if record.risk_tier == "critical":
        return _c6x2_cache_refusal(
            status="unsupported",
            refusal_code="critical_risk_cache_use_blocked",
            message="C6X2 blocks critical-risk cache use in offline or prepared-only mode.",
            record=record,
        )

    if action_intent == "final_commitment" and record.artifact_type in C6X2_ARTIFACTS_NOT_TRANSACTION_AUTHORITY:
        return _c6x2_cache_refusal(
            status="blocked",
            refusal_code="artifact_not_transaction_authority",
            message="C6X2 refuses adapter previews, seller cards, or discovery records as transaction authority.",
            record=record,
        )
    if action_intent == "final_commitment" and record.verifier_result_ref is None:
        return _c6x2_cache_refusal(
            status="blocked",
            refusal_code="stronger_commitment_evidence_missing",
            message=(
                "C6X2 final commitment requests require stronger cached verifier evidence "
                "and remain non-executing."
            ),
            record=record,
        )

    status: PersistentCacheEvaluationStatus = (
        "usable_for_non_binding_cache"
        if action_intent == "non_binding_preview"
        else "prepared_only_for_commitment_boundary"
    )
    allowed_to_prepare = action_intent != "non_binding_preview"
    offline_mode_status = (
        "grantex_available"
        if grantex_available
        else "grantex_unavailable_valid_cache_prepared_only"
    )
    buyer_safe_message = (
        "Cached OACP artifact may support non-binding preview without routing this turn through Grantex."
        if action_intent == "non_binding_preview"
        else "C6X2 can prepare a source-aware handoff from valid cache only; no execution occurs."
    )

    return {
        "evaluated": True,
        "status": status,
        "cache_record_id": record.cache_record_id,
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "authority": record.authority,
        "issuer": record.issuer,
        "scope_kind": record.scope_kind,
        "tenant_id": record.tenant_id,
        "merchant_id": record.merchant_id,
        "seller_agent_id": record.seller_agent_id,
        "buyer_agent_id": record.buyer_agent_id,
        "source_refs": list(record.source_refs),
        "evidence_refs": list(record.evidence_refs),
        "generated_at": record.generated_at,
        "cached_at": record.cached_at,
        "expires_at": record.expires_at,
        "freshness_status": record.freshness_status,
        "revocation_snapshot_status": record.revocation_snapshot_status,
        "revocation_snapshot_observed_at": record.revocation_snapshot_observed_at,
        "ttl_policy_seconds": record.ttl_policy_seconds,
        "risk_tier": record.risk_tier,
        "blocked_capabilities": list(record.blocked_capabilities),
        "unsupported_capabilities": list(record.unsupported_capabilities),
        "verifier_result_ref": record.verifier_result_ref,
        "offline_mode_status": offline_mode_status,
        "allowed_to_preview": True,
        "allowed_to_prepare": allowed_to_prepare,
        "allowed_to_execute": False,
        "prepared_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "commerce_facts_invented": False,
        "grantex_runtime_required": False,
        "buyer_safe_message": buyer_safe_message,
        "seller_safe_message": (
            "Seller-side use remains source-aware and prepared-only; merchant systems remain the operational source of "
            "record."
        ),
    }


def _c6x3_repository_refusal(
    *,
    status: PersistentCacheEvaluationStatus,
    refusal_code: str,
    message: str,
    record: OacpPersistentArtifactCacheRecord | None = None,
) -> dict[str, Any]:
    return {
        "stored": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
        "cache_record_id": None if record is None else record.cache_record_id,
        "artifact_id": None if record is None else record.artifact_id,
        "allowed_to_execute": False,
        "repository_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6x3_record_safe_for_repository(record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
    if not record.cache_record_id or not record.artifact_id or not record.authority or not record.issuer:
        return _c6x3_repository_refusal(
            status="blocked",
            refusal_code="cache_record_identity_missing",
            message="C6X3 repository records require local id, artifact id, authority, and issuer.",
            record=record,
        )
    if not _c6x2_scope_present(record):
        return _c6x3_repository_refusal(
            status="blocked",
            refusal_code="cache_scope_missing",
            message="C6X3 repository records require the expected tenant, merchant, seller, or buyer scope.",
            record=record,
        )
    if not _c6x2_non_enablement_flags_intact(record):
        return _c6x3_repository_refusal(
            status="unsafe",
            refusal_code="non_enablement_flags_missing",
            message="C6X3 repository refuses records with executable or enabling flags.",
            record=record,
        )
    refs_to_check = (
        *record.source_refs,
        *record.evidence_refs,
        *(() if record.verifier_result_ref is None else (record.verifier_result_ref,)),
    )
    if not record.source_refs or not record.evidence_refs or not _c6x2_refs_safe(refs_to_check):
        return _c6x3_repository_refusal(
            status="unsafe",
            refusal_code="cache_refs_missing_or_private",
            message="C6X3 repository refuses missing, private, raw, or enabling refs.",
            record=record,
        )
    return {
        "stored": True,
        "status": "stored",
        "cache_record_id": record.cache_record_id,
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "scope_kind": record.scope_kind,
        "allowed_to_execute": False,
        "repository_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6x3_query_matches(
    record: OacpPersistentArtifactCacheRecord,
    query: OacpArtifactCacheRepositoryQuery,
) -> bool:
    return (
        (query.scope_kind is None or record.scope_kind == query.scope_kind)
        and (query.tenant_id is None or record.tenant_id == query.tenant_id)
        and (query.merchant_id is None or record.merchant_id == query.merchant_id)
        and (query.seller_agent_id is None or record.seller_agent_id == query.seller_agent_id)
        and (query.buyer_agent_id is None or record.buyer_agent_id == query.buyer_agent_id)
        and (query.artifact_type is None or record.artifact_type == query.artifact_type)
        and (query.authority is None or record.authority == query.authority)
    )


class InMemoryOacpArtifactCacheRepository:
    """Internal test adapter for the cache repository port; it is not durable storage."""

    def __init__(self) -> None:
        self._records: dict[str, OacpPersistentArtifactCacheRecord] = {}

    def upsert(self, record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
        store_result = _c6x3_record_safe_for_repository(record)
        if store_result["stored"] is not True:
            return store_result
        self._records[record.cache_record_id] = record
        return store_result

    def get(self, cache_record_id: str) -> OacpPersistentArtifactCacheRecord | None:
        return self._records.get(cache_record_id)

    def list_for_scope(self, query: OacpArtifactCacheRepositoryQuery) -> tuple[OacpPersistentArtifactCacheRecord, ...]:
        return tuple(
            sorted(
                (record for record in self._records.values() if _c6x3_query_matches(record, query)),
                key=lambda record: record.cache_record_id,
            )
        )

    def evaluate(
        self,
        *,
        cache_record_id: str,
        action_intent: PersistentCacheActionIntent,
        now_iso: str,
        grantex_available: bool,
        expected_scope: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        return evaluate_oacp_persistent_artifact_cache_record(
            record=self.get(cache_record_id),
            action_intent=action_intent,
            now_iso=now_iso,
            grantex_available=grantex_available,
            expected_scope=expected_scope,
        )


def _c6x4_durable_repository_refusal(
    *,
    status: PersistentCacheEvaluationStatus,
    refusal_code: str,
    message: str,
    record: OacpPersistentArtifactCacheRecord | None = None,
) -> dict[str, Any]:
    return {
        "stored": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
        "cache_record_id": None if record is None else record.cache_record_id,
        "artifact_id": None if record is None else record.artifact_id,
        "durable_repository": True,
        "allowed_to_execute": False,
        "repository_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6x4_durable_record_safe_for_storage(record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
    base_result = _c6x3_record_safe_for_repository(record)
    if base_result["stored"] is not True:
        result = dict(base_result)
        result["durable_repository"] = True
        return result

    generated_at = _parse_iso(record.generated_at)
    cached_at = _parse_iso(record.cached_at)
    expires_at = _parse_iso(record.expires_at)
    revocation_observed_at = _parse_iso(record.revocation_snapshot_observed_at)
    if (
        generated_at is None
        or cached_at is None
        or expires_at is None
        or revocation_observed_at is None
        or generated_at > cached_at
        or cached_at >= expires_at
    ):
        return _c6x4_durable_repository_refusal(
            status="stale",
            refusal_code="cache_timestamps_invalid",
            message="C6X4 durable cache records require ordered generated, cached, revocation, and expiry timestamps.",
            record=record,
        )

    if record.ttl_policy_seconds <= 0 or record.ttl_policy_seconds > OACP_ARTIFACT_TTLS_SECONDS[record.artifact_type]:
        return _c6x4_durable_repository_refusal(
            status="stale",
            refusal_code="cache_ttl_policy_invalid",
            message="C6X4 durable cache records require a positive TTL not exceeding the artifact family default.",
            record=record,
        )
    if record.freshness_status not in {"fresh", "provisional"}:
        return _c6x4_durable_repository_refusal(
            status="stale",
            refusal_code="cache_freshness_stale",
            message="C6X4 durable cache records must be fresh or provisional at storage time.",
            record=record,
        )
    if record.revocation_snapshot_status != "fresh" or record.revocation_snapshot_age_seconds is None:
        return _c6x4_durable_repository_refusal(
            status="stale",
            refusal_code="cache_revocation_snapshot_ambiguous",
            message="C6X4 durable cache records require a fresh local revocation snapshot.",
            record=record,
        )

    return {
        **base_result,
        "durable_repository": True,
    }


def _c6x4_record_ttl_policy(record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
    return {
        "ttl_policy_seconds": record.ttl_policy_seconds,
        "artifact_family": record.artifact_type,
        "source": "grantex_oacp_cached_artifact_verifier_result",
    }


def _c6x4_row_to_record(row: Any) -> OacpPersistentArtifactCacheRecord:
    return OacpPersistentArtifactCacheRecord(
        cache_record_id=str(row.cache_record_id),
        artifact_id=str(row.artifact_id),
        artifact_type=cast(ArtifactType, row.artifact_type),
        authority=str(row.authority),
        issuer=str(row.issuer),
        scope_kind=cast(PersistentCacheScopeKind, row.scope_kind),
        tenant_id=row.tenant_id,
        merchant_id=row.merchant_id,
        seller_agent_id=row.seller_agent_id,
        buyer_agent_id=row.buyer_agent_id,
        source_refs=tuple(row.source_refs or ()),
        evidence_refs=tuple(row.evidence_refs or ()),
        generated_at=row.generated_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        cached_at=row.cached_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        expires_at=row.expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        freshness_status=str(row.freshness_status),
        revocation_snapshot_status=str(row.revocation_snapshot_status),
        revocation_snapshot_observed_at=(
            None
            if row.revocation_snapshot_observed_at is None
            else row.revocation_snapshot_observed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ),
        ttl_policy_seconds=int(row.ttl_policy_seconds),
        risk_tier=cast(RiskTier, row.risk_tier),
        blocked_capabilities=tuple(row.blocked_capabilities or ()),
        unsupported_capabilities=tuple(row.unsupported_capabilities or ()),
        verifier_result_ref=row.verifier_result_ref,
        revocation_snapshot_age_seconds=row.revocation_snapshot_age_seconds,
        allowed_to_execute=bool(row.allowed_to_execute),
        non_authoritative_for_transaction=bool(row.non_authoritative_for_transaction),
        no_checkout_payment_enablement=bool(row.no_checkout_payment_enablement),
        no_live_provider_enablement=bool(row.no_live_provider_enablement),
        no_public_discovery_enablement=bool(row.no_public_discovery_enablement),
    )


def _c6x4_apply_record_to_row(row: Any, record: OacpPersistentArtifactCacheRecord) -> None:
    row.artifact_id = record.artifact_id
    row.artifact_type = record.artifact_type
    row.artifact_family = record.artifact_type
    row.authority = record.authority
    row.issuer = record.issuer
    row.scope_kind = record.scope_kind
    row.tenant_id = record.tenant_id
    row.merchant_id = record.merchant_id
    row.seller_agent_id = record.seller_agent_id
    row.buyer_agent_id = record.buyer_agent_id
    row.source_refs = list(record.source_refs)
    row.evidence_refs = list(record.evidence_refs)
    row.generated_at = _parse_iso(record.generated_at)
    row.issued_at = _parse_iso(record.generated_at)
    row.cached_at = _parse_iso(record.cached_at)
    row.expires_at = _parse_iso(record.expires_at)
    row.freshness_status = record.freshness_status
    row.revocation_snapshot_status = record.revocation_snapshot_status
    row.revocation_snapshot_age_seconds = record.revocation_snapshot_age_seconds
    row.revocation_snapshot_observed_at = _parse_iso(record.revocation_snapshot_observed_at)
    row.ttl_policy = _c6x4_record_ttl_policy(record)
    row.ttl_policy_seconds = record.ttl_policy_seconds
    row.risk_tier = record.risk_tier
    row.blocked_capabilities = list(record.blocked_capabilities)
    row.unsupported_capabilities = list(record.unsupported_capabilities)
    row.verifier_result_ref = record.verifier_result_ref
    row.allowed_to_execute = False
    row.non_authoritative_for_transaction = True
    row.no_checkout_payment_enablement = True
    row.no_live_provider_enablement = True
    row.no_public_discovery_enablement = True


class DurableOacpArtifactCacheRepository:
    """Async SQLAlchemy-backed OACP cache repository; it performs no external calls."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, record: OacpPersistentArtifactCacheRecord) -> dict[str, Any]:
        from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow

        store_result = _c6x4_durable_record_safe_for_storage(record)
        if store_result["stored"] is not True:
            return store_result

        row = await self._session.get(OacpArtifactCacheRecordRow, record.cache_record_id)
        if row is None:
            row = OacpArtifactCacheRecordRow(cache_record_id=record.cache_record_id)
            self._session.add(row)
        _c6x4_apply_record_to_row(row, record)
        await self._session.flush()
        return store_result

    async def get(self, cache_record_id: str) -> OacpPersistentArtifactCacheRecord | None:
        from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow

        row = await self._session.get(OacpArtifactCacheRecordRow, cache_record_id)
        return None if row is None else _c6x4_row_to_record(row)

    async def list_for_scope(
        self,
        query: OacpArtifactCacheRepositoryQuery,
    ) -> tuple[OacpPersistentArtifactCacheRecord, ...]:
        from sqlalchemy import select

        from core.models.oacp_artifact_cache import OacpArtifactCacheRecordRow

        statement = select(OacpArtifactCacheRecordRow)
        if query.scope_kind is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.scope_kind == query.scope_kind)
        if query.tenant_id is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.tenant_id == query.tenant_id)
        if query.merchant_id is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.merchant_id == query.merchant_id)
        if query.seller_agent_id is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.seller_agent_id == query.seller_agent_id)
        if query.buyer_agent_id is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.buyer_agent_id == query.buyer_agent_id)
        if query.artifact_type is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.artifact_type == query.artifact_type)
        if query.authority is not None:
            statement = statement.where(OacpArtifactCacheRecordRow.authority == query.authority)

        rows = (await self._session.scalars(statement.order_by(OacpArtifactCacheRecordRow.cache_record_id))).all()
        records = tuple(_c6x4_row_to_record(row) for row in rows)
        return tuple(record for record in records if _c6x3_query_matches(record, query))

    async def evaluate(
        self,
        *,
        cache_record_id: str,
        action_intent: PersistentCacheActionIntent,
        now_iso: str,
        grantex_available: bool,
        expected_scope: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        return evaluate_oacp_persistent_artifact_cache_record(
            record=await self.get(cache_record_id),
            action_intent=action_intent,
            now_iso=now_iso,
            grantex_available=grantex_available,
            expected_scope=expected_scope,
        )


_C6X5_RISK_RANK: dict[RiskTier, int] = {
    "informational": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
_C6X5_COMMITMENT_REVOCATION_MAX_AGE_SECONDS = 2 * 60


def _c6x5_safe_public_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value for value in values if _c6x2_refs_safe((value,)))


def _c6x5_remaining_ttl_seconds(record: OacpPersistentArtifactCacheRecord, now: datetime | None) -> int | None:
    expires_at = _parse_iso(record.expires_at)
    if now is None or expires_at is None:
        return None
    return max(0, int((expires_at - now).total_seconds()))


def _c6x5_refresh_recommended(record: OacpPersistentArtifactCacheRecord, now: datetime | None) -> bool:
    remaining = _c6x5_remaining_ttl_seconds(record, now)
    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[record.risk_tier]
    ttl_refresh_window = max(60, int(record.ttl_policy_seconds * 0.2))
    revocation_refresh_window = None if max_revocation_age is None else int(max_revocation_age * 0.8)
    return (
        record.freshness_status == "provisional"
        or remaining is None
        or remaining <= ttl_refresh_window
        or (
            revocation_refresh_window is not None
            and record.revocation_snapshot_age_seconds is not None
            and record.revocation_snapshot_age_seconds >= revocation_refresh_window
        )
    )


def _c6x5_record_action(
    *,
    record: OacpPersistentArtifactCacheRecord,
    outcome: OacpCacheMaintenanceOutcome,
    reason_codes: tuple[str, ...],
    action_intent: PersistentCacheActionIntent,
    grantex_available: bool,
    now: datetime | None,
) -> dict[str, Any]:
    return {
        "cache_record_id": record.cache_record_id,
        "artifact_id": record.artifact_id,
        "artifact_type": record.artifact_type,
        "scope_kind": record.scope_kind,
        "tenant_id": record.tenant_id,
        "merchant_id": record.merchant_id,
        "seller_agent_id": record.seller_agent_id,
        "buyer_agent_id": record.buyer_agent_id,
        "maintenance_outcome": outcome,
        "reason_codes": list(reason_codes),
        "action_intent": action_intent,
        "risk_tier": record.risk_tier,
        "source_refs": list(_c6x5_safe_public_refs(record.source_refs)),
        "evidence_refs": list(_c6x5_safe_public_refs(record.evidence_refs)),
        "verifier_result_ref": (
            None
            if record.verifier_result_ref is None or not _c6x2_refs_safe((record.verifier_result_ref,))
            else record.verifier_result_ref
        ),
        "expires_at": record.expires_at,
        "remaining_ttl_seconds": _c6x5_remaining_ttl_seconds(record, now),
        "freshness_status": record.freshness_status,
        "revocation_snapshot_status": record.revocation_snapshot_status,
        "revocation_snapshot_age_seconds": record.revocation_snapshot_age_seconds,
        "blocked_capabilities": list(record.blocked_capabilities),
        "unsupported_capabilities": list(record.unsupported_capabilities),
        "grantex_available": grantex_available,
        "allowed_to_execute": False,
        "maintenance_plan_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
    }


def _c6x5_classify_record(
    *,
    record: OacpPersistentArtifactCacheRecord,
    now: datetime | None,
    grantex_available: bool,
    action_intent: PersistentCacheActionIntent,
    risk_tier: RiskTier,
    scope_filter: OacpArtifactCacheRepositoryQuery | None,
) -> dict[str, Any]:
    if not record.cache_record_id or not record.artifact_id or not record.authority or not record.issuer:
        return _c6x5_record_action(
            record=record,
            outcome="blocked_unsafe",
            reason_codes=("cache_record_identity_missing",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if not _c6x2_scope_present(record):
        return _c6x5_record_action(
            record=record,
            outcome="blocked_unsafe",
            reason_codes=("cache_scope_missing",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if scope_filter is not None and not _c6x3_query_matches(record, scope_filter):
        return _c6x5_record_action(
            record=record,
            outcome="quarantine_scope_mismatch",
            reason_codes=("cache_scope_mismatch",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if not _c6x2_non_enablement_flags_intact(record):
        return _c6x5_record_action(
            record=record,
            outcome="blocked_unsafe",
            reason_codes=("non_enablement_flags_missing",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    refs_to_check = (
        *record.source_refs,
        *record.evidence_refs,
        *(() if record.verifier_result_ref is None else (record.verifier_result_ref,)),
    )
    if not record.source_refs or not record.evidence_refs or not _c6x2_refs_safe(refs_to_check):
        return _c6x5_record_action(
            record=record,
            outcome="quarantine_private_or_raw_ref",
            reason_codes=("cache_refs_missing_or_private",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    generated_at = _parse_iso(record.generated_at)
    cached_at = _parse_iso(record.cached_at)
    expires_at = _parse_iso(record.expires_at)
    if now is None or generated_at is None or cached_at is None or expires_at is None or generated_at > cached_at:
        return _c6x5_record_action(
            record=record,
            outcome="source_refresh_needed",
            reason_codes=("cache_freshness_missing",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if now >= expires_at:
        return _c6x5_record_action(
            record=record,
            outcome="evict_expired",
            reason_codes=("cache_record_expired",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if record.ttl_policy_seconds <= 0 or record.ttl_policy_seconds > OACP_ARTIFACT_TTLS_SECONDS[record.artifact_type]:
        return _c6x5_record_action(
            record=record,
            outcome="source_refresh_needed",
            reason_codes=("cache_ttl_policy_invalid",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if record.freshness_status not in {"fresh", "provisional"}:
        return _c6x5_record_action(
            record=record,
            outcome="source_refresh_needed",
            reason_codes=("cache_freshness_stale",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    if record.revocation_snapshot_status == "revoked":
        return _c6x5_record_action(
            record=record,
            outcome="purge_revoked",
            reason_codes=("cache_record_revoked",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    max_revocation_age = OACP_REVOCATION_SNAPSHOT_MAX_AGE_SECONDS[record.risk_tier]
    if (
        max_revocation_age is None
        or record.revocation_snapshot_status != "fresh"
        or record.revocation_snapshot_observed_at is None
        or _parse_iso(record.revocation_snapshot_observed_at) is None
        or record.revocation_snapshot_age_seconds is None
        or record.revocation_snapshot_age_seconds > max_revocation_age
    ):
        return _c6x5_record_action(
            record=record,
            outcome="quarantine_ambiguous_revocation",
            reason_codes=("cache_revocation_snapshot_ambiguous",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    if _C6X5_RISK_RANK[risk_tier] >= _C6X5_RISK_RANK["critical"] or record.risk_tier == "critical":
        return _c6x5_record_action(
            record=record,
            outcome="blocked_unsafe",
            reason_codes=("critical_risk_cache_use_blocked",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    if action_intent == "final_commitment":
        if record.artifact_type in C6X2_ARTIFACTS_NOT_TRANSACTION_AUTHORITY:
            return _c6x5_record_action(
                record=record,
                outcome="human_review_required",
                reason_codes=("artifact_not_transaction_authority",),
                action_intent=action_intent,
                grantex_available=grantex_available,
                now=now,
            )
        if (
            record.freshness_status != "fresh"
            or record.revocation_snapshot_age_seconds > _C6X5_COMMITMENT_REVOCATION_MAX_AGE_SECONDS
            or _C6X5_RISK_RANK[record.risk_tier] < _C6X5_RISK_RANK[risk_tier]
            or not grantex_available
            or _c6x5_refresh_recommended(record, now)
        ):
            return _c6x5_record_action(
                record=record,
                outcome="refresh_required_before_commitment",
                reason_codes=("final_commitment_requires_fresh_source_posture",),
                action_intent=action_intent,
                grantex_available=grantex_available,
                now=now,
            )

    if action_intent == "prepare_only" and not grantex_available and record.freshness_status == "provisional":
        return _c6x5_record_action(
            record=record,
            outcome="source_refresh_needed",
            reason_codes=("prepared_flow_needs_source_refresh",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )
    if _c6x5_refresh_recommended(record, now):
        return _c6x5_record_action(
            record=record,
            outcome="refresh_recommended",
            reason_codes=("ttl_or_revocation_refresh_window_reached",),
            action_intent=action_intent,
            grantex_available=grantex_available,
            now=now,
        )

    return _c6x5_record_action(
        record=record,
        outcome="keep_usable",
        reason_codes=("cache_record_usable_for_requested_intent",),
        action_intent=action_intent,
        grantex_available=grantex_available,
        now=now,
    )


def _c6x5_ids_for_outcomes(actions: Sequence[dict[str, Any]], outcomes: set[str]) -> list[str]:
    return [str(action["cache_record_id"]) for action in actions if action["maintenance_outcome"] in outcomes]


def plan_oacp_artifact_cache_maintenance(
    *,
    records: Sequence[OacpPersistentArtifactCacheRecord],
    now_iso: str,
    grantex_available: bool,
    action_intent: PersistentCacheActionIntent,
    risk_tier: RiskTier,
    max_batch_size: int | None = None,
    scope_filter: OacpArtifactCacheRepositoryQuery | None = None,
) -> dict[str, Any]:
    selected_records = tuple(records[: max(0, max_batch_size)] if max_batch_size is not None else records)
    now = _parse_iso(now_iso)
    actions = tuple(
        _c6x5_classify_record(
            record=record,
            now=now,
            grantex_available=grantex_available,
            action_intent=action_intent,
            risk_tier=risk_tier,
            scope_filter=scope_filter,
        )
        for record in selected_records
    )
    evidence_refs = sorted({ref for action in actions for ref in action["evidence_refs"]})
    source_refs = sorted({ref for action in actions for ref in action["source_refs"]})
    plan_payload = {
        "generated_at": now_iso,
        "action_intent": action_intent,
        "risk_tier": risk_tier,
        "grantex_available": grantex_available,
        "records": [(action["cache_record_id"], action["maintenance_outcome"]) for action in actions],
    }
    digest = hashlib.sha256(canonicalize_oacp_payload(plan_payload).encode("utf-8")).hexdigest()
    return {
        "plan_id": f"oacp_c6x5_maintenance_plan_{digest[:20]}",
        "generated_at": now_iso,
        "action_intent": action_intent,
        "risk_tier": risk_tier,
        "grantex_available": grantex_available,
        "records_seen": len(selected_records),
        "records_kept": _c6x5_ids_for_outcomes(actions, {"keep_usable"}),
        "records_to_refresh": _c6x5_ids_for_outcomes(
            actions,
            {"refresh_recommended", "refresh_required_before_commitment", "source_refresh_needed"},
        ),
        "records_to_evict": _c6x5_ids_for_outcomes(actions, {"evict_expired", "purge_revoked"}),
        "records_to_quarantine": _c6x5_ids_for_outcomes(
            actions,
            {
                "quarantine_ambiguous_revocation",
                "quarantine_scope_mismatch",
                "quarantine_private_or_raw_ref",
                "blocked_unsafe",
            },
        ),
        "records_requiring_human_review": _c6x5_ids_for_outcomes(actions, {"human_review_required"}),
        "per_record_reason_codes": {
            str(action["cache_record_id"]): list(action["reason_codes"]) for action in actions
        },
        "record_actions": list(actions),
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
        "allowed_to_execute": False,
        "no_execution": True,
        "maintenance_plan_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
        "buyer_safe_message": (
            "C6X5 planned cache maintenance only. Non-binding cache use may continue only for valid local records."
        ),
        "seller_safe_message": (
            "C6X5 does not refresh, evict, schedule, call Grantex, or call merchant or provider systems."
        ),
    }


_C6X6_REPORT_KINDS: frozenset[str] = frozenset(
    {
        "cache_maintenance_dry_run_report",
        "operator_review_packet",
        "blocked_cache_action_report",
        "stale_or_revoked_artifact_summary",
        "source_refresh_request_preview",
    }
)
_C6X6_KNOWN_OUTCOMES: frozenset[str] = frozenset(
    {
        "keep_usable",
        "refresh_recommended",
        "refresh_required_before_commitment",
        "evict_expired",
        "purge_revoked",
        "quarantine_ambiguous_revocation",
        "quarantine_scope_mismatch",
        "quarantine_private_or_raw_ref",
        "source_refresh_needed",
        "human_review_required",
        "blocked_unsafe",
    }
)
_C6X6_SAFE_FALLBACK_GENERATED_AT = "1970-01-01T00:00:00.000Z"


def _c6x6_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _c6x6_string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _c6x6_report_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonicalize_oacp_payload(dict(payload)).encode("utf-8")).hexdigest()
    return f"oacp_c6x6_cache_report_{digest[:20]}"


def _c6x6_count_by(actions: Sequence[Mapping[str, Any]], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        value = _c6x6_string(action.get(field_name))
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _c6x6_scope_summary(actions: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    scope_fields = {
        "buyer_agent": "buyer_agent_id",
        "seller_agent": "seller_agent_id",
        "tenant": "tenant_id",
        "merchant": "merchant_id",
    }
    return {scope_name: _c6x6_count_by(actions, field_name) for scope_name, field_name in scope_fields.items()}


def _c6x6_safe_reason_codes(value: Any) -> list[str]:
    codes = _c6x6_string_list(value)
    return [code for code in codes if all(char.isalnum() or char == "_" for char in code)]


def _c6x6_plan_flags_safe(plan: Mapping[str, Any]) -> bool:
    return (
        plan.get("allowed_to_execute") is False
        and plan.get("no_execution") is True
        and plan.get("maintenance_plan_only") is True
        and plan.get("non_authoritative_for_transaction") is True
        and plan.get("no_checkout_payment_enablement") is True
        and plan.get("no_live_provider_enablement") is True
        and plan.get("no_public_discovery_enablement") is True
    )


def _c6x6_action_flags_safe(action: Mapping[str, Any]) -> bool:
    return (
        action.get("allowed_to_execute") is False
        and action.get("maintenance_plan_only") is True
        and action.get("non_authoritative_for_transaction") is True
        and action.get("no_checkout_payment_enablement") is True
        and action.get("no_live_provider_enablement") is True
        and action.get("no_public_discovery_enablement") is True
    )


def _c6x6_refs_from_plan(plan: Mapping[str, Any], actions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(_c6x6_string_list(plan.get("source_refs")))
    refs.extend(_c6x6_string_list(plan.get("evidence_refs")))
    for action in actions:
        refs.extend(_c6x6_string_list(action.get("source_refs")))
        refs.extend(_c6x6_string_list(action.get("evidence_refs")))
        verifier_result_ref = _c6x6_string(action.get("verifier_result_ref"))
        if verifier_result_ref is not None:
            refs.append(verifier_result_ref)
    return tuple(refs)


def _c6x6_plan_refs_safe(plan: Mapping[str, Any], actions: Sequence[Mapping[str, Any]]) -> bool:
    refs = _c6x6_refs_from_plan(plan, actions)
    return not refs or _c6x2_refs_safe(refs)


def _c6x6_actions_safe(actions: Sequence[Mapping[str, Any]]) -> bool:
    for action in actions:
        outcome = _c6x6_string(action.get("maintenance_outcome"))
        if outcome not in _C6X6_KNOWN_OUTCOMES:
            return False
        if not _c6x6_string(action.get("cache_record_id")):
            return False
        if not _c6x6_action_flags_safe(action):
            return False
        if len(_c6x6_safe_reason_codes(action.get("reason_codes"))) != len(
            _c6x6_string_list(action.get("reason_codes"))
        ):
            return False
    return True


def _c6x6_blocked_report(
    *,
    report_kind: str,
    reason_code: str,
    message: str,
    maintenance_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = (
        _c6x6_string(maintenance_plan.get("generated_at")) if maintenance_plan is not None else None
    ) or _C6X6_SAFE_FALLBACK_GENERATED_AT
    source_plan_id = _c6x6_string(maintenance_plan.get("plan_id")) if maintenance_plan is not None else None
    payload = {
        "report_kind": report_kind,
        "reason_code": reason_code,
        "source_plan_id": source_plan_id,
        "generated_at": generated_at,
    }
    return {
        "report_id": _c6x6_report_id(payload),
        "report_kind": "blocked_cache_action_report",
        "requested_report_kind": report_kind,
        "source_plan_id": source_plan_id,
        "generated_at": generated_at,
        "status": "blocked",
        "block_reason": reason_code,
        "buyer_safe_message": message,
        "operator_safe_message": message,
        "scope_summary": {"buyer_agent": {}, "seller_agent": {}, "tenant": {}, "merchant": {}},
        "artifact_family_counts": {},
        "records_seen": 0,
        "records_kept": [],
        "records_to_refresh": [],
        "records_to_evict": [],
        "records_to_quarantine": [],
        "records_requiring_human_review": [],
        "per_record_reason_codes": {},
        "source_refs": [],
        "evidence_refs": [],
        "freshness_summary": {},
        "ttl_summary": {"records_with_ttl": 0, "minimum_remaining_ttl_seconds": None},
        "revocation_snapshot_summary": {},
        "risk_tier_summary": {},
        "unsupported_capability_summary": {},
        "blocked_capability_summary": {reason_code: 1},
        "next_step_labels": ["operator_review_required_no_execution"],
        "source_refresh_request_preview": {
            "preview_only": True,
            "next_system_step_label": "operator_review_required_no_api_call",
            "records": [],
        },
        "allowed_to_execute": False,
        "no_execution": True,
        "dry_run_report_only": True,
        "operator_review_only": True,
        "maintenance_report_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
    }


def build_oacp_cache_maintenance_dry_run_report(
    *,
    maintenance_plan: Mapping[str, Any] | None,
    report_kind: OacpCacheMaintenanceReportKind = "cache_maintenance_dry_run_report",
) -> dict[str, Any]:
    if report_kind not in _C6X6_REPORT_KINDS:
        return _c6x6_blocked_report(
            report_kind=str(report_kind),
            reason_code="unsupported_report_kind",
            message="C6X6 only prepares known internal maintenance dry-run reports.",
            maintenance_plan=maintenance_plan,
        )
    if maintenance_plan is None:
        return _c6x6_blocked_report(
            report_kind=report_kind,
            reason_code="maintenance_plan_missing",
            message="C6X6 requires a C6X5 maintenance plan before preparing an operator report.",
        )
    try:
        assert_no_forbidden_oacp_artifact_fields(maintenance_plan)
    except ValueError:
        return _c6x6_blocked_report(
            report_kind=report_kind,
            reason_code="private_or_enabling_plan_field",
            message="C6X6 refuses maintenance plans with private, raw, or enabling fields.",
            maintenance_plan=maintenance_plan,
        )

    source_plan_id = _c6x6_string(maintenance_plan.get("plan_id"))
    generated_at = _c6x6_string(maintenance_plan.get("generated_at"))
    raw_actions = maintenance_plan.get("record_actions")
    if source_plan_id is None or generated_at is None or not isinstance(raw_actions, list):
        return _c6x6_blocked_report(
            report_kind=report_kind,
            reason_code="maintenance_plan_malformed",
            message="C6X6 requires plan id, generated time, and record actions.",
            maintenance_plan=maintenance_plan,
        )
    actions: list[Mapping[str, Any]] = []
    for action in raw_actions:
        if not isinstance(action, Mapping):
            return _c6x6_blocked_report(
                report_kind=report_kind,
                reason_code="maintenance_plan_malformed",
                message="C6X6 requires each record action to be a structured map.",
                maintenance_plan=maintenance_plan,
            )
        actions.append(action)
    if not _c6x6_plan_flags_safe(maintenance_plan):
        return _c6x6_blocked_report(
            report_kind=report_kind,
            reason_code="maintenance_plan_executable_or_enabling",
            message="C6X6 refuses executable or enabling maintenance plans.",
            maintenance_plan=maintenance_plan,
        )
    if not _c6x6_actions_safe(actions) or not _c6x6_plan_refs_safe(maintenance_plan, actions):
        return _c6x6_blocked_report(
            report_kind=report_kind,
            reason_code="maintenance_plan_private_or_unsafe",
            message="C6X6 refuses unsafe maintenance actions or private/raw refs.",
            maintenance_plan=maintenance_plan,
        )

    records_to_refresh = _c6x6_string_list(maintenance_plan.get("records_to_refresh"))
    records_to_evict = _c6x6_string_list(maintenance_plan.get("records_to_evict"))
    records_to_quarantine = _c6x6_string_list(maintenance_plan.get("records_to_quarantine"))
    records_requiring_human_review = _c6x6_string_list(maintenance_plan.get("records_requiring_human_review"))
    next_step_labels = ["review_report_no_execution"]
    if records_to_refresh:
        next_step_labels.append("prepare_source_refresh_request_preview")
    if records_to_evict:
        next_step_labels.append("operator_review_evict_or_purge_label")
    if records_to_quarantine:
        next_step_labels.append("operator_review_quarantine_label")
    if records_requiring_human_review:
        next_step_labels.append("human_review_required_label")
    if len(next_step_labels) == 1:
        next_step_labels.append("no_action_for_valid_cache_records")

    ttl_values = [
        action["remaining_ttl_seconds"]
        for action in actions
        if type(action.get("remaining_ttl_seconds")) is int
    ]
    per_record_reason_codes = {
        str(action["cache_record_id"]): _c6x6_safe_reason_codes(action.get("reason_codes")) for action in actions
    }
    source_refs = sorted(set(_c6x6_string_list(maintenance_plan.get("source_refs"))))
    evidence_refs = sorted(set(_c6x6_string_list(maintenance_plan.get("evidence_refs"))))
    unsupported_capabilities = [
        capability for action in actions for capability in _c6x6_string_list(action.get("unsupported_capabilities"))
    ]
    blocked_capabilities = [
        capability for action in actions for capability in _c6x6_string_list(action.get("blocked_capabilities"))
    ]
    report_payload = {
        "report_kind": report_kind,
        "source_plan_id": source_plan_id,
        "generated_at": generated_at,
        "records": [(action["cache_record_id"], action["maintenance_outcome"]) for action in actions],
    }
    return {
        "report_id": _c6x6_report_id(report_payload),
        "report_kind": report_kind,
        "source_plan_id": source_plan_id,
        "generated_at": generated_at,
        "status": "prepared_for_operator_review",
        "scope_summary": _c6x6_scope_summary(actions),
        "artifact_family_counts": _c6x6_count_by(actions, "artifact_type"),
        "records_seen": maintenance_plan.get("records_seen", len(actions)),
        "records_kept": _c6x6_string_list(maintenance_plan.get("records_kept")),
        "records_to_refresh": records_to_refresh,
        "records_to_evict": records_to_evict,
        "records_to_quarantine": records_to_quarantine,
        "records_requiring_human_review": records_requiring_human_review,
        "per_record_reason_codes": per_record_reason_codes,
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
        "freshness_summary": _c6x6_count_by(actions, "freshness_status"),
        "ttl_summary": {
            "records_with_ttl": len(ttl_values),
            "minimum_remaining_ttl_seconds": min(ttl_values) if ttl_values else None,
        },
        "revocation_snapshot_summary": _c6x6_count_by(actions, "revocation_snapshot_status"),
        "risk_tier_summary": _c6x6_count_by(actions, "risk_tier"),
        "unsupported_capability_summary": dict(
            sorted((item, unsupported_capabilities.count(item)) for item in set(unsupported_capabilities))
        ),
        "blocked_capability_summary": dict(
            sorted((item, blocked_capabilities.count(item)) for item in set(blocked_capabilities))
        ),
        "next_step_labels": next_step_labels,
        "source_refresh_request_preview": {
            "preview_only": True,
            "next_system_step_label": "source_refresh_request_label_only_no_api_call",
            "records": records_to_refresh,
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
        },
        "buyer_safe_message": "C6X6 prepared a cache maintenance dry-run report only; no cache action was executed.",
        "seller_safe_message": (
            "C6X6 produced label-only review output and did not call source, provider, or merchant systems."
        ),
        "operator_safe_message": (
            "Review the report labels and redacted refs before any separately approved maintenance action."
        ),
        "allowed_to_execute": False,
        "no_execution": True,
        "dry_run_report_only": True,
        "operator_review_only": True,
        "maintenance_report_only": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


_C6X7_DECISION_KINDS: frozenset[str] = frozenset(
    {
        "approve_future_refresh_request",
        "approve_future_eviction_request",
        "approve_future_quarantine_request",
        "request_more_evidence",
        "reject_maintenance_action",
        "defer_until_freshness_update",
        "escalate_to_human_support",
        "block_unsafe_action",
    }
)
_C6X7_DECISION_NEXT_STEP_LABELS: dict[str, str] = {
    "approve_future_refresh_request": "future_refresh_request_label_only_no_api_call",
    "approve_future_eviction_request": "future_eviction_request_label_only_no_api_call",
    "approve_future_quarantine_request": "future_quarantine_request_label_only_no_api_call",
    "request_more_evidence": "request_more_redacted_evidence_no_api_call",
    "reject_maintenance_action": "reject_maintenance_action_no_execution",
    "defer_until_freshness_update": "defer_until_freshness_update_no_execution",
    "escalate_to_human_support": "human_support_review_label_only_no_api_call",
    "block_unsafe_action": "block_unsafe_action_no_execution",
}
_C6X7_SAFE_FALLBACK_DECIDED_AT = "1970-01-01T00:00:00.000Z"
_C6X7_OPAQUE_REVIEWER_PREFIXES = ("operator_ref_", "reviewer_ref_")
_C6X7_REVIEWER_PRIVATE_MARKERS = (
    "@",
    "mailto:",
    "tel:",
    "phone",
    "email",
    "token",
    "jwt",
    "secret",
    "password",
    "credential",
    "private",
    "raw",
)


def _c6x7_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _c6x7_string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _c6x7_decision_record_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonicalize_oacp_payload(dict(payload)).encode("utf-8")).hexdigest()
    return f"oacp_c6x7_operator_decision_{digest[:20]}"


def _c6x7_packet_flags_safe(packet: Mapping[str, Any]) -> bool:
    return (
        packet.get("allowed_to_execute") is False
        and packet.get("no_execution") is True
        and packet.get("maintenance_report_only") is True
        and packet.get("operator_review_only") is True
        and packet.get("non_authoritative_for_transaction") is True
        and packet.get("no_checkout_payment_enablement") is True
        and packet.get("no_live_provider_enablement") is True
        and packet.get("no_public_discovery_enablement") is True
    )


def _c6x7_refs_safe(packet: Mapping[str, Any]) -> bool:
    refs: list[str] = []
    refs.extend(_c6x7_string_list(packet.get("source_refs")))
    refs.extend(_c6x7_string_list(packet.get("evidence_refs")))
    source_refresh_preview = packet.get("source_refresh_request_preview")
    if isinstance(source_refresh_preview, Mapping):
        refs.extend(_c6x7_string_list(source_refresh_preview.get("source_refs")))
        refs.extend(_c6x7_string_list(source_refresh_preview.get("evidence_refs")))
    return bool(refs) and _c6x2_refs_safe(tuple(refs))


def _c6x7_reviewer_ref_safe(reviewer_identity_ref: str | None) -> bool:
    if reviewer_identity_ref is None:
        return False
    normalized = reviewer_identity_ref.strip().lower()
    if not normalized.startswith(_C6X7_OPAQUE_REVIEWER_PREFIXES):
        return False
    if any(marker in normalized for marker in _C6X7_REVIEWER_PRIVATE_MARKERS):
        return False
    return _c6x2_refs_safe((reviewer_identity_ref,))


def _c6x7_packet_malformed(packet: Mapping[str, Any]) -> bool:
    return (
        _c6x7_string(packet.get("report_id")) is None
        or _c6x7_string(packet.get("source_plan_id")) is None
        or _c6x7_string(packet.get("generated_at")) is None
        or not isinstance(packet.get("scope_summary"), Mapping)
        or not isinstance(packet.get("artifact_family_counts"), Mapping)
        or not isinstance(packet.get("per_record_reason_codes"), Mapping)
    )


def _c6x7_risky_state_requires_future_label(
    packet: Mapping[str, Any],
    decision_kind: str,
    next_step_label: str,
) -> bool:
    if not decision_kind.startswith("approve_future_"):
        return True
    revocation_summary = packet.get("revocation_snapshot_summary")
    risk_summary = packet.get("risk_tier_summary")
    risky = False
    if isinstance(revocation_summary, Mapping):
        risky = any(key in revocation_summary for key in ("revoked", "ambiguous", "stale", "expired"))
    if isinstance(risk_summary, Mapping):
        risky = risky or any(key in risk_summary for key in ("high", "critical"))
    if not risky:
        return True
    return "future" in next_step_label or "review" in next_step_label


def _c6x7_blocked_decision_record(
    *,
    requested_decision_kind: str,
    reason_code: str,
    message: str,
    review_packet: Mapping[str, Any] | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    decision_time = (
        decided_at
        or (_c6x7_string(review_packet.get("generated_at")) if review_packet is not None else None)
        or _C6X7_SAFE_FALLBACK_DECIDED_AT
    )
    review_packet_id = _c6x7_string(review_packet.get("report_id")) if review_packet is not None else None
    maintenance_plan_id = _c6x7_string(review_packet.get("source_plan_id")) if review_packet is not None else None
    payload = {
        "decision_kind": "block_unsafe_action",
        "requested_decision_kind": requested_decision_kind,
        "review_packet_id": review_packet_id,
        "maintenance_plan_id": maintenance_plan_id,
        "decided_at": decision_time,
        "reason_code": reason_code,
    }
    return {
        "decision_record_id": _c6x7_decision_record_id(payload),
        "decision_kind": "block_unsafe_action",
        "requested_decision_kind": requested_decision_kind,
        "review_packet_id": review_packet_id,
        "maintenance_plan_id": maintenance_plan_id,
        "generated_at": decision_time,
        "decided_at": decision_time,
        "status": "blocked",
        "block_reason": reason_code,
        "scope_summary": {"buyer_agent": {}, "seller_agent": {}, "tenant": {}, "merchant": {}},
        "artifact_families_affected": [],
        "artifact_family_counts": {},
        "redacted_reason_codes": {},
        "source_refs": [],
        "evidence_refs": [],
        "reviewer_identity_ref": None,
        "next_step_labels": ["block_unsafe_action_no_execution"],
        "buyer_safe_message": message,
        "seller_safe_message": message,
        "operator_safe_message": message,
        "allowed_to_execute": False,
        "no_execution": True,
        "operator_decision_only": True,
        "audit_safe_decision_record": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def build_oacp_cache_operator_decision_record(
    *,
    review_packet: Mapping[str, Any] | None,
    decision_kind: OacpCacheOperatorDecisionKind | str,
    reviewer_identity_ref: str | None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    if decision_kind not in _C6X7_DECISION_KINDS:
        return _c6x7_blocked_decision_record(
            requested_decision_kind=str(decision_kind),
            reason_code="unsupported_or_executable_decision_kind",
            message="C6X7 only records known future-only operator decisions.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if review_packet is None:
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="review_packet_missing",
            message="C6X7 requires a C6X6 operator review packet before recording a decision.",
            decided_at=decided_at,
        )
    try:
        assert_no_forbidden_oacp_artifact_fields(review_packet)
    except ValueError:
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="private_or_enabling_review_packet_field",
            message="C6X7 refuses review packets with private, raw, or enabling fields.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if review_packet.get("report_kind") != "operator_review_packet":
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="review_packet_kind_mismatch",
            message="C6X7 consumes C6X6 operator review packets only.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if _c6x7_packet_malformed(review_packet):
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="review_packet_malformed",
            message="C6X7 requires report id, maintenance plan id, scope, family, and reason summaries.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if not _c6x7_packet_flags_safe(review_packet):
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="review_packet_executable_or_enabling",
            message="C6X7 refuses executable or enabling operator review packets.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if not _c6x7_refs_safe(review_packet):
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="review_packet_private_or_missing_refs",
            message="C6X7 requires redacted source and evidence refs only.",
            review_packet=review_packet,
            decided_at=decided_at,
        )
    if not _c6x7_reviewer_ref_safe(reviewer_identity_ref):
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="reviewer_identity_not_opaque",
            message="C6X7 requires an opaque reviewer reference, not raw contact or credential data.",
            review_packet=review_packet,
            decided_at=decided_at,
        )

    next_step_label = _C6X7_DECISION_NEXT_STEP_LABELS[decision_kind]
    if not _c6x7_risky_state_requires_future_label(review_packet, decision_kind, next_step_label):
        return _c6x7_blocked_decision_record(
            requested_decision_kind=decision_kind,
            reason_code="risky_state_without_future_or_review_label",
            message="C6X7 refuses risky decisions without future-only or review-only labels.",
            review_packet=review_packet,
            decided_at=decided_at,
        )

    review_packet_id = cast(str, review_packet["report_id"])
    maintenance_plan_id = cast(str, review_packet["source_plan_id"])
    decision_time = decided_at or cast(str, review_packet["generated_at"])
    artifact_family_counts = dict(cast(Mapping[str, Any], review_packet["artifact_family_counts"]))
    payload = {
        "decision_kind": decision_kind,
        "review_packet_id": review_packet_id,
        "maintenance_plan_id": maintenance_plan_id,
        "reviewer_identity_ref": reviewer_identity_ref,
        "decided_at": decision_time,
    }
    return {
        "decision_record_id": _c6x7_decision_record_id(payload),
        "decision_kind": decision_kind,
        "review_packet_id": review_packet_id,
        "maintenance_plan_id": maintenance_plan_id,
        "generated_at": decision_time,
        "decided_at": decision_time,
        "status": "recorded_for_future_review",
        "scope_summary": dict(cast(Mapping[str, Any], review_packet["scope_summary"])),
        "artifact_families_affected": sorted(str(key) for key in artifact_family_counts),
        "artifact_family_counts": artifact_family_counts,
        "redacted_reason_codes": dict(cast(Mapping[str, Any], review_packet["per_record_reason_codes"])),
        "source_refs": sorted(set(_c6x7_string_list(review_packet.get("source_refs")))),
        "evidence_refs": sorted(set(_c6x7_string_list(review_packet.get("evidence_refs")))),
        "reviewer_identity_ref": reviewer_identity_ref,
        "next_step_labels": [next_step_label],
        "buyer_safe_message": (
            "C6X7 recorded an operator cache-maintenance decision only; no cache action was executed."
        ),
        "seller_safe_message": (
            "C6X7 does not refresh, evict, quarantine, schedule, call Grantex, or call merchant or provider systems."
        ),
        "operator_safe_message": "Decision recorded as label-only future intent. Execute nothing in C6X7.",
        "allowed_to_execute": False,
        "no_execution": True,
        "operator_decision_only": True,
        "audit_safe_decision_record": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def _c6x8_decision_repository_refusal(
    *,
    status: str,
    refusal_code: str,
    message: str,
    decision_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "stored": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
        "decision_id": None if decision_record is None else _c6x7_string(decision_record.get("decision_record_id")),
        "review_packet_id": None if decision_record is None else _c6x7_string(decision_record.get("review_packet_id")),
        "maintenance_plan_id": (
            None if decision_record is None else _c6x7_string(decision_record.get("maintenance_plan_id"))
        ),
        "durable_repository": True,
        "future_action_allowed": False,
        "allowed_to_execute": False,
        "prepared_only": True,
        "operator_decision_only": True,
        "audit_safe_decision_record": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6x8_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _c6x8_scope_id(scope_summary: Mapping[str, Any], scope_kind: str) -> str | None:
    values = scope_summary.get(scope_kind)
    if not isinstance(values, Mapping) or not values:
        return None
    for key in sorted(str(candidate) for candidate in values if isinstance(candidate, str) and candidate):
        return key
    return None


def _c6x8_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _c6x8_flags_safe(decision_record: Mapping[str, Any]) -> bool:
    return (
        decision_record.get("allowed_to_execute") is False
        and decision_record.get("no_execution") is True
        and decision_record.get("operator_decision_only") is True
        and decision_record.get("audit_safe_decision_record") is True
        and decision_record.get("non_authoritative_for_transaction") is True
        and decision_record.get("no_checkout_payment_enablement") is True
        and decision_record.get("no_live_provider_enablement") is True
        and decision_record.get("no_public_discovery_enablement") is True
    )


def _c6x8_labels_safe(values: tuple[str, ...]) -> bool:
    if not values:
        return False
    unsafe_markers = (
        "execute",
        "payment",
        "provider",
        "public_discovery",
        "live_rail",
        "merchant_private_api",
        "checkout",
        "order",
        "hold",
        "refund",
        "return",
        "shipping",
        "publish",
        "certif",
        "compliance",
        "conformance",
        "standard",
        "readiness",
    )
    safe_markers = ("no_", "future_", "request_", "reject_", "defer_", "block_", "label_only", "review")
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            return False
        if any(marker in normalized for marker in unsafe_markers) and not any(
            marker in normalized for marker in safe_markers
        ):
            return False
    return _c6x2_refs_safe(values)


def _c6x8_decision_safe_for_storage(decision_record: Mapping[str, Any]) -> dict[str, Any]:
    decision_id = _c6x7_string(decision_record.get("decision_record_id"))
    review_packet_id = _c6x7_string(decision_record.get("review_packet_id"))
    maintenance_plan_id = _c6x7_string(decision_record.get("maintenance_plan_id"))
    generated_at = _c6x7_string(decision_record.get("generated_at"))
    decided_at = _c6x7_string(decision_record.get("decided_at"))
    decision_kind = _c6x7_string(decision_record.get("decision_kind"))
    reviewer_ref = _c6x7_string(decision_record.get("reviewer_identity_ref"))
    scope_summary = _c6x8_mapping(decision_record.get("scope_summary"))
    tenant_id = _c6x8_scope_id(scope_summary, "tenant")
    if not all((decision_id, review_packet_id, maintenance_plan_id, generated_at, decided_at, decision_kind)):
        return _c6x8_decision_repository_refusal(
            status="blocked",
            refusal_code="decision_identity_missing",
            message="C6X8 durable decisions require decision, packet, plan, timestamp, and kind identifiers.",
            decision_record=decision_record,
        )
    if decision_kind not in _C6X7_DECISION_KINDS:
        return _c6x8_decision_repository_refusal(
            status="blocked",
            refusal_code="decision_kind_unsupported",
            message="C6X8 stores known C6X7 operator decision kinds only.",
            decision_record=decision_record,
        )
    if not tenant_id:
        return _c6x8_decision_repository_refusal(
            status="blocked",
            refusal_code="tenant_scope_missing",
            message="C6X8 durable decisions require tenant-scoped audit storage.",
            decision_record=decision_record,
        )
    try:
        assert_no_forbidden_oacp_artifact_fields(dict(decision_record))
    except ValueError:
        return _c6x8_decision_repository_refusal(
            status="unsafe",
            refusal_code="private_or_enabling_decision_field",
            message="C6X8 refuses decision records with private, raw, or enabling fields.",
            decision_record=decision_record,
        )
    if not _c6x8_flags_safe(decision_record):
        return _c6x8_decision_repository_refusal(
            status="unsafe",
            refusal_code="non_enablement_flags_missing",
            message="C6X8 refuses executable or enabling operator decision records.",
            decision_record=decision_record,
        )
    refs = (*_c6x8_list(decision_record.get("source_refs")), *_c6x8_list(decision_record.get("evidence_refs")))
    labels = tuple(_c6x8_list(decision_record.get("next_step_labels")))
    if not refs or not _c6x2_refs_safe(refs):
        return _c6x8_decision_repository_refusal(
            status="unsafe",
            refusal_code="decision_refs_missing_or_private",
            message="C6X8 stores redacted source and evidence refs only.",
            decision_record=decision_record,
        )
    if not _c6x8_labels_safe(labels):
        return _c6x8_decision_repository_refusal(
            status="unsafe",
            refusal_code="decision_labels_executable_or_private",
            message="C6X8 stores next-step labels only, not execution targets.",
            decision_record=decision_record,
        )
    if not _c6x7_reviewer_ref_safe(reviewer_ref):
        return _c6x8_decision_repository_refusal(
            status="unsafe",
            refusal_code="reviewer_identity_not_opaque",
            message="C6X8 stores opaque reviewer references only.",
            decision_record=decision_record,
        )
    parsed_generated_at = _parse_iso(generated_at)
    parsed_decided_at = _parse_iso(decided_at)
    if parsed_generated_at is None or parsed_decided_at is None or parsed_generated_at > parsed_decided_at:
        return _c6x8_decision_repository_refusal(
            status="stale",
            refusal_code="decision_timestamps_invalid",
            message="C6X8 durable decisions require ordered generated and decided timestamps.",
            decision_record=decision_record,
        )
    return {
        "stored": True,
        "status": "stored",
        "decision_id": decision_id,
        "review_packet_id": review_packet_id,
        "maintenance_plan_id": maintenance_plan_id,
        "tenant_id": tenant_id,
        "decision_kind": decision_kind,
        "durable_repository": True,
        "future_action_allowed": False,
        "allowed_to_execute": False,
        "prepared_only": True,
        "operator_decision_only": True,
        "audit_safe_decision_record": True,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6x8_apply_decision_to_row(row: Any, decision_record: Mapping[str, Any]) -> None:
    scope_summary = _c6x8_mapping(decision_record.get("scope_summary"))
    row.review_packet_id = cast(str, decision_record["review_packet_id"])
    row.maintenance_plan_id = cast(str, decision_record["maintenance_plan_id"])
    row.generated_at = _parse_iso(cast(str, decision_record["generated_at"]))
    row.decided_at = _parse_iso(cast(str, decision_record["decided_at"]))
    row.decision_kind = cast(str, decision_record["decision_kind"])
    row.tenant_id = _c6x8_scope_id(scope_summary, "tenant")
    row.merchant_id = _c6x8_scope_id(scope_summary, "merchant")
    row.seller_agent_id = _c6x8_scope_id(scope_summary, "seller_agent")
    row.buyer_agent_id = _c6x8_scope_id(scope_summary, "buyer_agent")
    row.scope_summary = dict(scope_summary)
    row.artifact_family_summary = dict(_c6x8_mapping(decision_record.get("artifact_family_counts")))
    row.artifact_families_affected = _c6x8_list(decision_record.get("artifact_families_affected"))
    row.redacted_reason_codes = dict(_c6x8_mapping(decision_record.get("redacted_reason_codes")))
    row.source_refs = _c6x8_list(decision_record.get("source_refs"))
    row.evidence_refs = _c6x8_list(decision_record.get("evidence_refs"))
    row.reviewer_ref = cast(str, decision_record["reviewer_identity_ref"])
    row.next_step_labels = _c6x8_list(decision_record.get("next_step_labels"))
    row.allowed_to_execute = False
    row.future_action_allowed = False
    row.prepared_only = True
    row.operator_decision_only = True
    row.audit_safe_decision_record = True
    row.non_authoritative_for_transaction = True
    row.no_checkout_payment_enablement = True
    row.no_live_provider_enablement = True
    row.no_public_discovery_enablement = True


def _c6x8_row_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(value)


def _c6x8_row_to_decision(row: Any) -> dict[str, Any]:
    return {
        "decision_record_id": str(row.decision_id),
        "review_packet_id": str(row.review_packet_id),
        "maintenance_plan_id": str(row.maintenance_plan_id),
        "generated_at": _c6x8_row_time(row.generated_at),
        "decided_at": _c6x8_row_time(row.decided_at),
        "decision_kind": str(row.decision_kind),
        "scope_summary": dict(row.scope_summary or {}),
        "artifact_family_counts": dict(row.artifact_family_summary or {}),
        "artifact_families_affected": list(row.artifact_families_affected or []),
        "redacted_reason_codes": dict(row.redacted_reason_codes or {}),
        "source_refs": list(row.source_refs or []),
        "evidence_refs": list(row.evidence_refs or []),
        "reviewer_identity_ref": str(row.reviewer_ref),
        "next_step_labels": list(row.next_step_labels or []),
        "allowed_to_execute": bool(row.allowed_to_execute),
        "future_action_allowed": bool(row.future_action_allowed),
        "prepared_only": bool(row.prepared_only),
        "no_execution": True,
        "operator_decision_only": bool(row.operator_decision_only),
        "audit_safe_decision_record": bool(row.audit_safe_decision_record),
        "non_authoritative_for_transaction": bool(row.non_authoritative_for_transaction),
        "no_checkout_payment_enablement": bool(row.no_checkout_payment_enablement),
        "no_live_provider_enablement": bool(row.no_live_provider_enablement),
        "no_public_discovery_enablement": bool(row.no_public_discovery_enablement),
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def _c6x8_query_matches(decision_record: Mapping[str, Any], query: OacpOperatorDecisionRepositoryQuery) -> bool:
    scope_summary = _c6x8_mapping(decision_record.get("scope_summary"))
    return (
        (query.tenant_id is None or _c6x8_scope_id(scope_summary, "tenant") == query.tenant_id)
        and (query.merchant_id is None or _c6x8_scope_id(scope_summary, "merchant") == query.merchant_id)
        and (query.seller_agent_id is None or _c6x8_scope_id(scope_summary, "seller_agent") == query.seller_agent_id)
        and (query.buyer_agent_id is None or _c6x8_scope_id(scope_summary, "buyer_agent") == query.buyer_agent_id)
        and (query.decision_kind is None or decision_record.get("decision_kind") == query.decision_kind)
        and (query.review_packet_id is None or decision_record.get("review_packet_id") == query.review_packet_id)
        and (
            query.maintenance_plan_id is None
            or decision_record.get("maintenance_plan_id") == query.maintenance_plan_id
        )
    )


class DurableOacpOperatorDecisionRepository:
    """Async SQLAlchemy-backed operator decision repository; it performs no maintenance actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_decision(self, decision_record: Mapping[str, Any]) -> dict[str, Any]:
        from core.models.oacp_operator_decision import OacpOperatorDecisionRecordRow

        store_result = _c6x8_decision_safe_for_storage(decision_record)
        if store_result["stored"] is not True:
            return store_result

        decision_id = cast(str, store_result["decision_id"])
        row = await self._session.get(OacpOperatorDecisionRecordRow, decision_id)
        if row is None:
            row = OacpOperatorDecisionRecordRow(decision_id=decision_id)
            self._session.add(row)
        _c6x8_apply_decision_to_row(row, decision_record)
        await self._session.flush()
        return store_result

    async def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        from core.models.oacp_operator_decision import OacpOperatorDecisionRecordRow

        row = await self._session.get(OacpOperatorDecisionRecordRow, decision_id)
        return None if row is None else _c6x8_row_to_decision(row)

    async def list_decisions_for_scope(
        self,
        query: OacpOperatorDecisionRepositoryQuery,
    ) -> tuple[dict[str, Any], ...]:
        from sqlalchemy import select

        from core.models.oacp_operator_decision import OacpOperatorDecisionRecordRow

        statement = select(OacpOperatorDecisionRecordRow)
        if query.tenant_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.tenant_id == query.tenant_id)
        if query.merchant_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.merchant_id == query.merchant_id)
        if query.seller_agent_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.seller_agent_id == query.seller_agent_id)
        if query.buyer_agent_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.buyer_agent_id == query.buyer_agent_id)
        if query.decision_kind is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.decision_kind == query.decision_kind)
        if query.review_packet_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.review_packet_id == query.review_packet_id)
        if query.maintenance_plan_id is not None:
            statement = statement.where(OacpOperatorDecisionRecordRow.maintenance_plan_id == query.maintenance_plan_id)

        rows = (await self._session.scalars(statement.order_by(OacpOperatorDecisionRecordRow.decision_id))).all()
        decisions = tuple(_c6x8_row_to_decision(row) for row in rows)
        return tuple(decision for decision in decisions if _c6x8_query_matches(decision, query))

    async def evaluate_decision_for_future_action(self, decision_id: str) -> dict[str, Any]:
        decision_record = await self.get_decision(decision_id)
        if decision_record is None:
            return _c6x8_decision_repository_refusal(
                status="blocked",
                refusal_code="decision_missing",
                message="C6X8 requires a stored operator decision before future action review.",
            )
        store_result = _c6x8_decision_safe_for_storage(decision_record)
        if store_result["stored"] is not True:
            return store_result
        return {
            "evaluated": True,
            "status": "future_action_requires_separate_approval",
            "decision_id": decision_id,
            "decision_kind": decision_record["decision_kind"],
            "future_action_allowed": False,
            "allowed_to_execute": False,
            "prepared_only": True,
            "operator_decision_only": True,
            "audit_safe_decision_record": True,
            "non_authoritative_for_transaction": True,
            "no_checkout_payment_enablement": True,
            "no_live_provider_enablement": True,
            "no_public_discovery_enablement": True,
            "grantex_runtime_required": False,
            "message": "C6X8 stores an audit-safe decision only; it does not execute maintenance.",
        }


_C6X9_SAFE_FALLBACK_GENERATED_AT = "2026-06-11T00:00:00.000Z"
_C6X9_RISKY_REVOCATION_STATES = frozenset({"revoked", "ambiguous", "stale", "expired"})
_C6X9_HIGH_RISK_TIERS = frozenset({"high", "critical"})
_C6X9_OVERCLAIM_MARKERS = (
    "certification",
    "certified",
    "compliance",
    "compliant",
    "conformance",
    "standardization",
    "standardized",
    "production_ready",
    "public_launch",
    "execution_ready",
    "merchant_approval",
    "checkout_approval",
    "payment_approval",
    "mandate_approval",
    "live_provider_readiness",
    "oacp_approval",
)


def _c6x9_bundle_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonicalize_oacp_payload(dict(payload)).encode("utf-8")).hexdigest()
    return f"oacp_c6x9_audit_export_{digest[:20]}"


def _c6x9_blocked_bundle(
    *,
    reason_code: str,
    message: str,
    generated_at: str | None = None,
    tenant_id: str | None = None,
    merchant_id: str | None = None,
    seller_agent_id: str | None = None,
    buyer_agent_id: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _C6X9_SAFE_FALLBACK_GENERATED_AT
    return {
        "bundle_id": _c6x9_bundle_id(
            {
                "status": "blocked",
                "reason_code": reason_code,
                "tenant_id": tenant_id,
                "merchant_id": merchant_id,
                "generated_at": generated,
            }
        ),
        "bundle_kind": "blocked_oacp_cache_operator_audit_export_bundle",
        "status": "blocked",
        "block_reason": reason_code,
        "message": message,
        "generated_at": generated,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "scope_summary": {"buyer_agent": {}, "seller_agent": {}, "tenant": {}, "merchant": {}},
        "artifact_family_counts": {},
        "cache_record_references": [],
        "maintenance_plan_references": [],
        "review_packet_references": [],
        "decision_record_references": [],
        "redacted_reason_codes": {},
        "redacted_source_refs": [],
        "redacted_evidence_refs": [],
        "freshness_ttl_summary": {"freshness": {}, "records_with_ttl": 0, "minimum_ttl_policy_seconds": None},
        "revocation_snapshot_summary": {},
        "risk_tier_summary": {},
        "unsupported_capability_summary": {},
        "blocked_capability_summary": {reason_code: 1},
        "next_step_labels": ["operator_review_required_no_export_execution"],
        "allowed_to_execute": False,
        "no_execution": True,
        "audit_export_bundle_only": True,
        "export_ready": False,
        "generated_artifact_written": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def _c6x9_unique(values: Sequence[str]) -> list[str]:
    return sorted({value for value in values if value})


def _c6x9_count(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _c6x9_scope_count(
    records: Sequence[OacpPersistentArtifactCacheRecord],
    field_name: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = getattr(record, field_name)
        if isinstance(value, str) and value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _c6x9_scope_summary(records: Sequence[OacpPersistentArtifactCacheRecord]) -> dict[str, dict[str, int]]:
    return {
        "buyer_agent": _c6x9_scope_count(records, "buyer_agent_id"),
        "seller_agent": _c6x9_scope_count(records, "seller_agent_id"),
        "tenant": _c6x9_scope_count(records, "tenant_id"),
        "merchant": _c6x9_scope_count(records, "merchant_id"),
    }


def _c6x9_record_scope_matches(
    record: OacpPersistentArtifactCacheRecord,
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
) -> bool:
    if record.tenant_id != tenant_id or record.merchant_id != merchant_id:
        return False
    if seller_agent_id is not None and record.seller_agent_id not in (None, seller_agent_id):
        return False
    if buyer_agent_id is not None and record.buyer_agent_id not in (None, buyer_agent_id):
        return False
    return True


def _c6x9_mapping_scope_matches(
    item: Mapping[str, Any],
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
) -> bool:
    direct_fields = {
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
    }
    direct_scope_present = False
    for field_name, expected in direct_fields.items():
        value = item.get(field_name)
        direct_scope_present = direct_scope_present or (field_name in item and isinstance(value, str) and bool(value))
        if expected is not None and isinstance(value, str) and value and value != expected:
            return False

    scope_summary = _c6x8_mapping(item.get("scope_summary"))
    if not scope_summary and direct_scope_present:
        return True
    scope_expectations = {
        "tenant": (tenant_id, True),
        "merchant": (merchant_id, True),
        "seller_agent": (seller_agent_id, False),
        "buyer_agent": (buyer_agent_id, False),
    }
    for scope_kind, (expected, required) in scope_expectations.items():
        values = scope_summary.get(scope_kind)
        if not isinstance(values, Mapping) or not values:
            if required:
                return False
            continue
        keys = {str(key) for key in values if isinstance(key, str) and key}
        if expected is not None and expected not in keys:
            return False
    return True


def _c6x9_components_scope_match(
    *,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None,
    buyer_agent_id: str | None,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    maintenance_plans: Sequence[Mapping[str, Any]],
    review_packets: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> bool:
    for record in cache_records:
        if not _c6x9_record_scope_matches(
            record,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        ):
            return False
    mapped_components: list[Mapping[str, Any]] = list(review_packets) + list(decision_records)
    for plan in maintenance_plans:
        for action in plan.get("record_actions", []):
            if isinstance(action, Mapping):
                mapped_components.append(action)
    return all(
        _c6x9_mapping_scope_matches(
            component,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
        for component in mapped_components
    )


def _c6x9_private_or_overclaim_values(values: Sequence[str]) -> bool:
    for value in values:
        normalized = value.strip().lower()
        if not normalized:
            return True
        if any(marker in normalized for marker in C6X2_PRIVATE_REF_MARKERS):
            return True
        if any(marker in normalized for marker in _C6X9_OVERCLAIM_MARKERS):
            return True
    return False


def _c6x9_reason_code_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                values.append(key)
            values.extend(_c6x9_reason_code_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_c6x9_reason_code_values(child))
    elif isinstance(value, str):
        values.append(value)
    return values


def _c6x9_inputs_private_or_overclaim(
    *,
    maintenance_plans: Sequence[Mapping[str, Any]],
    review_packets: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> bool:
    strings: list[str] = []
    for plan in maintenance_plans:
        strings.extend(_c6x6_string_list(plan.get("source_refs")))
        strings.extend(_c6x6_string_list(plan.get("evidence_refs")))
        strings.extend(_c6x9_reason_code_values(plan.get("per_record_reason_codes")))
    for packet in review_packets:
        strings.extend(_c6x7_string_list(packet.get("source_refs")))
        strings.extend(_c6x7_string_list(packet.get("evidence_refs")))
        strings.extend(_c6x7_string_list(packet.get("next_step_labels")))
        strings.extend(_c6x9_reason_code_values(packet.get("per_record_reason_codes")))
    for decision in decision_records:
        strings.extend(_c6x8_list(decision.get("source_refs")))
        strings.extend(_c6x8_list(decision.get("evidence_refs")))
        strings.extend(_c6x8_list(decision.get("next_step_labels")))
        strings.extend(_c6x9_reason_code_values(decision.get("redacted_reason_codes")))
        reviewer_ref = _c6x7_string(decision.get("reviewer_identity_ref"))
        if reviewer_ref is not None:
            strings.append(reviewer_ref)
    return _c6x9_private_or_overclaim_values(strings)


def _c6x9_timestamps_valid(
    *,
    generated_at: str,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    maintenance_plans: Sequence[Mapping[str, Any]],
    review_packets: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> bool:
    values: list[str | None] = [generated_at]
    for record in cache_records:
        values.extend(
            [
                record.generated_at,
                record.cached_at,
                record.expires_at,
                record.revocation_snapshot_observed_at,
            ]
        )
    for plan in maintenance_plans:
        values.append(_c6x6_string(plan.get("generated_at")))
    for packet in review_packets:
        values.append(_c6x7_string(packet.get("generated_at")))
    for decision in decision_records:
        values.extend([_c6x7_string(decision.get("generated_at")), _c6x7_string(decision.get("decided_at"))])
    return all(_parse_iso(value) is not None for value in values)


def _c6x9_components_non_executing(
    *,
    maintenance_plans: Sequence[Mapping[str, Any]],
    review_packets: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> bool:
    for plan in maintenance_plans:
        if not _c6x6_plan_flags_safe(plan):
            return False
        actions = plan.get("record_actions")
        if not isinstance(actions, list):
            return False
        mapped_actions = [action for action in actions if isinstance(action, Mapping)]
        if len(mapped_actions) != len(actions) or not _c6x6_actions_safe(mapped_actions):
            return False
    for packet in review_packets:
        if not _c6x7_packet_flags_safe(packet) or not _c6x7_refs_safe(packet):
            return False
    for decision in decision_records:
        if _c6x8_decision_safe_for_storage(decision).get("stored") is not True:
            return False
    return True


def _c6x9_approved_risky_state(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    review_packets: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> bool:
    risky_cache = any(
        record.risk_tier in _C6X9_HIGH_RISK_TIERS
        or record.revocation_snapshot_status in _C6X9_RISKY_REVOCATION_STATES
        or record.freshness_status in {"stale", "expired"}
        for record in cache_records
    )
    risky_packet = False
    for packet in review_packets:
        revocation_summary = _c6x8_mapping(packet.get("revocation_snapshot_summary"))
        risk_summary = _c6x8_mapping(packet.get("risk_tier_summary"))
        freshness_summary = _c6x8_mapping(packet.get("freshness_summary"))
        risky_packet = risky_packet or any(key in revocation_summary for key in _C6X9_RISKY_REVOCATION_STATES)
        risky_packet = risky_packet or any(key in risk_summary for key in _C6X9_HIGH_RISK_TIERS)
        risky_packet = risky_packet or any(key in freshness_summary for key in ("stale", "expired"))
    approved = any(
        str(decision.get("decision_kind", "")).startswith("approve_future_")
        for decision in decision_records
    )
    return approved and (risky_cache or risky_packet)


def _c6x9_merge_reason_codes(*items: Mapping[str, Any]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        for key, value in _c6x8_mapping(item).items():
            codes = [str(code) for code in value] if isinstance(value, list) else [str(value)]
            merged[str(key)] = _c6x9_unique([*merged.get(str(key), []), *codes])
    return dict(sorted(merged.items()))


def _c6x9_capability_summary(records: Sequence[OacpPersistentArtifactCacheRecord], field_name: str) -> dict[str, int]:
    values: list[str] = []
    for record in records:
        field_value = getattr(record, field_name)
        if isinstance(field_value, tuple):
            values.extend(str(value) for value in field_value)
    return _c6x9_count(values)


def build_oacp_c6x9_audit_export_bundle(
    *,
    cache_records: Sequence[OacpPersistentArtifactCacheRecord],
    maintenance_plans: Sequence[Mapping[str, Any]],
    review_packets: Sequence[Mapping[str, Any]],
    operator_decision_records: Sequence[Mapping[str, Any]],
    durable_decision_records: Sequence[Mapping[str, Any]] = (),
    generated_at: str,
    tenant_id: str,
    merchant_id: str,
    seller_agent_id: str | None = None,
    buyer_agent_id: str | None = None,
) -> dict[str, Any]:
    """Build a redacted C6X9 audit export bundle without writing, publishing, or executing it."""

    all_decision_records = tuple(operator_decision_records) + tuple(durable_decision_records)
    if not tenant_id or not merchant_id:
        return _c6x9_blocked_bundle(
            reason_code="tenant_or_merchant_scope_missing",
            message="C6X9 audit export bundles require tenant and merchant scope.",
            generated_at=generated_at,
            tenant_id=tenant_id or None,
            merchant_id=merchant_id or None,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not cache_records or not all_decision_records:
        return _c6x9_blocked_bundle(
            reason_code="audit_export_lineage_missing",
            message="C6X9 requires local cache records and operator decision records before preparing an audit bundle.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not _c6x9_timestamps_valid(
        generated_at=generated_at,
        cache_records=cache_records,
        maintenance_plans=maintenance_plans,
        review_packets=review_packets,
        decision_records=all_decision_records,
    ):
        return _c6x9_blocked_bundle(
            reason_code="malformed_timestamp",
            message="C6X9 refuses audit bundles with malformed cache, plan, report, or decision timestamps.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not all(_c6x3_record_safe_for_repository(record).get("stored") is True for record in cache_records):
        return _c6x9_blocked_bundle(
            reason_code="cache_record_private_or_unsafe",
            message="C6X9 refuses cache records with missing scope, private refs, or executable posture.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not _c6x9_components_scope_match(
        tenant_id=tenant_id,
        merchant_id=merchant_id,
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
        cache_records=cache_records,
        maintenance_plans=maintenance_plans,
        review_packets=review_packets,
        decision_records=all_decision_records,
    ):
        return _c6x9_blocked_bundle(
            reason_code="audit_export_scope_mismatch",
            message="C6X9 refuses bundles whose cache, plan, report, or decision scopes do not match.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    try:
        for component in [*maintenance_plans, *review_packets, *all_decision_records]:
            assert_no_forbidden_oacp_artifact_fields(dict(component))
    except ValueError:
        return _c6x9_blocked_bundle(
            reason_code="private_or_enabling_export_field",
            message="C6X9 refuses private, raw, credential, or enabling export input fields.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not _c6x9_components_non_executing(
        maintenance_plans=maintenance_plans,
        review_packets=review_packets,
        decision_records=all_decision_records,
    ):
        return _c6x9_blocked_bundle(
            reason_code="export_component_executable_or_enabling",
            message="C6X9 refuses executable or enabling maintenance, review, or decision records.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if _c6x9_inputs_private_or_overclaim(
        maintenance_plans=maintenance_plans,
        review_packets=review_packets,
        decision_records=all_decision_records,
    ):
        return _c6x9_blocked_bundle(
            reason_code="private_ref_or_overclaim_detected",
            message="C6X9 refuses private refs, raw reviewer identity, or publication/readiness claims.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if _c6x9_approved_risky_state(
        cache_records=cache_records,
        review_packets=review_packets,
        decision_records=all_decision_records,
    ):
        return _c6x9_blocked_bundle(
            reason_code="risky_state_represented_as_approved",
            message="C6X9 refuses to export stale, revoked, or high-risk states as approved.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )

    sorted_records = tuple(sorted(cache_records, key=lambda record: record.cache_record_id))
    sorted_plans = tuple(sorted(maintenance_plans, key=lambda plan: str(plan.get("plan_id", ""))))
    sorted_packets = tuple(sorted(review_packets, key=lambda packet: str(packet.get("report_id", ""))))
    sorted_decisions = tuple(
        sorted(all_decision_records, key=lambda decision: str(decision.get("decision_record_id", "")))
    )
    source_refs = _c6x9_unique(
        [
            *(ref for record in sorted_records for ref in record.source_refs),
            *(ref for plan in sorted_plans for ref in _c6x6_string_list(plan.get("source_refs"))),
            *(ref for packet in sorted_packets for ref in _c6x7_string_list(packet.get("source_refs"))),
            *(ref for decision in sorted_decisions for ref in _c6x8_list(decision.get("source_refs"))),
        ]
    )
    evidence_refs = _c6x9_unique(
        [
            *(ref for record in sorted_records for ref in record.evidence_refs),
            *(ref for plan in sorted_plans for ref in _c6x6_string_list(plan.get("evidence_refs"))),
            *(ref for packet in sorted_packets for ref in _c6x7_string_list(packet.get("evidence_refs"))),
            *(ref for decision in sorted_decisions for ref in _c6x8_list(decision.get("evidence_refs"))),
        ]
    )
    if not evidence_refs or not _c6x2_refs_safe((*source_refs, *evidence_refs)):
        return _c6x9_blocked_bundle(
            reason_code="redacted_evidence_refs_missing_or_private",
            message="C6X9 requires redacted source and evidence refs only.",
            generated_at=generated_at,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )

    plan_reason_maps = [_c6x8_mapping(plan.get("per_record_reason_codes")) for plan in sorted_plans]
    packet_reason_maps = [_c6x8_mapping(packet.get("per_record_reason_codes")) for packet in sorted_packets]
    decision_reason_maps = [_c6x8_mapping(decision.get("redacted_reason_codes")) for decision in sorted_decisions]
    bundle_payload = {
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "generated_at": generated_at,
        "cache_records": [record.cache_record_id for record in sorted_records],
        "maintenance_plans": [str(plan.get("plan_id", "")) for plan in sorted_plans],
        "review_packets": [str(packet.get("report_id", "")) for packet in sorted_packets],
        "decision_records": [str(decision.get("decision_record_id", "")) for decision in sorted_decisions],
    }
    return {
        "bundle_id": _c6x9_bundle_id(bundle_payload),
        "bundle_kind": "oacp_cache_operator_decision_audit_export_bundle",
        "status": "export_ready",
        "generated_at": generated_at,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "scope_summary": _c6x9_scope_summary(sorted_records),
        "artifact_family_counts": _c6x9_count([record.artifact_type for record in sorted_records]),
        "cache_record_references": [record.cache_record_id for record in sorted_records],
        "maintenance_plan_references": [str(plan.get("plan_id")) for plan in sorted_plans],
        "review_packet_references": [str(packet.get("report_id")) for packet in sorted_packets],
        "decision_record_references": [str(decision.get("decision_record_id")) for decision in sorted_decisions],
        "redacted_reason_codes": _c6x9_merge_reason_codes(
            *plan_reason_maps,
            *packet_reason_maps,
            *decision_reason_maps,
        ),
        "redacted_source_refs": source_refs,
        "redacted_evidence_refs": evidence_refs,
        "freshness_ttl_summary": {
            "freshness": _c6x9_count([record.freshness_status for record in sorted_records]),
            "records_with_ttl": len([record for record in sorted_records if record.ttl_policy_seconds > 0]),
            "minimum_ttl_policy_seconds": min(record.ttl_policy_seconds for record in sorted_records),
            "earliest_expires_at": min(record.expires_at for record in sorted_records),
        },
        "revocation_snapshot_summary": _c6x9_count([record.revocation_snapshot_status for record in sorted_records]),
        "risk_tier_summary": _c6x9_count([record.risk_tier for record in sorted_records]),
        "unsupported_capability_summary": _c6x9_capability_summary(sorted_records, "unsupported_capabilities"),
        "blocked_capability_summary": _c6x9_capability_summary(sorted_records, "blocked_capabilities"),
        "next_step_labels": _c6x9_unique(
            [label for decision in sorted_decisions for label in _c6x8_list(decision.get("next_step_labels"))]
        ),
        "allowed_to_execute": False,
        "no_execution": True,
        "audit_export_bundle_only": True,
        "export_ready": True,
        "generated_artifact_written": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
        "buyer_safe_message": "C6X9 prepared an internal redacted audit export bundle only; no commerce action ran.",
        "seller_safe_message": "C6X9 does not call Grantex live, merchant systems, providers, or schedulers.",
        "operator_safe_message": (
            "Review this export-ready bundle as evidence only. It is not an execution instruction."
        ),
    }


_C6Y1_SAFE_FALLBACK_GENERATED_AT = "2026-06-12T00:00:00.000Z"
_C6Y1_RETENTION_DAYS_BY_CLASS: dict[OacpAuditExportReviewRetentionClass, int] = {
    "short_lived_internal_review": 30,
    "standard_internal_review": 90,
    "legal_hold_candidate": 365,
}
_C6Y1_BUNDLE_REF_FIELDS = (
    "cache_record_references",
    "maintenance_plan_references",
    "review_packet_references",
    "decision_record_references",
    "redacted_source_refs",
    "redacted_evidence_refs",
    "next_step_labels",
)


def _c6y1_manifest_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonicalize_oacp_payload(dict(payload)).encode("utf-8")).hexdigest()
    return f"oacp_c6y1_review_manifest_{digest[:20]}"


def _c6y1_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _c6y1_blocked_manifest(
    *,
    reason_code: str,
    message: str,
    generated_at: str | None = None,
    bundle_id: str | None = None,
    tenant_id: str | None = None,
    merchant_id: str | None = None,
    seller_agent_id: str | None = None,
    buyer_agent_id: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or _C6Y1_SAFE_FALLBACK_GENERATED_AT
    return {
        "manifest_id": _c6y1_manifest_id(
            {
                "status": "blocked",
                "reason_code": reason_code,
                "bundle_id": bundle_id,
                "tenant_id": tenant_id,
                "merchant_id": merchant_id,
                "generated_at": generated,
            }
        ),
        "manifest_kind": "blocked_oacp_audit_export_review_manifest",
        "status": "blocked",
        "block_reason": reason_code,
        "message": message,
        "generated_at": generated,
        "bundle_id": bundle_id,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "scope_summary": {"buyer_agent": {}, "seller_agent": {}, "tenant": {}, "merchant": {}},
        "artifact_family_counts": {},
        "cache_record_references": [],
        "maintenance_plan_references": [],
        "review_packet_references": [],
        "decision_record_references": [],
        "redacted_reason_codes": {},
        "redacted_source_refs": [],
        "redacted_evidence_refs": [],
        "freshness_ttl_summary": {},
        "revocation_snapshot_summary": {},
        "risk_tier_summary": {},
        "unsupported_capability_summary": {},
        "blocked_capability_summary": {reason_code: 1},
        "retention_boundary": {
            "retention_class": "blocked_no_retention_action",
            "retention_days": 0,
            "retain_until": None,
            "persistence_required": False,
            "requires_separate_persistence_approval": True,
            "export_file_writer_added": False,
        },
        "redaction_boundary": {
            "redacted_refs_only": True,
            "raw_payloads_included": False,
            "private_values_blocked": True,
            "non_sensitive_evidence_refs_only": True,
        },
        "next_step_labels": ["operator_review_required_no_export_execution"],
        "allowed_to_execute": False,
        "no_execution": True,
        "review_manifest_only": True,
        "retention_boundary_only": True,
        "audit_export_bundle_review_only": True,
        "export_file_written": False,
        "export_writer_added": False,
        "generated_artifact_written": False,
        "migration_added": False,
        "scheduler_added": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
    }


def _c6y1_bundle_flags_safe(bundle: Mapping[str, Any]) -> bool:
    return (
        bundle.get("allowed_to_execute") is False
        and bundle.get("no_execution") is True
        and bundle.get("audit_export_bundle_only") is True
        and bundle.get("export_ready") is True
        and bundle.get("generated_artifact_written") is False
        and bundle.get("export_file_written") is not True
        and bundle.get("non_authoritative_for_transaction") is True
        and bundle.get("no_checkout_payment_enablement") is True
        and bundle.get("no_live_provider_enablement") is True
        and bundle.get("no_public_discovery_enablement") is True
        and bundle.get("raw_payloads_included") is False
        and bundle.get("grantex_runtime_required") is False
    )


def _c6y1_bundle_strings(bundle: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field_name in _C6Y1_BUNDLE_REF_FIELDS:
        values.extend(_c6x8_list(bundle.get(field_name)))
    for field_name in ("buyer_safe_message", "seller_safe_message", "operator_safe_message", "message"):
        value = _c6x7_string(bundle.get(field_name))
        if value is not None:
            values.append(value)
    values.extend(_c6x9_reason_code_values(bundle.get("redacted_reason_codes")))
    values.extend(_c6x9_reason_code_values(bundle.get("blocked_capability_summary")))
    values.extend(_c6x9_reason_code_values(bundle.get("unsupported_capability_summary")))
    return values


def _c6y1_bundle_refs_safe(bundle: Mapping[str, Any]) -> bool:
    refs = tuple(
        ref
        for field_name in _C6Y1_BUNDLE_REF_FIELDS
        for ref in _c6x8_list(bundle.get(field_name))
    )
    return bool(_c6x8_list(bundle.get("redacted_evidence_refs"))) and _c6x2_refs_safe(refs)


def _c6y1_values_private_or_overclaim(values: Sequence[str]) -> bool:
    c6y1_overclaim_markers = ("readiness", "public launch", "approved")
    return _c6x9_private_or_overclaim_values(values) or any(
        any(marker in value.strip().lower() for marker in c6y1_overclaim_markers)
        for value in values
    )


def build_oacp_c6y1_audit_export_review_manifest(
    *,
    audit_export_bundle: Mapping[str, Any],
    generated_at: str,
    retention_class: OacpAuditExportReviewRetentionClass = "standard_internal_review",
) -> dict[str, Any]:
    """Build an internal C6Y1 review manifest over a C6X9 bundle without writing or executing it."""

    bundle_id = _c6x7_string(audit_export_bundle.get("bundle_id"))
    tenant_id = _c6x7_string(audit_export_bundle.get("tenant_id"))
    merchant_id = _c6x7_string(audit_export_bundle.get("merchant_id"))
    seller_agent_id = _c6x7_string(audit_export_bundle.get("seller_agent_id"))
    buyer_agent_id = _c6x7_string(audit_export_bundle.get("buyer_agent_id"))
    generated = _parse_iso(generated_at)
    bundle_generated = _parse_iso(_c6x7_string(audit_export_bundle.get("generated_at")))

    if generated is None or bundle_generated is None:
        return _c6y1_blocked_manifest(
            reason_code="manifest_timestamp_malformed",
            message="C6Y1 review manifests require ordered ISO timestamps.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if retention_class not in _C6Y1_RETENTION_DAYS_BY_CLASS:
        return _c6y1_blocked_manifest(
            reason_code="retention_class_unsupported",
            message="C6Y1 accepts only internal retention review classes.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if (
        audit_export_bundle.get("bundle_kind") != "oacp_cache_operator_decision_audit_export_bundle"
        or audit_export_bundle.get("status") != "export_ready"
        or not bundle_id
    ):
        return _c6y1_blocked_manifest(
            reason_code="audit_export_bundle_not_ready",
            message="C6Y1 review manifests consume C6X9 export-ready audit bundles only.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not tenant_id or not merchant_id:
        return _c6y1_blocked_manifest(
            reason_code="tenant_or_merchant_scope_missing",
            message="C6Y1 review manifests require tenant and merchant scope.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    try:
        assert_no_forbidden_oacp_artifact_fields(dict(audit_export_bundle))
    except ValueError:
        return _c6y1_blocked_manifest(
            reason_code="private_or_enabling_manifest_input",
            message="C6Y1 refuses audit bundle fields that contain private, raw, credential, or enabling data.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not _c6y1_bundle_flags_safe(audit_export_bundle):
        return _c6y1_blocked_manifest(
            reason_code="bundle_non_enablement_flags_invalid",
            message="C6Y1 refuses executable audit bundles or bundles that imply export-file writing.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )
    if not _c6y1_bundle_refs_safe(audit_export_bundle) or _c6y1_values_private_or_overclaim(
        _c6y1_bundle_strings(audit_export_bundle)
    ):
        return _c6y1_blocked_manifest(
            reason_code="private_ref_or_overclaim_detected",
            message="C6Y1 refuses private refs, raw labels, publication wording, or approval/readiness claims.",
            generated_at=generated_at,
            bundle_id=bundle_id,
            tenant_id=tenant_id,
            merchant_id=merchant_id,
            seller_agent_id=seller_agent_id,
            buyer_agent_id=buyer_agent_id,
        )

    retention_days = _C6Y1_RETENTION_DAYS_BY_CLASS[retention_class]
    retain_until = _c6y1_iso(generated + timedelta(days=retention_days))
    manifest_payload = {
        "bundle_id": bundle_id,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "generated_at": generated_at,
        "retention_class": retention_class,
    }
    return {
        "manifest_id": _c6y1_manifest_id(manifest_payload),
        "manifest_kind": "oacp_audit_export_review_manifest",
        "status": "ready_for_internal_review",
        "generated_at": generated_at,
        "bundle_id": bundle_id,
        "bundle_kind": audit_export_bundle["bundle_kind"],
        "bundle_status": audit_export_bundle["status"],
        "bundle_generated_at": audit_export_bundle["generated_at"],
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "seller_agent_id": seller_agent_id,
        "buyer_agent_id": buyer_agent_id,
        "scope_summary": dict(_c6x8_mapping(audit_export_bundle.get("scope_summary"))),
        "artifact_family_counts": dict(_c6x8_mapping(audit_export_bundle.get("artifact_family_counts"))),
        "cache_record_references": _c6x8_list(audit_export_bundle.get("cache_record_references")),
        "maintenance_plan_references": _c6x8_list(audit_export_bundle.get("maintenance_plan_references")),
        "review_packet_references": _c6x8_list(audit_export_bundle.get("review_packet_references")),
        "decision_record_references": _c6x8_list(audit_export_bundle.get("decision_record_references")),
        "redacted_reason_codes": dict(_c6x8_mapping(audit_export_bundle.get("redacted_reason_codes"))),
        "redacted_source_refs": _c6x8_list(audit_export_bundle.get("redacted_source_refs")),
        "redacted_evidence_refs": _c6x8_list(audit_export_bundle.get("redacted_evidence_refs")),
        "freshness_ttl_summary": dict(_c6x8_mapping(audit_export_bundle.get("freshness_ttl_summary"))),
        "revocation_snapshot_summary": dict(_c6x8_mapping(audit_export_bundle.get("revocation_snapshot_summary"))),
        "risk_tier_summary": dict(_c6x8_mapping(audit_export_bundle.get("risk_tier_summary"))),
        "unsupported_capability_summary": dict(
            _c6x8_mapping(audit_export_bundle.get("unsupported_capability_summary"))
        ),
        "blocked_capability_summary": dict(_c6x8_mapping(audit_export_bundle.get("blocked_capability_summary"))),
        "retention_boundary": {
            "retention_class": retention_class,
            "retention_days": retention_days,
            "retain_until": retain_until,
            "retention_clock_source": "manifest_generated_at",
            "persistence_required": False,
            "requires_separate_persistence_approval": True,
            "export_file_writer_added": False,
            "generated_artifact_written": False,
        },
        "redaction_boundary": {
            "redacted_refs_only": True,
            "raw_payloads_included": False,
            "private_values_blocked": True,
            "raw_reviewer_identity_included": False,
            "non_sensitive_evidence_refs_only": True,
        },
        "next_step_labels": _c6x9_unique(
            [
                *_c6x8_list(audit_export_bundle.get("next_step_labels")),
                "operator_review_manifest_label_only",
                "retention_boundary_review_label_only",
            ]
        ),
        "allowed_to_execute": False,
        "no_execution": True,
        "review_manifest_only": True,
        "retention_boundary_only": True,
        "audit_export_bundle_review_only": True,
        "export_file_written": False,
        "export_writer_added": False,
        "generated_artifact_written": False,
        "migration_added": False,
        "scheduler_added": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "raw_payloads_included": False,
        "grantex_runtime_required": False,
        "buyer_safe_message": "C6Y1 prepared an internal audit review manifest only; no commerce action ran.",
        "seller_safe_message": "C6Y1 does not call Grantex live, merchant systems, providers, or schedulers.",
        "operator_safe_message": (
            "Review this manifest and retention boundary as label-only evidence; it is not an export writer."
        ),
    }


def _c6y2_manifest_repository_refusal(
    *,
    status: str,
    refusal_code: str,
    message: str,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "stored": False,
        "status": status,
        "refusal_code": refusal_code,
        "message": message,
        "manifest_id": None if manifest is None else _c6x7_string(manifest.get("manifest_id")),
        "bundle_id": None if manifest is None else _c6x7_string(manifest.get("bundle_id")),
        "tenant_id": None if manifest is None else _c6x7_string(manifest.get("tenant_id")),
        "merchant_id": None if manifest is None else _c6x7_string(manifest.get("merchant_id")),
        "durable_repository": True,
        "future_export_allowed": False,
        "allowed_to_execute": False,
        "no_execution": True,
        "review_manifest_only": True,
        "retention_boundary_only": True,
        "audit_export_bundle_review_only": True,
        "export_file_written": False,
        "export_writer_added": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6y2_retention_boundary(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    return _c6x8_mapping(manifest.get("retention_boundary"))


def _c6y2_retention_class(manifest: Mapping[str, Any]) -> str | None:
    retention_class = _c6x7_string(_c6y2_retention_boundary(manifest).get("retention_class"))
    return retention_class if retention_class in _C6Y1_RETENTION_DAYS_BY_CLASS else None


def _c6y2_manifest_scope_matches(manifest: Mapping[str, Any]) -> bool:
    tenant_id = _c6x7_string(manifest.get("tenant_id"))
    merchant_id = _c6x7_string(manifest.get("merchant_id"))
    seller_agent_id = _c6x7_string(manifest.get("seller_agent_id"))
    buyer_agent_id = _c6x7_string(manifest.get("buyer_agent_id"))
    scope_summary = _c6x8_mapping(manifest.get("scope_summary"))
    return _c6x9_mapping_scope_matches(
        {
            "tenant_id": tenant_id,
            "merchant_id": merchant_id,
            "seller_agent_id": seller_agent_id,
            "buyer_agent_id": buyer_agent_id,
            "scope_summary": scope_summary,
        },
        tenant_id=tenant_id or "",
        merchant_id=merchant_id or "",
        seller_agent_id=seller_agent_id,
        buyer_agent_id=buyer_agent_id,
    )


def _c6y2_manifest_flags_safe(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("allowed_to_execute") is False
        and manifest.get("no_execution") is True
        and manifest.get("review_manifest_only") is True
        and manifest.get("retention_boundary_only") is True
        and manifest.get("audit_export_bundle_review_only") is True
        and manifest.get("export_file_written") is False
        and manifest.get("export_writer_added") is False
        and manifest.get("generated_artifact_written") is False
        and manifest.get("scheduler_added") is False
        and manifest.get("non_authoritative_for_transaction") is True
        and manifest.get("no_checkout_payment_enablement") is True
        and manifest.get("no_live_provider_enablement") is True
        and manifest.get("no_public_discovery_enablement") is True
        and manifest.get("raw_payloads_included") is False
        and manifest.get("grantex_runtime_required") is False
    )


def _c6y2_manifest_strings(manifest: Mapping[str, Any]) -> list[str]:
    values = _c6y1_bundle_strings(manifest)
    values.extend(_c6x9_reason_code_values(manifest.get("artifact_family_counts")))
    values.extend(_c6x9_reason_code_values(manifest.get("freshness_ttl_summary")))
    values.extend(_c6x9_reason_code_values(manifest.get("revocation_snapshot_summary")))
    values.extend(_c6x9_reason_code_values(manifest.get("risk_tier_summary")))
    for field_name in ("buyer_safe_message", "seller_safe_message", "operator_safe_message", "message"):
        value = _c6x7_string(manifest.get(field_name))
        if value is not None:
            values.append(value)
    return values


def _c6y2_manifest_refs_safe(manifest: Mapping[str, Any]) -> bool:
    refs = tuple(
        ref
        for field_name in _C6Y1_BUNDLE_REF_FIELDS
        for ref in _c6x8_list(manifest.get(field_name))
    )
    return bool(_c6x8_list(manifest.get("redacted_evidence_refs"))) and _c6x2_refs_safe(refs)


def _c6y2_manifest_safe_for_storage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    manifest_id = _c6x7_string(manifest.get("manifest_id"))
    bundle_id = _c6x7_string(manifest.get("bundle_id"))
    tenant_id = _c6x7_string(manifest.get("tenant_id"))
    merchant_id = _c6x7_string(manifest.get("merchant_id"))
    generated_at = _c6x7_string(manifest.get("generated_at"))
    bundle_generated_at = _c6x7_string(manifest.get("bundle_generated_at"))
    retention = _c6y2_retention_boundary(manifest)
    retention_class = _c6y2_retention_class(manifest)
    retention_days = retention.get("retention_days")
    retain_until = _c6x7_string(retention.get("retain_until"))
    retention_clock_source = _c6x7_string(retention.get("retention_clock_source"))

    if not all((manifest_id, bundle_id, tenant_id, merchant_id)):
        return _c6y2_manifest_repository_refusal(
            status="blocked",
            refusal_code="manifest_identity_or_scope_missing",
            message="C6Y2 durable review manifests require manifest, bundle, tenant, and merchant identifiers.",
            manifest=manifest,
        )
    if (
        manifest.get("manifest_kind") != "oacp_audit_export_review_manifest"
        or manifest.get("status") != "ready_for_internal_review"
    ):
        return _c6y2_manifest_repository_refusal(
            status="blocked",
            refusal_code="manifest_not_ready_for_storage",
            message="C6Y2 stores C6Y1 ready internal review manifests only.",
            manifest=manifest,
        )
    if retention_class is None or not isinstance(retention_days, int) or retention_days <= 0:
        return _c6y2_manifest_repository_refusal(
            status="blocked",
            refusal_code="retention_class_invalid",
            message="C6Y2 stores only supported internal retention review classes.",
            manifest=manifest,
        )
    parsed_generated_at = _parse_iso(generated_at)
    parsed_bundle_generated_at = _parse_iso(bundle_generated_at)
    parsed_retain_until = _parse_iso(retain_until)
    if (
        parsed_generated_at is None
        or parsed_bundle_generated_at is None
        or parsed_retain_until is None
        or parsed_bundle_generated_at > parsed_generated_at
        or parsed_generated_at >= parsed_retain_until
        or not retention_clock_source
    ):
        return _c6y2_manifest_repository_refusal(
            status="blocked",
            refusal_code="retention_timestamps_invalid",
            message="C6Y2 requires ordered bundle, manifest, and retention timestamps.",
            manifest=manifest,
        )
    if not _c6y2_manifest_scope_matches(manifest):
        return _c6y2_manifest_repository_refusal(
            status="blocked",
            refusal_code="manifest_scope_mismatch",
            message="C6Y2 refuses manifests whose direct and summary scopes do not match.",
            manifest=manifest,
        )
    try:
        assert_no_forbidden_oacp_artifact_fields(dict(manifest))
    except ValueError:
        return _c6y2_manifest_repository_refusal(
            status="unsafe",
            refusal_code="private_or_enabling_manifest_field",
            message="C6Y2 refuses manifests with private, raw, credential, or enabling fields.",
            manifest=manifest,
        )
    if not _c6y2_manifest_flags_safe(manifest):
        return _c6y2_manifest_repository_refusal(
            status="unsafe",
            refusal_code="non_enablement_flags_missing",
            message="C6Y2 refuses review manifests with executable posture or writer/scheduler flags.",
            manifest=manifest,
        )
    labels = tuple(_c6x8_list(manifest.get("next_step_labels")))
    if not labels or not _c6x8_labels_safe(labels):
        return _c6y2_manifest_repository_refusal(
            status="unsafe",
            refusal_code="manifest_labels_executable_or_private",
            message="C6Y2 stores label-only next steps, not execution targets.",
            manifest=manifest,
        )
    if not _c6y2_manifest_refs_safe(manifest) or _c6y1_values_private_or_overclaim(_c6y2_manifest_strings(manifest)):
        return _c6y2_manifest_repository_refusal(
            status="unsafe",
            refusal_code="manifest_refs_private_or_overclaim",
            message="C6Y2 stores redacted refs only and blocks publication or approval/readiness claims.",
            manifest=manifest,
        )
    return {
        "stored": True,
        "status": "stored",
        "manifest_id": manifest_id,
        "bundle_id": bundle_id,
        "tenant_id": tenant_id,
        "merchant_id": merchant_id,
        "retention_class": retention_class,
        "retention_days": retention_days,
        "retain_until": retain_until,
        "durable_repository": True,
        "future_export_allowed": False,
        "allowed_to_execute": False,
        "no_execution": True,
        "review_manifest_only": True,
        "retention_boundary_only": True,
        "audit_export_bundle_review_only": True,
        "export_file_written": False,
        "export_writer_added": False,
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
        "grantex_runtime_required": False,
    }


def _c6y2_apply_manifest_to_row(row: Any, manifest: Mapping[str, Any]) -> None:
    retention = _c6y2_retention_boundary(manifest)
    row.bundle_id = cast(str, manifest["bundle_id"])
    row.generated_at = _parse_iso(cast(str, manifest["generated_at"]))
    row.bundle_generated_at = _parse_iso(cast(str, manifest["bundle_generated_at"]))
    row.tenant_id = cast(str, manifest["tenant_id"])
    row.merchant_id = cast(str, manifest["merchant_id"])
    row.seller_agent_id = _c6x7_string(manifest.get("seller_agent_id"))
    row.buyer_agent_id = _c6x7_string(manifest.get("buyer_agent_id"))
    row.scope_summary = dict(_c6x8_mapping(manifest.get("scope_summary")))
    row.retention_class = cast(str, retention["retention_class"])
    row.retention_days = int(cast(int, retention["retention_days"]))
    row.retain_until = _parse_iso(cast(str, retention["retain_until"]))
    row.retention_clock_source = cast(str, retention["retention_clock_source"])
    row.artifact_family_counts = dict(_c6x8_mapping(manifest.get("artifact_family_counts")))
    row.cache_record_references = _c6x8_list(manifest.get("cache_record_references"))
    row.maintenance_plan_references = _c6x8_list(manifest.get("maintenance_plan_references"))
    row.review_packet_references = _c6x8_list(manifest.get("review_packet_references"))
    row.decision_record_references = _c6x8_list(manifest.get("decision_record_references"))
    row.redacted_reason_codes = dict(_c6x8_mapping(manifest.get("redacted_reason_codes")))
    row.redacted_source_refs = _c6x8_list(manifest.get("redacted_source_refs"))
    row.redacted_evidence_refs = _c6x8_list(manifest.get("redacted_evidence_refs"))
    row.freshness_ttl_summary = dict(_c6x8_mapping(manifest.get("freshness_ttl_summary")))
    row.revocation_snapshot_summary = dict(_c6x8_mapping(manifest.get("revocation_snapshot_summary")))
    row.risk_tier_summary = dict(_c6x8_mapping(manifest.get("risk_tier_summary")))
    row.unsupported_capability_summary = dict(_c6x8_mapping(manifest.get("unsupported_capability_summary")))
    row.blocked_capability_summary = dict(_c6x8_mapping(manifest.get("blocked_capability_summary")))
    row.next_step_labels = _c6x8_list(manifest.get("next_step_labels"))
    row.allowed_to_execute = False
    row.no_execution = True
    row.review_manifest_only = True
    row.retention_boundary_only = True
    row.audit_export_bundle_review_only = True
    row.export_file_written = False
    row.export_writer_added = False
    row.generated_artifact_written = False
    row.non_authoritative_for_transaction = True
    row.no_checkout_payment_enablement = True
    row.no_live_provider_enablement = True
    row.no_public_discovery_enablement = True


def _c6y2_row_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(value)


def _c6y2_row_to_manifest(row: Any) -> dict[str, Any]:
    return {
        "manifest_id": str(row.manifest_id),
        "manifest_kind": "oacp_audit_export_review_manifest",
        "status": "ready_for_internal_review",
        "generated_at": _c6y2_row_time(row.generated_at),
        "bundle_id": str(row.bundle_id),
        "bundle_generated_at": _c6y2_row_time(row.bundle_generated_at),
        "tenant_id": str(row.tenant_id),
        "merchant_id": str(row.merchant_id),
        "seller_agent_id": None if row.seller_agent_id is None else str(row.seller_agent_id),
        "buyer_agent_id": None if row.buyer_agent_id is None else str(row.buyer_agent_id),
        "scope_summary": dict(row.scope_summary or {}),
        "artifact_family_counts": dict(row.artifact_family_counts or {}),
        "cache_record_references": list(row.cache_record_references or []),
        "maintenance_plan_references": list(row.maintenance_plan_references or []),
        "review_packet_references": list(row.review_packet_references or []),
        "decision_record_references": list(row.decision_record_references or []),
        "redacted_reason_codes": dict(row.redacted_reason_codes or {}),
        "redacted_source_refs": list(row.redacted_source_refs or []),
        "redacted_evidence_refs": list(row.redacted_evidence_refs or []),
        "freshness_ttl_summary": dict(row.freshness_ttl_summary or {}),
        "revocation_snapshot_summary": dict(row.revocation_snapshot_summary or {}),
        "risk_tier_summary": dict(row.risk_tier_summary or {}),
        "unsupported_capability_summary": dict(row.unsupported_capability_summary or {}),
        "blocked_capability_summary": dict(row.blocked_capability_summary or {}),
        "retention_boundary": {
            "retention_class": str(row.retention_class),
            "retention_days": int(row.retention_days),
            "retain_until": _c6y2_row_time(row.retain_until),
            "retention_clock_source": str(row.retention_clock_source),
            "persistence_required": False,
            "requires_separate_persistence_approval": False,
            "export_file_writer_added": bool(row.export_writer_added),
            "generated_artifact_written": bool(row.generated_artifact_written),
        },
        "redaction_boundary": {
            "redacted_refs_only": True,
            "raw_payloads_included": False,
            "private_values_blocked": True,
            "raw_reviewer_identity_included": False,
            "non_sensitive_evidence_refs_only": True,
        },
        "next_step_labels": list(row.next_step_labels or []),
        "allowed_to_execute": bool(row.allowed_to_execute),
        "no_execution": bool(row.no_execution),
        "review_manifest_only": bool(row.review_manifest_only),
        "retention_boundary_only": bool(row.retention_boundary_only),
        "audit_export_bundle_review_only": bool(row.audit_export_bundle_review_only),
        "export_file_written": bool(row.export_file_written),
        "export_writer_added": bool(row.export_writer_added),
        "generated_artifact_written": bool(row.generated_artifact_written),
        "scheduler_added": False,
        "raw_payloads_included": False,
        "non_authoritative_for_transaction": bool(row.non_authoritative_for_transaction),
        "no_checkout_payment_enablement": bool(row.no_checkout_payment_enablement),
        "no_live_provider_enablement": bool(row.no_live_provider_enablement),
        "no_public_discovery_enablement": bool(row.no_public_discovery_enablement),
        "grantex_runtime_required": False,
    }


def _c6y2_query_matches(manifest: Mapping[str, Any], query: OacpAuditReviewManifestRepositoryQuery) -> bool:
    return (
        (query.tenant_id is None or manifest.get("tenant_id") == query.tenant_id)
        and (query.merchant_id is None or manifest.get("merchant_id") == query.merchant_id)
        and (query.seller_agent_id is None or manifest.get("seller_agent_id") == query.seller_agent_id)
        and (query.buyer_agent_id is None or manifest.get("buyer_agent_id") == query.buyer_agent_id)
        and (query.bundle_id is None or manifest.get("bundle_id") == query.bundle_id)
        and (
            query.retention_class is None
            or _c6y2_retention_boundary(manifest).get("retention_class") == query.retention_class
        )
    )


class DurableOacpAuditReviewManifestRepository:
    """Async SQLAlchemy-backed review manifest repository; it writes no export files and executes nothing."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow

        store_result = _c6y2_manifest_safe_for_storage(manifest)
        if store_result["stored"] is not True:
            return store_result

        manifest_id = cast(str, store_result["manifest_id"])
        row = await self._session.get(OacpAuditReviewManifestRecordRow, manifest_id)
        if row is None:
            row = OacpAuditReviewManifestRecordRow(manifest_id=manifest_id)
            self._session.add(row)
        _c6y2_apply_manifest_to_row(row, manifest)
        await self._session.flush()
        return store_result

    async def get_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow

        row = await self._session.get(OacpAuditReviewManifestRecordRow, manifest_id)
        return None if row is None else _c6y2_row_to_manifest(row)

    async def list_manifests_for_scope(
        self,
        query: OacpAuditReviewManifestRepositoryQuery,
    ) -> tuple[dict[str, Any], ...]:
        from sqlalchemy import select

        from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow

        statement = select(OacpAuditReviewManifestRecordRow)
        if query.tenant_id is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.tenant_id == query.tenant_id)
        if query.merchant_id is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.merchant_id == query.merchant_id)
        if query.seller_agent_id is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.seller_agent_id == query.seller_agent_id)
        if query.buyer_agent_id is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.buyer_agent_id == query.buyer_agent_id)
        if query.bundle_id is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.bundle_id == query.bundle_id)
        if query.retention_class is not None:
            statement = statement.where(OacpAuditReviewManifestRecordRow.retention_class == query.retention_class)

        rows = (await self._session.scalars(statement.order_by(OacpAuditReviewManifestRecordRow.manifest_id))).all()
        manifests = tuple(_c6y2_row_to_manifest(row) for row in rows)
        return tuple(manifest for manifest in manifests if _c6y2_query_matches(manifest, query))

    async def evaluate_manifest_for_internal_review(self, manifest_id: str) -> dict[str, Any]:
        manifest = await self.get_manifest(manifest_id)
        if manifest is None:
            return _c6y2_manifest_repository_refusal(
                status="blocked",
                refusal_code="manifest_missing",
                message="C6Y2 requires a stored review manifest before internal review evaluation.",
            )
        store_result = _c6y2_manifest_safe_for_storage(manifest)
        if store_result["stored"] is not True:
            return store_result
        return {
            "evaluated": True,
            "status": "internal_review_requires_separate_export_approval",
            "manifest_id": manifest_id,
            "bundle_id": manifest["bundle_id"],
            "retention_class": _c6y2_retention_boundary(manifest)["retention_class"],
            "retain_until": _c6y2_retention_boundary(manifest)["retain_until"],
            "future_export_allowed": False,
            "allowed_to_execute": False,
            "no_execution": True,
            "review_manifest_only": True,
            "retention_boundary_only": True,
            "audit_export_bundle_review_only": True,
            "export_file_written": False,
            "export_writer_added": False,
            "non_authoritative_for_transaction": True,
            "no_checkout_payment_enablement": True,
            "no_live_provider_enablement": True,
            "no_public_discovery_enablement": True,
            "grantex_runtime_required": False,
            "message": "C6Y2 stores an audit review manifest only; it does not write export files.",
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
