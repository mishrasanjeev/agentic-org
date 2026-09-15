# SPDX-License-Identifier: Apache-2.0
"""The provider seam for business verification, ownership, screening and monitoring data.

Agents, the policy engine and the evidence package depend only on this interface, so any data
source can sit behind it. A provider:

- declares ``name`` and the ``capabilities`` it actually offers. A method for an undeclared
  capability raises :class:`CapabilityNotSupported` - never ``NotImplementedError`` - and callers
  use :func:`call_capability`, which turns that into :class:`NotAvailable` so a workflow marks the
  section ``not_available`` instead of failing;
- starts verification and monitoring and lets the caller poll: ``verification_result`` returns
  :class:`Pending` as an ordinary value until the result is ready;
- takes a :class:`Deadline` on every I/O method and lets cancellation propagate;
- raises only :class:`ProviderError` subclasses;
- returns ``None`` from ``verify_webhook`` for any payload it cannot verify. A verified event is
  still only a trigger to re-query, never data to trust.

``connectors/providers/mock`` is the in-repository implementation and
``agenticorg.testing.provider_conformance`` checks an implementation against this contract. See
``docs/providers/writing-a-verification-provider.md`` and ``docs/adr/0009-provider-seam.md``.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal

import structlog

from connectors.framework.verification_types import (
    SCHEMA_VERSION,
    Address,
    BusinessCandidate,
    BusinessQuery,
    BusinessRef,
    BusinessSubject,
    BusinessVerification,
    CheckOutcome,
    CheckResult,
    DeclaredBusiness,
    Evidence,
    Identifier,
    ListSource,
    ListType,
    MonitorAlert,
    MonitorHandle,
    MonitorOptions,
    Officer,
    OfficerRole,
    OwnershipEdge,
    OwnershipGraph,
    OwnershipNode,
    OwnershipNodeKind,
    OwnershipRelationship,
    PartyKind,
    Pending,
    PercentageRange,
    PersonSubject,
    ProviderEvent,
    ProviderEventType,
    RegistryStatus,
    ScreeningHit,
    ScreeningResult,
    ScreeningSubject,
    ScreenOptions,
    UntrustedText,
    VerificationCheck,
    VerificationHandle,
    VerifyOptions,
    WebDomain,
    WebPage,
    WebPresence,
)

logger = structlog.get_logger()


class Capability(StrEnum):
    RESOLVE = "resolve"
    VERIFY = "verify"
    OWNERSHIP = "ownership"
    SCREEN_PERSON = "screen_person"
    SCREEN_BUSINESS = "screen_business"
    WEB_PRESENCE = "web_presence"
    MONITOR = "monitor"


#: The interface methods each capability covers.
CAPABILITY_METHODS: Mapping[Capability, tuple[str, ...]] = {
    Capability.RESOLVE: ("resolve_business",),
    Capability.VERIFY: ("verify_business", "verification_result"),
    Capability.OWNERSHIP: ("ownership",),
    Capability.SCREEN_PERSON: ("screen_person",),
    Capability.SCREEN_BUSINESS: ("screen_business",),
    Capability.WEB_PRESENCE: ("web_presence",),
    Capability.MONITOR: ("monitor_enroll", "monitor_result"),
}


# --- errors -------------------------------------------------------------------------------------


class ProviderError(Exception):
    """Base of every error a provider may raise. ``reason`` is a stable, low-cardinality code.

    Messages must not contain personal data or credentials: they reach logs.
    """

    reason: ClassVar[str] = "provider_error"
    retryable: ClassVar[bool] = False

    def __init__(self, provider: str, message: str = "", *, capability: Capability | None = None) -> None:
        self.provider = provider
        self.capability = capability
        self.message = message
        where = f"{provider}.{capability.value}" if capability else provider
        super().__init__(f"{self.reason}: {where}" + (f": {message}" if message else ""))


class CapabilityNotSupported(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The provider does not offer this capability. Callers map it to ``not_available``."""

    reason = "capability_not_supported"

    def __init__(self, provider: str, capability: Capability, message: str = "") -> None:
        super().__init__(provider, message, capability=capability)
        self.capability: Capability = capability


