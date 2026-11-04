# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Agent identity for desk and case agents (MO-60 to MO-64): the provider seam, the guard, Grantex, the wiring."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from connectors.agent_identity import mock as mock_identity
from connectors.agent_identity.registry import AgentIdentityProviderRegistry, AgentIdentityRegistryError
from connectors.framework.agent_identity_provider import (
    AgentIdentityProvider,
    AgentIdentityUnavailable,
    AgentRef,
    IdentityStatus,
)
from connectors.framework.verification_provider import Deadline, VerificationProvider, VerifyOptions
from connectors.framework.verification_types import PresentedIdentity
from connectors.providers.mock import MockProvider
from core.agent_identity import grantex, guard, scopes
from core.cases.states import CaseError
from core.config import settings
from core.onboarding_desk import applications, outreach, store
from core.tool_gateway.provider_gateway import (
    ProviderToolGateway,
    ToolCallRecord,
    ToolDecision,
    ToolRefusedError,
    canonical_sha256,
)
from tests.unit import test_onboarding_desk_applications as _application_tests
from tests.unit import test_onboarding_desk_outreach as _outreach_tests

TENANT = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)
LEAD = uuid.uuid4()

# The outreach engine and text conversation fakes those modules' tests use.
engine = _outreach_tests.engine
lead_row = _outreach_tests.lead_row
chat = _application_tests.chat
say = _application_tests.say


def deadline() -> Deadline:
    return Deadline.after(5)


# ── Fakes ────────────────────────────────────────────────────────────────────


@dataclass
class FakeGrants:
    """The agent's Grantex grant as the guard sees it; ``error`` holds the action."""

    thumbprint: str = ""
    active: bool = True
    scopes: frozenset[str] = field(default_factory=mock_identity.default_scopes)
    error: str = ""
    calls: int = 0

    async def grant(self, tenant_id: str, role: str) -> guard.AgentGrantView:
        self.calls += 1
        if self.error:
            raise guard.GrantUnavailableError(self.error)
        thumbprint = (
            self.thumbprint
            or mock_identity.MockAgentIdentityProvider(clock=lambda: NOW).credential(role).key_thumbprint
        )
        return guard.AgentGrantView("grnt_desk_1", self.active, self.scopes, thumbprint)


@pytest.fixture
def identity(monkeypatch):
    """Agent identity on, with the mock identity provider and a fake Grantex grant."""
    monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", True)
    monkeypatch.setattr(settings, "onboarding_desk_agent_identity_provider", "mock")
    guard.clear_cache()
    provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
    grants = FakeGrants()
    clock = {"now": NOW}
    the_guard = guard.AgentIdentityGuard(
        provider_factory=lambda: provider,
        grant_source=grants,
        clock=lambda: clock["now"],
        cache_seconds=lambda: 60,
        provider_name=lambda: "mock",
    )
    monkeypatch.setattr(guard, "default_guard", lambda: the_guard)
    yield SimpleNamespace(provider=provider, grants=grants, guard=the_guard, clock=clock)
    guard.clear_cache()


def check(state, role=scopes.OUTREACH_ROLE, scope=scopes.OUTREACH_SEND):
    return asyncio.run(state.guard.check(TENANT, role, scope))


# ── MO-60: settings ──────────────────────────────────────────────────────────


class TestSettings:
    def test_off_by_default(self):
        from core.config import Settings

        fresh = Settings()
        assert fresh.onboarding_desk_agent_identity_enabled is False
        assert fresh.onboarding_desk_agent_identity_provider == ""
        assert fresh.onboarding_desk_agent_identity_status_cache_seconds == 60

    def test_off_calls_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        assert guard.default_guard() is None
        assert asyncio.run(guard.check_action(TENANT, scopes.OUTREACH_ROLE)) is guard.OFF

        def boom():
            raise AssertionError("no provider while off")

        off_guard = guard.AgentIdentityGuard(provider_factory=boom)
        result = asyncio.run(off_guard.check(TENANT, scopes.OUTREACH_ROLE, scopes.OUTREACH_SEND))
        assert result.enabled is False and result.allowed is True and result.stamp_dict() is None


# ── The provider seam, the mock and the registry ─────────────────────────────


