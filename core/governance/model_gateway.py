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

Every decision is logged with its correlation id, the policy evaluated, the
provider and model chosen and the reason, and metered
(``agenticorg_model_gateway_decisions_total``); every policy change is written
as a signed audit row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings

logger = structlog.get_logger()

FLAG_KEY = "model_gateway.enabled"
ERROR_CODE = "E1014"
CACHE_TTL_SECONDS = 5
_CACHE_PREFIX = "model_gateway:policies:"

# The use cases the platform's own call sites name; a policy may name any string.
USE_CASES: tuple[str, ...] = ("agent_run", "agent_resume", "completion")
SENSITIVITIES: tuple[str, ...] = ("public", "internal", "confidential", "restricted")
TIERS: tuple[str, ...] = ("tier1", "tier2", "tier3")
MATCH_FIELDS: tuple[str, ...] = ("use_case", "sensitivity", "agent_id", "business_unit", "language")
ROUTE_FIELDS: tuple[str, ...] = ("provider", "model", "tier", "allowed_providers", "in_region_only")


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
    allowed_providers: tuple[str, ...] | None = None
    in_region_only: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_providers"] = list(self.allowed_providers) if self.allowed_providers is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        allowed = data.get("allowed_providers")
        return cls(**{**data, "allowed_providers": tuple(allowed) if allowed is not None else None})

    def matches(self, request: RouteRequest) -> bool:
        """Every match field the policy sets must equal the request's (case-insensitive)."""
        for name in MATCH_FIELDS:
            wanted = getattr(self, name)
            if wanted is None:
                continue
            actual = getattr(request, name)
            if actual is None or str(actual).strip().lower() != str(wanted).strip().lower():
                return False
        return True


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelGatewayRefused(RuntimeError):  # noqa: N818 - surface name used in error payloads
    """Raised when the gateway refuses a model call: a fence, a restriction or an unreadable policy set."""

    def __init__(
        self,
        reason: str,
        *,
        correlation_id: str,
        policy_id: str | None = None,
        policy_name: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.correlation_id = correlation_id
        self.policy_id = policy_id
        self.policy_name = policy_name

    def to_error(self) -> dict[str, Any]:
        return {
            "error": {"code": ERROR_CODE, "message": self.reason},
            "model_gateway": {
                "correlation_id": self.correlation_id,
                "policy_id": self.policy_id,
                "policy_name": self.policy_name,
            },
        }


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
        "error_code": ERROR_CODE,
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
        in_region_only=bool(row.in_region_only),
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


async def active_policies(tenant_id: uuid.UUID) -> list[Policy]:
    """Enabled policies in priority order: the shared Redis cache first, then the database."""
    from core.async_redis import get_async_redis

    redis = None
    try:
        redis = await get_async_redis()
        if redis is not None:
            cached = await redis.get(_cache_key(tenant_id))
            if cached:
                return [Policy.from_dict(item) for item in json.loads(cached)]
    # enterprise-gate: broad-except-ok reason=policy-cache-miss-falls-through-to-the-database
    except Exception as exc:
        logger.warning("model_gateway_cache_read_failed", error_type=type(exc).__name__)
        redis = None

    policies = await _load_policies(tenant_id)
    if redis is not None:
        try:
            await redis.set(_cache_key(tenant_id), json.dumps([p.to_dict() for p in policies]), ex=CACHE_TTL_SECONDS)
        # enterprise-gate: broad-except-ok reason=policy-cache-write-is-best-effort-ttl-bounds-staleness
        except Exception as exc:
            logger.warning("model_gateway_cache_write_failed", error_type=type(exc).__name__)
    return policies


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
    return RouteDecision(
        provider=provider_for_model(request.requested_model, request.requested_provider),
        model=request.requested_model,
        correlation_id=correlation_id,
        reason=reason,
    )


def tier_model(tier: str) -> str:
    """The concrete model behind a cost tier in the deployment's current mode."""
    from core.llm.router import tier_model as _tier_model

    return _tier_model(tier)


def _apply(policy: Policy, provider: str | None, model: str) -> tuple[str | None, str]:
    """The provider and model a matched policy gives a request that asked for ``provider`` and ``model``."""
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
    reason: str, *, correlation_id: str, policy: Policy | None, request: RouteRequest, dry_run: bool = False
) -> ModelGatewayRefused:
    if not dry_run:
        _meter("refused")
        logger.warning(
            "model_gateway_refused",
            correlation_id=correlation_id,
            use_case=request.use_case,
            policy_id=policy.id if policy else None,
            reason=reason,
        )
    return ModelGatewayRefused(
        reason,
        correlation_id=correlation_id,
        policy_id=policy.id if policy else None,
        policy_name=policy.name if policy else None,
    )


