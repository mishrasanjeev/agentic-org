# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The check before each agent action: is the agent's identity active, and is its grant bound to it?

``AgentIdentityGuard.check(tenant, role, scope)`` answers an :class:`IdentityCheck`:

1. Off (``onboarding_desk_agent_identity_enabled`` false): ``enabled=False, allowed=True``, and
   nothing is called. Every caller treats that as "act exactly as before".
2. The identity provider resolves the agent's credential and reports its status. ``revoked``,
   ``suspended`` and ``expired`` (or a credential past its ``expires_at``) *stop* the action;
   ``unknown``, a credential the provider does not hold, an unconfigured provider or one that cannot
   be reached *hold* it, with the reason.
3. The agent's Grantex grant must be active and bound to the credential's key (``cnf.jkt`` equal to
   the key thumbprint); otherwise the action stops. A grant that cannot be checked holds it.
4. The action runs only when its scope is in both the attestation's allowed scopes and the grant's
   scopes: the narrower of the two sets.

A result is cached per provider, tenant and role for ``onboarding_desk_agent_identity_status_cache_seconds``
and never past the credential's own expiry. Failures are never cached.

:class:`IdentityBoundAuthorizer` applies the same check to each provider call a case agent makes
through the tool gateway, and attaches the agent's identity to the call record.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from connectors.framework.agent_identity_provider import (
    AgentIdentityCredential,
    AgentIdentityError,
    AgentIdentityNotFound,
    AgentIdentityProvider,
    AgentRef,
    IdentityStatus,
)
from connectors.framework.verification_provider import Deadline
from connectors.framework.verification_types import PresentedIdentity
from core.agent_identity import scopes as identity_scopes

logger = structlog.get_logger()

CALL_SECONDS = 5.0


@dataclass(frozen=True)
class IdentityStamp:
    """What every agent action record carries about the agent that took it."""

    identifier: str
    attestation_sha256: str
    trust_tier: str

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_identity_id": self.identifier,
            "attestation_sha256": self.attestation_sha256,
            "trust_tier": self.trust_tier,
        }


@dataclass(frozen=True)
class IdentityCheck:
    enabled: bool
    allowed: bool
    held: bool = False
    reason: str = ""
    stamp: IdentityStamp | None = None
    scopes: frozenset[str] = frozenset()
    grant_id: str = ""
    credential: AgentIdentityCredential | None = field(default=None, repr=False)

    @property
    def outcome(self) -> str:
        return "allowed" if self.allowed else ("held" if self.held else "stopped")

    def stamp_dict(self) -> dict[str, str] | None:
        return self.stamp.to_dict() if self.stamp is not None else None


OFF = IdentityCheck(enabled=False, allowed=True)


@dataclass(frozen=True)
class AgentGrantView:
    grant_id: str
    active: bool
    scopes: frozenset[str]
    key_thumbprint: str


class GrantUnavailableError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def enabled() -> bool:
    from core.config import settings

    return bool(getattr(settings, "onboarding_desk_agent_identity_enabled", False))


def _cache_seconds() -> int:
    from core.config import settings

    return max(0, min(3600, int(getattr(settings, "onboarding_desk_agent_identity_status_cache_seconds", 60) or 0)))


def _configured_provider() -> AgentIdentityProvider:
    from connectors.agent_identity.registry import AgentIdentityProviderRegistry, AgentIdentityRegistryError
    from core.config import settings

    name = str(getattr(settings, "onboarding_desk_agent_identity_provider", "") or "").strip()
    if not name:
        raise GrantUnavailableError("identity_provider_not_configured")
    try:
        return AgentIdentityProviderRegistry.create(name)
    except AgentIdentityRegistryError as exc:
        logger.error("agent_identity_provider_unavailable", reason=exc.reason)
        raise GrantUnavailableError("identity_provider_unavailable") from exc


