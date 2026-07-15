"""Fail-closed, file-backed claim registry service."""

from __future__ import annotations

from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from core.claims.schema import (
    CapabilityRecord,
    ClaimKind,
    ClaimRecord,
    ClaimRegistryDocument,
    ClaimTreatment,
    EvidenceResult,
    GateResult,
    InternalMaturity,
    PublicAvailability,
    ValidationIssue,
    ValidationReport,
)

_MATURITY = {value: index for index, value in enumerate(InternalMaturity)}
_AVAILABILITY = {
    PublicAvailability.UNAVAILABLE: 0,
    PublicAvailability.PREVIEW: 1,
    PublicAvailability.BETA: 2,
    PublicAvailability.LIMITED_AVAILABILITY: 3,
    PublicAvailability.GA: 4,
    PublicAvailability.DEPRECATED: -1,
}
_TREATMENT = {value: index for index, value in enumerate(ClaimTreatment)}
_MAX_AVAILABILITY = {
    InternalMaturity.MISSING: PublicAvailability.UNAVAILABLE,
    InternalMaturity.SCAFFOLDED: PublicAvailability.PREVIEW,
    InternalMaturity.IMPLEMENTED: PublicAvailability.BETA,
    InternalMaturity.INTEGRATED: PublicAvailability.BETA,
    InternalMaturity.SANDBOX_PROVEN: PublicAvailability.LIMITED_AVAILABILITY,
    InternalMaturity.PRODUCTION_PROVEN: PublicAvailability.LIMITED_AVAILABILITY,
    InternalMaturity.GA: PublicAvailability.GA,
}


def load_claim_registry(path: str | Path) -> ClaimRegistryDocument:
    return ClaimRegistryDocument.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    return current


def _surface_allowed(surface: str, patterns: list[str]) -> bool:
    normalized = surface.replace("\\", "/").lstrip("./")
    return any(fnmatchcase(normalized, pattern.replace("\\", "/").lstrip("./")) for pattern in patterns)


