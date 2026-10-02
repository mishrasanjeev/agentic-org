# SPDX-License-Identifier: Apache-2.0
"""The guardrail engine: evaluate a tenant's rules at one stage of a call.

``evaluate`` runs every enabled rule that matches the stage (and the agent,
use case or risk tier the rule names) in priority order. A rule whose detector
finds something at or above its threshold produces an outcome; what the
outcome does depends on enforcement:

* with ``guardrails.enforce`` off for the tenant (the default), every rule
  only records what it would have done: findings are metered and logged, the
  text travels on unchanged and nothing is blocked (flag-only mode);
* with it on, ``mask``, ``redact`` and ``tokenise`` transform the text before
  it travels on, ``block`` refuses the stage with ``E1016``, and each applied
  action writes a signed audit row.

Each detector runs off the event loop under a time budget
(``guardrails_detector_timeout_seconds``). A detector that fails or runs out
of time cannot say what it would have found: with enforcement on in a strict
runtime its stage is refused rather than let through unchecked; otherwise the
failure is logged and the rule records no outcome.

A dry run (the evaluation endpoint) applies the rules to the returned text so
an administrator sees the effect, and meters, logs and audits nothing.

Rules are read through a short shared cache and then the database; in a
strict runtime an unreadable rule set refuses the stage, a relaxed runtime
lets it through unguarded and logs it. The correlation id is the request id
bound for the request, so a guardrail outcome, the model call it guarded and
the audit row share one id.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from core.config import is_strict_runtime_env, settings
from core.governance.guardrails.detectors import REGISTRY, apply_transform
from core.governance.guardrails.schema import (
    ERROR_CODE,
    TRANSFORMS,
    GuardrailBlocked,
    GuardrailResult,
    Outcome,
    Rule,
    validate_rule_fields,
)

logger = structlog.get_logger()

FLAG_KEY = "guardrails.enforce"
CACHE_TTL_SECONDS = 5
_CACHE_PREFIX = "guardrails:rules:"

__all__ = [
    "ERROR_CODE",
    "FLAG_KEY",
    "GuardrailBlocked",
    "GuardrailResult",
    "active_rules",
    "blocked_run_result",
    "delete_rule",
    "enforcing",
    "evaluate",
    "invalidate",
    "report_section",
    "set_rule",
    "update_rule",
]


def _as_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Flag and rule reads
# ---------------------------------------------------------------------------


async def enforcing(tenant_id: uuid.UUID | str | None) -> bool:
    """Whether guardrails enforce for ``tenant_id`` (settings switch or the authority flag, read strictly)."""
    if settings.guardrails_enforce:
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


def _rule(row: Any) -> Rule:
    return Rule(
        id=str(row.id),
        name=row.name,
        stage=row.stage,
        detector=row.detector,
        action=row.action,
        priority=int(row.priority),
        enabled=bool(row.enabled),
        threshold=float(row.threshold),
        agent_id=row.agent_id,
        use_case=row.use_case,
        risk_tier=row.risk_tier,
        options=dict(row.options or {}),
        reason=row.reason or "",
    )


async def _load_rules(tenant_id: uuid.UUID) -> list[Rule]:
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.guardrail_rule import GuardrailRule

    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(GuardrailRule)
                .where(GuardrailRule.tenant_id == tenant_id, GuardrailRule.enabled.is_(True))
                .order_by(GuardrailRule.priority, GuardrailRule.name)
            )
        ).scalars()
        return [_rule(row) for row in rows]


async def active_rules(tenant_id: uuid.UUID) -> list[Rule]:
    """Enabled rules in priority order: the shared Redis cache first, then the database."""
    from core.async_redis import get_async_redis

    redis = None
    try:
        redis = await get_async_redis()
        if redis is not None:
            cached = await redis.get(_cache_key(tenant_id))
            if cached:
                return [Rule.from_dict(item) for item in json.loads(cached)]
    # enterprise-gate: broad-except-ok reason=rule-cache-miss-falls-through-to-the-database
    except Exception as exc:
        logger.warning("guardrail_cache_read_failed", error_type=type(exc).__name__)
        redis = None
    rules = await _load_rules(tenant_id)
    if redis is not None:
        try:
            await redis.set(_cache_key(tenant_id), json.dumps([r.to_dict() for r in rules]), ex=CACHE_TTL_SECONDS)
        # enterprise-gate: broad-except-ok reason=rule-cache-write-is-best-effort-ttl-bounds-staleness
        except Exception as exc:
            logger.warning("guardrail_cache_write_failed", error_type=type(exc).__name__)
    return rules


async def invalidate(tenant_id: uuid.UUID) -> None:
    from core.async_redis import get_async_redis

    try:
        redis = await get_async_redis()
        if redis is not None:
            await redis.delete(_cache_key(tenant_id))
    # enterprise-gate: broad-except-ok reason=cache-invalidation-is-best-effort-ttl-bounds-staleness
    except Exception as exc:
        logger.warning("guardrail_cache_invalidate_failed", error_type=type(exc).__name__)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _correlation_id(given: str | None) -> str:
    if given:
        return given
    try:
        value = structlog.contextvars.get_contextvars().get("request_id")
    # enterprise-gate: broad-except-ok reason=a-missing-log-context-degrades-to-a-fresh-correlation-id
    except Exception:
        value = None
    return str(value or "").strip()[:128] or uuid.uuid4().hex


def _meter(stage: str, detector: str, action: str, mode: str) -> None:
    try:
        from observability.metrics import guardrail_outcomes_total

        guardrail_outcomes_total.labels(stage=stage, detector=detector, action=action, mode=mode).inc()
    # enterprise-gate: broad-except-ok reason=metrics-outage-degrades-to-an-unmetered-outcome-never-changes-it
    except Exception:
        logger.debug("guardrail_metric_skipped", stage=stage, detector=detector)


@dataclass(frozen=True)
class _Scope:
    tenant_id: uuid.UUID | None
    stage: str
    agent_id: str | None
    use_case: str | None
    risk_tier: str | None
    correlation_id: str


async def _rules_for(scope: _Scope) -> list[Rule] | None:
    """The matching rules, or None when the rule set cannot be read in a relaxed runtime."""
    if scope.tenant_id is None:
        return []
    try:
        rules = await active_rules(scope.tenant_id)
    # enterprise-gate: broad-except-ok reason=rule-read-failure-fails-closed-in-strict-runtime
    except Exception as exc:
        logger.error("guardrail_rules_unreadable", error_type=type(exc).__name__, correlation_id=scope.correlation_id)
        if is_strict_runtime_env(settings.env):
            raise GuardrailBlocked(
                "Guardrails: the tenant's rules could not be read; refusing the call.",
                stage=scope.stage,
                correlation_id=scope.correlation_id,
                rule_id=None,
                rule_name=None,
            ) from exc
        return None
    return [
        r
        for r in rules
        if r.matches(scope.stage, agent_id=scope.agent_id, use_case=scope.use_case, risk_tier=scope.risk_tier)
    ]


async def evaluate(
    stage: str,
    text: str,
    *,
    tenant_id: uuid.UUID | str | None,
    agent_id: str | None = None,
    use_case: str | None = None,
    risk_tier: str | None = None,
    correlation_id: str | None = None,
    dry_run: bool = False,
) -> GuardrailResult:
    """Run the tenant's rules for ``stage`` over ``text``.

    Returns the text to travel on (transformed when enforcement is on or in a
    dry run), whether the stage may continue, and what each matching rule did.
    Raises ``GuardrailBlocked`` when a ``block`` rule matched with enforcement
    on (never in a dry run: the result says ``allowed`` is False instead).
    """
    scope = _Scope(
        tenant_id=_as_uuid(tenant_id),
        stage=stage,
        agent_id=str(agent_id or "") or None,
        use_case=use_case,
        risk_tier=risk_tier,
        correlation_id=_correlation_id(correlation_id),
    )
    rules = await _rules_for(scope)
    if rules is None:
        logger.warning("guardrail_rules_skipped", stage=stage, correlation_id=scope.correlation_id)
        rules = []
    enforced = False
    if rules and scope.tenant_id is not None:
        if dry_run:
            enforced = await enforcing(scope.tenant_id)
        else:
            try:
                enforced = await enforcing(scope.tenant_id)
            # enterprise-gate: broad-except-ok reason=flag-read-failure-fails-closed-in-strict-runtime
            except Exception as exc:
                logger.error(
                    "guardrail_flag_unreadable", error_type=type(exc).__name__, correlation_id=scope.correlation_id
                )
                if is_strict_runtime_env(settings.env):
                    raise GuardrailBlocked(
                        "Guardrails: the enforcement flag could not be read; refusing the call.",
                        stage=stage,
                        correlation_id=scope.correlation_id,
                        rule_id=None,
                        rule_name=None,
                    ) from exc
                enforced = False
    applying = enforced or dry_run
    mode = "dry_run" if dry_run else ("enforced" if enforced else "flag_only")
    result = GuardrailResult(
        stage=stage, text=text, allowed=True, enforced=enforced, correlation_id=scope.correlation_id
    )
    counters: dict[str, int] = {}
    blocker: Rule | None = None
    for rule in rules:
        detector = REGISTRY.get(rule.detector)
        if detector is None:
            logger.warning("guardrail_detector_unknown", detector=rule.detector, rule_id=rule.id)
            _unverifiable(rule, scope, enforced, dry_run, "unknown detector")
            continue
        try:
            findings = await asyncio.wait_for(
                asyncio.to_thread(detector.detect, result.text, rule.options, threshold=rule.threshold),
                timeout=settings.guardrails_detector_timeout_seconds,
            )
        # enterprise-gate: broad-except-ok reason=a-failing-detector-fails-closed-when-enforced-in-strict-else-logged
        except Exception as exc:
            logger.error(
                "guardrail_detector_failed", detector=rule.detector, rule_id=rule.id, error_type=type(exc).__name__
            )
            _unverifiable(rule, scope, enforced, dry_run, type(exc).__name__)
            continue
        if not findings:
            continue
        score = max(f.score for f in findings)
        if score < rule.threshold:
            continue
        outcome = Outcome(
            rule_id=rule.id,
            rule_name=rule.name,
            stage=stage,
            detector=rule.detector,
            action=rule.action,
            findings=len(findings),
            score=round(score, 4),
            kinds=sorted({f.kind for f in findings}),
            applied=applying,
        )
        if rule.action == "block":
            outcome.blocked = applying
            if applying:
                result.allowed = False
                blocker = blocker or rule
        elif rule.action in TRANSFORMS and applying:
            result.text, token_map = apply_transform(result.text, findings, rule.action, counters)
            result.token_map.update(token_map)
            outcome.transformed = True
        result.outcomes.append(outcome)
        if not dry_run:
            _meter(stage, rule.detector, rule.action, mode)
            logger.info(
                "guardrail_outcome",
                correlation_id=scope.correlation_id,
                stage=stage,
                rule_id=rule.id,
                detector=rule.detector,
                action=rule.action,
                findings=len(findings),
                kinds=outcome.kinds,
                applied=outcome.applied,
                mode=mode,
            )
            if enforced and rule.action != "flag":
                await _audit_outcome(scope, outcome)
    if blocker is not None and not dry_run:
        raise GuardrailBlocked(
            f"Guardrails: {stage} blocked by rule {blocker.name} ({blocker.detector}).",
            stage=stage,
            correlation_id=scope.correlation_id,
            rule_id=blocker.id,
            rule_name=blocker.name,
        )
    return result


def blocked_run_result(exc: GuardrailBlocked) -> dict[str, Any]:
    """Runner result for a run a guardrail blocked (the shape of a failed run)."""
    return {
        "status": "guardrail_blocked",
        "output": {},
        "confidence": 0.0,
        "reasoning_trace": [exc.reason],
        "tool_calls_log": [],
        "tool_calls": [],
        "hitl_trigger": "",
        "error": exc.reason,
        "error_code": ERROR_CODE,
        "guardrail": exc.to_error()["guardrail"],
        "performance": {"total_latency_ms": 0, "llm_tokens_used": 0, "llm_cost_usd": 0.0},
    }


async def report_section(tenant_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
    """The guardrail section of the compliance evidence package: mode, rules in effect, outcomes over the window."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from core.database import get_tenant_session
    from core.models.audit import AuditLog

    section: dict[str, Any] = {
        "control_id": "AI-GR-1",
        "hooks_enabled": bool(settings.guardrails_hooks_enabled),
        "status": "collected",
    }
    try:
        section["enforcing"] = await enforcing(tenant_id)
    # enterprise-gate: broad-except-ok reason=unreadable-flag-degrades-to-unknown-in-the-evidence-package-logged
    except Exception as exc:
        logger.warning("guardrail_evidence_flag_unreadable", error_type=type(exc).__name__)
        section["enforcing"] = None
        section["enforcing_error"] = type(exc).__name__
    try:
        # The evidence describes the stored rules, so this read bypasses the shared cache.
        rules = await _load_rules(tenant_id)
        section["rules"] = len(rules)
        section["rules_by_stage"] = {
            stage: sum(1 for r in rules if r.stage == stage) for stage in sorted({r.stage for r in rules})
        }
    # enterprise-gate: broad-except-ok reason=unreadable-rules-degrade-to-unknown-in-the-evidence-package-logged
    except Exception as exc:
        logger.warning("guardrail_evidence_rules_unreadable", error_type=type(exc).__name__)
        section["rules"] = None
        section["rules_error"] = type(exc).__name__
    since = datetime.now(UTC) - timedelta(days=days)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    select(AuditLog.outcome, func.count())
                    .where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.event_type == "guardrail.outcome",
                        AuditLog.created_at >= since,
                    )
                    .group_by(AuditLog.outcome)
                )
            ).all()
        section["outcomes"] = {"window_days": days, **{str(row[0]): int(row[1]) for row in rows}}
    # enterprise-gate: broad-except-ok reason=unreadable-audit-rows-degrade-to-unknown-in-the-evidence-package-logged
    except Exception as exc:
        logger.warning("guardrail_evidence_outcomes_unreadable", error_type=type(exc).__name__)
        section["outcomes"] = None
        section["outcomes_error"] = type(exc).__name__
    return section


