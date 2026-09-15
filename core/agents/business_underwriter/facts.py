# SPDX-License-Identifier: Apache-2.0
"""Deterministic facts derived from provider data: screening parties and policy evidence fields.

Everything a recommendation depends on is computed here from provider responses, the
application and the extractor's structured fields - never from model output. Missing or
unusable data becomes ``None``, which the policy engine treats as unresolved and never as a pass.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from connectors.framework.verification_provider import (
    Address,
    BusinessSubject,
    BusinessVerification,
    Identifier,
    OwnershipGraph,
    OwnershipNodeKind,
    PersonSubject,
    ScreeningResult,
)
from core.agents.business_underwriter.reconciliation import Reconciliation, normalise_name
from core.extraction._worker import ACTIVITY_KEYWORDS

TAX_ID_SCHEMES: Mapping[str, str] = {"US": "us_ein"}


@dataclass
class Party:
    """Someone to screen. ``sources`` names where the party came from (``applicant``, ``registry``, ``ownership``)."""

    party_id: str
    kind: str
    name: str
    date_of_birth: str | None = None
    nationalities: tuple[str, ...] = ()
    jurisdiction: str | None = None
    identifiers: tuple[dict[str, str], ...] = ()
    address: dict[str, Any] | None = None
    roles: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def key(self) -> str:
        tokens = " ".join(normalise_name(self.name, business=self.kind == "business"))
        return hashlib.sha256(f"{self.kind}\x1f{tokens}\x1f{self.date_of_birth or ''}".encode()).hexdigest()[:20]

    def person_subject(self) -> PersonSubject:
        return PersonSubject(
            full_name=self.name,
            date_of_birth=self.date_of_birth,
            nationalities=self.nationalities,
            address=Address.model_validate(self.address) if self.address else None,
            identifiers=tuple(Identifier.model_validate(i) for i in self.identifiers),
        )

    def business_subject(self) -> BusinessSubject:
        return BusinessSubject(
            legal_name=self.name,
            jurisdiction=self.jurisdiction,
            identifiers=tuple(Identifier.model_validate(i) for i in self.identifiers),
            address=Address.model_validate(self.address) if self.address else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "party_id": self.party_id,
            "kind": self.kind,
            "name": self.name,
            "date_of_birth": self.date_of_birth,
            "nationalities": list(self.nationalities),
            "jurisdiction": self.jurisdiction,
            "identifiers": [dict(i) for i in self.identifiers],
            "address": self.address,
            "roles": sorted(set(self.roles)),
            "sources": sorted(set(self.sources)),
        }


def _dates_agree(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    shared = min(len(left), len(right))
    return left[:shared] == right[:shared]


def screening_parties(
    application: Mapping[str, Any],
    *,
    verification: BusinessVerification | None,
    graph: OwnershipGraph | None,
    subject_legal_name: str | None,
    subject_jurisdiction: str | None,
    subject_identifiers: tuple[Identifier, ...] = (),
) -> list[Party]:
    """Every party to screen: the business, declared owners, current officers and graph owners.

    The same person reached from several sources is screened once, with the most specific
    identifiers any source gave. Order is deterministic.
    """
    parties: list[Party] = []

    def add(candidate: Party) -> None:
        business = candidate.kind == "business"
        tokens = normalise_name(candidate.name, business=business)
        for existing in parties:
            if existing.kind != candidate.kind:
                continue
            if normalise_name(existing.name, business=business) != tokens:
                continue
            if not _dates_agree(existing.date_of_birth, candidate.date_of_birth):
                continue
            existing.date_of_birth = max(
                (d for d in (existing.date_of_birth, candidate.date_of_birth) if d), key=len, default=None
            )
            existing.nationalities = tuple(sorted(set(existing.nationalities) | set(candidate.nationalities)))
            existing.address = existing.address or candidate.address
            existing.identifiers = existing.identifiers or candidate.identifiers
            existing.jurisdiction = existing.jurisdiction or candidate.jurisdiction
            existing.roles.extend(candidate.roles)
            existing.sources.extend(candidate.sources)
            return
        parties.append(candidate)

    business_name = subject_legal_name or str(application["legal_name"])
    add(
        Party(
            party_id="",
            kind="business",
            name=business_name,
            jurisdiction=subject_jurisdiction or application.get("jurisdiction"),
            identifiers=tuple(i.model_dump(mode="json") for i in subject_identifiers)
            or tuple(application.get("identifiers") or ()),
            address=(
                verification.registered_address.model_dump(mode="json")
                if verification and verification.registered_address
                else application.get("registered_address")
            ),
            roles=["subject"],
            sources=["registry" if subject_legal_name else "applicant"],
        )
    )
    if graph is not None:
        for node in graph.nodes:
            if node.node_id == graph.subject_node_id:
                continue
            kind = "person" if node.kind is OwnershipNodeKind.PERSON else "business"
            add(
                Party(
                    party_id="",
                    kind=kind,
                    name=node.name,
                    date_of_birth=node.date_of_birth,
                    nationalities=node.nationalities,
                    jurisdiction=node.jurisdiction,
                    identifiers=tuple(i.model_dump(mode="json") for i in node.identifiers),
                    address=node.address.model_dump(mode="json") if node.address else None,
                    roles=["owner"],
                    sources=["ownership"],
                )
            )
    if verification is not None:
        for officer in verification.officers:
            if officer.resigned_on:
                continue
            add(
                Party(
                    party_id="",
                    kind="person",
                    name=officer.name,
                    date_of_birth=officer.date_of_birth,
                    nationalities=officer.nationalities,
                    roles=[officer.role.value],
                    sources=["registry"],
                )
            )
    for owner in application.get("declared_owners") or ():
        add(
            Party(
                party_id="",
                kind=str(owner.get("kind") or "person"),
                name=str(owner["name"]),
                date_of_birth=owner.get("date_of_birth"),
                nationalities=tuple(owner.get("nationalities") or ()),
                identifiers=tuple(owner.get("identifiers") or ()),
                roles=["declared_owner"],
                sources=["applicant"],
            )
        )
    for index, party in enumerate(parties):
        party.party_id = f"party-{index + 1}"
    return parties


def observed_activity_categories(extractions: list[Mapping[str, Any]]) -> set[str]:
    observed: set[str] = set()
    for extraction in extractions:
        if extraction.get("ok"):
            observed.update(extraction.get("fields", {}).get("activity_categories") or ())
    return observed


def declared_activity_categories(declared_activity: str | None) -> set[str]:
    """Extractor activity categories a declared activity term maps to, by the extractor's own keywords."""
    if not declared_activity:
        return set()
    tokens = set(declared_activity.split("_"))
    return {
        category
        for category, keywords in ACTIVITY_KEYWORDS.items()
        if declared_activity == category or category in tokens or tokens & set(keywords)
    }


