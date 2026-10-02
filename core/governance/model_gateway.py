# SPDX-License-Identifier: Apache-2.0
"""Model gateway: policy-driven model selection in front of every provider.

A tenant administrator writes routing policies; with the gateway on, a model call
that carries a tenant asks the gateway which provider and model to use before a
credential is resolved or a provider is called. A policy matches a request on its
use case, data sensitivity, agent, business unit and language (a field the policy
leaves empty matches every request) and names what the match gets: a provider,
a model or a cost tier, the providers allowed, and whether the call must stay in
the tenant's data region. The first enabled policy in priority order that
matches decides; a request no policy matches keeps what the caller asked for.

The gateway is off until ``model_gateway.enabled`` is on for the tenant (an
authority flag, operator managed, read strictly) or
``AGENTICORG_MODEL_GATEWAY_ENABLED`` is set. Off, ``decide`` returns the
caller's own choice and reads nothing, so the existing paths are unchanged.

Decision rules:

* A request tagged ``restricted``, or matched by a policy with ``in_region_only``,
  may only use a provider inside the deployment or one attested for the
  tenant's data region (``core.governance.residency``, enforced whether or not
  residency enforcement is on); otherwise the call is refused, never re-routed
  to an outside provider.
* A policy's ``allowed_providers`` is a hard fence: a provider outside it is
  refused with the policy named, so a misconfigured policy fails loudly rather
  than quietly picking something else.
* Policies and the flag are read through a short shared cache and then the
  database. In a strict runtime a read failure refuses the call; a relaxed
  runtime passes the caller's choice through and logs.

After the routing policies have chosen the provider and model, the access
policies say whether this caller may use them: the first enabled access policy
in priority order that matches the call (on its use case, sensitivity, agent,
business unit, language, the calling application and principal, and the
provider and model chosen) decides, ``deny`` refusing it and ``allow`` letting
it through fenced to its ``allowed_providers`` and ``allowed_models``; a call no
access policy matches is allowed. The caller's application and principal come
from the identity the auth middleware binds for the request
(``core.governance.caller_identity``); work outside a request carries none.

A routing policy may split its matches across several provider and model
pairs by weight (``targets``); the choice is stable per correlation id.

Each model call is admitted under the tenant's per-model limits
(``core.governance.model_gateway_limits``) just before it is sent: every
reasoning turn of an agent run (the runner binds the run's decision with
:func:`bind_route` and the graph's reasoning node admits against it) and every
direct completion. A call above the concurrency or rate configured for its
provider or model is refused with ``E1015`` (retryable) and the refusal is
metered; the concurrency slot is released when the call returns.

Every decision is logged with its correlation id, the policy evaluated, the
provider and model chosen and the reason, and metered
(``agenticorg_model_gateway_decisions_total``); every policy change is written
as a signed audit row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings
from core.governance.caller_identity import current_identity
from core.governance.model_gateway_limits import Lease, Limit, validate_limit_fields
from core.governance.model_gateway_limits import admit as _admit_limits
from core.governance.model_gateway_limits import release as _release_lease

logger = structlog.get_logger()

FLAG_KEY = "model_gateway.enabled"
ERROR_CODE = "E1014"
LIMIT_ERROR_CODE = "E1015"
CACHE_TTL_SECONDS = 5
_CACHE_PREFIX = "model_gateway:policyset:"

# The use cases the platform's own call sites name; a policy may name any string.
USE_CASES: tuple[str, ...] = ("agent_run", "agent_resume", "completion")
SENSITIVITIES: tuple[str, ...] = ("public", "internal", "confidential", "restricted")
TIERS: tuple[str, ...] = ("tier1", "tier2", "tier3")
MATCH_FIELDS: tuple[str, ...] = ("use_case", "sensitivity", "agent_id", "business_unit", "language")
ROUTE_FIELDS: tuple[str, ...] = ("provider", "model", "tier", "targets", "allowed_providers", "in_region_only")
EFFECTS: tuple[str, ...] = ("allow", "deny")
# An access policy also matches on who is calling and on the route chosen.
ACCESS_MATCH_FIELDS: tuple[str, ...] = (*MATCH_FIELDS, "application", "principal", "provider", "model")


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


# The router names provider families by model prefix; the catalogue names
# providers. ``openai_compatible`` and ``azure_openai`` keep their own ids: the
# factory dispatches them to their own endpoints.
_PROVIDER_ALIASES = {"claude": "anthropic", "gpt": "openai"}


def normalise_provider(name: object) -> str | None:
    """A provider id as the catalogue spells it (``claude`` and ``anthropic`` are one provider)."""
    key = (_clean(name) or "").lower()
    return _PROVIDER_ALIASES.get(key, key) or None


def provider_for_model(model: str, provider: str | None = None) -> str | None:
    """The provider a model dispatches to: the explicit one, else the catalogue's, else the router's inference."""
    if provider:
        return normalise_provider(provider)
    name = (model or "").strip()
    if not name:
        return None
    if name.startswith("ollama:"):
        return "ollama"
    if name.startswith("vllm:"):
        return "vllm"
    from core.ai_providers.catalog import LLM_CATALOG

    for entry in LLM_CATALOG:
        if entry.model == name:
            return entry.provider
    from core.llm.router import provider_of_model

    return normalise_provider(provider_of_model(name))


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    priority: int
    enabled: bool = True
    use_case: str | None = None
    sensitivity: str | None = None
    agent_id: str | None = None
    business_unit: str | None = None
    language: str | None = None
    provider: str | None = None
    model: str | None = None
    tier: str | None = None
    targets: tuple[dict[str, Any], ...] | None = None
    allowed_providers: tuple[str, ...] | None = None
    in_region_only: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_providers"] = list(self.allowed_providers) if self.allowed_providers is not None else None
        data["targets"] = [dict(t) for t in self.targets] if self.targets is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        allowed = data.get("allowed_providers")
        targets = data.get("targets")
        return cls(
            **{
                **data,
                "allowed_providers": tuple(allowed) if allowed is not None else None,
                "targets": tuple(dict(t) for t in targets) if targets is not None else None,
            }
        )

    def matches(self, request: RouteRequest) -> bool:
        """Every match field the policy sets must equal the request's (case-insensitive)."""
        return _fields_match(self, request, MATCH_FIELDS)


