# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The ``mock`` agent identity provider: deterministic credentials for local runs and tests only.

Each role gets an Ed25519 key derived from its name and an attestation signed by a fixed mock
issuer key, so every run sees the same identifiers, thumbprints and hashes. A fixture per role
chooses what the provider answers:

- ``active`` (the default), ``revoked``, ``suspended``, ``expired``: the credential with that status
  (``expired`` also carries an expiry in the past);
- ``unknown``: the credential resolves but its status is ``unknown``;
- ``unreachable``: every call raises :class:`AgentIdentityUnavailable`.

``AGENTICORG_MOCK_AGENT_IDENTITY_FIXTURE`` sets the default fixture; :meth:`set_fixture` sets one
role's. Enrolments are kept in memory and answered ``received`` with a reference derived from the
idempotency key. The provider refuses to run unless ``AGENTICORG_ENV`` is explicitly local,
development or test.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from connectors.framework.agent_identity_provider import (
    AgentIdentityCredential,
    AgentIdentityNotFound,
    AgentIdentityProvider,
    AgentIdentityRefused,
    AgentIdentityUnavailable,
    AgentRef,
    EnrolmentReceipt,
    EnrolmentRequest,
    IdentityStatus,
)
from connectors.framework.verification_provider import Deadline
from connectors.framework.verification_types import PresentedIdentity
from core.config import is_relaxed_env

FIXTURES = ("active", "revoked", "suspended", "expired", "unknown", "unreachable")
FIXTURE_ENV = "AGENTICORG_MOCK_AGENT_IDENTITY_FIXTURE"
ISSUER = "did:example:mock-accredited-issuer"
TRUST_TIER = "mock_tier_2"
ATTESTATION_TYP = "agent-attestation+jwt"
_STATUS = {
    "active": IdentityStatus.ACTIVE,
    "revoked": IdentityStatus.REVOKED,
    "suspended": IdentityStatus.SUSPENDED,
    "expired": IdentityStatus.EXPIRED,
    "unknown": IdentityStatus.UNKNOWN,
}


class MockAgentIdentityRefusedError(RuntimeError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _key(label: str) -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"agenticorg-mock-identity:{label}".encode()).digest())


def public_jwk(private_key: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {"crv": "Ed25519", "kty": "OKP", "x": _b64(raw)}


def jwk_thumbprint(jwk: Mapping[str, str]) -> str:
    """RFC 7638 thumbprint of an OKP key."""
    members = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}, separators=(",", ":"), sort_keys=True)
    return _b64(hashlib.sha256(members.encode("utf-8")).digest())


def sign_compact(private_key: Any, header: Mapping[str, Any], claims: Mapping[str, Any]) -> str:
    signing_input = (
        _b64(json.dumps(dict(header), separators=(",", ":"), sort_keys=True).encode())
        + "."
        + _b64(json.dumps(dict(claims), separators=(",", ":"), sort_keys=True, default=str).encode())
    )
    return signing_input + "." + _b64(private_key.sign(signing_input.encode("ascii")))


def default_scopes() -> frozenset[str]:
    from core.agent_identity import scopes

    tools = ("resolve_business", "verify_business", "verification_result", "ownership", "screen_person",
             "screen_business", "web_presence")  # fmt: skip
    return frozenset(
        {
            scopes.OUTREACH_SEND,
            scopes.SMS_REPLY,
            scopes.ENROLMENT,
            *(scopes.provider_tool_scope("mock", t) for t in tools),
        }
    )