def _unverifiable(rule: Rule, scope: _Scope, enforced: bool, dry_run: bool, why: str) -> None:
    """An enforced rule whose detector gave no answer refuses the stage in a strict runtime."""
    if enforced and not dry_run and rule.action != "flag" and is_strict_runtime_env(settings.env):
        raise GuardrailBlocked(
            f"Guardrails: rule {rule.name} could not be evaluated ({why}); refusing the {scope.stage}.",
            stage=scope.stage,
            correlation_id=scope.correlation_id,
            rule_id=rule.id,
            rule_name=rule.name,
        )


async def _audit_outcome(scope: _Scope, outcome: Outcome) -> None:
    """A signed audit row for an applied block or transform; a write failure is logged, never raised."""
    if scope.tenant_id is None:
        return
    try:
        from core.database import get_tenant_session

        entry = _audit_entry(
            scope.tenant_id,
            actor_id="guardrails",
            actor_type="system",
            event="outcome",
            resource_id=outcome.rule_id,
            outcome="blocked" if outcome.blocked else "transformed",
            details={**outcome.to_dict(), "correlation_id": scope.correlation_id, "agent_id": scope.agent_id},
            trace_id=scope.correlation_id,
        )
        async with get_tenant_session(scope.tenant_id) as session:
            session.add(entry)
    # enterprise-gate: broad-except-ok reason=audit-write-failure-is-logged-and-the-applied-action-stands
    except Exception as exc:
        logger.warning(
            "guardrail_audit_write_failed", error_type=type(exc).__name__, correlation_id=scope.correlation_id
        )


