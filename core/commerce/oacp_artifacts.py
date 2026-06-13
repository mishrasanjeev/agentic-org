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
from typing import Any, Literal, Protocol, cast

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
