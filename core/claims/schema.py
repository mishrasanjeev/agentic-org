"""Versioned schemas for public product claims and their evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

CLAIM_REGISTRY_SCHEMA_VERSION = "agenticorg.claim-registry.v1"
_ID = r"^[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)+$"


class InternalMaturity(StrEnum):
    MISSING = "Missing"
    SCAFFOLDED = "Scaffolded"
    IMPLEMENTED = "Implemented"
    INTEGRATED = "Integrated"
    SANDBOX_PROVEN = "SandboxProven"
    PRODUCTION_PROVEN = "ProductionProven"
    GA = "GA"


class GateResult(StrEnum):
    BLOCKED = "Blocked"
    IN_REVIEW = "InReview"
    PASSED = "Passed"
    EXPIRED = "Expired"
    NOT_ASSESSED = "NotAssessed"


class PublicAvailability(StrEnum):
    UNAVAILABLE = "Unavailable"
    PREVIEW = "Preview"
    BETA = "Beta"
    LIMITED_AVAILABILITY = "LimitedAvailability"
    GA = "GA"
    DEPRECATED = "Deprecated"


class ClaimTreatment(StrEnum):
    HIDDEN = "Hidden"
    ILLUSTRATIVE = "Illustrative"
    QUALIFIED = "Qualified"
    EVIDENCE_BACKED = "EvidenceBacked"


class ClaimKind(StrEnum):
    INVENTORY = "inventory"
    COMMERCIAL = "commercial"
    AVAILABILITY = "availability"
    PERFORMANCE = "performance"
    OUTCOME = "outcome"
    CERTIFICATION = "certification"
    RATING = "rating"


class EvidenceResult(StrEnum):
    PASSED = "Passed"
    FAILED = "Failed"
    PARTIAL = "Partial"
    REVOKED = "Revoked"


class StrictRegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value


def _unique(values: list[str], name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    if any(not value for value in values):
        raise ValueError(f"{name} must not contain blank values")
    return values


class CapabilityRecord(StrictRegistryModel):
    capability_id: str = Field(pattern=_ID)
    domain: str = Field(min_length=1)
    title: str = Field(min_length=1)
    maturity: InternalMaturity
    gate_result: GateResult
    public_availability: PublicAvailability
    claim_treatment: ClaimTreatment
    owner: str = Field(min_length=1)
    expires_at: datetime
    evidence_ids: list[str] = Field(default_factory=list)
    permitted_claim_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    _aware_expiry = field_validator("expires_at")(_aware)

    @field_validator("evidence_ids", "permitted_claim_ids")
    @classmethod
    def unique_refs(cls, value: list[str], info: ValidationInfo) -> list[str]:
        return _unique(value, info.field_name)


class EvidenceRecord(StrictRegistryModel):
    evidence_id: str = Field(pattern=_ID)
    capability_ids: list[str] = Field(min_length=1)
    uri: str = Field(min_length=1)
    checksum: str = Field(pattern=r"^sha256:[0-9a-fA-F]{64}$")
    environment: str = Field(min_length=1)
    provider_account_class: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    executed_at: datetime
    state: InternalMaturity
    result: EvidenceResult
    reviewer: str = Field(min_length=1)
    expires_at: datetime
    _aware_execution = field_validator("executed_at")(_aware)
    _aware_expiry = field_validator("expires_at")(_aware)

    @field_validator("capability_ids")
    @classmethod
    def unique_capabilities(cls, value: list[str]) -> list[str]:
        return _unique(value, "capability_ids")

    @model_validator(mode="after")
    def expiry_follows_execution(self) -> Self:
        if self.expires_at <= self.executed_at:
            raise ValueError("expires_at must be later than executed_at")
        return self


class ClaimRecord(StrictRegistryModel):
    claim_id: str = Field(pattern=_ID)
    kind: ClaimKind
    treatment: ClaimTreatment
    approved_text: list[str] = Field(min_length=1)
    capability_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    required_evidence_state: InternalMaturity | None = None
    asserted_availability: PublicAvailability | None = None
    owner: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    expires_at: datetime
    surfaces: list[str] = Field(default_factory=list)
    inventory_source: str | None = None
    limitations: list[str] = Field(default_factory=list)
    _aware_expiry = field_validator("expires_at")(_aware)

    @field_validator("approved_text", "capability_ids", "evidence_ids", "surfaces")
    @classmethod
    def unique_values(cls, value: list[str], info: ValidationInfo) -> list[str]:
        return _unique(value, info.field_name)

    @model_validator(mode="after")
    def enforce_claim_class(self) -> Self:
        inventory = self.kind is ClaimKind.INVENTORY
        illustrative = self.treatment is ClaimTreatment.ILLUSTRATIVE
        if self.treatment is ClaimTreatment.HIDDEN:
            if self.surfaces:
                raise ValueError("Hidden claims cannot map to public surfaces")
        elif not self.surfaces:
            raise ValueError("public claims must map to at least one surface")
        if inventory:
            if not self.inventory_source:
                raise ValueError("inventory claims require inventory_source")
            if self.evidence_ids or self.required_evidence_state is not None:
                raise ValueError("inventory claims use inventory_source, not evidence records")
        else:
            if self.inventory_source is not None:
                raise ValueError("inventory_source is only valid for inventory claims")
            if not self.capability_ids:
                raise ValueError("non-inventory claims require capability_ids")
        if illustrative:
            if self.evidence_ids or self.required_evidence_state is not None:
                raise ValueError("illustrative claims must remain separate from evidence-backed claims")
        elif not inventory and self.treatment is not ClaimTreatment.HIDDEN:
            if not self.evidence_ids or self.required_evidence_state is None:
                raise ValueError("non-inventory public claims require evidence_ids and required_evidence_state")
        if self.kind is ClaimKind.AVAILABILITY:
            if self.asserted_availability is None:
                raise ValueError("availability claims require asserted_availability")
        elif self.asserted_availability is not None:
            raise ValueError("asserted_availability is only valid for availability claims")
        return self


class ClaimRegistryDocument(StrictRegistryModel):
    schema_version: Literal["agenticorg.claim-registry.v1"]
    registry_version: str = Field(min_length=1)
    product_version: str = Field(min_length=1)
    generated_at: datetime
    capabilities: list[CapabilityRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    _aware_generated = field_validator("generated_at")(_aware)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        _unique([x.capability_id for x in self.capabilities], "capabilities")
        _unique([x.evidence_id for x in self.evidence], "evidence")
        _unique([x.claim_id for x in self.claims], "claims")
        return self


class ValidationIssue(StrictRegistryModel):
    severity: Literal["error", "warning"] = "error"
    code: str
    message: str
    claim_id: str | None = None
    capability_id: str | None = None
    evidence_id: str | None = None
    surface: str | None = None
    line: int | None = None


class ValidationReport(StrictRegistryModel):
    schema_version: Literal["agenticorg.claim-registry.v1"] = CLAIM_REGISTRY_SCHEMA_VERSION
    registry_version: str
    checked_at: datetime
    issues: list[ValidationIssue] = Field(default_factory=list)
    _aware_checked = field_validator("checked_at")(_aware)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)