# ---------------------------------------------------------------------------
# Rule changes (audited)
# ---------------------------------------------------------------------------


def _audit_entry(
    tenant_id: uuid.UUID,
    *,
    actor_id: str,
    event: str,
    resource_id: str,
    details: dict[str, Any],
    actor_type: str = "user",
    outcome: str = "success",
    trace_id: str = "",
) -> Any:
    from core.models.audit import AuditLog
    from core.tool_gateway.audit_logger import sign_audit_record

    entry: dict[str, Any] = {
        "tenant_id": tenant_id,
        "event_type": f"guardrail_rule.{event}" if actor_type == "user" else f"guardrail.{event}",
        "actor_type": actor_type,
        "actor_id": actor_id,
        "agent_id": None,
        "workflow_run_id": None,
        "resource_type": "guardrail_rule",
        "resource_id": resource_id,
        "action": event,
        "outcome": outcome,
        "details": details,
        "trace_id": trace_id,
        "created_at": datetime.now(UTC),
    }
    entry["signature"] = sign_audit_record(entry, settings.secret_key.encode())
    return AuditLog(**entry)


async def set_rule(tenant_id: uuid.UUID, *, actor_id: str, **fields: Any) -> Rule:
    """Create a rule and write its audit row in the same transaction."""
    from core.database import get_tenant_session
    from core.models.guardrail_rule import GuardrailRule

    clean = validate_rule_fields(fields)
    row = GuardrailRule(tenant_id=tenant_id, created_by=actor_id, **clean)
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        rule = _rule(row)
        session.add(
            _audit_entry(tenant_id, actor_id=actor_id, event="set", resource_id=rule.id, details=rule.to_dict())
        )
    await invalidate(tenant_id)
    logger.info("guardrail_rule_set", rule_id=rule.id, actor_id=actor_id)
    return rule


