# SPDX-License-Identifier: Apache-2.0
"""The mock provider's synthetic fixtures, loaded and validated.

Fixtures describe businesses in domain terms - a registry record, owners, a website, events and a
sample application - not in any real source's response format. Every name is invented, every
identifier is in a reserved range and every domain is under ``example.com``.

Loading fails closed: a malformed fixture, a duplicate key or identifier, or any file the loader
does not recognise raises :class:`MockFixtureError`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StringConstraints, ValidationError

from connectors.framework.verification_types import (
    Address,
    CountryCode,
    DomainModel,
    DomainName,
    Identifier,
    IdentifierToken,
    Jurisdiction,
    ListSource,
    ListType,
    Name,
    NodeId,
    OfficerRole,
    OwnershipNodeKind,
    OwnershipRelationship,
    PartialDate,
    PartyKind,
    PercentageRange,
    ProviderEventType,
    RegistryStatus,
    VocabularyTerm,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

Scenario = Literal[
    "clean",
    "corporate_owner",
    "missing_owner",
    "undeclared_owner",
    "probable_false_positive",
    "true_match",
    "dissolved",
    "thin_file",
    "hostile_web_content",
    "hostile_company_name",
    "hostile_screening_alias",
]


class MockFixtureError(ValueError):
    pass


class OfficerFixture(DomainModel):
    name: Name
    role: OfficerRole
    appointed_on: PartialDate | None = None
    resigned_on: PartialDate | None = None
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()


class OwnerFixture(DomainModel):
    node_id: NodeId
    kind: OwnershipNodeKind
    name: Name
    owns: NodeId = "subject"
    relationship: OwnershipRelationship = OwnershipRelationship.SHAREHOLDING
    share_pct: PercentageRange | None = None
    voting_pct: PercentageRange | None = None
    jurisdiction: Jurisdiction | None = None
    identifiers: tuple[Identifier, ...] = ()
    date_of_birth: PartialDate | None = None
    nationalities: tuple[CountryCode, ...] = ()
    started_on: PartialDate | None = None


class EventFixture(DomainModel):
    event_id: IdentifierToken
    event_type: ProviderEventType
    occurred_at: AwareDatetime


class RegistryFixture(DomainModel):
    provider_ref: IdentifierToken
    jurisdiction: Jurisdiction
    identifiers: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    legal_name: Name
    registry_status: RegistryStatus
    entity_type: VocabularyTerm
    incorporated_on: PartialDate
    dissolved_on: PartialDate | None = None
    registered_address: Address
    officers: tuple[OfficerFixture, ...]
    ownership_completeness: Literal["complete", "partial", "unknown"]
    owners: tuple[OwnerFixture, ...]
    monitor_events: tuple[EventFixture, ...] = ()


class PageFixture(DomainModel):
    path: Annotated[str, StringConstraints(pattern=r"^/[^\s]*$")]
    http_status: Annotated[int, Field(ge=100, le=599)]
    media_type: Annotated[str, StringConstraints(pattern=r"^[a-z]+/[a-z0-9.+\-]+$")]
    content: Annotated[str, StringConstraints(max_length=100_000)]


class WebFixture(DomainModel):
    domain: DomainName
    registered_on: PartialDate | None = None
    pages: tuple[PageFixture, ...]


class BusinessFixture(DomainModel):
    key: Annotated[str, StringConstraints(pattern=r"^(us|gb)-[a-z0-9-]+$")]
    scenarios: Annotated[tuple[Scenario, ...], Field(min_length=1)]
    snapshot_at: AwareDatetime
    application: dict[str, Any]
    registry: RegistryFixture | None
    web: WebFixture | None


class WatchlistEntry(DomainModel):
    entry_id: IdentifierToken
    list_type: ListType
    source: ListSource
    entry_kind: PartyKind
    name: Name
    aliases: tuple[Name, ...] = ()
    dates_of_birth: tuple[PartialDate, ...] = ()
    nationalities: tuple[CountryCode, ...] = ()
    addresses: tuple[Address, ...] = ()
    associated_entities: tuple[Name, ...] = ()
    listed_on: PartialDate | None = None


class Watchlist(DomainModel):
    entries: Annotated[tuple[WatchlistEntry, ...], Field(min_length=1)]


class WebhookFixture(DomainModel):
    """A recorded webhook delivery and whether verification must accept it at ``verify_at``."""

    description: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    verify_at: int
    headers: dict[str, str]
    body: str
    expected: Literal["verified", "rejected"]


@dataclass(frozen=True)
class MockDataset:
    businesses: tuple[BusinessFixture, ...]
    watchlist: tuple[WatchlistEntry, ...]
    webhooks: Mapping[str, WebhookFixture]

    def by_provider_ref(self) -> dict[str, BusinessFixture]:
        return {b.registry.provider_ref: b for b in self.businesses if b.registry is not None}

    def business(self, key: str) -> BusinessFixture:
        for business in self.businesses:
            if business.key == key:
                return business
        raise KeyError(key)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MockFixtureError(f"{path.name}: unreadable fixture: {exc}") from exc


def _parse[M: DomainModel](model: type[M], path: Path) -> M:
    try:
        return model.model_validate(_read(path))
    except ValidationError as exc:
        raise MockFixtureError(f"{path.name}: {exc}") from exc


def load_dataset(root: Path = FIXTURES_DIR) -> MockDataset:
    known: set[Path] = set()
    businesses: list[BusinessFixture] = []
    for path in sorted((root / "businesses").glob("*.json")):
        known.add(path)
        business = _parse(BusinessFixture, path)
        if business.key != path.stem:
            raise MockFixtureError(f"{path.name}: key {business.key!r} does not match the file name")
        businesses.append(business)
    watchlist_path = root / "watchlist.json"
    known.add(watchlist_path)
    watchlist = _parse(Watchlist, watchlist_path)
    webhooks: dict[str, WebhookFixture] = {}
    for path in sorted((root / "webhooks").glob("*.json")):
        known.add(path)
        webhooks[path.stem] = _parse(WebhookFixture, path)

    unknown = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p not in known)
    if unknown:
        raise MockFixtureError(f"unrecognised fixture files: {', '.join(unknown)}")
    _check_unique(businesses, watchlist.entries)
    return MockDataset(businesses=tuple(businesses), watchlist=watchlist.entries, webhooks=webhooks)


def _check_unique(businesses: list[BusinessFixture], entries: tuple[WatchlistEntry, ...]) -> None:
    refs: set[str] = set()
    identifiers: set[tuple[str, str]] = set()
    for business in businesses:
        if business.registry is None:
            continue
        if business.registry.provider_ref in refs:
            raise MockFixtureError(f"duplicate provider_ref {business.registry.provider_ref}")
        refs.add(business.registry.provider_ref)
        for identifier in business.registry.identifiers:
            pair = (identifier.scheme, identifier.value)
            if pair in identifiers:
                raise MockFixtureError(f"duplicate identifier {pair}")
            identifiers.add(pair)
        owner_ids = {o.node_id for o in business.registry.owners}
        for owner in business.registry.owners:
            if owner.owns != "subject" and owner.owns not in owner_ids:
                raise MockFixtureError(f"{business.key}: owner {owner.node_id} owns unknown node {owner.owns}")
    entry_ids = [e.entry_id for e in entries]
    if len(set(entry_ids)) != len(entry_ids):
        raise MockFixtureError("duplicate watchlist entry_id")


@cache
def default_dataset() -> MockDataset:
    return load_dataset()