def activity_mismatch(declared_activity: str | None, extractions: list[Mapping[str, Any]]) -> bool | None:
    """``True``/``False`` when declared and observed activity can be compared, ``None`` otherwise."""
    declared = declared_activity_categories(declared_activity)
    observed = observed_activity_categories(extractions)
    if not declared or not observed:
        return None
    return not declared & observed


def tax_id_match(application: Mapping[str, Any], registry_identifiers: tuple[Identifier, ...]) -> bool | None:
    jurisdiction = str(application.get("jurisdiction") or "")
    scheme = TAX_ID_SCHEMES.get(jurisdiction.split("-")[0])
    if scheme is None:
        return None
    declared = {i["value"] for i in application.get("identifiers") or () if i.get("scheme") == scheme}
    on_record = {i.value for i in registry_identifiers if i.scheme == scheme}
    if not declared or not on_record:
        return None
    return declared <= on_record


def screening_counts(
    results: list[ScreeningResult], *, complete: bool, reviews: Mapping[str, str] | None = None
) -> tuple[int | None, int | None]:
    """``(unresolved_true_matches, unresolved_possible_matches)``.

    A hit counts as a true match only when a human review recorded ``true_match``
    (``reviews`` maps hit id to the reviewed outcome); an agent's proposal never does. Unreviewed
    hits are possible matches. When any party could not be screened, possible matches are
    ``None`` so the policy cannot read the gap as clear.
    """
    reviews = reviews or {}
    hits = [hit for result in results for hit in result.hits]
    true_matches = sum(1 for hit in hits if reviews.get(hit.hit_id) == "true_match")
    possible = sum(1 for hit in hits if hit.hit_id not in reviews)
    return true_matches, (possible if complete else None)


def policy_evidence(
    *,
    application: Mapping[str, Any],
    registry_match: bool | None,
    verification: BusinessVerification | None,
    registry_identifiers: tuple[Identifier, ...],
    reconciliation: Reconciliation | None,
    screening_results: list[ScreeningResult],
    screening_complete: bool,
    screening_available: bool,
    extractions: list[Mapping[str, Any]],
    reviews: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The nested evidence mapping the example onboarding policies read."""
    true_matches, possible = (
        screening_counts(screening_results, complete=screening_complete, reviews=reviews)
        if screening_available
        else (None, None)
    )
    return {
        "verification": {
            "status": verification.registry_status.value if verification else None,
            "registry_match": registry_match,
            "tax_id_match": tax_id_match(application, registry_identifiers),
            # No provider-neutral interface field reports filing status; left unresolved.
            "overdue_filings": None,
        },
        "ownership": {
            "missing_owners": len(reconciliation.missing_owners) if reconciliation else None,
            "undeclared_owners": len(reconciliation.undeclared_owners) if reconciliation else None,
        },
        "screening": {
            "unresolved_true_matches": true_matches,
            "unresolved_possible_matches": possible,
        },
        "web_presence": {"activity_mismatch": activity_mismatch(application.get("declared_activity"), extractions)},
    }