class TestMockProvider:
    def test_fixtures(self):
        provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
        active = asyncio.run(provider.resolve(AgentRef(str(TENANT), "onboarding_outreach"), deadline=deadline()))
        assert active.status is IdentityStatus.ACTIVE and active.expires_at > NOW
        assert active.attestation.count(".") == 2 and active.attestation_sha256.startswith("sha256:")
        assert active.key_thumbprint == mock_identity.jwk_thumbprint(active.public_jwk)
        assert scopes.OUTREACH_SEND in active.allowed_scopes and active.trust_tier == mock_identity.TRUST_TIER
        again = provider.credential("onboarding_outreach")
        assert again.attestation_sha256 == active.attestation_sha256, "deterministic per role"
        for fixture, status in (("revoked", "revoked"), ("suspended", "suspended"), ("expired", "expired"),
                                ("unknown", "unknown")):  # fmt: skip
            provider.set_fixture("r", fixture)
            assert asyncio.run(provider.check_status(provider.identifier_for("r"), deadline=deadline())) == status
        assert provider.credential("r").expires_at > NOW
        provider.set_fixture("r", "expired")
        assert provider.credential("r").expires_at < NOW
        provider.set_fixture("r", "unreachable")
        with pytest.raises(AgentIdentityUnavailable):
            asyncio.run(provider.resolve(AgentRef("t", "r"), deadline=deadline()))
        with pytest.raises(ValueError):
            provider.set_fixture("r", "nope")

    def test_present_and_prove(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
        credential = provider.credential("business_underwriter")
        presented = asyncio.run(provider.present(credential, audience="mock", deadline=deadline()))
        assert presented.identifier == credential.identifier
        assert presented.attestation_sha256 == credential.attestation_sha256
        proof = asyncio.run(
            provider.prove_possession(credential, {"nonce": "n1", "aud": "a"}, typ="agent-key-proof+jwt",
                                      deadline=deadline())
        )  # fmt: skip
        head, body, signature = proof.split(".")
        header = json.loads(base64.urlsafe_b64decode(head + "=="))
        assert header == {"alg": "EdDSA", "kid": credential.key_thumbprint, "typ": "agent-key-proof+jwt"}
        raw = base64.urlsafe_b64decode(credential.public_jwk["x"] + "==")
        Ed25519PublicKey.from_public_bytes(raw).verify(
            base64.urlsafe_b64decode(signature + "=="), f"{head}.{body}".encode()
        )

    def test_mock_refuses_outside_local_and_test(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_ENV", "production")
        assert mock_identity.mock_agent_identity_available() is False
        with pytest.raises(mock_identity.MockAgentIdentityRefusedError):
            mock_identity.create_mock_agent_identity_provider()
        with pytest.raises(AgentIdentityRegistryError) as caught:
            AgentIdentityProviderRegistry.create("mock")
        assert caught.value.reason == "unavailable"


class _PluginProvider(AgentIdentityProvider):
    name = "example_identity"

    async def resolve(self, agent, *, deadline):
        raise AgentIdentityUnavailable(self.name)

    async def check_status(self, identifier, *, deadline):
        return IdentityStatus.UNKNOWN

    async def present(self, credential, *, audience, deadline):
        raise AgentIdentityUnavailable(self.name)

    async def prove_possession(self, credential, claims, *, typ, deadline):
        raise AgentIdentityUnavailable(self.name)


class TestRegistry:
    def test_native_and_plugins(self, monkeypatch):
        monkeypatch.setenv("AGENTICORG_ENV", "test")
        monkeypatch.setattr(AgentIdentityProviderRegistry, "_providers", dict(AgentIdentityProviderRegistry._providers))
        assert "mock" in AgentIdentityProviderRegistry.names()
        assert isinstance(AgentIdentityProviderRegistry.create("mock"), mock_identity.MockAgentIdentityProvider)
        with pytest.raises(AgentIdentityRegistryError) as caught:
            AgentIdentityProviderRegistry.create("nope")
        assert caught.value.reason == "unknown_provider"
        assert AgentIdentityProviderRegistry.register_plugin(_PluginProvider) == "example_identity"
        assert AgentIdentityProviderRegistry.get("example_identity").source == "plugin"
        for bad, reason in (
            (_PluginProvider, "name_conflict"),
            (VerificationProvider, "invalid_type"),
            (object(), "invalid_type"),
        ):
            with pytest.raises(AgentIdentityRegistryError) as caught:
                AgentIdentityProviderRegistry.register_plugin(bad)
            assert caught.value.reason == reason

    def test_entry_point_group(self, monkeypatch):
        from connectors import plugins

        monkeypatch.setattr(AgentIdentityProviderRegistry, "_providers", dict(AgentIdentityProviderRegistry._providers))
        assert plugins.IDENTITY_PROVIDERS_GROUP == "agenticorg.agent_identity_providers"
        assert plugins.IDENTITY_PROVIDERS_GROUP in plugins.PLUGIN_GROUPS

        class EntryPoint:
            name = "example_identity"
            dist = SimpleNamespace(name="example-identity-agenticorg")

            def load(self):
                return _PluginProvider

        def entry_points(group):
            return [EntryPoint()] if group == plugins.IDENTITY_PROVIDERS_GROUP else []

        outcomes = plugins.load_plugins(
            enabled=True, allowlist=["example-identity-agenticorg"], entry_points=entry_points
        )
        assert [(o.group, o.loaded, o.detail) for o in outcomes] == [
            (plugins.IDENTITY_PROVIDERS_GROUP, True, "example_identity")
        ]
        again = plugins.load_plugins(enabled=True, allowlist=["example-identity-agenticorg"], entry_points=entry_points)
        assert again[0].loaded is False and again[0].reason == "name_conflict"


# ── MO-62: the guard ─────────────────────────────────────────────────────────


class TestGuard:
    def test_active_and_bound_is_allowed_within_the_narrower_scopes(self, identity):
        identity.grants.scopes = frozenset({scopes.OUTREACH_SEND, "agenticorg:other:write:x"})
        result = check(identity)
        assert result.allowed and result.outcome == "allowed" and result.grant_id == "grnt_desk_1"
        assert result.scopes == frozenset({scopes.OUTREACH_SEND}), "only scopes both sets hold"
        credential = identity.provider.credential(scopes.OUTREACH_ROLE)
        assert result.stamp_dict() == {
            "agent_identity_id": credential.identifier,
            "attestation_sha256": credential.attestation_sha256,
            "trust_tier": "mock_tier_2",
        }
        # In the grant but not the attestation, and in the attestation but not the grant: both stopped.
        assert check(identity, scope="agenticorg:other:write:x").reason == "agent_scope_not_allowed"
        assert check(identity, scope=scopes.SMS_REPLY).reason == "agent_scope_not_allowed"

    @pytest.mark.parametrize(
        ("fixture", "outcome", "reason"),
        [
            ("revoked", "stopped", "agent_identity_revoked"),
            ("suspended", "stopped", "agent_identity_suspended"),
            ("expired", "stopped", "agent_identity_expired"),
            ("unknown", "held", "agent_identity_unknown"),
            ("unreachable", "held", "agent_identity_unreachable"),
        ],
    )
    def test_statuses(self, identity, fixture, outcome, reason):
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, fixture)
        result = check(identity)
        assert (result.allowed, result.outcome, result.reason) == (False, outcome, reason)
        assert identity.grants.calls == 0, "the grant is only checked for an active identity"

    def test_grant_problems(self, identity):
        identity.grants.error = "grantex_unreachable"
        assert (check(identity).outcome, check(identity).reason) == ("held", "grantex_unreachable")
        identity.grants.error = ""
        identity.grants.active = False
        guard.clear_cache()
        assert (check(identity).outcome, check(identity).reason) == ("stopped", "agent_grant_inactive")
        identity.grants.active = True
        identity.grants.thumbprint = "another-key"
        guard.clear_cache()
        assert check(identity).reason == "agent_grant_not_bound"

    def test_provider_not_configured_or_unavailable_holds(self, identity, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_provider", "")
        unconfigured = guard.AgentIdentityGuard(grant_source=identity.grants, provider_name=lambda: "")
        result = asyncio.run(unconfigured.check(TENANT, scopes.OUTREACH_ROLE, scopes.OUTREACH_SEND))
        assert (result.outcome, result.reason) == ("held", "identity_provider_not_configured")
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_provider", "missing")
        missing = guard.AgentIdentityGuard(grant_source=identity.grants, provider_name=lambda: "missing")
        assert asyncio.run(missing.check(TENANT, "r", None)).reason == "identity_provider_unavailable"

    def test_cache_is_brief_and_never_past_expiry(self, identity):
        assert check(identity).allowed
        calls = len(identity.provider.calls)
        assert check(identity).allowed and len(identity.provider.calls) == calls, "served from the cache"
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "revoked")
        assert check(identity).allowed, "still cached"
        identity.clock["now"] = NOW + timedelta(seconds=61)
        assert check(identity).reason == "agent_identity_revoked"
        # A credential expiring sooner than the cache period is re-checked at its expiry.
        guard.clear_cache()
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "active")
        identity.clock["now"] = NOW
        short = identity.provider.credential(scopes.OUTREACH_ROLE)
        soon = NOW + timedelta(seconds=10)
        identity.provider.credential = lambda role, c=short: _replace(c, expires_at=soon)
        assert check(identity).allowed
        identity.clock["now"] = NOW + timedelta(seconds=11)
        assert check(identity).reason == "agent_identity_expired"

    def test_failures_are_not_cached(self, identity):
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "unreachable")
        assert check(identity).held
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "active")
        assert check(identity).allowed


