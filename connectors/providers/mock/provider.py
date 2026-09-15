# SPDX-License-Identifier: Apache-2.0
"""``MockProvider``: an in-process, fixture-backed :class:`VerificationProvider`.

Built from the domain (see ``fixtures/``), not from any real source's responses. It offers every
capability unless configured with fewer, returns ``Pending`` for a configurable number of polls,
honours deadlines and cancellation, injects latency and failures deterministically under a seed,
and signs and verifies webhook events.

The mock keeps its state (verifications, screenings, monitors, emitted events, injected faults) in
the instance. It is for development, tests and demos and refuses to be created by the registry in a
non-local environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from connectors.framework.verification_provider import (
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    BusinessVerification,
    Capability,
    CheckOutcome,
    CheckResult,
    Deadline,
    DeclaredBusiness,
    Evidence,
    InvalidQuery,
    ListType,
    MonitorAlert,
    MonitorHandle,
    MonitorOptions,
    NotFound,
    Officer,
    OwnershipEdge,
    OwnershipGraph,
    OwnershipNode,
    OwnershipNodeKind,
    PartyKind,
    Pending,
    PersonSubject,
    ProviderAuthenticationFailed,
    ProviderEvent,
    ProviderEventType,
    ProviderRateLimited,
    ProviderResponseInvalid,
    ProviderUnavailable,
    RegistryStatus,
    ScreeningHit,
    ScreeningResult,
    ScreeningSubject,
    ScreenOptions,
    UntrustedText,
    VerificationCheck,
    VerificationHandle,
    VerificationProvider,
    VerifyOptions,
    WebDomain,
    WebPage,
    WebPresence,
)
from connectors.providers.mock import webhooks
from connectors.providers.mock.config import FaultKind, MockConfig, unit_draw
from connectors.providers.mock.data import BusinessFixture, MockDataset, RegistryFixture, default_dataset
from connectors.providers.mock.matching import normalise_name, query_score, similarity

PROVIDER_NAME = "mock"
RESOLVE_THRESHOLD = 0.6
SCREENING_THRESHOLD = 0.85
PENDING_RETRY_AFTER_SECONDS = 0.1

_IDENTIFIER_FORMATS = {
    "gb_company_number": re.compile(r"^[0-9]{8}$"),
    "us_ein": re.compile(r"^[0-9]{2}-[0-9]{7}$"),
    "us_state_file_number": re.compile(r"^[0-9]{7}$"),
}


def _fingerprint(*values: object) -> str:
    def plain(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, frozenset | set):
            return sorted(str(v) for v in value)
        return value

    canonical = json.dumps([plain(v) for v in values], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class InjectedFault:
    kind: FaultKind
    capability: Capability | None
    remaining: int
    delay_seconds: float = 0.0


@dataclass
class _Verification:
    handle: VerificationHandle
    opts: VerifyOptions
    polls: int = 0
    result: BusinessVerification | None = None


@dataclass
class _Monitor:
    handle: MonitorHandle
    opts: MonitorOptions


@dataclass
class _EmittedEvent:
    event_id: str
    event_type: ProviderEventType
    occurred_at: datetime


@dataclass
class _State:
    verifications: dict[str, _Verification] = field(default_factory=dict)
    screenings: dict[str, ScreeningResult] = field(default_factory=dict)
    monitors: dict[str, _Monitor] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], str] = field(default_factory=dict)
    emitted: dict[str, list[_EmittedEvent]] = field(default_factory=dict)
    status_overrides: dict[str, tuple[RegistryStatus, str]] = field(default_factory=dict)
    attempts: dict[tuple[str, str], int] = field(default_factory=dict)
    faults: list[InjectedFault] = field(default_factory=list)


class MockProvider(VerificationProvider):
    name = PROVIDER_NAME

    def __init__(self, config: MockConfig | None = None, dataset: MockDataset | None = None) -> None:
        self.config = config or MockConfig()
        self.capabilities = self.config.capabilities
        self._data = dataset or default_dataset()
        self._records = self._data.by_provider_ref()
        self._state = _State()

    # --- test and demo controls (not part of the interface) -----------------------------------

    def inject_fault(
        self,
        kind: FaultKind,
        *,
        capability: Capability | None = None,
        times: int = 1,
        delay_seconds: float = 0.0,
    ) -> None:
        """Make the next ``times`` calls (to ``capability``, or to any capability) fail or slow down."""
        if times < 1:
            raise ValueError("times must be at least 1")
        if not 0 <= delay_seconds <= 600:
            raise ValueError("delay_seconds must be between 0 and 600")
        self._state.faults.append(InjectedFault(kind, capability, times, delay_seconds))

    def reset(self) -> None:
        self._state = _State()

    def emit_event(self, ref: BusinessRef, event_type: ProviderEventType) -> tuple[dict[str, str], bytes]:
        """Record an event for a business and return it as a signed webhook delivery."""
        record = self._record(ref, Capability.MONITOR)
        now = self.config.clock()
        emitted = self._state.emitted.setdefault(record.provider_ref, [])
        event_id = "evt-" + _fingerprint(self.config.seed, record.provider_ref, event_type, len(emitted))[:16]
        emitted.append(_EmittedEvent(event_id, event_type, now))
        if event_type is ProviderEventType.BUSINESS_DISSOLVED:
            self._state.status_overrides[record.provider_ref] = (RegistryStatus.DISSOLVED, now.date().isoformat())
        body = webhooks.event_body(
            provider=self.name, event_id=event_id, event_type=event_type, occurred_at=now, subject=self._ref(record)
        )
        headers = webhooks.sign(self.config.webhook_secret, body, event_id=event_id, timestamp=int(now.timestamp()))
        return headers, body

    def fixture(self, key: str) -> BusinessFixture:
        return self._data.business(key)

    def ref_for(self, key: str) -> BusinessRef:
        business = self._data.business(key)
        if business.registry is None:
            raise NotFound(self.name, f"fixture {key} has no registry record")
        return self._ref(business.registry)

    # --- plumbing --------------------------------------------------------------------------------

    def _take_fault(self, capability: Capability) -> InjectedFault | None:
        for fault in self._state.faults:
            if fault.capability in (None, capability):
                fault.remaining -= 1
                if fault.remaining == 0:
                    self._state.faults.remove(fault)
                return fault
        return None

    async def _network(self, capability: Capability, deadline: Deadline, *call: object) -> None:
        """Stand in for the round trip: capability check, deadline, latency and injected failures."""
        self.require(capability)
        async with deadline.enforce(self.name, capability):
            fingerprint = _fingerprint(capability, *call)
            attempt_key = (capability.value, fingerprint)
            attempt = self._state.attempts.get(attempt_key, 0) + 1
            self._state.attempts[attempt_key] = attempt
            seed, parts = self.config.seed, (capability.value, fingerprint, str(attempt))

            forced = self._take_fault(capability)
            low, high = self.config.latency_ms
            delay = (low + (high - low) * unit_draw(seed, "latency", *parts)) / 1000
            if forced is not None and forced.kind is FaultKind.SLOW:
                delay = forced.delay_seconds
            if delay:
                await asyncio.sleep(delay)
            if forced is not None and forced.kind is FaultKind.HANG:
                await asyncio.Event().wait()

            kind = forced.kind if forced is not None else None
            if kind is None and unit_draw(seed, "failure", *parts) < self.config.failure_rate:
                kinds = self.config.failure_kinds
                kind = kinds[int(unit_draw(seed, "kind", *parts) * len(kinds))]
            self._raise_for(kind, capability)

    def _raise_for(self, kind: FaultKind | None, capability: Capability) -> None:
        if kind is FaultKind.UNAVAILABLE:
            raise ProviderUnavailable(self.name, "injected failure", capability=capability)
        if kind is FaultKind.RATE_LIMITED:
            raise ProviderRateLimited(
                self.name, "injected failure", capability=capability, retry_after_seconds=PENDING_RETRY_AFTER_SECONDS
            )
        if kind is FaultKind.AUTHENTICATION_FAILED:
            raise ProviderAuthenticationFailed(self.name, "injected failure", capability=capability)
        if kind is FaultKind.RESPONSE_INVALID:
            raise ProviderResponseInvalid(self.name, "injected failure", capability=capability)

    def _record(self, ref: BusinessRef, capability: Capability) -> RegistryFixture:
        if ref.provider != self.name:
            raise InvalidQuery(self.name, "reference belongs to another provider", capability=capability)
        business = self._records.get(ref.provider_ref)
        if business is None or business.registry is None:
            raise NotFound(self.name, "no such business", capability=capability)
        return business.registry

    def _snapshot(self, record: RegistryFixture) -> datetime:
        return self._records[record.provider_ref].snapshot_at

    def _ref(self, record: RegistryFixture) -> BusinessRef:
        return BusinessRef(
            provider=self.name,
            provider_ref=record.provider_ref,
            jurisdiction=record.jurisdiction,
            identifiers=record.identifiers,
        )

    def _status(self, record: RegistryFixture) -> tuple[RegistryStatus, str | None]:
        override = self._state.status_overrides.get(record.provider_ref)
        if override is not None:
            return override
        return record.registry_status, record.dissolved_on

    def _evidence(self, record_id: str, field_path: str, at: datetime, excerpt_ref: str | None = None) -> Evidence:
        return Evidence(
            provider=self.name, record_id=record_id, field=field_path, retrieved_at=at, excerpt_ref=excerpt_ref
        )

    def _claim_idempotency_key(self, operation: str, key: str, request: str, capability: Capability) -> None:
        slot = (operation, key)
        seen = self._state.idempotency.setdefault(slot, request)
        if seen != request:
            raise InvalidQuery(
                self.name, "idempotency_key was already used for a different request", capability=capability
            )

    # --- resolve -----------------------------------------------------------------------------------

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        self.require(Capability.RESOLVE)
        if not q.has_search_terms:
            raise InvalidQuery(self.name, "a name or an identifier is required", capability=Capability.RESOLVE)
        for identifier in q.identifiers:
            pattern = _IDENTIFIER_FORMATS.get(identifier.scheme)
            if pattern is not None and not pattern.match(identifier.value):
                raise InvalidQuery(self.name, f"malformed {identifier.scheme}", capability=Capability.RESOLVE)
        await self._network(Capability.RESOLVE, deadline, q)

        wanted = {(i.scheme, i.value) for i in q.identifiers}
        query_name = normalise_name(q.name or "", business=True)
        scored: list[tuple[float, RegistryFixture]] = []
        for business in self._records.values():
            record = business.registry
            if record is None:
                continue
            if q.jurisdiction and not (
                record.jurisdiction == q.jurisdiction or record.jurisdiction.startswith(q.jurisdiction + "-")
            ):
                continue
            if wanted & {(i.scheme, i.value) for i in record.identifiers}:
                score = 1.0
            elif query_name:
                score = query_score(query_name, normalise_name(record.legal_name, business=True))
            else:
                score = 0.0
            if score >= RESOLVE_THRESHOLD:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].provider_ref))

        candidates = []
        for score, record in scored[q.offset : q.offset + q.limit]:
            status, _ = self._status(record)
            candidates.append(
                BusinessCandidate(
                    ref=self._ref(record),
                    legal_name=record.legal_name,
                    registry_status=status,
                    registered_address=record.registered_address,
                    match_score=score,
                    evidence=(
                        self._evidence(
                            f"mock:company:{record.provider_ref}:profile", "legal_name", self._snapshot(record)
                        ),
                    ),
                )
            )
        return candidates

    # --- verify ------------------------------------------------------------------------------------

    async def verify_business(self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline) -> VerificationHandle:
        await self._network(Capability.VERIFY, deadline, ref, opts)
        record = self._record(ref, Capability.VERIFY)
        self._claim_idempotency_key("verify", opts.idempotency_key, _fingerprint(ref, opts), Capability.VERIFY)
        verification_id = "ver-" + _fingerprint(record.provider_ref, opts.idempotency_key)[:16]
        existing = self._state.verifications.get(verification_id)
        if existing is not None:
            return existing.handle
        handle = VerificationHandle(
            provider=self.name,
            verification_id=verification_id,
            ref=self._ref(record),
            requested_at=self.config.clock(),
        )
        self._state.verifications[verification_id] = _Verification(handle=handle, opts=opts)
        return handle

    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        await self._network(Capability.VERIFY, deadline, h)
        if h.provider != self.name:
            raise InvalidQuery(self.name, "handle belongs to another provider", capability=Capability.VERIFY)
        verification = self._state.verifications.get(h.verification_id)
        if verification is None or verification.handle.ref != h.ref:
            raise NotFound(self.name, "no such verification", capability=Capability.VERIFY)
        if verification.result is not None:
            return verification.result
        if verification.polls < self.config.polls_until_ready:
            verification.polls += 1
            return Pending(
                handle=verification.handle,
                state="queued" if verification.polls == 1 else "in_progress",
                retry_after_seconds=PENDING_RETRY_AFTER_SECONDS,
            )
        verification.result = self._complete(verification)
        return verification.result

    def _complete(self, verification: _Verification) -> BusinessVerification:
        record = self._record(verification.handle.ref, Capability.VERIFY)
        at = self._snapshot(record)
        profile = f"mock:company:{record.provider_ref}:profile"
        status, dissolved_on = self._status(record)
        declared = verification.opts.declared
        checks = []
        for check in sorted(verification.opts.checks):
            outcome, field_path = self._check(check, record, status, declared)
            checks.append(
                CheckResult(
                    check=check,
                    outcome=outcome,
                    evidence=(self._evidence(profile, field_path, at),)
                    if outcome is not CheckOutcome.NOT_CHECKED
                    else (),
                )
            )
        return BusinessVerification(
            handle=verification.handle,
            completed_at=verification.handle.requested_at,
            legal_name=record.legal_name,
            registry_status=status,
            entity_type=record.entity_type,
            incorporated_on=record.incorporated_on,
            dissolved_on=dissolved_on,
            registered_address=record.registered_address,
            identifiers=record.identifiers,
            officers=tuple(
                Officer(
                    **officer.model_dump(),
                    evidence=(self._evidence(f"mock:company:{record.provider_ref}:officers:{index}", "name", at),),
                )
                for index, officer in enumerate(record.officers)
            ),
            checks=tuple(checks),
            evidence=(self._evidence(profile, "registry_status", at),),
        )

    @staticmethod
    def _check(
        check: VerificationCheck, record: RegistryFixture, status: RegistryStatus, declared: DeclaredBusiness | None
    ) -> tuple[CheckOutcome, str]:
        if check is VerificationCheck.REGISTRATION:
            return (CheckOutcome.PASSED if status is RegistryStatus.ACTIVE else CheckOutcome.FAILED), "registry_status"
        if check is VerificationCheck.OFFICERS:
            current = [o for o in record.officers if o.resigned_on is None]
            return (CheckOutcome.PASSED if current else CheckOutcome.INCONCLUSIVE), "officers"
        if declared is None:
            return CheckOutcome.NOT_CHECKED, "registry_status"
        if check is VerificationCheck.NAME:
            if declared.legal_name is None:
                return CheckOutcome.NOT_CHECKED, "legal_name"
            score = similarity(
                normalise_name(declared.legal_name, business=True), normalise_name(record.legal_name, business=True)
            )
            return (CheckOutcome.PASSED if score >= 0.95 else CheckOutcome.FAILED), "legal_name"
        if check is VerificationCheck.ADDRESS:
            if declared.registered_address is None:
                return CheckOutcome.NOT_CHECKED, "registered_address"
            same = (
                declared.registered_address.country == record.registered_address.country
                and (declared.registered_address.postal_code or "").replace(" ", "").upper()
                == (record.registered_address.postal_code or "").replace(" ", "").upper()
            )
            return (CheckOutcome.PASSED if same else CheckOutcome.FAILED), "registered_address"
        # IDENTIFIERS
        if not declared.identifiers:
            return CheckOutcome.NOT_CHECKED, "identifiers"
        on_record = {(i.scheme, i.value) for i in record.identifiers}
        schemes = {i.scheme for i in record.identifiers}
        comparable = [(i.scheme, i.value) for i in declared.identifiers if i.scheme in schemes]
        if not comparable:
            return CheckOutcome.INCONCLUSIVE, "identifiers"
        return (CheckOutcome.PASSED if all(p in on_record for p in comparable) else CheckOutcome.FAILED), "identifiers"

    # --- ownership ---------------------------------------------------------------------------------

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        await self._network(Capability.OWNERSHIP, deadline, ref)
        record = self._record(ref, Capability.OWNERSHIP)
        at = self._snapshot(record)
        number = record.provider_ref
        subject_id = f"biz-{number}"
        nodes = [
            OwnershipNode(
                node_id=subject_id,
                kind=OwnershipNodeKind.BUSINESS,
                name=record.legal_name,
                jurisdiction=record.jurisdiction,
                identifiers=record.identifiers,
                address=record.registered_address,
                evidence=(self._evidence(f"mock:company:{number}:profile", "legal_name", at),),
            )
        ]
        edges = []
        for owner in record.owners:
            source = f"mock:company:{number}:owners:{owner.node_id}"
            nodes.append(
                OwnershipNode(
                    node_id=owner.node_id,
                    kind=owner.kind,
                    name=owner.name,
                    jurisdiction=owner.jurisdiction,
                    identifiers=owner.identifiers,
                    date_of_birth=owner.date_of_birth,
                    nationalities=owner.nationalities,
                    evidence=(self._evidence(source, "name", at),),
                )
            )
            edges.append(
                OwnershipEdge(
                    from_node_id=owner.node_id,
                    to_node_id=subject_id if owner.owns == "subject" else owner.owns,
                    relationship=owner.relationship,
                    share_pct=owner.share_pct,
                    voting_pct=owner.voting_pct,
                    started_on=owner.started_on,
                    evidence=(self._evidence(source, "share_pct", at),),
                )
            )
        return OwnershipGraph(
            provider=self.name,
            subject=self._ref(record),
            subject_node_id=subject_id,
            as_of=at,
            completeness=record.ownership_completeness,
            nodes=tuple(nodes),
            edges=tuple(edges),
            evidence=(self._evidence(f"mock:company:{number}:owners", "items", at),),
        )

    # --- screening ---------------------------------------------------------------------------------

    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        await self._network(Capability.SCREEN_PERSON, deadline, s, opts)
        subject = ScreeningSubject(
            name=s.full_name,
            date_of_birth=s.date_of_birth,
            nationalities=s.nationalities,
            address=s.address,
            identifiers=s.identifiers,
        )
        return self._screen(PartyKind.PERSON, subject, opts, Capability.SCREEN_PERSON)

    async def screen_business(self, s: BusinessSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        await self._network(Capability.SCREEN_BUSINESS, deadline, s, opts)
        subject = ScreeningSubject(
            name=s.legal_name, jurisdiction=s.jurisdiction, address=s.address, identifiers=s.identifiers
        )
        return self._screen(PartyKind.BUSINESS, subject, opts, Capability.SCREEN_BUSINESS)

    def _screen(
        self, kind: PartyKind, subject: ScreeningSubject, opts: ScreenOptions, capability: Capability
    ) -> ScreeningResult:
        operation = f"screen_{kind.value}"
        self._claim_idempotency_key(operation, opts.idempotency_key, _fingerprint(subject, opts), capability)
        screening_id = "scr-" + _fingerprint(operation, opts.idempotency_key)[:16]
        existing = self._state.screenings.get(screening_id)
        if existing is not None:
            return existing

        at = self.config.clock()
        business = kind is PartyKind.BUSINESS
        wanted = normalise_name(subject.name, business=business)
        hits = []
        for entry in sorted(self._data.watchlist, key=lambda e: e.entry_id):
            if entry.entry_kind is not kind or entry.list_type not in opts.list_types:
                continue
            names = [("names[0]", entry.name)] + [(f"aliases[{i}]", alias) for i, alias in enumerate(entry.aliases)]
            score, field_path = max(
                (similarity(wanted, normalise_name(value, business=business)), path) for path, value in names
            )
            if score < SCREENING_THRESHOLD:
                continue
            hits.append(
                ScreeningHit(
                    hit_id="hit-" + _fingerprint(screening_id, entry.entry_id)[:16],
                    list_type=entry.list_type,
                    source=entry.source,
                    entry_kind=entry.entry_kind,
                    matched_name=entry.name,
                    aliases=entry.aliases,
                    dates_of_birth=entry.dates_of_birth,
                    nationalities=entry.nationalities,
                    addresses=entry.addresses,
                    associated_entities=entry.associated_entities,
                    listed_on=entry.listed_on,
                    name_similarity=score,
                    evidence=(
                        self._evidence(
                            f"mock:watchlist:{entry.entry_id}",
                            field_path,
                            at,
                            f"excerpt:mock-watchlist-{entry.entry_id}",
                        ),
                    ),
                )
            )
        result = ScreeningResult(
            screening_id=screening_id,
            provider=self.name,
            subject_kind=kind,
            subject=subject,
            screened_at=at,
            list_types=tuple(t for t in ListType if t in opts.list_types),
            hits=tuple(hits),
            evidence=(self._evidence(f"mock:screening:{screening_id}", "hits", at),),
        )
        self._state.screenings[screening_id] = result
        return result

    # --- web presence ------------------------------------------------------------------------------

    async def web_presence(self, ref: BusinessRef, *, deadline: Deadline) -> WebPresence:
        await self._network(Capability.WEB_PRESENCE, deadline, ref)
        record = self._record(ref, Capability.WEB_PRESENCE)
        business = self._records[record.provider_ref]
        at = business.snapshot_at
        web = business.web
        if web is None:
            return WebPresence(provider=self.name, subject=self._ref(record), observed_at=at)
        pages = []
        for page in web.pages:
            digest = hashlib.sha256(page.content.encode("utf-8")).hexdigest()
            url = f"https://{web.domain}{page.path}"
            excerpt_ref = f"excerpt:mock-web-{digest[:16]}"
            pages.append(
                WebPage(
                    url=url,
                    fetched_at=at,
                    http_status=page.http_status,
                    media_type=page.media_type,
                    content=UntrustedText(value=page.content),
                    content_sha256=f"sha256:{digest}",
                    excerpt_ref=excerpt_ref,
                    evidence=(self._evidence(f"mock:web:{web.domain}{page.path}", "content", at, excerpt_ref),),
                )
            )
        domain_evidence = self._evidence(f"mock:web:{web.domain}", "registered_on", at)
        return WebPresence(
            provider=self.name,
            subject=self._ref(record),
            observed_at=at,
            domains=(
                WebDomain(
                    domain=web.domain,
                    registered_on=web.registered_on,
                    resolves=True,
                    tls_valid=True,
                    evidence=(domain_evidence,),
                ),
            ),
            pages=tuple(pages),
            evidence=(domain_evidence,),
        )

    # --- monitoring --------------------------------------------------------------------------------

    async def monitor_enroll(self, ref: BusinessRef, opts: MonitorOptions, *, deadline: Deadline) -> MonitorHandle:
        await self._network(Capability.MONITOR, deadline, ref, opts)
        record = self._record(ref, Capability.MONITOR)
        self._claim_idempotency_key("monitor", opts.idempotency_key, _fingerprint(ref, opts), Capability.MONITOR)
        monitor_id = "mon-" + _fingerprint(record.provider_ref, opts.idempotency_key)[:16]
        existing = self._state.monitors.get(monitor_id)
        if existing is not None:
            return existing.handle
        handle = MonitorHandle(
            provider=self.name, monitor_id=monitor_id, ref=self._ref(record), enrolled_at=self.config.clock()
        )
        self._state.monitors[monitor_id] = _Monitor(handle=handle, opts=opts)
        return handle

    async def monitor_result(self, h: MonitorHandle, *, deadline: Deadline) -> list[MonitorAlert]:
        await self._network(Capability.MONITOR, deadline, h)
        if h.provider != self.name:
            raise InvalidQuery(self.name, "handle belongs to another provider", capability=Capability.MONITOR)
        monitor = self._state.monitors.get(h.monitor_id)
        if monitor is None or monitor.handle.ref != h.ref:
            raise NotFound(self.name, "no such monitor", capability=Capability.MONITOR)
        record = self._record(h.ref, Capability.MONITOR)
        events: list[tuple[datetime, str, ProviderEventType]] = [
            (e.occurred_at, e.event_id, e.event_type) for e in record.monitor_events
        ]
        events += [(e.occurred_at, e.event_id, e.event_type) for e in self._state.emitted.get(record.provider_ref, [])]
        selected = sorted(e for e in events if e[2] in monitor.opts.event_types)
        return [
            MonitorAlert(
                alert_id=f"alr-{event_id}",
                provider=self.name,
                monitor_id=h.monitor_id,
                ref=monitor.handle.ref,
                event_type=event_type,
                occurred_at=occurred_at,
                evidence=(self._evidence(f"mock:events:{event_id}", "event_type", occurred_at),),
            )
            for occurred_at, event_id, event_type in selected[h.offset : h.offset + h.limit]
        ]

    # --- webhooks ----------------------------------------------------------------------------------

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent | None:
        return webhooks.verify(
            headers,
            body,
            secrets=(self.config.webhook_secret,),
            provider=self.name,
            now=int(self.config.clock().timestamp()),
            tolerance_seconds=self.config.webhook_tolerance_seconds,
        )