def _fields_match(policy: Any, subject: Any, names: tuple[str, ...]) -> bool:
    for name in names:
        wanted = getattr(policy, name)
        if wanted is None:
            continue
        actual = getattr(subject, name, None)
        if actual is None or str(actual).strip().lower() != str(wanted).strip().lower():
            return False
    return True


@dataclass(frozen=True)
class AccessPolicy:
    """Who may use which provider or model: first match in priority order decides."""

    id: str
    name: str
    priority: int
    enabled: bool = True
    use_case: str | None = None
    sensitivity: str | None = None
    agent_id: str | None = None
    business_unit: str | None = None
    language: str | None = None
    application: str | None = None
    principal: str | None = None
    provider: str | None = None
    model: str | None = None
    effect: str = "allow"
    allowed_providers: tuple[str, ...] | None = None
    allowed_models: tuple[str, ...] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for name in ("allowed_providers", "allowed_models"):
            value = getattr(self, name)
            data[name] = list(value) if value is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessPolicy:
        out = dict(data)
        for name in ("allowed_providers", "allowed_models"):
            value = data.get(name)
            out[name] = tuple(value) if value is not None else None
        return cls(**out)

    def matches(self, request: RouteRequest, provider: str | None, model: str) -> bool:
        subject = _AccessSubject(request, provider, model)
        return _fields_match(self, subject, ACCESS_MATCH_FIELDS)


class _AccessSubject:
    """A routed call as an access policy sees it: the request plus the route chosen."""

    def __init__(self, request: RouteRequest, provider: str | None, model: str) -> None:
        self._request = request
        self.provider = provider
        self.model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)