def _replace(credential, **changes):
    import dataclasses

    return dataclasses.replace(credential, **changes)


# ── MO-61: Grantex key binding and grant checks ──────────────────────────────


class FakeGrantex:
    """The Grantex routes agent identity uses, checking the key-possession proof like Grantex does."""

    def __init__(self) -> None:
        self.keys: dict[str, dict[str, Any]] = {}
        self.nonce = "nonce-123"
        self.grants: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.down = False
        self.verify_answer: dict[str, Any] = {"active": True, "claims": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if self.down:
            raise httpx.ConnectError("down")
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))
        parts = path.strip("/").split("/")
        if path.endswith("/keys") and request.method == "GET":
            return httpx.Response(200, json={"agentId": parts[2], "keys": list(self.keys.values())})
        if path.endswith("/keys") and request.method == "POST":
            jwk = body["publicJwk"]
            thumbprint = mock_identity.jwk_thumbprint(jwk)
            self.keys[thumbprint] = {"thumbprint": thumbprint, "jwk": jwk, "status": "pending"}
            return httpx.Response(201, json=self.keys[thumbprint])
        if path.endswith("/challenge"):
            return httpx.Response(201, json={"thumbprint": parts[4], "challenge": self.nonce,
                                             "audience": "https://grantex.example", "subject": parts[2],
                                             "typ": "agent-key-proof+jwt", "alg": "EdDSA"})  # fmt: skip
        if path.endswith("/prove"):
            key = self.keys[parts[4]]
            head, payload, signature = body["proof"].split(".")
            raw = base64.urlsafe_b64decode(key["jwk"]["x"] + "==")
            Ed25519PublicKey.from_public_bytes(raw).verify(
                base64.urlsafe_b64decode(signature + "=="), f"{head}.{payload}".encode()
            )
            claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
            if claims["nonce"] != self.nonce or claims["aud"] != "https://grantex.example":
                return httpx.Response(400, json={"code": "key_unproven"})
            key["status"] = "active"
            return httpx.Response(200, json=key)
        if path == "/v1/grants/verify":
            return httpx.Response(200, json=self.verify_answer)
        if path == "/v1/authorize":
            return httpx.Response(201, json={"authRequestId": "areq_1", "consentUrl": "https://grantex.example/c/1"})
        if path == "/v1/grants" and request.method == "GET":
            return httpx.Response(200, json={"grants": [g for g in self.grants.values() if g["status"] == "active"]})
        if path.startswith("/v1/grants/") and request.method == "GET":
            grant = self.grants.get(parts[2])
            return httpx.Response(200, json=grant) if grant else httpx.Response(404, json={"code": "NOT_FOUND"})
        if path.startswith("/v1/grants/") and request.method == "DELETE":
            grant = self.grants.get(parts[2])
            if not grant or grant["status"] != "active":
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            grant["status"] = "revoked"
            return httpx.Response(204)
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    def service(self) -> grantex.GrantexIdentityService:
        return grantex.GrantexIdentityService(
            base_url="https://grantex.example",
            api_key="test-key",
            client_factory=lambda: httpx.AsyncClient(
                base_url="https://grantex.example", transport=httpx.MockTransport(self.handler)
            ),
        )


