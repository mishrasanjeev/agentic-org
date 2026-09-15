# SPDX-License-Identifier: Apache-2.0
"""Domain types exchanged with a :class:`~connectors.framework.verification_provider.VerificationProvider`.

These are domain concepts - businesses, registry status, owners and controllers, screening hits -
not any one data source's response shape. Documents that have a published schema serialise to it:
``OwnershipGraph`` to ``ownership_graph`` and ``ScreeningResult`` to ``screening_result``, and
``Evidence`` is the ``evidence`` definition every schema cites (see ``docs/schemas/domain-schemas.md``).

Import these from ``connectors.framework.verification_provider``; this module exists to keep that
one small.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION = "1.0.0"

ProviderName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
IdentifierToken = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")
]
IdempotencyKey = Annotated[str, StringConstraints(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")]
Jurisdiction = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
PartialDate = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12][0-9]|3[01]))?)?$")]
ExcerptRef = Annotated[str, StringConstraints(pattern=r"^excerpt:[A-Za-z0-9._:\-]{1,128}$")]
FieldPath = Annotated[str, StringConstraints(max_length=256, pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+|\[[0-9]+\])*$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
VocabularyTerm = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
Name = Annotated[str, StringConstraints(min_length=1, max_length=512)]
HttpUrl = Annotated[str, StringConstraints(max_length=2048, pattern=r"^https?://[^\s/]+(/[^\s]*)?$")]
DomainName = Annotated[
    str, StringConstraints(max_length=253, pattern=r"^([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
]


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- shared -------------------------------------------------------------------------------------


class Evidence(DomainModel):
    """One upstream record behind a value."""

    provider: ProviderName
    record_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    field: FieldPath
    retrieved_at: AwareDatetime
    excerpt_ref: ExcerptRef | None = None


class Identifier(DomainModel):
    """A registry or tax identifier, e.g. ``Identifier(scheme="gb_company_number", value="00000001")``."""

    scheme: VocabularyTerm
    value: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class Address(DomainModel):
    lines: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=256)], ...], Field(min_length=1, max_length=6)
    ]
    locality: Annotated[str, StringConstraints(max_length=128)] | None = None
    region: Annotated[str, StringConstraints(max_length=128)] | None = None
    postal_code: Annotated[str, StringConstraints(max_length=32)] | None = None
    country: CountryCode


class PartyKind(StrEnum):
    PERSON = "person"
    BUSINESS = "business"


class RegistryStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DISSOLVED = "dissolved"
    IN_INSOLVENCY = "in_insolvency"
    UNKNOWN = "unknown"


class BusinessRef(DomainModel):
    """A business as one provider identifies it. ``provider_ref`` is opaque to everyone but that provider."""

    provider: ProviderName
    provider_ref: IdentifierToken
    jurisdiction: Jurisdiction
    identifiers: tuple[Identifier, ...] = ()


# --- resolve ------------------------------------------------------------------------------------


class BusinessQuery(DomainModel):
    """Find candidate businesses. Providers raise ``InvalidQuery`` when neither a name nor an identifier is given.

    Results are ordered best match first and paged with ``offset`` and ``limit``; a page shorter than
    ``limit`` is the last one.
    """

    name: Name | None = None
    jurisdiction: Jurisdiction | None = None
    identifiers: tuple[Identifier, ...] = ()
    address: Address | None = None
    website: HttpUrl | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0, le=10_000)] = 0

    @property
    def has_search_terms(self) -> bool:
        return bool((self.name and self.name.strip()) or self.identifiers)

    def next_page(self, page: Sequence[object]) -> Self:
        return self.model_copy(update={"offset": self.offset + len(page)})


class BusinessCandidate(DomainModel):
    ref: BusinessRef
    legal_name: Name
    registry_status: RegistryStatus
    registered_address: Address | None = None
    match_score: Annotated[float, Field(ge=0, le=1)] | None = None
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


# --- verify -------------------------------------------------------------------------------------


class VerificationCheck(StrEnum):
    REGISTRATION = "registration"
    NAME = "name"
    ADDRESS = "address"
    IDENTIFIERS = "identifiers"
    OFFICERS = "officers"


class DeclaredBusiness(DomainModel):
    """What the applicant declared, for the provider to check against the registry."""

    legal_name: Name | None = None
    registered_address: Address | None = None
    identifiers: tuple[Identifier, ...] = ()


class VerifyOptions(DomainModel):
    """``idempotency_key`` makes a repeated start return the same handle instead of starting another."""

    idempotency_key: IdempotencyKey
    checks: frozenset[VerificationCheck] = frozenset(VerificationCheck)
    declared: DeclaredBusiness | None = None


class VerificationHandle(DomainModel):
    provider: ProviderName
    verification_id: IdentifierToken
    ref: BusinessRef
    requested_at: AwareDatetime


class Pending(DomainModel):
    """A verification that has not finished. A value, not an error: poll again after ``retry_after_seconds``."""

    handle: VerificationHandle
    state: Literal["queued", "in_progress"]
    retry_after_seconds: Annotated[float, Field(ge=0, le=3600)]


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    NOT_CHECKED = "not_checked"


class CheckResult(DomainModel):
    check: VerificationCheck
    outcome: CheckOutcome
    evidence: tuple[Evidence, ...] = ()


class OfficerRole(StrEnum):
    DIRECTOR = "director"
    SECRETARY = "secretary"
    MANAGER = "manager"
    MEMBER = "member"
    PARTNER = "partner"
    OTHER_OFFICER = "other_officer"


class Officer(DomainModel):
    name: Name
    role: OfficerRole
    appointed_on: PartialDate | None = None
    resigned_on: PartialDate | None = None
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class BusinessVerification(DomainModel):
    handle: VerificationHandle
    completed_at: AwareDatetime
    legal_name: Name
    registry_status: RegistryStatus
    entity_type: VocabularyTerm | None = None
    incorporated_on: PartialDate | None = None
    dissolved_on: PartialDate | None = None
    registered_address: Address | None = None
    identifiers: tuple[Identifier, ...] = ()
    officers: tuple[Officer, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


# --- ownership ----------------------------------------------------------------------------------


NodeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")]


class OwnershipNodeKind(StrEnum):
    PERSON = "person"
    BUSINESS = "business"
    TRUST = "trust"
    PUBLIC_BODY = "public_body"
    UNKNOWN = "unknown"


class OwnershipRelationship(StrEnum):
    SHAREHOLDING = "shareholding"
    VOTING_RIGHTS = "voting_rights"
    APPOINTMENT_RIGHTS = "appointment_rights"
    SIGNIFICANT_INFLUENCE = "significant_influence"
    TRUSTEE = "trustee"
    NOMINEE = "nominee"


class PercentageRange(DomainModel):
    """Registries often report bands (25-50%); an exact figure is a range with ``min == max``."""

    min: Annotated[float, Field(ge=0, le=100)]
    max: Annotated[float, Field(ge=0, le=100)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


class OwnershipNode(DomainModel):
    node_id: NodeId
    kind: OwnershipNodeKind
    name: Name
    jurisdiction: Jurisdiction | None = None
    identifiers: tuple[Identifier, ...] = ()
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()
    address: Address | None = None
    evidence: tuple[Evidence, ...]


class OwnershipEdge(DomainModel):
    """``from_node_id`` owns or controls ``to_node_id``."""

    from_node_id: NodeId
    to_node_id: NodeId
    relationship: OwnershipRelationship
    share_pct: PercentageRange | None = None
    voting_pct: PercentageRange | None = None
    started_on: PartialDate | None = None
    ended_on: PartialDate | None = None
    evidence: tuple[Evidence, ...]


class OwnershipGraph(DomainModel):
    """Serialises to the ``ownership_graph`` schema. Edges must reference nodes in the graph."""

    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.[0-9]+\.[0-9]+$")] = SCHEMA_VERSION
    provider: ProviderName
    subject: BusinessRef
    subject_node_id: NodeId
    as_of: AwareDatetime
    completeness: Literal["complete", "partial", "unknown"]
    nodes: tuple[OwnershipNode, ...]
    edges: tuple[OwnershipEdge, ...]
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def _referentially_intact(self) -> Self:
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node_id values must be unique")
        known = set(ids)
        if self.subject_node_id not in known:
            raise ValueError("subject_node_id must be one of the nodes")
        for edge in self.edges:
            if edge.from_node_id not in known or edge.to_node_id not in known:
                raise ValueError(f"edge {edge.from_node_id}->{edge.to_node_id} references an unknown node")
        return self


# --- screening ----------------------------------------------------------------------------------


class ListType(StrEnum):
    SANCTIONS = "sanctions"
    PEP = "pep"
    WATCHLIST = "watchlist"
    ENFORCEMENT = "enforcement"
    ADVERSE_MEDIA = "adverse_media"


class PersonSubject(DomainModel):
    full_name: Name
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()
    address: Address | None = None
    identifiers: tuple[Identifier, ...] = ()


class BusinessSubject(DomainModel):
    legal_name: Name
    jurisdiction: Jurisdiction | None = None
    identifiers: tuple[Identifier, ...] = ()
    address: Address | None = None
    ref: BusinessRef | None = None


class ScreenOptions(DomainModel):
    idempotency_key: IdempotencyKey
    list_types: frozenset[ListType] = frozenset(ListType)


class ScreeningSubject(DomainModel):
    """The identifiers that were submitted, as recorded in the result."""

    name: Name
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()
    jurisdiction: Jurisdiction | None = None
    address: Address | None = None
    identifiers: tuple[Identifier, ...] = ()


class ListSource(DomainModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    authority: Annotated[str, StringConstraints(max_length=256)] | None = None
    jurisdiction: Jurisdiction | None = None


class ScreeningHit(DomainModel):
    """A candidate match. Deciding whether it is the subject is a disposition, not part of the hit."""

    hit_id: IdentifierToken
    list_type: ListType
    source: ListSource
    entry_kind: PartyKind | None = None
    matched_name: Name
    aliases: tuple[Name, ...] = ()
    dates_of_birth: tuple[PartialDate, ...] = ()
    nationalities: tuple[CountryCode, ...] = ()
    addresses: tuple[Address, ...] = ()
    associated_entities: tuple[Name, ...] = ()
    listed_on: PartialDate | None = None
    name_similarity: Annotated[float, Field(ge=0, le=1)] | None = None
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class ScreeningResult(DomainModel):
    """Serialises to the ``screening_result`` schema."""

    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.[0-9]+\.[0-9]+$")] = SCHEMA_VERSION
    screening_id: IdentifierToken
    provider: ProviderName
    subject_kind: PartyKind
    subject: ScreeningSubject
    screened_at: AwareDatetime
    list_types: Annotated[tuple[ListType, ...], Field(min_length=1)]
    hits: tuple[ScreeningHit, ...]
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def _list_types_unique(self) -> Self:
        if len(set(self.list_types)) != len(self.list_types):
            raise ValueError("list_types must not repeat")
        return self


# --- web presence -------------------------------------------------------------------------------


#: Serialisation context that includes :class:`UntrustedText` content, e.g.
#: ``model.model_dump(mode="json", context=INCLUDE_UNTRUSTED_TEXT)``. Use it only to move the content
#: to the untrusted-content extractor or between a provider and its own service.
INCLUDE_UNTRUSTED_TEXT: dict[str, bool] = {"include_untrusted_text": True}


class UntrustedText(DomainModel):
    """Attacker-controllable text (website copy). Never put it in a model prompt.

    The content does not leak by accident: ``str()`` and ``repr()`` show only its length, and
    ``model_dump()`` / JSON serialisation - of this value or of any model containing it - produce a
    redacted reference ``{"redacted": true, "characters": n, "sha256": "sha256:..."}`` unless the
    caller passes ``context=INCLUDE_UNTRUSTED_TEXT``. A redacted reference does not validate back
    into ``UntrustedText``. Read the content with :meth:`unsafe_value`, inside the untrusted-content
    extractor.
    """

    value: Annotated[str, StringConstraints(max_length=1_000_000), Field(repr=False)]

    def unsafe_value(self) -> str:
        """The raw content. Only the untrusted-content extractor should call this."""
        return self.value

    @property
    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.value.encode("utf-8")).hexdigest()

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo) -> Any:
        context = info.context
        if isinstance(context, dict) and context.get("include_untrusted_text") is True:
            return handler(self)
        return {"redacted": True, "characters": len(self.value), "sha256": self.sha256}

    def __str__(self) -> str:
        return f"<untrusted text: {len(self.value)} characters>"

    def __repr__(self) -> str:
        return f"UntrustedText(<{len(self.value)} characters>)"


class WebDomain(DomainModel):
    domain: DomainName
    registered_on: PartialDate | None = None
    resolves: bool | None = None
    tls_valid: bool | None = None
    evidence: tuple[Evidence, ...] = ()


class WebPage(DomainModel):
    url: HttpUrl
    fetched_at: AwareDatetime
    http_status: Annotated[int, Field(ge=100, le=599)]
    media_type: Annotated[str, StringConstraints(pattern=r"^[a-z]+/[a-z0-9.+\-]+$")]
    content: UntrustedText | None = None
    content_sha256: Sha256Digest | None = None
    excerpt_ref: ExcerptRef | None = None
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class WebPresence(DomainModel):
    provider: ProviderName
    subject: BusinessRef
    observed_at: AwareDatetime
    domains: tuple[WebDomain, ...] = ()
    pages: tuple[WebPage, ...] = ()
    evidence: tuple[Evidence, ...] = ()


# --- monitoring and events ----------------------------------------------------------------------


class ProviderEventType(StrEnum):
    BUSINESS_DISSOLVED = "business.dissolved"
    BUSINESS_STATUS_CHANGED = "business.status_changed"
    OWNERSHIP_CHANGED = "ownership.changed"
    OFFICERS_CHANGED = "officers.changed"
    SCREENING_NEW_MATCH = "screening.new_match"


class MonitorOptions(DomainModel):
    idempotency_key: IdempotencyKey
    event_types: frozenset[ProviderEventType] = frozenset(ProviderEventType)


class MonitorHandle(DomainModel):
    """Identifies an enrolment. ``offset`` and ``limit`` page through its alerts, oldest first."""

    provider: ProviderName
    monitor_id: IdentifierToken
    ref: BusinessRef
    enrolled_at: AwareDatetime
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=500)] = 50

    def next_page(self, page: Sequence[object]) -> Self:
        return self.model_copy(update={"offset": self.offset + len(page)})


class MonitorAlert(DomainModel):
    alert_id: IdentifierToken
    provider: ProviderName
    monitor_id: IdentifierToken
    ref: BusinessRef
    event_type: ProviderEventType
    occurred_at: AwareDatetime
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class ProviderEvent(DomainModel):
    """A webhook whose signature verified. Still only a trigger: re-query the provider before acting on it."""

    provider: ProviderName
    event_id: IdentifierToken
    event_type: ProviderEventType
    occurred_at: AwareDatetime
    subject: BusinessRef | None = None
    monitor_id: IdentifierToken | None = None
