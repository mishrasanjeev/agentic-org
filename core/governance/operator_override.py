# SPDX-License-Identifier: Apache-2.0
"""Operator override: halt or throttle a model, an agent, a workflow or the tool pipeline.

An administrator places an override on a target; from then on every enforcement
point refuses (``halt``) or rate-limits (``throttle``) work for that target until
the override is released or expires. The enforcement points are the model router
(``core.llm.router``), the LangGraph reason node (``core.langgraph.agent_graph``),
the agent runner and resume path (``core.langgraph.runner``), ``BaseAgent.execute``,
the workflow engine between steps (``workflows.engine``), the connector dispatch
boundary (``core.langgraph.tool_adapter``) and ``ToolGateway.execute``. The HTTP
run endpoints refuse early with 423 so a caller learns why before a run starts.

Behaviour is off until ``operator_override.enabled`` is on for the tenant (an
authority flag, operator-managed, read strictly) or
``AGENTICORG_OPERATOR_OVERRIDE_ENABLED`` is set. With the control off ``check``
always allows and reads nothing.

Decision rules:

* ``halt`` beats ``throttle`` when several overrides match one call, and a halt
  is re-checked at every enforcement point.
* A throttle is a fixed window of ``limit_per_minute`` calls per override; the
  counter is shared across replicas through Redis and counts one logical
  dispatch: an agent throttle is consumed once per agent run (``throttle_unit``
  ``"agent"``), a provider or model throttle once per model call (``"model"``),
  a connector, tool or pipeline throttle once per tool call (``"tool"``) and a
  workflow throttle once per workflow step (``"workflow"``). A boundary that
  passes no unit only applies halts. In a strict runtime a Redis failure blocks
  the call (fail closed); a relaxed runtime falls back to memory.
* Overrides, and the authority flag, are read through a short cache and then the
  database. In a strict runtime a read failure blocks the call; a relaxed
  runtime allows it and logs.

Every block is metered (``agenticorg_operator_override_blocks_total``) and the
change of an override is written as a signed audit row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings
from observability import tracing

logger = structlog.get_logger()

FLAG_KEY = "operator_override.enabled"
ERROR_CODE = "E1013"
CACHE_TTL_SECONDS = 5
_CACHE_PREFIX = "operator_overrides:"

# Provider spellings used by the model router ("gemini", "claude", "gpt") and the
# provider catalogue ("anthropic", "openai", ...) name the same upstream.
_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "gpt": "openai",
    "azure_openai": "openai",
    "openai_compatible": "openai",
}


def normalise_provider(name: str | None) -> str:
    key = (name or "").strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


class OperatorOverrideBlocked(RuntimeError):  # noqa: N818 - surface name used in error payloads
    """Raised at the model and agent enforcement points when an override blocks the call."""

    def __init__(self, decision: OverrideDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


@dataclass(frozen=True)
class Override:
    id: str
    target_kind: str
    target_id: str
    mode: str
    limit_per_minute: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OverrideDecision:
    blocked: bool
    reason: str = ""
    override: Override | None = None

    def to_error(self) -> dict[str, Any]:
        """The ``{"error": {...}}`` payload the tool pipeline returns for a block."""
        return {
            "error": {"code": ERROR_CODE, "message": self.reason},
            "override": self.override.to_dict() if self.override else None,
        }


ALLOWED = OverrideDecision(blocked=False)


async def enabled(tenant_id: uuid.UUID | str | None) -> bool:
    """Whether the control is on for ``tenant_id`` (settings switch or authority flag).

    The flag is read strictly: a lookup failure raises
    ``core.feature_flags.FeatureFlagLookupError`` rather than reading as off, so
    a store outage cannot silently lift an active override (``check`` fails
    closed on it in a strict runtime).
    """
    if settings.operator_override_enabled:
        return True
    tid = _as_uuid(tenant_id)
    if tid is None:
        return False
    # An authority flag: the global row and the tenant's row are read separately
    # and the control is on when either enables it, so a tenant row can never
    # switch off an operator's global setting (the pattern of the other
    # authority flags). The strict reader raises FeatureFlagLookupError rather
    # than reading an unreadable store as "off".
    from core.feature_flags import load_flag_rows_strict, row_enabled

    rows = await load_flag_rows_strict(FLAG_KEY, tenant_id=tid)
    subject = str(tid)
    return row_enabled(FLAG_KEY, rows.global_row, subject_id=subject) or row_enabled(
        FLAG_KEY, rows.tenant_row, subject_id=subject
    )


# The dispatch unit a throttle on each target kind is counted against.
THROTTLE_UNITS: dict[str, str] = {
    "provider": "model",
    "model": "model",
    "agent": "agent",
    "all_agents": "agent",
    "workflow": "workflow",
    "connector": "tool",
    "tool": "tool",
    "tool_pipeline": "tool",
}


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _cache_key(tenant_id: uuid.UUID) -> str:
    return f"{_CACHE_PREFIX}{tenant_id}"


async def _load_from_db(tenant_id: uuid.UUID) -> list[Override]:
    from sqlalchemy import or_, select

    from core.database import get_tenant_session
    from core.models.operator_override import OperatorOverride

    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(OperatorOverride).where(
                    OperatorOverride.tenant_id == tenant_id,
                    OperatorOverride.released_at.is_(None),
                    or_(OperatorOverride.expires_at.is_(None), OperatorOverride.expires_at > now),
                )
            )
        ).scalars()
        return [
            Override(
                id=str(row.id),
                target_kind=row.target_kind,
                target_id=row.target_id or "",
                mode=row.mode,
                limit_per_minute=row.limit_per_minute,
                reason=row.reason,
            )
            for row in rows
        ]


async def active_overrides(tenant_id: uuid.UUID) -> list[Override]:
    """Active overrides for the tenant: Redis cache first, then the database."""
    from core.async_redis import get_async_redis

    redis = None
    try:
        redis = await get_async_redis()
        if redis is not None:
            cached = await redis.get(_cache_key(tenant_id))
            if cached:
                return [Override(**item) for item in json.loads(cached)]
    # enterprise-gate: broad-except-ok reason=override-cache-miss-falls-through-to-the-database
    except Exception as exc:
        logger.warning("operator_override_cache_read_failed", error_type=type(exc).__name__)
        redis = None

    overrides = await _load_from_db(tenant_id)
    if redis is not None:
        try:
            await redis.set(_cache_key(tenant_id), json.dumps([o.to_dict() for o in overrides]), ex=CACHE_TTL_SECONDS)
        # enterprise-gate: broad-except-ok reason=override-cache-write-is-best-effort
        except Exception as exc:
            logger.warning("operator_override_cache_write_failed", error_type=type(exc).__name__)
    return overrides


async def invalidate(tenant_id: uuid.UUID) -> None:
    """Drop the cached override list so the next check reads the database."""
    from core.async_redis import get_async_redis

    try:
        redis = await get_async_redis()
        if redis is not None:
            await redis.delete(_cache_key(tenant_id))
    # enterprise-gate: broad-except-ok reason=cache-invalidation-is-best-effort-ttl-bounds-staleness
    except Exception as exc:
        logger.warning("operator_override_cache_invalidate_failed", error_type=type(exc).__name__)


def _matches(
    o: Override,
    *,
    provider: str,
    model: str,
    agent_id: str,
    workflow_id: str,
    connector: str,
    tool: str,
) -> bool:
    target = o.target_id.strip().lower()
    kind = o.target_kind
    if kind == "provider":
        return bool(provider) and normalise_provider(target) == provider
    if kind == "model":
        return bool(model) and target == model
    if kind == "agent":
        return bool(agent_id) and target == agent_id
    if kind == "all_agents":
        return bool(agent_id)
    if kind == "workflow":
        return bool(workflow_id) and target == workflow_id
    if kind == "connector":
        return bool(connector) and target == connector
    if kind == "tool":
        if not connector or not tool:
            return False
        return target in (tool, f"{connector}:{tool}")
    if kind == "tool_pipeline":
        return bool(connector)
    return False


def _describe(o: Override) -> str:
    target = f"{o.target_kind}" + (f" {o.target_id}" if o.target_id else "")
    if o.mode == "halt":
        return f"Operator override: {target} is halted ({o.reason})."
    return f"Operator override: {target} is throttled to {o.limit_per_minute} calls per minute ({o.reason})."


async def _throttled(tenant_id: uuid.UUID, o: Override) -> bool:
    from core.auth_state import check_window_rate

    limit = int(o.limit_per_minute or 0)
    if limit <= 0:
        return True
    try:
        return await check_window_rate("operator_override", f"{tenant_id}:{o.id}", limit, 60)
    except RuntimeError as exc:
        logger.warning("operator_override_throttle_counter_unavailable", override_id=o.id, error=str(exc))
        return True


async def check(
    tenant_id: uuid.UUID | str | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    agent_id: str | None = None,
    workflow_id: str | None = None,
    connector: str | None = None,
    tool: str | None = None,
    throttle_unit: str | None = None,
) -> OverrideDecision:
    """Decide whether an override blocks the call described by the keyword arguments.

    Halts apply at every boundary. A throttle is counted only at the boundary
    whose ``throttle_unit`` matches the override's target kind
    (``THROTTLE_UNITS``), so one logical dispatch consumes one token; a boundary
    with no unit ignores throttles.

    Without a tenant there is nothing to look up and the call is allowed; every
    production path carries the tenant. With the control off the call is allowed
    without a read.
    """
    tid = _as_uuid(tenant_id)
    if tid is None:
        return ALLOWED
    try:
        if not await enabled(tid):
            return ALLOWED
        overrides = await active_overrides(tid)
    # enterprise-gate: broad-except-ok reason=override-read-failure-fails-closed-in-strict-runtime
    except Exception as exc:
        logger.error("operator_override_read_failed", error_type=type(exc).__name__)
        if is_strict_runtime_env(settings.env):
            decision = OverrideDecision(blocked=True, reason="Operator overrides could not be read; refusing the call.")
            _meter("unknown", "read_failed")
            return decision
        return ALLOWED
    if not overrides:
        return ALLOWED

    ctx = {
        "provider": normalise_provider(provider),
        "model": (model or "").strip().lower(),
        "agent_id": (agent_id or "").strip().lower(),
        "workflow_id": (workflow_id or "").strip().lower(),
        "connector": (connector or "").strip().lower(),
        "tool": (tool or "").strip().lower(),
    }
    matched = [o for o in overrides if _matches(o, **ctx)]
    if not matched:
        return ALLOWED
    halts = [o for o in matched if o.mode == "halt"]
    if halts:
        o = halts[0]
        _meter(o.target_kind, o.mode)
        return OverrideDecision(blocked=True, reason=_describe(o), override=o)
    for o in matched:
        if throttle_unit is None or THROTTLE_UNITS.get(o.target_kind) != throttle_unit:
            continue
        if await _throttled(tid, o):
            _meter(o.target_kind, o.mode)
            return OverrideDecision(blocked=True, reason=_describe(o), override=o)
    return ALLOWED


def _meter(target_kind: str, mode: str) -> None:
    try:
        from observability.metrics import operator_override_blocks_total

        operator_override_blocks_total.labels(target_kind=target_kind, mode=mode).inc()
    # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-an-unmetered-decision-never-changes-it
    except Exception:
        logger.debug("operator_override_metric_unavailable")


def blocked_run_result(decision: OverrideDecision) -> dict[str, Any]:
    """Runner result for a run refused by an override (same shape as a failed run)."""
    return {
        "status": "operator_override",
        "output": {},
        "confidence": 0.0,
        "reasoning_trace": [],
        "tool_calls_log": [],
        "tool_calls": [],
        "hitl_trigger": "",
        "error": decision.reason,
        "override": decision.override.to_dict() if decision.override else None,
        "performance": {"total_latency_ms": 0, "llm_tokens_used": 0, "llm_cost_usd": 0.0},
    }


# ---------------------------------------------------------------------------
# Changes (used by the admin API)
# ---------------------------------------------------------------------------


def _audit_entry(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    override_id: str,
    details: dict[str, Any],
) -> Any:
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"operator_override.{action}",
        "actor_type": "user",
        "actor_id": actor_id,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "operator_override",
        "resource_id": override_id,
        "action": action,
        "outcome": "success",
        "details": details,
        "trace_id": tracing.audit_trace_id(),
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def set_override(
    tenant_id: uuid.UUID,
    *,
    target_kind: str,
    target_id: str,
    mode: str,
    limit_per_minute: int | None,
    reason: str,
    actor_id: str,
    expires_at: datetime | None,
) -> Override:
    """Place an override and write its audit row in the same transaction."""
    from core.database import get_tenant_session
    from core.models.operator_override import MODES, TARGET_KINDS, OperatorOverride

    if target_kind not in TARGET_KINDS:
        raise ValueError(f"unknown target kind: {target_kind}")
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if mode == "throttle" and (limit_per_minute is None or limit_per_minute < 0):
        raise ValueError("a throttle needs limit_per_minute >= 0")
    if mode == "halt":
        limit_per_minute = None
    if target_kind in ("all_agents", "tool_pipeline"):
        target_id = ""
    elif not target_id.strip():
        raise ValueError(f"target kind {target_kind} needs a target_id")

    row = OperatorOverride(
        tenant_id=tenant_id,
        target_kind=target_kind,
        target_id=target_id.strip(),
        mode=mode,
        limit_per_minute=limit_per_minute,
        reason=reason.strip(),
        created_by=actor_id,
        expires_at=expires_at,
    )
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="set",
                override_id=str(row.id),
                details={
                    "target_kind": target_kind,
                    "target_id": row.target_id,
                    "mode": mode,
                    "limit_per_minute": limit_per_minute,
                    "reason": row.reason,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            )
        )
        override = Override(
            id=str(row.id),
            target_kind=target_kind,
            target_id=row.target_id,
            mode=mode,
            limit_per_minute=limit_per_minute,
            reason=row.reason,
        )
    await invalidate(tenant_id)
    # Identifiers and the free-form reason stay in the signed audit row.
    logger.warning("operator_override_set", override_id=override.id, target_kind=target_kind, mode=mode)
    return override


async def release_override(tenant_id: uuid.UUID, override_id: uuid.UUID, *, actor_id: str) -> Override | None:
    """Release an active override; returns None when there is no such active row."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.operator_override import OperatorOverride

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(OperatorOverride).where(
                    OperatorOverride.id == override_id,
                    OperatorOverride.tenant_id == tenant_id,
                    OperatorOverride.released_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.released_at = datetime.now(UTC)
        row.released_by = actor_id
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                action="released",
                override_id=str(row.id),
                details={"target_kind": row.target_kind, "target_id": row.target_id, "mode": row.mode},
            )
        )
        override = Override(
            id=str(row.id),
            target_kind=row.target_kind,
            target_id=row.target_id or "",
            mode=row.mode,
            limit_per_minute=row.limit_per_minute,
            reason=row.reason,
        )
    await invalidate(tenant_id)
    logger.warning("operator_override_released", override_id=str(override_id), target_kind=override.target_kind)
    return override
