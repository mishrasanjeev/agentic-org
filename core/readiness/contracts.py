"""Canonical contracts for the capability readiness/evidence ledger."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from core.claims.schema import ClaimTreatment, GateResult, InternalMaturity, PublicAvailability

# Re-export the exact vocabulary used by the documentation and public-claim registry.
InternalMaturityState = InternalMaturity
ReleaseGateState = GateResult
PublicAvailabilityState = PublicAvailability
ClaimState = ClaimTreatment


class ScopeDisposition(StrEnum):
    MANDATORY = "Mandatory"
    CONDITIONAL = "Conditional"
    OUT_OF_SCOPE = "OutOfScope"


class EvidenceEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    INTEGRATION = "integration"
    VENDOR_SANDBOX = "vendor_sandbox"
    STAGING = "staging"
    CONTROLLED_PILOT = "controlled_pilot"
    PRODUCTION = "production"


class EvidenceTrustState(StrEnum):
    """Server-controlled evidence trust; public submissions start unverified."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


CANONICAL_GATE_IDS: tuple[str, ...] = (
    "product_workflow",
    "data_contract",
    "tenant_company_isolation",
    "connector_lifecycle",
    "agent_contract",
    "workflow_durability",
    "external_write_safety",
    "kpi_trust",
    "security_privacy",
    "reliability",
    "quality",
    "documentation",
    "support",
    "evidence",
    "public_truth",
)
REQUIRED_OWNER_ROLES = frozenset(
    {
        "product",
        "engineering",
        "data",
        "security_privacy",
        "domain_approver",
        "sre_support",
        "documentation",
    }
)
REQUIRED_TRACEABILITY_GROUPS = frozenset(
    {
        "gap_ids",
        "roadmap_ids",
        "implementation_refs",
        "migration_refs",
        "test_refs",
        "threat_privacy_refs",
        "runbook_refs",
        "slo_dashboard_refs",
        "release_manifest_refs",
    }
)
CONDITIONAL_MINIMUM_GATE_IDS = frozenset(
    {"tenant_company_isolation", "external_write_safety", "security_privacy", "quality", "evidence", "public_truth"}
)
INTERNAL_MATURITY_RANK = {state: rank for rank, state in enumerate(InternalMaturity)}
PUBLIC_AVAILABILITY_RANK = {
    PublicAvailability.DEPRECATED: -1,
    PublicAvailability.UNAVAILABLE: 0,
    PublicAvailability.PREVIEW: 1,
    PublicAvailability.BETA: 2,
    PublicAvailability.LIMITED_AVAILABILITY: 3,
    PublicAvailability.GA: 4,
}
CLAIM_STATE_RANK = {state: rank for rank, state in enumerate(ClaimTreatment)}
PUBLIC_MINIMUM_INTERNAL = {
    PublicAvailability.DEPRECATED: InternalMaturity.MISSING,
    PublicAvailability.UNAVAILABLE: InternalMaturity.MISSING,
    PublicAvailability.PREVIEW: InternalMaturity.SCAFFOLDED,
    PublicAvailability.BETA: InternalMaturity.IMPLEMENTED,
    PublicAvailability.LIMITED_AVAILABILITY: InternalMaturity.SANDBOX_PROVEN,
    PublicAvailability.GA: InternalMaturity.GA,
}
CAPABILITY_ID_PATTERN = re.compile(r"^(?:[A-Z]+-C\d{2}|[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_-]*)+)$")
CLAIM_ID_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
LEDGER_POLICY_VERSION = "capability-readiness-v1"


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    capability_id: str
    domain: str
    title: str
    description: str
    scope_disposition: ScopeDisposition
    scope_condition: str | None
    scope_details: dict[str, object]
    required_gate_ids: tuple[str, ...]
    owners: dict[str, str]
    approver_ids: tuple[str, ...]
    traceability: dict[str, tuple[str, ...]]
    limitations: tuple[str, ...]
    feature_flag: str | None
    review_expires_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceRegistration:
    evidence_version: str
    evidence_type: str
    artifact_uri: str
    sha256_checksum: str
    environment: EvidenceEnvironment
    provider_account_class: str
    product_version: str
    source_commit_sha: str
    observed_at: datetime
    expires_at: datetime
    supports_gate_ids: tuple[str, ...]
    supports_claim_ids: tuple[str, ...]
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    evidence_id: UUID
    tenant_id: UUID
    company_id: UUID | None
    capability_id: str
    evidence_version: str
    evidence_type: str
    artifact_uri: str
    sha256_checksum: str
    environment: EvidenceEnvironment
    provider_account_class: str
    product_version: str
    source_commit_sha: str
    trust_state: EvidenceTrustState
    submitted_by: str
    reviewed_at: datetime | None
    expires_at: datetime
    reviewed_by: str | None
    supports_gate_ids: tuple[str, ...]
    supports_claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateAttestation:
    gate_id: str
    status: GateResult
    evidence_ids: tuple[UUID, ...]
    reviewed_by: str | None
    reviewed_at: datetime | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class CurrentReadinessState:
    internal_maturity: InternalMaturity
    release_gate: GateResult
    public_availability: PublicAvailability
    claim_state: ClaimTreatment
    gate_results: dict[str, object]
    permitted_claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadinessTransition:
    target_internal_maturity: InternalMaturity
    target_release_gate: GateResult
    target_public_availability: PublicAvailability
    target_claim_state: ClaimTreatment
    gate_attestations: tuple[GateAttestation, ...]
    evidence_ids: tuple[UUID, ...]
    permitted_claim_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    reason: str
    approved_by: str
    approval_reference: str | None


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    release_gate: GateResult
    gate_results: dict[str, object]
    evidence_snapshot: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ReviewRenewal:
    expected_sequence: int
    valid_for_days: int
    reason: str


@dataclass(frozen=True, slots=True)
class OwnerRotation:
    expected_sequence: int
    owners: dict[str, str]
    approver_ids: tuple[str, ...]
    reason: str