@dataclass(frozen=True)
class PolicySet:
    """Everything the gateway reads for one tenant: routing policies, access policies and limits."""

    routing: tuple[Policy, ...] = ()
    access: tuple[AccessPolicy, ...] = ()
    limits: tuple[Limit, ...] = ()

    def to_json(self) -> str:
        return json.dumps(
            {
                "routing": [p.to_dict() for p in self.routing],
                "access": [p.to_dict() for p in self.access],
                "limits": [limit.to_dict() for limit in self.limits],
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> PolicySet:
        data = json.loads(raw)
        return cls(
            routing=tuple(Policy.from_dict(item) for item in data.get("routing", [])),
            access=tuple(AccessPolicy.from_dict(item) for item in data.get("access", [])),
            limits=tuple(Limit.from_dict(item) for item in data.get("limits", [])),
        )


@dataclass(frozen=True)
class RouteRequest:
    tenant_id: uuid.UUID | str | None
    use_case: str
    requested_provider: str | None = None
    requested_model: str = ""
    sensitivity: str | None = None
    agent_id: str | None = None
    business_unit: str | None = None
    language: str | None = None
    correlation_id: str = ""
    # Who is calling, from the request's bound identity when the caller does not say.
    application: str | None = None
    principal: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    provider: str | None
    model: str
    correlation_id: str
    reason: str
    applied: bool = False
    policy_id: str | None = None
    policy_name: str | None = None
    restricted: bool = False
    # The access policy that let the call through, when one matched.
    access_policy_id: str | None = None
    access_policy_name: str | None = None
    # ``gated`` says the gateway was on for this call, so its limits apply at admission.
    gated: bool = False
    tenant_id: str | None = None
    use_case: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RouteContext:
    """The routing decision bound for the current run, read at each model call."""

    decision: RouteDecision
    use_case: str = ""
    agent_id: str | None = None


_ROUTE: ContextVar[RouteContext | None] = ContextVar("agenticorg_model_route", default=None)


def bind_route(
    decision: RouteDecision, *, use_case: str = "", agent_id: str | None = None
) -> Token[RouteContext | None]:
    """Bind the run's routing decision for the span of the run; reset with :func:`reset_route`."""
    return _ROUTE.set(RouteContext(decision=decision, use_case=use_case, agent_id=agent_id))


def reset_route(token: Token[RouteContext | None]) -> None:
    _ROUTE.reset(token)


def current_route() -> RouteContext | None:
    """The routing decision bound for the current run, or None outside a routed run."""
    return _ROUTE.get()


class ModelGatewayRefused(RuntimeError):  # noqa: N818 - surface name used in error payloads
    """Raised when the gateway refuses a model call: a fence, a restriction or an unreadable policy set."""

    def __init__(
        self,
        reason: str,
        *,
        correlation_id: str,
        policy_id: str | None = None,
        policy_name: str | None = None,
        kind: str = "routing",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.correlation_id = correlation_id
        self.policy_id = policy_id
        self.policy_name = policy_name
        # ``routing`` (a fence, a restriction or an unreadable policy set),
        # ``access`` (an access policy) or ``limit`` (a per-model limit, retryable).
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds

    @property
    def code(self) -> str:
        return LIMIT_ERROR_CODE if self.kind == "limit" else ERROR_CODE

    def to_error(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "correlation_id": self.correlation_id,
            "policy_id": self.policy_id,
            "policy_name": self.policy_name,
            "kind": self.kind,
        }
        if self.retry_after_seconds is not None:
            detail["retry_after_seconds"] = self.retry_after_seconds
        return {"error": {"code": self.code, "message": self.reason}, "model_gateway": detail}


def refused_run_result(exc: ModelGatewayRefused) -> dict[str, Any]:
    """Runner result for a run the gateway refused (same shape as a failed run)."""
    return {
        "status": "model_gateway_refused",
        "output": {},
        "confidence": 0.0,
        "reasoning_trace": [],
        "tool_calls_log": [],
        "tool_calls": [],
        "hitl_trigger": "",
        "error": exc.reason,
        "error_code": exc.code,
        "model_gateway": exc.to_error()["model_gateway"],
        "performance": {"total_latency_ms": 0, "llm_tokens_used": 0, "llm_cost_usd": 0.0},
    }


# ---------------------------------------------------------------------------
# Flag and policy reads
# ---------------------------------------------------------------------------


async def enabled(tenant_id: uuid.UUID | str | None) -> bool:
    """Whether the gateway is on for ``tenant_id`` (settings switch or authority flag).

    The flag is read strictly: a lookup failure raises
    ``core.feature_flags.FeatureFlagLookupError`` rather than reading as off, so
    a store outage cannot silently turn routing policy off (``decide`` fails
    closed on it in a strict runtime).
    """
    if settings.model_gateway_enabled:
        return True
    tid = _as_uuid(tenant_id)
    if tid is None:
        return False
    from core.feature_flags import load_flag_rows_strict, row_enabled

    rows = await load_flag_rows_strict(FLAG_KEY, tenant_id=tid)
    subject = str(tid)
    return row_enabled(FLAG_KEY, rows.global_row, subject_id=subject) or row_enabled(
        FLAG_KEY, rows.tenant_row, subject_id=subject
    )


def _cache_key(tenant_id: uuid.UUID) -> str:
    return f"{_CACHE_PREFIX}{tenant_id}"


def _policy(row: Any) -> Policy:
    allowed = row.allowed_providers
    targets = getattr(row, "targets", None)
    return Policy(
        id=str(row.id),
        name=row.name,
        priority=int(row.priority),
        enabled=bool(row.enabled),
        use_case=row.use_case,
        sensitivity=row.sensitivity,
        agent_id=row.agent_id,
        business_unit=row.business_unit,
        language=row.language,
        provider=row.provider,
        model=row.model,
        tier=row.tier,
        allowed_providers=tuple(str(p) for p in allowed) if allowed is not None else None,
        targets=tuple(dict(t) for t in targets) if targets is not None else None,
        in_region_only=bool(row.in_region_only),
        reason=row.reason or "",
    )


def _access_policy(row: Any) -> AccessPolicy:
    def _tuple(value: Any) -> tuple[str, ...] | None:
        return tuple(str(v) for v in value) if value is not None else None

    return AccessPolicy(
        id=str(row.id),
        name=row.name,
        priority=int(row.priority),
        enabled=bool(row.enabled),
        use_case=row.use_case,
        sensitivity=row.sensitivity,
        agent_id=row.agent_id,
        business_unit=row.business_unit,
        language=row.language,
        application=row.application,
        principal=row.principal,
        provider=row.provider,
        model=row.model,
        effect=row.effect or "allow",
        allowed_providers=_tuple(row.allowed_providers),
        allowed_models=_tuple(row.allowed_models),
        reason=row.reason or "",
    )


def _limit(row: Any) -> Limit:
    return Limit(
        id=str(row.id),
        provider=row.provider,
        model=row.model,
        enabled=bool(row.enabled),
        max_concurrency=row.max_concurrency,
        requests_per_minute=row.requests_per_minute,
        reason=row.reason or "",
    )


async def _load_policies(tenant_id: uuid.UUID) -> list[Policy]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_routing_policy import ModelRoutingPolicy

    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(ModelRoutingPolicy)
                .where(ModelRoutingPolicy.tenant_id == tenant_id, ModelRoutingPolicy.enabled.is_(True))
                .order_by(ModelRoutingPolicy.priority, ModelRoutingPolicy.name)
            )
        ).scalars()
        return [_policy(row) for row in rows]


async def _load_access_policies(tenant_id: uuid.UUID) -> list[AccessPolicy]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_access_policy import ModelAccessPolicy

    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(ModelAccessPolicy)
                .where(ModelAccessPolicy.tenant_id == tenant_id, ModelAccessPolicy.enabled.is_(True))
                .order_by(ModelAccessPolicy.priority, ModelAccessPolicy.name)
            )
        ).scalars()
        return [_access_policy(row) for row in rows]


async def _load_limits(tenant_id: uuid.UUID) -> list[Limit]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_limit import ModelLimit

    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(ModelLimit)
                .where(ModelLimit.tenant_id == tenant_id, ModelLimit.enabled.is_(True))
                .order_by(ModelLimit.provider, ModelLimit.model)
            )
        ).scalars()
        return [_limit(row) for row in rows]


async def _load_policy_set(tenant_id: uuid.UUID) -> PolicySet:
    return PolicySet(
        routing=tuple(await _load_policies(tenant_id)),
        access=tuple(await _load_access_policies(tenant_id)),
        limits=tuple(await _load_limits(tenant_id)),
    )