class GrantexBoundGrantSource:
    """The agent's run grant, checked at Grantex: active, its scopes, and the key it is bound to."""

    def __init__(self, service_factory: Callable[[], Any] | None = None) -> None:
        self.service_factory = service_factory

    async def grant(self, tenant_id: str, role: str) -> AgentGrantView:
        from auth.grant_enforcement import EnforcementMode
        from auth.run_grants import resolve_run_grant
        from core.agent_identity.grantex import GrantexIdentityError, grantex_identity_service
        from core.cases.grant_authorizer import _active_agent

        service = (self.service_factory or grantex_identity_service)()
        if service is None:
            raise GrantUnavailableError("grantex_not_configured")
        try:
            agent = await _active_agent(tenant_id, role)
        except asyncio.CancelledError:
            raise
        # enterprise-gate: broad-except-ok reason=agent-lookup-failure-holds-the-action
        except Exception as exc:
            logger.error("agent_identity_agent_lookup_failed", role=role, error=type(exc).__name__)
            raise GrantUnavailableError("agent_lookup_failed") from exc
        if agent is None:
            raise GrantUnavailableError("desk_agent_not_configured")
        agent_id, config = agent
        run_grant = await resolve_run_grant(
            tenant_id=tenant_id,
            agent_id=agent_id,
            grantex_config=config,
            mode=EnforcementMode.DENY,
            runtime="agent_identity",
            isolation_key=f"agent_identity:{role}",
        )
        if not run_grant.token:
            raise GrantUnavailableError(run_grant.missing_sub_reason or "agent_grant_missing")
        try:
            verified = await service.verify_grant(run_grant.token)
        except GrantexIdentityError as exc:
            raise GrantUnavailableError(exc.reason) from exc
        claims = verified.get("claims") if isinstance(verified.get("claims"), dict) else {}
        raw_scopes = claims.get("scp") if isinstance(claims.get("scp"), list) else []
        cnf = claims.get("cnf") if isinstance(claims.get("cnf"), dict) else {}
        return AgentGrantView(
            grant_id=str(claims.get("grnt") or run_grant.grant_id or ""),
            active=verified.get("active") is True,
            scopes=frozenset(s for s in raw_scopes if isinstance(s, str)),
            key_thumbprint=str(cnf.get("jkt") or ""),
        )


@dataclass
class _Entry:
    credential: AgentIdentityCredential
    status: IdentityStatus
    grant: AgentGrantView | None
    until: datetime


_CACHE: dict[tuple[str, str, str], _Entry] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentIdentityGuard:
    provider_factory: Callable[[], AgentIdentityProvider] = _configured_provider
    grant_source: Any = field(default_factory=GrantexBoundGrantSource)
    clock: Callable[[], datetime] = _now
    cache_seconds: Callable[[], int] = _cache_seconds
    provider_name: Callable[[], str] = field(
        default=lambda: str(getattr(_settings(), "onboarding_desk_agent_identity_provider", "") or "")
    )

    def _held(self, reason: str) -> IdentityCheck:
        logger.warning("agent_identity_held", reason=reason)
        return IdentityCheck(enabled=True, allowed=False, held=True, reason=reason)

    async def _entry(self, tenant_id: str, role: str, moment: datetime) -> _Entry | IdentityCheck:
        key = (self.provider_name(), tenant_id, role)
        cached = _CACHE.get(key)
        if cached is not None and cached.until > moment:
            return cached
        try:
            provider = self.provider_factory()
        except GrantUnavailableError as exc:
            return self._held(exc.reason)
        deadline = Deadline.after(CALL_SECONDS)
        try:
            credential = await provider.resolve(AgentRef(tenant_id=tenant_id, role=role), deadline=deadline)
            status = await provider.check_status(credential.identifier, deadline=deadline)
        except AgentIdentityNotFound:
            return self._held("agent_identity_not_found")
        except AgentIdentityError:
            return self._held("agent_identity_unreachable")
        grant: AgentGrantView | None = None
        if status is IdentityStatus.ACTIVE and not _expired(credential, moment):
            try:
                grant = await self.grant_source.grant(tenant_id, role)
            except GrantUnavailableError as exc:
                return self._held(exc.reason)
        until = moment + timedelta(seconds=self.cache_seconds())
        if credential.expires_at is not None:
            until = min(until, credential.expires_at)
        entry = _Entry(credential=credential, status=status, grant=grant, until=until)
        if until > moment:
            _CACHE[key] = entry
        return entry

    async def check(self, tenant_id: Any, role: str, scope: str | None) -> IdentityCheck:
        """The answer for one action under ``scope`` (``None``: status and grant only)."""
        if not enabled():
            return OFF
        moment = self.clock()
        found = await self._entry(str(tenant_id), role, moment)
        if isinstance(found, IdentityCheck):
            return found
        credential = found.credential
        stamp = IdentityStamp(credential.identifier, credential.attestation_sha256, credential.trust_tier)

        def stop(reason: str, **extra: Any) -> IdentityCheck:
            logger.warning("agent_identity_stopped", role=role, reason=reason)
            return IdentityCheck(enabled=True, allowed=False, reason=reason, stamp=stamp, **extra)

        if found.status is IdentityStatus.EXPIRED or _expired(credential, moment):
            return stop("agent_identity_expired")
        if found.status in (IdentityStatus.REVOKED, IdentityStatus.SUSPENDED):
            return stop(f"agent_identity_{found.status.value}")
        if found.status is not IdentityStatus.ACTIVE:
            return dataclasses.replace(self._held("agent_identity_unknown"), stamp=stamp)
        grant = found.grant
        if grant is None or not grant.active:
            return stop("agent_grant_inactive")
        if not grant.key_thumbprint or grant.key_thumbprint != credential.key_thumbprint:
            return stop("agent_grant_not_bound", grant_id=grant.grant_id)
        narrowed = frozenset(credential.allowed_scopes & grant.scopes)
        if scope is not None and scope not in narrowed:
            return stop("agent_scope_not_allowed", grant_id=grant.grant_id, scopes=narrowed)
        return IdentityCheck(
            enabled=True,
            allowed=True,
            stamp=stamp,
            scopes=narrowed,
            grant_id=grant.grant_id,
            credential=credential,
        )

    async def present(self, check: IdentityCheck, *, audience: str) -> PresentedIdentity | None:
        """What the agent shows a verification provider that accepts it; None when it cannot be had."""
        if not check.allowed or check.credential is None:
            return None
        try:
            return await self.provider_factory().present(
                check.credential, audience=audience, deadline=Deadline.after(CALL_SECONDS)
            )
        except (AgentIdentityError, GrantUnavailableError) as exc:
            logger.warning("agent_identity_presentation_unavailable", error=type(exc).__name__)
            return None