class TestGrantex:
    def test_bind_registers_and_proves_the_key(self):
        fake = FakeGrantex()
        provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
        agent = AgentRef(str(TENANT), scopes.OUTREACH_ROLE)
        result = asyncio.run(grantex.bind_agent_key(fake.service(), provider, agent, "ag_1", now=NOW))
        thumbprint = provider.credential(scopes.OUTREACH_ROLE).key_thumbprint
        assert result == {"thumbprint": thumbprint, "status": "active", "registered": True}
        assert [(m, p) for m, p, _ in fake.requests] == [
            ("GET", "/v1/agents/ag_1/keys"),
            ("POST", "/v1/agents/ag_1/keys"),
            ("POST", f"/v1/agents/ag_1/keys/{thumbprint}/challenge"),
            ("POST", f"/v1/agents/ag_1/keys/{thumbprint}/prove"),
        ]
        again = asyncio.run(grantex.bind_agent_key(fake.service(), provider, agent, "ag_1", now=NOW))
        assert again["registered"] is False and again["status"] == "active"

    def test_bind_fails_closed(self):
        fake = FakeGrantex()
        provider = mock_identity.MockAgentIdentityProvider(clock=lambda: NOW)
        provider.set_fixture("r", "unreachable")
        with pytest.raises(grantex.GrantexIdentityError) as caught:
            asyncio.run(grantex.bind_agent_key(fake.service(), provider, AgentRef("t", "r"), "ag_1"))
        assert caught.value.reason == "agent_identity_unreachable"
        fake.down = True
        with pytest.raises(grantex.GrantexIdentityError) as caught:
            asyncio.run(grantex.bind_agent_key(fake.service(), provider, AgentRef("t", "ok"), "ag_1"))
        assert caught.value.reason == "grantex_unreachable"
        fake.down = False
        fake.keys["x"] = {"thumbprint": provider.credential("ok").key_thumbprint, "status": "compromised"}
        with pytest.raises(grantex.GrantexIdentityError) as caught:
            asyncio.run(grantex.bind_agent_key(fake.service(), provider, AgentRef("t", "ok"), "ag_1"))
        assert caught.value.reason == "agent_key_not_usable"

    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("GRANTEX_BASE_URL", raising=False)
        assert grantex.grantex_identity_service() is None
        monkeypatch.setenv("GRANTEX_BASE_URL", "https://grantex.example")
        monkeypatch.setenv("GRANTEX_API_KEY", "k")
        assert grantex.grantex_identity_service().base_url == "https://grantex.example"
        with pytest.raises(grantex.GrantexIdentityError):
            grantex.GrantexIdentityService(base_url="", api_key="")

    def test_grants_and_revocation(self):
        fake = FakeGrantex()
        service = fake.service()
        fake.grants["grnt_1"] = {"grantId": "grnt_1", "status": "active", "scopes": [scopes.ENROLMENT]}
        assert asyncio.run(service.active_grants(agent_id="ag", principal_id="p"))[0]["grantId"] == "grnt_1"
        assert asyncio.run(service.get_grant("grnt_1"))["status"] == "active"
        assert asyncio.run(service.revoke_grant("grnt_1")) is True
        assert asyncio.run(service.revoke_grant("grnt_1")) is False
        with pytest.raises(grantex.GrantexIdentityError) as caught:
            asyncio.run(service.get_grant("nope"))
        assert caught.value.reason == "grantex_refused"
        consent = asyncio.run(
            service.authorize_consent(agent_id="ag", principal_id="p", scopes=[scopes.ENROLMENT],
                                      purpose=grantex.ENROLMENT_PURPOSE)
        )  # fmt: skip
        assert consent["authRequestId"] == "areq_1"
        assert fake.requests[-1][2]["purpose"] == "x-agenticorg.agent_enrolment"

    def test_the_grant_source_checks_the_key_binding(self, monkeypatch):
        import auth.run_grants as run_grants
        import core.cases.grant_authorizer as authorizer

        fake = FakeGrantex()
        fake.verify_answer = {"active": True, "claims": {"grnt": "grnt_9", "scp": [scopes.OUTREACH_SEND, 7],
                                                         "cnf": {"jkt": "thumb"}}}  # fmt: skip
        agents = {"value": ("agent-1", {"grantex_agent_id": "ag_1"})}

        async def active_agent(tenant_id, role):
            return agents["value"]

        async def resolve(**kwargs):
            assert kwargs["runtime"] == "agent_identity"
            return run_grants.RunGrant(mode=kwargs["mode"], token="tok", grant_id="grnt_9")

        monkeypatch.setattr(authorizer, "_active_agent", active_agent)
        monkeypatch.setattr(run_grants, "resolve_run_grant", resolve)
        source = guard.GrantexBoundGrantSource(service_factory=fake.service)
        view = asyncio.run(source.grant(str(TENANT), scopes.OUTREACH_ROLE))
        assert view == guard.AgentGrantView("grnt_9", True, frozenset({scopes.OUTREACH_SEND}), "thumb")
        agents["value"] = None
        with pytest.raises(guard.GrantUnavailableError, match="desk_agent_not_configured"):
            asyncio.run(source.grant(str(TENANT), scopes.OUTREACH_ROLE))
        with pytest.raises(guard.GrantUnavailableError, match="grantex_not_configured"):
            asyncio.run(guard.GrantexBoundGrantSource(service_factory=lambda: None).grant("t", "r"))