class ClaimRegistryService:
    def __init__(self, document: ClaimRegistryDocument):
        self.document = document
        self.capabilities = {x.capability_id: x for x in document.capabilities}
        self.evidence = {x.evidence_id: x for x in document.evidence}
        self.claims = {x.claim_id: x for x in document.claims}

    @classmethod
    def from_file(cls, path: str | Path) -> ClaimRegistryService:
        return cls(load_claim_registry(path))

    def validate(self, *, now: datetime | None = None) -> ValidationReport:
        checked_at = _now(now)
        issues: list[ValidationIssue] = []
        if self.document.generated_at > checked_at:
            issues.append(
                ValidationIssue(code="registry_generated_in_future", message="registry generated_at is in the future")
            )
        for capability in self.document.capabilities:
            issues.extend(self._capability_issues(capability, checked_at))
        for claim in self.document.claims:
            issues.extend(self._claim_issues(claim, checked_at, surface=None))
        return ValidationReport(registry_version=self.document.registry_version, checked_at=checked_at, issues=issues)

    def authorize_claim(self, claim_id: str, *, surface: str, now: datetime | None = None) -> ValidationReport:
        checked_at = _now(now)
        claim = self.claims.get(claim_id)
        if claim is None:
            issues = [
                ValidationIssue(
                    code="claim_not_registered",
                    message=f"claim {claim_id!r} is not registered",
                    claim_id=claim_id,
                    surface=surface,
                )
            ]
        else:
            issues = self._claim_issues(claim, checked_at, surface=surface)
        return ValidationReport(registry_version=self.document.registry_version, checked_at=checked_at, issues=issues)

    def _capability_issues(self, capability: CapabilityRecord, now: datetime) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        def add(code: str, message: str, *, evidence_id: str | None = None) -> None:
            issues.append(
                ValidationIssue(
                    code=code, message=message, capability_id=capability.capability_id, evidence_id=evidence_id
                )
            )

        if capability.expires_at <= now:
            add("capability_expired", "capability readiness record is expired")
        if capability.gate_result is GateResult.EXPIRED:
            add("capability_gate_expired", "capability gate result is Expired")
        maximum = _MAX_AVAILABILITY[capability.maturity]
        if _AVAILABILITY[capability.public_availability] > _AVAILABILITY[maximum]:
            add("availability_exceeds_maturity", f"{capability.public_availability} exceeds {capability.maturity}")
        if capability.public_availability is PublicAvailability.GA and capability.gate_result is not GateResult.PASSED:
            add("ga_gate_not_passed", "GA availability requires a Passed gate")
        for evidence_id in capability.evidence_ids:
            evidence = self.evidence.get(evidence_id)
            if evidence is None:
                add("capability_evidence_missing", "capability references missing evidence", evidence_id=evidence_id)
            elif capability.capability_id not in evidence.capability_ids:
                add("capability_evidence_scope_mismatch", "evidence does not cover capability", evidence_id=evidence_id)
        for claim_id in capability.permitted_claim_ids:
            if claim_id not in self.claims:
                issues.append(
                    ValidationIssue(
                        code="permitted_claim_missing",
                        message="permitted claim is not registered",
                        capability_id=capability.capability_id,
                        claim_id=claim_id,
                    )
                )
        return issues

    def _claim_issues(self, claim: ClaimRecord, now: datetime, surface: str | None) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        def add(code: str, message: str, *, capability_id: str | None = None, evidence_id: str | None = None) -> None:
            issues.append(
                ValidationIssue(
                    code=code,
                    message=message,
                    claim_id=claim.claim_id,
                    capability_id=capability_id,
                    evidence_id=evidence_id,
                    surface=surface,
                )
            )

        if claim.expires_at <= now:
            add("claim_expired", "claim approval is expired")
        if claim.product_version != self.document.product_version:
            add("claim_product_version_mismatch", "claim product_version differs from registry")
        if surface is not None:
            if claim.treatment is ClaimTreatment.HIDDEN:
                add("hidden_claim_on_public_surface", "Hidden claim cannot appear publicly")
            elif not _surface_allowed(surface, claim.surfaces):
                add("claim_surface_not_permitted", "claim is not permitted on this surface")
        if claim.kind is ClaimKind.INVENTORY:
            return issues
        capabilities: list[CapabilityRecord] = []
        for capability_id in claim.capability_ids:
            capability = self.capabilities.get(capability_id)
            if capability is None:
                add("claim_capability_missing", "claim references missing capability", capability_id=capability_id)
                continue
            capabilities.append(capability)
            if claim.claim_id not in capability.permitted_claim_ids:
                add(
                    "claim_not_permitted_by_capability",
                    "capability does not permit this claim",
                    capability_id=capability_id,
                )
            if _TREATMENT[claim.treatment] > _TREATMENT[capability.claim_treatment]:
                add(
                    "claim_treatment_exceeds_capability",
                    "claim treatment exceeds capability treatment",
                    capability_id=capability_id,
                )
            if claim.expires_at > capability.expires_at:
                add("claim_expiry_exceeds_capability", "claim outlives capability review", capability_id=capability_id)
            if claim.kind is ClaimKind.AVAILABILITY and claim.asserted_availability is not None:
                if _AVAILABILITY[claim.asserted_availability] > _AVAILABILITY[capability.public_availability]:
                    add(
                        "claim_availability_exceeds_capability",
                        "asserted availability exceeds capability",
                        capability_id=capability_id,
                    )
        if claim.treatment in {ClaimTreatment.HIDDEN, ClaimTreatment.ILLUSTRATIVE}:
            return issues
        required = claim.required_evidence_state
        assert required is not None
        for capability in capabilities:
            if capability.expires_at <= now:
                add(
                    "claim_capability_expired",
                    "supporting capability is expired",
                    capability_id=capability.capability_id,
                )
            if capability.gate_result is not GateResult.PASSED:
                add(
                    "claim_capability_gate_not_passed",
                    "supporting capability gate is not Passed",
                    capability_id=capability.capability_id,
                )
            if _MATURITY[capability.maturity] < _MATURITY[required]:
                add(
                    "claim_capability_state_insufficient",
                    "capability maturity is below required evidence state",
                    capability_id=capability.capability_id,
                )
            if capability.public_availability in {PublicAvailability.UNAVAILABLE, PublicAvailability.DEPRECATED}:
                add(
                    "claim_capability_not_public",
                    "capability is not publicly available",
                    capability_id=capability.capability_id,
                )
        covered: set[str] = set()
        for evidence_id in claim.evidence_ids:
            evidence = self.evidence.get(evidence_id)
            if evidence is None:
                add("claim_evidence_missing", "claim references missing evidence", evidence_id=evidence_id)
                continue
            covered.update(evidence.capability_ids)
            if evidence.result is not EvidenceResult.PASSED:
                add("claim_evidence_not_passed", f"evidence result is {evidence.result}", evidence_id=evidence_id)
            if evidence.expires_at <= now:
                add("claim_evidence_expired", "supporting evidence is expired", evidence_id=evidence_id)
            if evidence.executed_at > now:
                add(
                    "claim_evidence_from_future",
                    "supporting evidence execution is in the future",
                    evidence_id=evidence_id,
                )
            if _MATURITY[evidence.state] < _MATURITY[required]:
                add(
                    "claim_evidence_state_insufficient",
                    "evidence state is below claim requirement",
                    evidence_id=evidence_id,
                )
            if evidence.product_version != claim.product_version:
                add(
                    "claim_evidence_product_version_mismatch",
                    "evidence product_version differs from claim",
                    evidence_id=evidence_id,
                )
            if claim.expires_at > evidence.expires_at:
                add("claim_expiry_exceeds_evidence", "claim outlives supporting evidence", evidence_id=evidence_id)
            for capability_id in evidence.capability_ids:
                capability = self.capabilities.get(capability_id)
                if capability is not None and evidence_id not in capability.evidence_ids:
                    add(
                        "evidence_not_registered_on_capability",
                        "evidence is not registered on capability",
                        capability_id=capability_id,
                        evidence_id=evidence_id,
                    )
        for capability_id in claim.capability_ids:
            if capability_id not in covered:
                add(
                    "claim_capability_evidence_missing",
                    "no claim evidence covers capability",
                    capability_id=capability_id,
                )
        return issues


def validate_claims(registry: ClaimRegistryDocument | str | Path, *, now: datetime | None = None) -> ValidationReport:
    document = load_claim_registry(registry) if isinstance(registry, (str, Path)) else registry
    return ClaimRegistryService(document).validate(now=now)