async def active_policy_set(tenant_id: uuid.UUID) -> PolicySet:
    """The enabled routing policies, access policies and limits: the shared Redis cache first, then the database."""
    from core.async_redis import get_async_redis

    redis = None
    try:
        redis = await get_async_redis()
        if redis is not None:
            cached = await redis.get(_cache_key(tenant_id))
            if cached:
                return PolicySet.from_json(cached)
    # enterprise-gate: broad-except-ok reason=policy-cache-miss-falls-through-to-the-database
    except Exception as exc:
        logger.warning("model_gateway_cache_read_failed", error_type=type(exc).__name__)
        redis = None

    policy_set = await _load_policy_set(tenant_id)
    if redis is not None:
        try:
            await redis.set(_cache_key(tenant_id), policy_set.to_json(), ex=CACHE_TTL_SECONDS)
        # enterprise-gate: broad-except-ok reason=policy-cache-write-is-best-effort-ttl-bounds-staleness
        except Exception as exc:
            logger.warning("model_gateway_cache_write_failed", error_type=type(exc).__name__)
    return policy_set


async def active_policies(tenant_id: uuid.UUID) -> list[Policy]:
    """Enabled routing policies in priority order (see :func:`active_policy_set`)."""
    return list((await active_policy_set(tenant_id)).routing)


async def active_access_policies(tenant_id: uuid.UUID) -> list[AccessPolicy]:
    """Enabled access policies in priority order (see :func:`active_policy_set`)."""
    return list((await active_policy_set(tenant_id)).access)


async def active_limits(tenant_id: uuid.UUID) -> list[Limit]:
    """Enabled per-model limits (see :func:`active_policy_set`)."""
    return list((await active_policy_set(tenant_id)).limits)


async def invalidate(tenant_id: uuid.UUID) -> None:
    """Drop the cached policy list so the next decision reads the database."""
    from core.async_redis import get_async_redis

    try:
        redis = await get_async_redis()
        if redis is not None:
            await redis.delete(_cache_key(tenant_id))
    # enterprise-gate: broad-except-ok reason=cache-invalidation-is-best-effort-ttl-bounds-staleness
    except Exception as exc:
        logger.warning("model_gateway_cache_invalidate_failed", error_type=type(exc).__name__)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _meter(outcome: str) -> None:
    try:
        from observability.metrics import model_gateway_decisions_total

        model_gateway_decisions_total.labels(outcome=outcome).inc()
    # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-an-unmetered-decision-never-changes-it
    except Exception:
        logger.debug("model_gateway_metric_skipped", outcome=outcome)


def _passthrough(request: RouteRequest, correlation_id: str, reason: str) -> RouteDecision:
    tid = _as_uuid(request.tenant_id)
    return RouteDecision(
        provider=provider_for_model(request.requested_model, request.requested_provider),
        model=request.requested_model,
        correlation_id=correlation_id,
        reason=reason,
        tenant_id=str(tid) if tid is not None else None,
        use_case=request.use_case,
    )


def _with_identity(request: RouteRequest) -> RouteRequest:
    """Fill the caller's application and principal from the bound identity when the request does not name them."""
    if request.application is not None and request.principal is not None:
        return request
    identity = current_identity()
    if identity is None:
        return request
    return RouteRequest(
        **{
            **asdict(request),
            "application": request.application if request.application is not None else identity.application,
            "principal": request.principal if request.principal is not None else identity.principal,
        }
    )


def tier_model(tier: str) -> str:
    """The concrete model behind a cost tier in the deployment's current mode."""
    from core.llm.router import tier_model as _tier_model

    return _tier_model(tier)


def pick_target(targets: tuple[dict[str, Any], ...], correlation_id: str) -> dict[str, Any]:
    """The weighted target a correlation id lands on: stable for the id, proportional over many."""
    total = sum(int(t.get("weight", 1)) for t in targets)
    point = int.from_bytes(hashlib.sha256(correlation_id.encode("utf-8")).digest()[:8], "big") % max(total, 1)
    for target in targets:
        point -= int(target.get("weight", 1))
        if point < 0:
            return target
    return targets[-1]


def _apply(policy: Policy, provider: str | None, model: str, correlation_id: str = "") -> tuple[str | None, str]:
    """The provider and model a matched policy gives a request that asked for ``provider`` and ``model``."""
    if policy.targets:
        target = pick_target(policy.targets, correlation_id)
        return normalise_provider(target.get("provider")), str(target.get("model") or model)
    if policy.provider:
        provider = policy.provider
        if policy.model:
            model = policy.model
        else:
            from core.ai_providers.catalog import find_llm, llm_models_for

            if find_llm(provider, model) is None:
                # The caller's model is another provider's: the first catalogue
                # model of the policy's provider stands in.
                candidates = [m for m in llm_models_for(provider) if m != "*" and not m.startswith("deployment:")]
                model = candidates[0] if candidates else model
    elif policy.model:
        model = policy.model
        provider = provider_for_model(model)
    elif policy.tier:
        model = tier_model(policy.tier)
        provider = provider_for_model(model)
    return provider, model


def _refuse(
    reason: str,
    *,
    correlation_id: str,
    policy: Policy | AccessPolicy | None,
    request: RouteRequest,
    dry_run: bool = False,
    kind: str = "routing",
    retry_after_seconds: float | None = None,
) -> ModelGatewayRefused:
    if not dry_run:
        _meter("refused")
        logger.warning(
            "model_gateway_refused",
            correlation_id=correlation_id,
            use_case=request.use_case,
            policy_id=policy.id if policy else None,
            kind=kind,
            reason=reason,
        )
    return ModelGatewayRefused(
        reason,
        correlation_id=correlation_id,
        policy_id=policy.id if policy else None,
        policy_name=policy.name if policy else None,
        kind=kind,
        retry_after_seconds=retry_after_seconds,
    )