def _expired(credential: AgentIdentityCredential, moment: datetime) -> bool:
    return credential.expires_at is not None and credential.expires_at <= moment


def _settings() -> Any:
    from core.config import settings

    return settings


_DEFAULT: AgentIdentityGuard | None = None


def default_guard() -> AgentIdentityGuard | None:
    """The process guard, or None while agent identity is off."""
    global _DEFAULT
    if not enabled():
        return None
    if _DEFAULT is None:
        _DEFAULT = AgentIdentityGuard()
    return _DEFAULT


async def check_action(tenant_id: Any, role: str, scope: str | None = None) -> IdentityCheck:
    """The check for one desk action; ``OFF`` when agent identity is off."""
    guard = default_guard()
    if guard is None:
        return OFF
    return await guard.check(
        tenant_id, role, scope if scope is not None else identity_scopes.DESK_ROLE_SCOPES.get(role)
    )


# --- case agents: the per-call check through the tool gateway ------------------------------------


@dataclass(frozen=True, slots=True)
class IdentityBoundAuthorizer:
    """Wraps a case run's grant check: each provider call also needs the agent's identity and scope."""

    inner: Any
    guard: AgentIdentityGuard
    tenant_id: str
    role: str

    async def authorize(self, *, connector: str, tool: str) -> Any:
        from core.tool_gateway.provider_gateway import AUTHORIZATION_UNAVAILABLE, ToolDecision

        check = await self.guard.check(self.tenant_id, self.role, identity_scopes.provider_tool_scope(connector, tool))
        if not check.allowed:
            return ToolDecision(
                allowed=False,
                reason=AUTHORIZATION_UNAVAILABLE if check.held else check.reason,
                sub_reason=check.reason,
                grant_id=check.grant_id,
                agent_identity=check.stamp_dict(),
            )
        decision = await self.inner.authorize(connector=connector, tool=tool)
        if not isinstance(decision, ToolDecision):
            return decision
        presented = await self.guard.present(check, audience=connector) if tool == "verify_business" else None
        return dataclasses.replace(decision, agent_identity=check.stamp_dict(), presented_identity=presented)