# ── MO-62 and MO-63: outreach ────────────────────────────────────────────────


@pytest.fixture
def actions(monkeypatch):
    recorded: list[dict[str, Any]] = []

    async def record(tenant_id, *, role, action, check, **kwargs):
        if check.enabled:
            recorded.append({"role": role, "action": action, "outcome": check.outcome, "reason": check.reason,
                             "stamp": check.stamp_dict(), **kwargs})  # fmt: skip

    monkeypatch.setattr(store, "record_agent_action", record)
    return recorded


class TestOutreach:
    def test_off_is_release_one(self, engine, actions, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        claimed = []

        async def claim(tid, lid, channel, kind, *, now):
            claimed.append(channel)
            return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(store, "claim_attempt", claim)
        assert asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW)).sent
        assert claimed == ["sms"] and actions == []

    def test_allowed_records_the_identity_on_the_attempt(self, engine, actions, identity, monkeypatch):
        claimed = []

        async def claim(tid, lid, channel, kind, *, now, identity=None):
            claimed.append(identity)
            return SimpleNamespace(id=uuid.uuid4())

        monkeypatch.setattr(store, "claim_attempt", claim)
        assert asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW)).sent
        stamp = identity.provider.credential(scopes.OUTREACH_ROLE)
        assert claimed[0]["agent_identity_id"] == stamp.identifier
        assert claimed[0]["attestation_sha256"] == stamp.attestation_sha256
        assert actions[0]["outcome"] == "allowed" and actions[0]["action"] == "outreach_send"

    def test_held_reschedules_and_stopped_parks(self, engine, actions, identity, monkeypatch):
        async def claim(*args, **kwargs):
            raise AssertionError("nothing is claimed for a held or stopped agent")

        monkeypatch.setattr(store, "claim_attempt", claim)
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "unreachable")
        held = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert not held.sent and held.reason == "agent_identity_unreachable"
        assert held.next_allowed_at == NOW + timedelta(minutes=15) and engine["next"][-1] == held.next_allowed_at
        identity.provider.set_fixture(scopes.OUTREACH_ROLE, "revoked")
        stopped = asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert stopped.reason == "agent_identity_revoked" and stopped.next_allowed_at is None
        assert engine["next"][-1] == NOW + timedelta(hours=24), "a new lead is checked again daily"
        engine["lead"] = lead_row(status="contacted")
        asyncio.run(outreach.contact_lead(TENANT, LEAD, now=NOW))
        assert engine["next"][-1] is None
        assert engine["sent"] == [] and [a["outcome"] for a in actions] == ["held", "stopped", "stopped"]