class MockAgentIdentityProvider(AgentIdentityProvider):
    name = "mock"

    def __init__(self, *, clock: Callable[[], datetime] | None = None, default_fixture: str | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self.default_fixture = default_fixture or os.getenv(FIXTURE_ENV, "") or "active"
        if self.default_fixture not in FIXTURES:
            self.default_fixture = "active"
        self.fixtures: dict[str, str] = {}
        self.scopes: dict[str, frozenset[str]] = {}
        self.enrolments: dict[str, tuple[EnrolmentRequest, EnrolmentReceipt]] = {}
        self.enrolment_status = "received"
        self.calls: list[str] = []

    # --- test and demo controls (not part of the interface) -----------------------------------

    def set_fixture(self, role: str, fixture: str) -> None:
        if fixture not in FIXTURES:
            raise ValueError(f"fixture must be one of {FIXTURES}")
        self.fixtures[role] = fixture

    def reset(self) -> None:
        self.fixtures.clear()
        self.scopes.clear()
        self.enrolments.clear()
        self.calls.clear()
        self.enrolment_status = "received"

    def _fixture(self, role: str) -> str:
        return self.fixtures.get(role, self.default_fixture)

    @staticmethod
    def identifier_for(role: str) -> str:
        return f"did:example:mock-agent:{role}"

    @staticmethod
    def role_of(identifier: str) -> str:
        prefix = "did:example:mock-agent:"
        if not identifier.startswith(prefix):
            raise AgentIdentityNotFound("mock", "unknown identifier")
        return identifier[len(prefix) :]

    def credential(self, role: str) -> AgentIdentityCredential:
        fixture = self._fixture(role)
        now = self.clock()
        expires = now - timedelta(days=1) if fixture == "expired" else now + timedelta(days=30)
        key = _key(f"agent:{role}")
        jwk = public_jwk(key)
        allowed = self.scopes.get(role, default_scopes())
        identifier = self.identifier_for(role)
        attestation = sign_compact(
            _key("issuer"),
            {"alg": "EdDSA", "typ": ATTESTATION_TYP, "kid": ISSUER + "#key-1"},
            {
                "iss": ISSUER,
                "sub": identifier,
                "tier": TRUST_TIER,
                "scope": sorted(allowed),
                "cnf": {"jkt": jwk_thumbprint(jwk)},
                # Fixed issue time: the same role always gets the same attestation and hash.
                "iat": 1_790_000_000,
            },
        )
        return AgentIdentityCredential(
            identifier=identifier,
            attestation=attestation,
            trust_tier=TRUST_TIER,
            allowed_scopes=allowed,
            status=_STATUS.get(fixture, IdentityStatus.UNKNOWN),
            expires_at=expires,
            key_thumbprint=jwk_thumbprint(jwk),
            public_jwk=jwk,
        )

    def _reachable(self, role: str, call: str) -> None:
        self.calls.append(f"{call}:{role}")
        if self._fixture(role) == "unreachable":
            raise AgentIdentityUnavailable(self.name, "mock fixture unreachable")

    # --- the interface ---------------------------------------------------------------------------

    async def resolve(self, agent: AgentRef, *, deadline: Deadline) -> AgentIdentityCredential:
        self._reachable(agent.role, "resolve")
        return self.credential(agent.role)

    async def check_status(self, identifier: str, *, deadline: Deadline) -> IdentityStatus:
        role = self.role_of(identifier)
        self._reachable(role, "status")
        return _STATUS.get(self._fixture(role), IdentityStatus.UNKNOWN)

    async def present(
        self, credential: AgentIdentityCredential, *, audience: str, deadline: Deadline
    ) -> PresentedIdentity:
        self._reachable(self.role_of(credential.identifier), "present")
        return PresentedIdentity(
            identifier=credential.identifier,
            attestation=credential.attestation,
            attestation_sha256=credential.attestation_sha256,
            trust_tier=credential.trust_tier,
        )

    async def prove_possession(
        self, credential: AgentIdentityCredential, claims: Mapping[str, Any], *, typ: str, deadline: Deadline
    ) -> str:
        role = self.role_of(credential.identifier)
        self._reachable(role, "prove")
        return sign_compact(
            _key(f"agent:{role}"), {"alg": "EdDSA", "typ": typ, "kid": credential.key_thumbprint}, claims
        )

    async def enrol_business(self, request: EnrolmentRequest, *, deadline: Deadline) -> EnrolmentReceipt:
        self.calls.append("enrol")
        if self.default_fixture == "unreachable":
            raise AgentIdentityUnavailable(self.name, "mock fixture unreachable")
        if not request.verification_ref or not request.consent_grant_id:
            raise AgentIdentityRefused(self.name, "an enrolment needs a verification reference and a consent grant")
        existing = self.enrolments.get(request.idempotency_key)
        if existing is not None:
            return existing[1]
        reference = "mock-enrol-" + hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
        receipt = EnrolmentReceipt(reference=reference, status=self.enrolment_status)
        self.enrolments[request.idempotency_key] = (request, receipt)
        return receipt


_SHARED: MockAgentIdentityProvider | None = None


def mock_agent_identity_available() -> bool:
    return is_relaxed_env(os.getenv("AGENTICORG_ENV", ""))


def create_mock_agent_identity_provider() -> MockAgentIdentityProvider:
    """One process-wide mock, so a test or a local run can set fixtures the desk then sees."""
    global _SHARED
    if not mock_agent_identity_available():
        raise MockAgentIdentityRefusedError("the mock identity provider only runs in local, development and test")
    if _SHARED is None:
        _SHARED = MockAgentIdentityProvider()
    return _SHARED