def _check_access(
    request: RouteRequest,
    provider: str | None,
    model: str,
    access: tuple[AccessPolicy, ...] | list[AccessPolicy],
    *,
    correlation_id: str,
    dry_run: bool,
) -> AccessPolicy | None:
    """The access policy that lets the routed call through (None when none matches); a refusal raises."""
    ordered = sorted(access, key=lambda p: (p.priority, p.name))
    policy = next((p for p in ordered if p.matches(request, provider, model)), None)
    if policy is None:
        return None
    who = request.application or request.principal or "this caller"
    if policy.effect == "deny":
        raise _refuse(
            f"Model gateway: access policy {policy.name} denies {who} the use of "
            f"{provider or 'the provider'} {model}.".replace("  ", " "),
            correlation_id=correlation_id,
            policy=policy,
            request=request,
            dry_run=dry_run,
            kind="access",
        )
    if policy.allowed_providers is not None and (provider or "") not in policy.allowed_providers:
        raise _refuse(
            f"Model gateway: provider {provider or 'unknown'} is outside the providers access policy "
            f"{policy.name} allows {who}.",
            correlation_id=correlation_id,
            policy=policy,
            request=request,
            dry_run=dry_run,
            kind="access",
        )
    if policy.allowed_models is not None and model not in policy.allowed_models:
        raise _refuse(
            f"Model gateway: model {model} is outside the models access policy {policy.name} allows {who}.",
            correlation_id=correlation_id,
            policy=policy,
            request=request,
            dry_run=dry_run,
            kind="access",
        )
    return policy


async def _decide(
    request: RouteRequest, policy_set: PolicySet, correlation_id: str, *, dry_run: bool = False
) -> RouteDecision:
    tid = _as_uuid(request.tenant_id)
    policies = policy_set.routing
    policy = next((p for p in policies if p.matches(request)), None)
    restricted = (request.sensitivity or "").strip().lower() == "restricted" or bool(policy and policy.in_region_only)
    # A caller that pinned no provider (legacy agent rows) still names one through its model.
    provider = provider_for_model(request.requested_model, request.requested_provider)
    model = request.requested_model
    if policy is None:
        reason = "no policy matched; the caller's choice stands"
    else:
        provider, model = _apply(policy, provider, model, correlation_id)
        reason = f"policy {policy.name}"
        if policy.allowed_providers is not None and (provider or "") not in policy.allowed_providers:
            raise _refuse(
                f"Model gateway: provider {provider or 'unknown'} is outside the providers policy "
                f"{policy.name} allows.",
                correlation_id=correlation_id,
                policy=policy,
                request=request,
                dry_run=dry_run,
            )
    access_policy = _check_access(
        request, provider, model, policy_set.access, correlation_id=correlation_id, dry_run=dry_run
    )
    if restricted:
        from core.governance.residency import check_provider

        target = provider or provider_for_model(model) or ""
        if not target:
            # Nothing to check against: a restricted request whose provider
            # cannot be named is refused, never waved through.
            raise _refuse(
                "Model gateway: restricted data needs a provider the gateway can name; "
                "the request named neither a provider nor a model.",
                correlation_id=correlation_id,
                policy=policy,
                request=request,
                dry_run=dry_run,
            )
        residency = await check_provider(tid, target, kind="llm", enforce=True)
        if residency.blocked:
            raise _refuse(
                f"Model gateway: restricted data may not reach provider {target}. {residency.reason}",
                correlation_id=correlation_id,
                policy=policy,
                request=request,
                dry_run=dry_run,
            )
    decision = RouteDecision(
        provider=provider,
        model=model,
        correlation_id=correlation_id,
        reason=reason,
        applied=policy is not None,
        policy_id=policy.id if policy else None,
        policy_name=policy.name if policy else None,
        restricted=restricted,
        access_policy_id=access_policy.id if access_policy else None,
        access_policy_name=access_policy.name if access_policy else None,
        gated=True,
        tenant_id=str(tid) if tid is not None else None,
        use_case=request.use_case,
    )
    if dry_run:
        return decision
    _meter("applied" if policy is not None else "passthrough")
    logger.info(
        "model_gateway_decision",
        correlation_id=correlation_id,
        use_case=request.use_case,
        policy_id=decision.policy_id,
        access_policy_id=decision.access_policy_id,
        provider=provider,
        model=model,
        restricted=restricted,
        application=request.application,
        principal=request.principal,
        reason=reason,
    )
    return decision


@dataclass(frozen=True)
class Evaluation:
    """A dry run: what the policies would decide, and whether the gateway is on for the tenant."""

    enabled: bool
    decision: RouteDecision | None = None
    refusal: ModelGatewayRefused | None = None


async def evaluate(request: RouteRequest) -> Evaluation:
    """Evaluate the policies for a request whether or not the gateway is on; nothing is metered or logged.

    An administrator uses this before enabling the gateway to see what the
    policies would do. A read failure propagates: a dry run has nothing to
    fall back to.
    """
    tid = _as_uuid(request.tenant_id)
    if tid is None:
        raise ValueError("a dry run needs a tenant")
    correlation_id = request.correlation_id or uuid.uuid4().hex
    on = await enabled(tid)
    policy_set = await active_policy_set(tid)
    try:
        decision = await _decide(request, policy_set, correlation_id, dry_run=True)
    except ModelGatewayRefused as exc:
        return Evaluation(enabled=on, refusal=exc)
    return Evaluation(enabled=on, decision=decision)