class ProviderTimeout(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The deadline passed before the provider answered."""

    reason = "provider_timeout"
    retryable = True


class ProviderUnavailable(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The provider could not be reached or reported a transient failure."""

    reason = "provider_unavailable"
    retryable = True


class ProviderRateLimited(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The provider refused the call for rate or quota reasons."""

    reason = "provider_rate_limited"
    retryable = True

    def __init__(
        self,
        provider: str,
        message: str = "",
        *,
        capability: Capability | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(provider, message, capability=capability)
        self.retry_after_seconds = retry_after_seconds


class InvalidQuery(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The request cannot be answered as asked, e.g. a query with neither a name nor an identifier."""

    reason = "invalid_query"


class NotFound(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The referenced business, verification or monitor does not exist at this provider."""

    reason = "not_found"


class ProviderAuthenticationFailed(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The provider rejected this deployment's credentials."""

    reason = "provider_authentication_failed"


class ProviderResponseInvalid(ProviderError):  # noqa: N818 - names are the specified taxonomy
    """The provider answered with something that is not a valid domain value. Never used partially."""

    reason = "provider_response_invalid"


# --- deadlines ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deadline:
    """An absolute point on the monotonic clock by which a call must finish.

    Create one per unit of work and pass the same deadline down, so retries and nested calls share
    one budget rather than each getting a fresh timeout.
    """

    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> Deadline:
        if seconds < 0:
            raise ValueError("a deadline cannot be in the past")
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    @asynccontextmanager
    async def enforce(self, provider: str, capability: Capability) -> AsyncIterator[None]:
        """Bound the enclosed work by this deadline, raising :class:`ProviderTimeout` when it passes.

        Cancellation from outside propagates unchanged.
        """
        if self.expired:
            raise ProviderTimeout(provider, "deadline already passed", capability=capability)
        try:
            async with asyncio.timeout(self.remaining()):
                yield
        except TimeoutError as exc:
            raise ProviderTimeout(provider, "deadline passed", capability=capability) from exc


# --- the interface ------------------------------------------------------------------------------


class VerificationProvider(ABC):  # noqa: B024 - every method has a capability-honest default
    """Implement the methods for the capabilities you declare; leave the rest.

    Each method's default raises :class:`CapabilityNotSupported`, so a provider that offers only
    resolution and verification overrides only those. A provider whose capability set can vary per
    instance calls :meth:`require` at the top of each method.
    """

    name: str
    capabilities: frozenset[Capability]

    def require(self, capability: Capability) -> None:
        if capability not in self.capabilities:
            raise CapabilityNotSupported(self.name, capability)

    async def resolve_business(self, q: BusinessQuery, *, deadline: Deadline) -> list[BusinessCandidate]:
        raise CapabilityNotSupported(self.name, Capability.RESOLVE)

    async def verify_business(self, ref: BusinessRef, opts: VerifyOptions, *, deadline: Deadline) -> VerificationHandle:
        raise CapabilityNotSupported(self.name, Capability.VERIFY)

    async def verification_result(self, h: VerificationHandle, *, deadline: Deadline) -> BusinessVerification | Pending:
        raise CapabilityNotSupported(self.name, Capability.VERIFY)

    async def ownership(self, ref: BusinessRef, *, deadline: Deadline) -> OwnershipGraph:
        raise CapabilityNotSupported(self.name, Capability.OWNERSHIP)

    async def screen_person(self, s: PersonSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        raise CapabilityNotSupported(self.name, Capability.SCREEN_PERSON)

    async def screen_business(self, s: BusinessSubject, opts: ScreenOptions, *, deadline: Deadline) -> ScreeningResult:
        raise CapabilityNotSupported(self.name, Capability.SCREEN_BUSINESS)

    async def web_presence(self, ref: BusinessRef, *, deadline: Deadline) -> WebPresence:
        raise CapabilityNotSupported(self.name, Capability.WEB_PRESENCE)

    async def monitor_enroll(self, ref: BusinessRef, opts: MonitorOptions, *, deadline: Deadline) -> MonitorHandle:
        raise CapabilityNotSupported(self.name, Capability.MONITOR)

    async def monitor_result(self, h: MonitorHandle, *, deadline: Deadline) -> list[MonitorAlert]:
        raise CapabilityNotSupported(self.name, Capability.MONITOR)

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent | None:
        """Return the event only when the payload's authenticity is proven; otherwise ``None``.

        Synchronous and local: no I/O, so it needs no deadline. Must never raise. Header names are
        case-insensitive. Replay detection (a repeated ``event_id``) is the caller's job.
        """
        return None


# --- graceful degradation -----------------------------------------------------------------------


@dataclass(frozen=True)
class NotAvailable:
    """The capability is not offered; the workflow marks the dependent section ``not_available``."""

    capability: Capability
    reason: Literal["capability_not_supported"] = "capability_not_supported"


async def call_capability[T](
    provider: VerificationProvider, capability: Capability, call: Callable[[], Awaitable[T]]
) -> T | NotAvailable:
    """Run ``call`` if ``provider`` declares ``capability``; otherwise return :class:`NotAvailable`.

    An undeclared capability is never invoked. A declared capability whose method still raises
    :class:`CapabilityNotSupported` also degrades to :class:`NotAvailable`, and is logged because
    the provider's declaration is wrong. Every other error propagates.
    """
    if capability not in provider.capabilities:
        return NotAvailable(capability)
    try:
        return await call()
    except CapabilityNotSupported as exc:
        if exc.capability is not capability:
            raise
        logger.warning(
            "provider_capability_declared_but_unsupported", provider=provider.name, capability=capability.value
        )
        return NotAvailable(capability)


__all__ = [
    "CAPABILITY_METHODS",
    "SCHEMA_VERSION",
    "Address",
    "BusinessCandidate",
    "BusinessQuery",
    "BusinessRef",
    "BusinessSubject",
    "BusinessVerification",
    "Capability",
    "CapabilityNotSupported",
    "CheckOutcome",
    "CheckResult",
    "Deadline",
    "DeclaredBusiness",
    "Evidence",
    "Identifier",
    "InvalidQuery",
    "ListSource",
    "ListType",
    "MonitorAlert",
    "MonitorHandle",
    "MonitorOptions",
    "NotAvailable",
    "NotFound",
    "Officer",
    "OfficerRole",
    "OwnershipEdge",
    "OwnershipGraph",
    "OwnershipNode",
    "OwnershipNodeKind",
    "OwnershipRelationship",
    "PartyKind",
    "Pending",
    "PercentageRange",
    "PersonSubject",
    "ProviderAuthenticationFailed",
    "ProviderError",
    "ProviderEvent",
    "ProviderEventType",
    "ProviderRateLimited",
    "ProviderResponseInvalid",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RegistryStatus",
    "ScreenOptions",
    "ScreeningHit",
    "ScreeningResult",
    "ScreeningSubject",
    "UntrustedText",
    "VerificationCheck",
    "VerificationHandle",
    "VerificationProvider",
    "VerifyOptions",
    "WebDomain",
    "WebPage",
    "WebPresence",
    "call_capability",
]