async def _decide(
    request: RouteRequest, policies: list[Policy], correlation_id: str, *, dry_run: bool = False
) -> RouteDecision:
    tid = _as_uuid(request.tenant_id)
    policy = next((p for p in policies if p.matches(request)), None)
    restricted = (request.sensitivity or "").strip().lower() == "restricted" or bool(policy and policy.in_region_only)
    # A caller that pinned no provider (legacy agent rows) still names one through its model.
    provider = provider_for_model(request.requested_model, request.requested_provider)
    model = request.requested_model
    if policy is None:
        reason = "no policy matched; the caller's choice stands"
    else:
        provider, model = _apply(policy, provider, model)
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
    )
    if dry_run:
        return decision
    _meter("applied" if policy is not None else "passthrough")
    logger.info(
        "model_gateway_decision",
        correlation_id=correlation_id,
        use_case=request.use_case,
        policy_id=decision.policy_id,
        provider=provider,
        model=model,
        restricted=restricted,
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
    policies = await active_policies(tid)
    try:
        decision = await _decide(request, policies, correlation_id, dry_run=True)
    except ModelGatewayRefused as exc:
        return Evaluation(enabled=on, refusal=exc)
    return Evaluation(enabled=on, decision=decision)


async def _policies_or_passthrough(
    request: RouteRequest, correlation_id: str
) -> tuple[list[Policy] | None, RouteDecision | None]:
    """The enabled policies, or the pass-through decision when the gateway is off or unreadable."""
    tid = _as_uuid(request.tenant_id)
    if tid is None:
        return None, _passthrough(request, correlation_id, "no tenant; the caller's choice stands")
    try:
        on = await enabled(tid)
        policies = await active_policies(tid) if on else []
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
    policies, passthrough = await _policies_or_passthrough(request, correlation_id)
    if passthrough is not None:
        return passthrough
    return await _decide(request, policies or [], correlation_id)


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
    request = RouteRequest(
        tenant_id=tenant_id,
        use_case=use_case,
        requested_provider=requested_provider,
        requested_model=requested_model,
        agent_id=str(agent_id or "") or None,
        business_unit=business_unit,
        correlation_id=correlation_id,
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
    return await _decide(request, policies or [], correlation_id)


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
    if out["provider"] and out["model"]:
        from core.ai_providers.catalog import validate_llm_selection

        out["provider"], out["model"] = validate_llm_selection(out["provider"], out["model"])
    elif out["model"] and provider_for_model(out["model"]) is None:
        raise ValueError(f"model {out['model']} is not in the provider catalogue")
    if out["provider"] and out["allowed_providers"] is not None and out["provider"] not in out["allowed_providers"]:
        raise ValueError("the policy's provider must be among its allowed_providers")
    if not any(out[name] not in (None, False) for name in ROUTE_FIELDS):
        raise ValueError("a policy must route (provider, model or tier), fence (allowed_providers) or restrict")
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


def _audit_entry(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    policy_id: str,
    details: dict[str, Any],
) -> Any:
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"model_gateway_policy.{action}",
        "actor_type": "user",
        "actor_id": actor_id,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "model_routing_policy",
        "resource_id": policy_id,
        "action": action,
        "outcome": "success",
        "details": details,
        "trace_id": "",
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def set_policy(tenant_id: uuid.UUID, *, actor_id: str, **fields: Any) -> Policy:
    """Create a policy and write its audit row in the same transaction."""
    from core.database import get_tenant_session
    from core.models.model_routing_policy import ModelRoutingPolicy

    clean = validate_policy_fields(fields)
    row = ModelRoutingPolicy(tenant_id=tenant_id, created_by=actor_id, **clean)
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        policy = _policy(row)
        session.add(
            _audit_entry(tenant_id, actor_id=actor_id, action="set", policy_id=policy.id, details=policy.to_dict())
        )
    await invalidate(tenant_id)
    logger.info("model_gateway_policy_set", policy_id=policy.id, actor_id=actor_id)
    return policy


async def update_policy(
    tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Policy | None:
    """Apply ``changes`` to a policy (None when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_routing_policy import ModelRoutingPolicy

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ModelRoutingPolicy).where(
                    ModelRoutingPolicy.id == policy_id, ModelRoutingPolicy.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        current = _policy(row).to_dict()
        merged = {**current, **{k: v for k, v in changes.items() if k in current}}
        clean = validate_policy_fields(merged)
        for name, value in clean.items():
            setattr(row, name, value)
        row.updated_by = actor_id
        row.updated_at = datetime.now(UTC)
        await session.flush()
        policy = _policy(row)
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="update",
                policy_id=policy.id,
                details={"changes": {k: v for k, v in changes.items() if k in current}, "policy": policy.to_dict()},
            )
        )
    await invalidate(tenant_id)
    logger.info("model_gateway_policy_updated", policy_id=policy.id, actor_id=actor_id)
    return policy


async def delete_policy(tenant_id: uuid.UUID, policy_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete a policy (False when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.model_routing_policy import ModelRoutingPolicy

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ModelRoutingPolicy).where(
                    ModelRoutingPolicy.id == policy_id, ModelRoutingPolicy.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        policy = _policy(row)
        await session.delete(row)
        session.add(
            _audit_entry(tenant_id, actor_id=actor_id, action="delete", policy_id=policy.id, details=policy.to_dict())
        )
    await invalidate(tenant_id)
    logger.info("model_gateway_policy_deleted", policy_id=policy.id, actor_id=actor_id)
    return True