async def _policies_or_passthrough(
    request: RouteRequest, correlation_id: str
) -> tuple[PolicySet | None, RouteDecision | None]:
    """The enabled policy set, or the pass-through decision when the gateway is off or unreadable."""
    tid = _as_uuid(request.tenant_id)
    if tid is None:
        return None, _passthrough(request, correlation_id, "no tenant; the caller's choice stands")
    try:
        on = await enabled(tid)
        policies = await active_policy_set(tid) if on else PolicySet()
    # enterprise-gate: broad-except-ok reason=policy-read-failure-fails-closed-in-strict-runtime
    except Exception as exc:
        logger.error("model_gateway_read_failed", error_type=type(exc).__name__, correlation_id=correlation_id)
        if is_strict_runtime_env(settings.env):
            raise _refuse(
                "Model gateway: routing policies could not be read; refusing the call.",
                correlation_id=correlation_id,
                policy=None,
                request=request,
            ) from exc
        return None, _passthrough(request, correlation_id, "policies unreadable; the caller's choice stands")
    if not on:
        return None, _passthrough(request, correlation_id, "gateway off")
    return policies, None


async def decide(request: RouteRequest) -> RouteDecision:
    """The provider and model a request gets, with the policy that decided it.

    Raises ``ModelGatewayRefused`` when a policy fence or a restriction refuses
    the call, or when the policies cannot be read in a strict runtime.
    """
    correlation_id = request.correlation_id or uuid.uuid4().hex
    request = _with_identity(request)
    policies, passthrough = await _policies_or_passthrough(request, correlation_id)
    if passthrough is not None:
        return passthrough
    return await _decide(request, policies or PolicySet(), correlation_id)


async def admit(decision: RouteDecision) -> Lease | None:
    """Admit one model call under the tenant's per-model limits, just before it is sent.

    Returns the lease to hand to :func:`release` when the call returns (None
    when the gateway was off for the call, which reads nothing). Raises
    ``ModelGatewayRefused`` with kind ``limit`` and ``E1015`` when a limit
    refuses the call; nothing is held after a refusal.
    """
    if not decision.gated or not decision.tenant_id:
        return None
    tid = _as_uuid(decision.tenant_id)
    if tid is None:
        return None
    try:
        limits = await active_limits(tid)
    # enterprise-gate: broad-except-ok reason=limit-read-failure-degrades-to-an-admitted-metered-call
    except Exception as exc:
        logger.warning(
            "model_gateway_limits_read_failed", error_type=type(exc).__name__, correlation_id=decision.correlation_id
        )
        return Lease(lease_id=decision.correlation_id, outcome="unavailable")
    admission = await _admit_limits(
        str(tid), decision.provider, decision.model, limits, correlation_id=decision.correlation_id
    )
    if admission.rejected is not None:
        rejected = admission.rejected
        scope = f"{rejected.limit.provider}" + (f" {rejected.limit.model}" if rejected.limit.model else "")
        what = (
            f"{rejected.limit.max_concurrency} calls in flight"
            if rejected.kind == "concurrency"
            else f"{rejected.limit.requests_per_minute} calls per minute"
        )
        request = RouteRequest(tenant_id=decision.tenant_id, use_case=decision.use_case)
        raise _refuse(
            f"Model gateway: {scope} is at its limit of {what}; retry after {rejected.retry_after_seconds:.1f} s.",
            correlation_id=decision.correlation_id,
            policy=None,
            request=request,
            kind="limit",
            retry_after_seconds=rejected.retry_after_seconds,
        )
    return admission.lease


async def release(lease: Lease | None) -> None:
    """Give back the concurrency slot a lease holds; safe to call with None or a lease that holds none."""
    await _release_lease(lease)


