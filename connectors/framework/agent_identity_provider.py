# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The provider seam for agent identity: who a desk agent is, and whether it may still act.

An agent identity provider holds an *agent identity credential* for each agent the platform runs:
an identifier, an attestation from an accredited issuer (a compact JWS or an opaque string), a
trust tier, the scopes the attestation allows, a status and an expiry, and the agent's public key.
The desk asks the provider before each agent action and acts only while the credential is active.

A provider:

- declares ``name`` (``^[a-z][a-z0-9_]{1,63}$``);
- ``resolve`` returns the credential for an agent, or raises :class:`AgentIdentityNotFound`;
- ``check_status`` answers the credential's current status. ``unknown`` is a value, not an error;
  a provider that cannot be reached raises :class:`AgentIdentityUnavailable`;
- ``present`` returns what the agent shows a verification provider that accepts a presented
  identity (:class:`connectors.framework.verification_types.PresentedIdentity`);
- ``prove_possession`` signs a key-possession proof with the agent's private key, which never
  leaves the provider;
- ``enrol_business`` hands a verified business to the provider for agent enrolment and returns the
  provider's own reference and status, exactly as received.

Every method takes a :class:`Deadline` and raises only :class:`AgentIdentityError` subclasses.
``connectors/agent_identity/mock.py`` is the local and test implementation; a private adapter plugs
in through the ``agenticorg.agent_identity_providers`` entry-point group.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from connectors.framework.verification_provider import Deadline
from connectors.framework.verification_types import PresentedIdentity


class IdentityStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


#: Statuses that stop an agent outright.
STOPPING_STATUSES = frozenset({IdentityStatus.REVOKED, IdentityStatus.SUSPENDED, IdentityStatus.EXPIRED})


class AgentIdentityError(Exception):
    """Base of every error a provider may raise. ``reason`` is a stable, low-cardinality code."""

    reason: ClassVar[str] = "agent_identity_error"

    def __init__(self, provider: str, message: str = "") -> None:
        self.provider = provider
        self.message = message
        super().__init__(f"{self.reason}: {provider}" + (f": {message}" if message else ""))


class AgentIdentityUnavailable(AgentIdentityError):  # noqa: N818 - names are the specified taxonomy
    """The provider could not be reached or did not answer in time."""

    reason = "agent_identity_unreachable"


class AgentIdentityNotFound(AgentIdentityError):  # noqa: N818 - names are the specified taxonomy
    """The provider holds no credential for this agent."""

    reason = "agent_identity_not_found"


class AgentIdentityRefused(AgentIdentityError):  # noqa: N818 - names are the specified taxonomy
    """The provider refused the request (for example an enrolment it will not take)."""

    reason = "agent_identity_refused"


def attestation_sha256(attestation: str) -> str:
    return "sha256:" + hashlib.sha256(attestation.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentRef:
    """The agent the platform asks about: its tenant and its role (for example ``onboarding_outreach``)."""

    tenant_id: str
    role: str


@dataclass(frozen=True)
class AgentIdentityCredential:
    identifier: str
    #: A compact JWS from the accredited issuer, or an opaque string the provider defines.
    attestation: str
    trust_tier: str
    allowed_scopes: frozenset[str]
    status: IdentityStatus
    expires_at: datetime | None
    #: RFC 7638 thumbprint of the agent's public key.
    key_thumbprint: str
    #: The agent's public key as a JWK, for registering it with the grant issuer.
    public_jwk: Mapping[str, Any] = field(default_factory=dict)

    @property
    def attestation_sha256(self) -> str:
        return attestation_sha256(self.attestation)


@dataclass(frozen=True)
class EnrolmentRequest:
    """A verified business handed to the provider. Only the verification reference and verified fields."""

    verification_ref: Mapping[str, Any]
    verified_fields: Mapping[str, Any]
    consent_grant_id: str
    idempotency_key: str


@dataclass(frozen=True)
class EnrolmentReceipt:
    """The provider's answer, recorded as received."""

    reference: str
    status: str


class AgentIdentityProvider(ABC):
    name: ClassVar[str]

    @abstractmethod
    async def resolve(self, agent: AgentRef, *, deadline: Deadline) -> AgentIdentityCredential: ...

    @abstractmethod
    async def check_status(self, identifier: str, *, deadline: Deadline) -> IdentityStatus: ...

    @abstractmethod
    async def present(
        self, credential: AgentIdentityCredential, *, audience: str, deadline: Deadline
    ) -> PresentedIdentity: ...

    @abstractmethod
    async def prove_possession(
        self, credential: AgentIdentityCredential, claims: Mapping[str, Any], *, typ: str, deadline: Deadline
    ) -> str:
        """A compact JWS over ``claims`` signed with the agent's key, ``kid`` set to its thumbprint."""

    async def enrol_business(self, request: EnrolmentRequest, *, deadline: Deadline) -> EnrolmentReceipt:
        raise AgentIdentityRefused(self.name, "this provider does not take agent enrolments")


__all__ = [
    "STOPPING_STATUSES",
    "AgentIdentityCredential",
    "AgentIdentityError",
    "AgentIdentityNotFound",
    "AgentIdentityProvider",
    "AgentIdentityRefused",
    "AgentIdentityUnavailable",
    "AgentRef",
    "EnrolmentReceipt",
    "EnrolmentRequest",
    "IdentityStatus",
    "PresentedIdentity",
    "attestation_sha256",
]
