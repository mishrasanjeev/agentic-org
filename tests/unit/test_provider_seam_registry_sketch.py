# SPDX-License-Identifier: Apache-2.0
"""A second implementation sketch, against the shape of a public government company registry.

ADR 0009 requires one before the provider interface is frozen: if the interface only fitted the
mock provider, which was modelled on the domain, mapping a real public source would need fields the
interface does not have or leave required fields empty. The registry here is described generically
(no particular country's API): a search endpoint, a company profile, an officers list and a
"persons with control" list that reports control in bands. Payloads are invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from connectors.framework.verification_provider import (
    Address,
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessVerification,
    Capability,
    CapabilityNotSupported,
    CheckOutcome,
    CheckResult,
    Deadline,
    Evidence,
    Identifier,
    InvalidQuery,
    NotAvailable,
    NotFound,
    Officer,
    OfficerRole,
    OwnershipEdge,
    OwnershipGraph,
    OwnershipNode,
    OwnershipNodeKind,
    OwnershipRelationship,
    Pending,
    PercentageRange,
    RegistryStatus,
    VerificationCheck,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
    call_capability,
)
from core.domain_schemas import validate

RETRIEVED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# What a public registry returns, keyed by registration number. Field names are generic.
REGISTRY: dict[str, dict[str, Any]] = {
    "00000001": {
        "profile": {
            "registration_number": "00000001",
            "entity_name": "BRIGHTWATER LANTERN WORKS LTD",
            "status": "live",
            "entity_kind": "private-limited",
            "formed_on": "2019-05-20",
            "office_address": {"line_1": "1 Example Street", "town": "Exampleton", "postcode": "ZZ99 9ZZ"},
        },
        "officers": [
            {
                "id": "o1",
                "name": "VENNCASTLE, Orla",
                "role": "director",
                "appointed": "2019-05-20",
                "born": "1971-04",
                "nationality": "GB",
            },
        ],
        "controllers": [
            {
                "id": "c1",
                "kind": "individual",
                "name": "Orla Venncastle",
                "born": "1971-04",
                "nationality": "GB",
                "control": ["shares-50-to-75", "votes-50-to-75"],
            },
            {
                "id": "c2",
                "kind": "corporate",
                "name": "Quellbridge Holdings Ltd",
                "registration_number": "00000009",
                "control": ["shares-25-to-50"],
            },
        ],
    }
}

_STATUS = {
    "live": RegistryStatus.ACTIVE,
    "dissolved": RegistryStatus.DISSOLVED,
    "insolvency": RegistryStatus.IN_INSOLVENCY,
}
_BANDS = {"25-to-50": (25.0, 50.0), "50-to-75": (50.0, 75.0), "75-to-100": (75.0, 100.0)}


def _evidence(record: str, field: str) -> Evidence:
    return Evidence(provider="public_registry", record_id=record, field=field, retrieved_at=RETRIEVED)


# docs-snippet: start provider-skeleton
class PublicRegistrySketch(VerificationProvider):
    """Registry data only: no screening, no web presence. Lookups are synchronous at the source."""

    name = "public_registry"
    capabilities = frozenset({Capability.RESOLVE, Capability.VERIFY, Capability.OWNERSHIP})

    def _ref(self, number: str) -> BusinessRef:
        return BusinessRef(
            provider=self.name,
            provider_ref=number,
            jurisdiction="GB",
            identifiers=(Identifier(scheme="gb_company_number", value=number),),
        )

    def _profile(self, ref: BusinessRef) -> dict[str, Any]:
        record = REGISTRY.get(ref.provider_ref)
        if record is None:
            raise NotFound(self.name, capability=Capability.VERIFY)
        return record

    def _address(self, raw: dict[str, str]) -> Address:
        return Address(lines=(raw["line_1"],), locality=raw.get("town"), postal_code=raw.get("postcode"), country="GB")

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        if not q.has_search_terms:
            raise InvalidQuery(self.name, "a name or an identifier is required", capability=Capability.RESOLVE)
        async with deadline.enforce(self.name, Capability.RESOLVE):
            wanted = {i.value for i in q.identifiers if i.scheme == "gb_company_number"}
            name = (q.name or "").upper()
            hits = [
                record["profile"]
                for number, record in sorted(REGISTRY.items())
                if number in wanted or (name and name in record["profile"]["entity_name"])
            ]
        return [
            BusinessCandidate(
                ref=self._ref(p["registration_number"]),
                legal_name=p["entity_name"],
                registry_status=_STATUS.get(p["status"], RegistryStatus.UNKNOWN),
                registered_address=self._address(p["office_address"]),
                match_score=None,  # the registry ranks results but publishes no score
                evidence=(_evidence(f"profile:{p['registration_number']}", "entity_name"),),
            )
            for p in hits[q.offset : q.offset + q.limit]
        ]

    # docs-snippet: end provider-skeleton

    async def verify_business(self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline) -> VerificationHandle:
        self._profile(ref)
        return VerificationHandle(
            provider=self.name,
            verification_id=f"{ref.provider_ref}-{opts.idempotency_key}",
            ref=ref,
            requested_at=RETRIEVED,
        )

    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        # A synchronous source still fits start-and-poll: the first poll simply returns the result.
        record = self._profile(h.ref)
        profile = record["profile"]
        record_id = f"profile:{profile['registration_number']}"
        return BusinessVerification(
            handle=h,
            completed_at=RETRIEVED,
            legal_name=profile["entity_name"],
            registry_status=_STATUS.get(profile["status"], RegistryStatus.UNKNOWN),
            entity_type=profile["entity_kind"].replace("-", "_"),
            incorporated_on=profile["formed_on"],
            registered_address=self._address(profile["office_address"]),
            identifiers=h.ref.identifiers,
            officers=tuple(
                Officer(
                    name=o["name"],
                    role=OfficerRole(o["role"]),
                    appointed_on=o["appointed"],
                    date_of_birth=o["born"],
                    nationalities=(o["nationality"],),
                    evidence=(_evidence(f"officers:{profile['registration_number']}:{o['id']}", "name"),),
                )
                for o in record["officers"]
            ),
            checks=(
                CheckResult(
                    check=VerificationCheck.REGISTRATION,
                    outcome=CheckOutcome.PASSED,
                    evidence=(_evidence(record_id, "status"),),
                ),
            ),
            evidence=(_evidence(record_id, "status"),),
        )

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        record = self._profile(ref)
        number = ref.provider_ref
        nodes = [
            OwnershipNode(
                node_id=number,
                kind=OwnershipNodeKind.BUSINESS,
                name=record["profile"]["entity_name"],
                jurisdiction="GB",
                identifiers=ref.identifiers,
                evidence=(_evidence(f"profile:{number}", "entity_name"),),
            )
        ]
        edges = []
        for c in record["controllers"]:
            evidence = (_evidence(f"controllers:{number}:{c['id']}", "control"),)
            corporate = c["kind"] == "corporate"
            nodes.append(
                OwnershipNode(
                    node_id=c["id"],
                    kind=OwnershipNodeKind.BUSINESS if corporate else OwnershipNodeKind.PERSON,
                    name=c["name"],
                    identifiers=(Identifier(scheme="gb_company_number", value=c["registration_number"]),)
                    if corporate
                    else (),
                    date_of_birth=c.get("born"),
                    nationalities=(c["nationality"],) if c.get("nationality") else (),
                    evidence=evidence,
                )
            )
            share = next((_BANDS[k.removeprefix("shares-")] for k in c["control"] if k.startswith("shares-")), None)
            votes = next((_BANDS[k.removeprefix("votes-")] for k in c["control"] if k.startswith("votes-")), None)
            edges.append(
                OwnershipEdge(
                    from_node_id=c["id"],
                    to_node_id=number,
                    relationship=OwnershipRelationship.SHAREHOLDING if share else OwnershipRelationship.VOTING_RIGHTS,
                    share_pct=PercentageRange(min=share[0], max=share[1]) if share else None,
                    voting_pct=PercentageRange(min=votes[0], max=votes[1]) if votes else None,
                    evidence=evidence,
                )
            )
        return OwnershipGraph(
            provider=self.name,
            subject=ref,
            subject_node_id=number,
            as_of=RETRIEVED,
            completeness="partial",  # a corporate controller's own owners are a further lookup
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=(_evidence(f"controllers:{number}", "items"),),
        )


async def test_public_registry_maps_onto_the_interface_without_extra_fields() -> None:
    provider = PublicRegistrySketch()
    deadline = Deadline.after(5)

    [candidate] = await provider.resolve_business(BusinessQuery(name="Brightwater"), deadline=deadline)
    handle = await provider.verify_business(
        candidate.ref, VerifyOptions(idempotency_key="case-0001-verify"), deadline=deadline
    )
    verification = await provider.verification_result(handle, deadline=deadline)
    graph = await provider.ownership(candidate.ref, deadline=deadline)

    assert isinstance(verification, BusinessVerification)
    assert verification.registry_status is RegistryStatus.ACTIVE
    assert verification.officers[0].role is OfficerRole.DIRECTOR
    validate("ownership_graph", graph.model_dump(mode="json"))
    assert {e.share_pct for e in graph.edges} == {PercentageRange(min=50, max=75), PercentageRange(min=25, max=50)}


async def test_capabilities_the_registry_does_not_offer_degrade() -> None:
    provider = PublicRegistrySketch()
    ref = provider._ref("00000001")
    with pytest.raises(CapabilityNotSupported):
        await provider.web_presence(ref, deadline=Deadline.after(1))

    async def never_called() -> None:
        raise AssertionError("an undeclared capability was invoked")

    result = await call_capability(provider, Capability.SCREEN_PERSON, never_called)
    assert isinstance(result, NotAvailable)


async def test_unknown_registration_number_is_not_found_and_an_empty_query_is_invalid() -> None:
    provider = PublicRegistrySketch()
    with pytest.raises(NotFound):
        await provider.ownership(provider._ref("00000099"), deadline=Deadline.after(1))
    with pytest.raises(InvalidQuery):
        await provider.resolve_business(BusinessQuery(), deadline=Deadline.after(1))