async def agent_sensitivity(tenant_id: uuid.UUID, agent_id: uuid.UUID | str) -> str | None:
    """The data sensitivity recorded on the agent (``llm_config.sensitivity``), or None."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    aid = _as_uuid(agent_id)
    if aid is None:
        return None
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT llm_config FROM agents WHERE id = :aid AND tenant_id = :tid"),
                {"aid": str(aid), "tid": str(tenant_id)},
            )
        ).fetchone()
    if not row or not row[0]:
        return None
    config = row[0]
    if isinstance(config, str):
        config = json.loads(config)
    value = config.get("sensitivity") if isinstance(config, dict) else None
    return _clean(value).lower() if _clean(value) else None


async def route_for_agent(
    tenant_id: uuid.UUID | str | None,
    *,
    use_case: str,
    agent_id: str,
    business_unit: str | None,
    requested_provider: str | None,
    requested_model: str,
) -> RouteDecision:
    """The runner's entry point: the agent's recorded sensitivity is read only when the gateway is on."""
    correlation_id = uuid.uuid4().hex
    request = _with_identity(
        RouteRequest(
            tenant_id=tenant_id,
            use_case=use_case,
            requested_provider=requested_provider,
            requested_model=requested_model,
            agent_id=str(agent_id or "") or None,
            business_unit=business_unit,
            correlation_id=correlation_id,
        )
    )
    policies, passthrough = await _policies_or_passthrough(request, correlation_id)
    if passthrough is not None:
        return passthrough
    tid = _as_uuid(tenant_id)
    try:
        sensitivity = await agent_sensitivity(tid, agent_id) if tid is not None else None
    # enterprise-gate: broad-except-ok reason=sensitivity-read-failure-fails-closed-in-strict-runtime
    except Exception as exc:
        logger.error("model_gateway_sensitivity_read_failed", error_type=type(exc).__name__, agent_id=agent_id)
        if is_strict_runtime_env(settings.env):
            raise _refuse(
                "Model gateway: the agent's data sensitivity could not be read; refusing the call.",
                correlation_id=correlation_id,
                policy=None,
                request=request,
            ) from exc
        sensitivity = None
    request = RouteRequest(**{**asdict(request), "sensitivity": sensitivity})
    return await _decide(request, policies or PolicySet(), correlation_id)


# ---------------------------------------------------------------------------
# Policy changes (audited)
# ---------------------------------------------------------------------------


def validate_policy_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalise and check a policy's fields; raise ``ValueError`` on an unusable policy."""
    out: dict[str, Any] = {}
    for name in MATCH_FIELDS:
        out[name] = _clean(fields.get(name))
        if out[name] is not None:
            out[name] = out[name].lower() if name != "agent_id" else out[name]
    if out["sensitivity"] is not None and out["sensitivity"] not in SENSITIVITIES:
        raise ValueError(f"sensitivity must be one of {', '.join(SENSITIVITIES)}")
    out["provider"] = normalise_provider(fields.get("provider"))
    out["model"] = _clean(fields.get("model"))
    out["tier"] = (_clean(fields.get("tier")) or "").lower() or None
    if out["tier"] is not None and out["tier"] not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}")
    allowed = fields.get("allowed_providers")
    if allowed is not None:
        cleaned = sorted({p for p in (normalise_provider(item) for item in allowed) if p})
        if not cleaned:
            raise ValueError("allowed_providers must name at least one provider when given")
        out["allowed_providers"] = cleaned
    else:
        out["allowed_providers"] = None
    out["in_region_only"] = bool(fields.get("in_region_only", False))
    out["targets"] = _clean_targets(fields.get("targets"))
    if out["targets"] is not None and (out["provider"] or out["model"] or out["tier"]):
        raise ValueError("a policy with targets names no single provider, model or tier")
    if out["provider"] and out["model"]:
        from core.ai_providers.catalog import validate_llm_selection

        out["provider"], out["model"] = validate_llm_selection(out["provider"], out["model"])
    elif out["model"] and provider_for_model(out["model"]) is None:
        raise ValueError(f"model {out['model']} is not in the provider catalogue")
    if out["provider"] and out["allowed_providers"] is not None and out["provider"] not in out["allowed_providers"]:
        raise ValueError("the policy's provider must be among its allowed_providers")
    if out["targets"] is not None and out["allowed_providers"] is not None:
        outside = sorted({t["provider"] for t in out["targets"]} - set(out["allowed_providers"]))
        if outside:
            raise ValueError(f"every target provider must be among allowed_providers; outside: {', '.join(outside)}")
    if not any(out[name] not in (None, False) for name in ROUTE_FIELDS):
        raise ValueError(
            "a policy must route (provider, model, tier or targets), fence (allowed_providers) or restrict"
        )
    priority = fields.get("priority", 100)
    if not isinstance(priority, int) or priority < 0:
        raise ValueError("priority must be a non-negative integer")
    out["priority"] = priority
    out["enabled"] = bool(fields.get("enabled", True))
    out["reason"] = (fields.get("reason") or "").strip()
    name = _clean(fields.get("name"))
    if not name:
        raise ValueError("a policy needs a name")
    out["name"] = name
    return out


def _clean_targets(raw: Any) -> list[dict[str, Any]] | None:
    """Weighted provider and model targets, each checked against the catalogue."""
    if raw is None:
        return None
    if not isinstance(raw, list | tuple) or not raw:
        raise ValueError("targets must be a non-empty list of {provider, model, weight}")
    from core.ai_providers.catalog import validate_llm_selection

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each target is an object with provider, model and weight")
        provider, model = validate_llm_selection(
            normalise_provider(item.get("provider")) or "", str(item.get("model") or "")
        )
        weight = item.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
            raise ValueError("a target weight is a positive integer")
        out.append({"provider": provider, "model": model, "weight": weight})
    return out


def validate_access_policy_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalise and check an access policy's fields; raise ``ValueError`` on an unusable policy."""
    out: dict[str, Any] = {}
    for name in ACCESS_MATCH_FIELDS:
        value = _clean(fields.get(name))
        if value is not None and name not in ("agent_id", "principal", "model"):
            value = value.lower()
        out[name] = value
    if out["sensitivity"] is not None and out["sensitivity"] not in SENSITIVITIES:
        raise ValueError(f"sensitivity must be one of {', '.join(SENSITIVITIES)}")
    out["provider"] = normalise_provider(out["provider"])
    if out["provider"] and out["model"]:
        from core.ai_providers.catalog import validate_llm_selection

        out["provider"], out["model"] = validate_llm_selection(out["provider"], out["model"])
    effect = (_clean(fields.get("effect")) or "allow").lower()
    if effect not in EFFECTS:
        raise ValueError(f"effect must be one of {', '.join(EFFECTS)}")
    out["effect"] = effect
    for name in ("allowed_providers", "allowed_models"):
        raw = fields.get(name)
        if raw is None:
            out[name] = None
            continue
        if effect == "deny":
            raise ValueError(f"{name} belongs on an allow policy")
        items = [normalise_provider(v) for v in raw] if name == "allowed_providers" else [_clean(v) for v in raw]
        cleaned = sorted({v for v in items if v})
        if not cleaned:
            raise ValueError(f"{name} must name at least one entry when given")
        out[name] = cleaned
    priority = fields.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise ValueError("priority must be a non-negative integer")
    out["priority"] = priority
    out["enabled"] = bool(fields.get("enabled", True))
    out["reason"] = (fields.get("reason") or "").strip()
    name = _clean(fields.get("name"))
    if not name:
        raise ValueError("a policy needs a name")
    out["name"] = name
    return out


@dataclass(frozen=True)
class _Kind:
    """How one row type is stored, validated, converted and audited."""

    resource_type: str
    event_prefix: str
    log_name: str
    validate: Any
    convert: Any
    model_path: tuple[str, str]
    not_found: str

    def model(self) -> Any:
        import importlib

        module, name = self.model_path
        return getattr(importlib.import_module(module), name)