class TestStoreRecords:
    def test_attempt_view_carries_identity_only_when_set(self):
        base = SimpleNamespace(id=uuid.uuid4(), channel="sms", kind="first_contact", status="sent", refusal_reason=None,
                               provider_ref="SM1", attempt_no=1, created_at=NOW, sent_at=NOW)  # fmt: skip
        assert "agent_identity" not in store.attempt_view(base)
        stamped = SimpleNamespace(**vars(base), agent_identity_id="did:x", agent_attestation_sha256="sha256:a",
                                  agent_trust_tier="t")  # fmt: skip
        assert store.attempt_view(stamped)["agent_identity"] == {
            "agent_identity_id": "did:x",
            "attestation_sha256": "sha256:a",
            "trust_tier": "t",
        }

    def test_record_agent_action_only_when_on(self, monkeypatch):
        added = []

        class Session:
            def add(self, row):
                added.append(row)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def session(tid):
            yield Session()

        monkeypatch.setattr(store, "_session", session)
        asyncio.run(store.record_agent_action(TENANT, role="r", action="a", check=guard.OFF))
        assert added == []
        check_ = guard.IdentityCheck(enabled=True, allowed=False, held=True, reason="agent_identity_unknown",
                                     stamp=guard.IdentityStamp("did:x", "sha256:a", "t"))  # fmt: skip
        asyncio.run(store.record_agent_action(TENANT, role="r", action="a", check=check_, lead_id=LEAD, channel="sms"))
        row = added[0]
        assert (row.outcome, row.reason, row.agent_identity_id, row.agent_trust_tier) == (
            "held",
            "agent_identity_unknown",
            "did:x",
            "t",
        )
        assert store.agent_action_view(SimpleNamespace(**{**vars(row), "created_at": NOW}))["outcome"] == "held"


# ── MO-62 and MO-63: text replies ────────────────────────────────────────────