async def update_rule(
    tenant_id: uuid.UUID, rule_id: uuid.UUID, *, actor_id: str, changes: dict[str, Any]
) -> Rule | None:
    """Apply ``changes`` to a rule (None when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.guardrail_rule import GuardrailRule

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(GuardrailRule).where(GuardrailRule.id == rule_id, GuardrailRule.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        current = _rule(row).to_dict()
        applied = {k: v for k, v in changes.items() if k in current}
        clean = validate_rule_fields({**current, **applied})
        for name, value in clean.items():
            setattr(row, name, value)
        row.updated_by = actor_id
        row.updated_at = datetime.now(UTC)
        await session.flush()
        rule = _rule(row)
        session.add(
            _audit_entry(
                tenant_id,
                actor_id=actor_id,
                event="update",
                resource_id=rule.id,
                details={"changes": applied, "rule": rule.to_dict()},
            )
        )
    await invalidate(tenant_id)
    logger.info("guardrail_rule_updated", rule_id=rule.id, actor_id=actor_id)
    return rule


async def delete_rule(tenant_id: uuid.UUID, rule_id: uuid.UUID, *, actor_id: str) -> bool:
    """Delete a rule (False when it does not exist) and write the audit row."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.guardrail_rule import GuardrailRule

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(GuardrailRule).where(GuardrailRule.id == rule_id, GuardrailRule.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        rule = _rule(row)
        await session.delete(row)
        session.add(
            _audit_entry(tenant_id, actor_id=actor_id, event="delete", resource_id=rule.id, details=rule.to_dict())
        )
    await invalidate(tenant_id)
    logger.info("guardrail_rule_deleted", rule_id=rule.id, actor_id=actor_id)
    return True