ROUTING_KIND = _Kind(
    resource_type="model_routing_policy",
    event_prefix="model_gateway_policy",
    log_name="model_gateway_policy",
    validate=validate_policy_fields,
    convert=_policy,
    model_path=("core.models.model_routing_policy", "ModelRoutingPolicy"),
    not_found="Routing policy not found",
)
ACCESS_KIND = _Kind(
    resource_type="model_access_policy",
    event_prefix="model_gateway_access_policy",
    log_name="model_gateway_access_policy",
    validate=validate_access_policy_fields,
    convert=_access_policy,
    model_path=("core.models.model_access_policy", "ModelAccessPolicy"),
    not_found="Access policy not found",
)
LIMIT_KIND = _Kind(
    resource_type="model_limit",
    event_prefix="model_gateway_limit",
    log_name="model_gateway_limit",
    validate=validate_limit_fields,
    convert=_limit,
    model_path=("core.models.model_limit", "ModelLimit"),
    not_found="Limit not found",
)


def _audit_entry(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    policy_id: str,
    details: dict[str, Any],
    kind: _Kind = ROUTING_KIND,
) -> Any:
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"{kind.event_prefix}.{action}",
        "actor_type": "user",
        "actor_id": actor_id,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": kind.resource_type,
        "resource_id": policy_id,
        "action": action,
        "outcome": "success",
        "details": details,
        "trace_id": "",
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def _create_row(kind: _Kind, tenant_id: uuid.UUID, *, actor_id: str, fields: dict[str, Any]) -> Any:
    """Create a row of ``kind`` and write its audit row in the same transaction."""
    from core.database import get_tenant_session

    clean = kind.validate(fields)
    row = kind.model()(tenant_id=tenant_id, created_by=actor_id, **clean)
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        item = kind.convert(row)
        session.add(
            _audit_entry(
                tenant_id, actor_id=actor_id, action="set", policy_id=item.id, details=item.to_dict(), kind=kind
            )
        )
    await invalidate(tenant_id)
    logger.info(f"{kind.log_name}_set", policy_id=item.id, actor_id=actor_id)
    return item


async def _update_row(
    kind: _Kind, tenant_id: uuid.UUID, row_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Any:
    """Apply ``changes`` to a row of ``kind`` (None when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session

    model = kind.model()
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(select(model).where(model.id == row_id, model.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        current = kind.convert(row).to_dict()
        applied = {k: v for k, v in changes.items() if k in current}
        clean = kind.validate({**current, **applied})
        for name, value in clean.items():
            setattr(row, name, value)
        row.updated_by = actor_id
        row.updated_at = datetime.now(UTC)
        await session.flush()
        item = kind.convert(row)
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="update",
                policy_id=item.id,
                details={"changes": applied, "policy": item.to_dict()},
                kind=kind,
            )
        )
    await invalidate(tenant_id)
    logger.info(f"{kind.log_name}_updated", policy_id=item.id, actor_id=actor_id)
    return item


async def _delete_row(kind: _Kind, tenant_id: uuid.UUID, row_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete a row of ``kind`` (False when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session

    model = kind.model()
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(select(model).where(model.id == row_id, model.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            return False
        item = kind.convert(row)
        await session.delete(row)
        session.add(
            _audit_entry(
                tenant_id, actor_id=actor_id, action="delete", policy_id=item.id, details=item.to_dict(), kind=kind
            )
        )
    await invalidate(tenant_id)
    logger.info(f"{kind.log_name}_deleted", policy_id=item.id, actor_id=actor_id)
    return True


async def set_policy(tenant_id: uuid.UUID, *, actor_id: str, **fields: Any) -> Policy:
    """Create a routing policy and write its audit row in the same transaction."""
    return await _create_row(ROUTING_KIND, tenant_id, actor_id=actor_id, fields=fields)


async def update_policy(
    tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Policy | None:
    """Apply ``changes`` to a routing policy (None when it does not exist) and write the audit row."""
    return await _update_row(ROUTING_KIND, tenant_id, policy_id, actor_id=actor_id, changes=changes)


async def delete_policy(tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete a routing policy (False when it does not exist) and write the audit row."""
    return await _delete_row(ROUTING_KIND, tenant_id, policy_id, actor_id=actor_id)


async def set_access_policy(tenant_id: uuid.UUID, *, actor_id: str, **fields: Any) -> AccessPolicy:
    """Create an access policy and write its audit row in the same transaction."""
    return await _create_row(ACCESS_KIND, tenant_id, actor_id=actor_id, fields=fields)


async def update_access_policy(
    tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> AccessPolicy | None:
    """Apply ``changes`` to an access policy (None when it does not exist) and write the audit row."""
    return await _update_row(ACCESS_KIND, tenant_id, policy_id, actor_id=actor_id, changes=changes)


async def delete_access_policy(tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete an access policy (False when it does not exist) and write the audit row."""
    return await _delete_row(ACCESS_KIND, tenant_id, policy_id, actor_id=actor_id)


async def set_limit(tenant_id: uuid.UUID, *, actor_id: str, **fields: Any) -> Limit:
    """Create a per-model limit and write its audit row in the same transaction."""
    return await _create_row(LIMIT_KIND, tenant_id, actor_id=actor_id, fields=fields)


async def update_limit(
    tenant_id: uuid.UUID, limit_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Limit | None:
    """Apply ``changes`` to a limit (None when it does not exist) and write the audit row."""
    return await _update_row(LIMIT_KIND, tenant_id, limit_id, actor_id=actor_id, changes=changes)


async def delete_limit(tenant_id: uuid.UUID, limit_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete a limit (False when it does not exist) and write the audit row."""
    return await _delete_row(LIMIT_KIND, tenant_id, limit_id, actor_id=actor_id)