class TestConversation:
    def test_allowed_reply_records_the_identity(self, chat, identity, actions):
        reply = say("hi")
        assert reply.startswith("Northwind Payments: Legal business name?")
        desk = chat["transcript"][1]
        assert desk["from"] == "desk" and desk["agent_identity"]["trust_tier"] == "mock_tier_2"
        assert actions[0]["action"] == "sms_reply" and actions[0]["outcome"] == "allowed"

    @pytest.mark.parametrize("fixture", ["revoked", "unknown", "unreachable", "suspended"])
    def test_no_reply_when_held_or_stopped(self, chat, identity, actions, fixture):
        identity.provider.set_fixture(scopes.CONVERSATION_ROLE, fixture)
        assert say("Northwind Coffee LLC") is None
        assert chat["transcript"] == [{"channel": "sms", "from": "applicant", "text": "Northwind Coffee LLC"}]
        assert chat["answers"] == {} and actions[0]["outcome"] in ("held", "stopped")

    def test_off_transcript_is_unchanged(self, chat, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        say("hi")
        assert all("agent_identity" not in entry for entry in chat["transcript"])

    def test_the_transcript_keeps_only_desk_identity_fields(self, monkeypatch):
        sealed = {}

        class Row:
            transcript_ciphertext = None

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def session(tid):
            yield SimpleNamespace()

        async def kek(tid):
            return "k"

        async def locked(session, tid, aid):
            return Row

        async def seal(k, text):
            sealed["text"] = text
            return text

        monkeypatch.setattr(store, "_session", session)
        monkeypatch.setattr(applications, "_kek", kek)
        monkeypatch.setattr(applications, "_locked", locked)
        monkeypatch.setattr(applications, "_seal", seal)
        entries = [
            {"from": "applicant", "text": "hi", "agent_identity": {"agent_identity_id": "x"}},
            {"from": "desk", "text": "ok", "agent_identity": {"agent_identity_id": "did:x", "extra": "dropped"}},
        ]
        asyncio.run(applications.append_transcript(TENANT, uuid.uuid4(), entries, now=NOW))
        stored = json.loads(sealed["text"])
        assert "agent_identity" not in stored[0]
        assert stored[1]["agent_identity"] == {"agent_identity_id": "did:x", "attestation_sha256": "", "trust_tier": ""}


# ── MO-62, MO-63 and MO-64: case agents ──────────────────────────────────────


class Allow:
    def __init__(self):
        self.calls = []

    async def authorize(self, *, connector, tool):
        self.calls.append(tool)
        return ToolDecision(allowed=True, grant_id="grnt_run_1")


def gateway(identity_state, provider=None, inner=None):
    return ProviderToolGateway(
        provider=provider or MockProvider(),
        agent="business_underwriter",
        tool_set=frozenset({"verify_business", "resolve_business"}),
        authorizer=guard.IdentityBoundAuthorizer(
            inner=inner or Allow(), guard=identity_state.guard, tenant_id=str(TENANT), role="business_underwriter"
        ),
    )


class TestCaseAgents:
    def test_each_call_is_checked_and_records_the_identity(self, identity):
        gw = gateway(identity)
        mock = gw.provider
        ref = mock.ref_for("gb-dissolved-ashcombe")
        handle = asyncio.run(
            gw.verify_business(ref, VerifyOptions(idempotency_key="case.verify.1"), deadline=deadline())
        )
        credential = identity.provider.credential("business_underwriter")
        assert handle.accepted_identity == credential.identifier, "the mock echoes the presented identity"
        record = gw.records[0].to_dict()
        assert record["agent_identity"]["agent_identity_id"] == credential.identifier
        assert record["identity_presented"] is True and record["grant_id"] == "grnt_run_1"
        identity.provider.set_fixture("business_underwriter", "suspended")
        guard.clear_cache()
        with pytest.raises(ToolRefusedError) as caught:
            asyncio.run(gw.verify_business(ref, VerifyOptions(idempotency_key="case.verify.2"), deadline=deadline()))
        assert caught.value.reason == "agent_identity_suspended"
        identity.provider.set_fixture("business_underwriter", "unreachable")
        guard.clear_cache()
        with pytest.raises(ToolRefusedError) as caught:
            asyncio.run(gw.verify_business(ref, VerifyOptions(idempotency_key="case.verify.3"), deadline=deadline()))
        assert caught.value.reason == "authorization_unavailable"
        assert caught.value.sub_reason == "agent_identity_unreachable"
        assert gw.records[-1].outcome == "denied"

    def test_out_of_scope_tool_is_refused_before_the_run_grant(self, identity):
        inner = Allow()
        identity.provider.scopes["business_underwriter"] = frozenset(
            {scopes.provider_tool_scope("mock", "resolve_business")}
        )
        gw = gateway(identity, inner=inner)
        ref = gw.provider.ref_for("gb-dissolved-ashcombe")
        with pytest.raises(ToolRefusedError) as caught:
            asyncio.run(gw.verify_business(ref, VerifyOptions(idempotency_key="case.verify.9"), deadline=deadline()))
        assert caught.value.reason == "agent_scope_not_allowed" and inner.calls == []

    def test_a_provider_that_does_not_accept_a_presented_identity_never_gets_one(self, identity):
        class Plain(MockProvider):
            accepts_presented_identity = False

        gw = gateway(identity, provider=Plain())
        ref = gw.provider.ref_for("gb-dissolved-ashcombe")
        handle = asyncio.run(
            gw.verify_business(ref, VerifyOptions(idempotency_key="case.verify.4"), deadline=deadline())
        )
        assert handle.accepted_identity is None and gw.records[0].to_dict()["identity_presented"] is False

    def test_off_records_and_requests_are_unchanged(self):
        record = ToolCallRecord(1, "verify_business", "verify", "mock", "ok", "", "sha256:a", None, (), "t")
        release_one = {"sequence", "tool", "capability", "provider", "outcome", "reason", "input_sha256",
                       "output_sha256", "record_ids", "started_at", "grant_id"}  # fmt: skip
        assert set(record.to_dict()) == release_one
        opts = VerifyOptions(idempotency_key="case.verify.1")
        dumped = opts.model_dump(mode="json")
        assert "presented_identity" not in dumped
        assert canonical_sha256(opts) == canonical_sha256(dumped)
        presented = PresentedIdentity(identifier="did:x", attestation="a.b.c", attestation_sha256="sha256:" + "0" * 64,
                                      trust_tier="t")  # fmt: skip
        assert (
            VerifyOptions(idempotency_key="case.verify.1", presented_identity=presented).model_dump()[
                "presented_identity"
            ]["identifier"]
            == "did:x"
        )
        assert VerificationProvider.accepts_presented_identity is False and MockProvider.accepts_presented_identity
        assert ToolDecision(allowed=True).agent_identity is None

    def test_the_run_is_refused_before_the_case_moves(self, identity):
        from core.cases import runtime as case_runtime

        identity.provider.set_fixture("business_underwriter", "revoked")

        class Runtime:
            identity_guard = staticmethod(lambda: identity.guard)
            require_agent_identity = case_runtime.CaseRuntime.require_agent_identity

            async def require_enabled(self, tenant_id):
                return None

            def session_factory(self, tenant):
                raise AssertionError("the case must not be opened")

        with pytest.raises(CaseError) as caught:
            asyncio.run(case_runtime.investigate_case(TENANT, "case_" + "a" * 24, runtime=Runtime(), actor="t"))
        assert caught.value.reason == "authorization_unavailable" and "agent_identity_revoked" in str(caught.value)

    def test_authorizer_for_wraps_only_with_identity(self, identity, monkeypatch):
        from core.cases.runtime import CaseRuntime

        inner = Allow()
        runtime = CaseRuntime(authorizer_factory=lambda *a: inner, identity_guard=lambda: identity.guard)
        assert runtime.authorizer_for(str(TENANT), "c", "business_underwriter", "p") is inner
        allowed = asyncio.run(runtime.require_agent_identity(TENANT, "c", "business_underwriter"))
        wrapped = runtime.authorizer_for(str(TENANT), "c", "business_underwriter", "p", identity=allowed)
        assert isinstance(wrapped, guard.IdentityBoundAuthorizer) and wrapped.inner is inner
        off = CaseRuntime(authorizer_factory=lambda *a: inner, identity_guard=lambda: None)
        assert asyncio.run(off.require_agent_identity(TENANT, "c", "business_underwriter")) is None
        with pytest.raises(CaseError):
            off.authorizer_for(str(TENANT), "c", "business_underwriter", "p", identity=allowed)


# ── Routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import deps
    from api.v1 import onboarding_desk as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_tenant] = lambda: str(TENANT)
    app.dependency_overrides[deps.get_current_user] = lambda: {"sub": str(uuid.uuid4())}
    app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
    monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
    return TestClient(app)


class TestRoutes:
    def test_identity_routes_are_not_found_while_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "onboarding_desk_agent_identity_enabled", False)
        base = "/api/v1/onboarding-desk"
        assert client.get(f"{base}/agent-identity").status_code == 404
        assert client.post(f"{base}/agent-identity/onboarding_outreach/bind").status_code == 404

    def test_status_and_bind(self, client, identity, monkeypatch):
        import core.cases.grant_authorizer as authorizer

        base = "/api/v1/onboarding-desk"
        identity.provider.set_fixture(scopes.CONVERSATION_ROLE, "revoked")
        agents = {a["role"]: a for a in client.get(f"{base}/agent-identity").json()["agents"]}
        assert agents["onboarding_outreach"]["outcome"] == "allowed"
        assert agents["onboarding_conversation"] == {**agents["onboarding_conversation"], "outcome": "stopped",
                                                     "reason": "agent_identity_revoked"}  # fmt: skip
        assert set(agents) == set(scopes.ALL_ROLES)
        assert client.post(f"{base}/agent-identity/nobody/bind").status_code == 404
        monkeypatch.delenv("GRANTEX_BASE_URL", raising=False)
        monkeypatch.setattr(grantex, "grantex_identity_service", lambda: None)
        assert client.post(f"{base}/agent-identity/onboarding_outreach/bind").json()["detail"]["error"] == (
            "grantex_not_configured"
        )
        fake = FakeGrantex()
        monkeypatch.setattr(grantex, "grantex_identity_service", fake.service)

        async def active_agent(tenant_id, role):
            return ("agent-1", {"grantex_agent_id": "ag_1"}) if role == scopes.OUTREACH_ROLE else None

        monkeypatch.setattr(authorizer, "_active_agent", active_agent)
        monkeypatch.setattr(guard, "_configured_provider", lambda: identity.provider)
        bound = client.post(f"{base}/agent-identity/onboarding_outreach/bind").json()
        assert bound["status"] == "active" and bound["role"] == "onboarding_outreach"
        assert client.post(f"{base}/agent-identity/onboarding_conversation/bind").status_code == 409
