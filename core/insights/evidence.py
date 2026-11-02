# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Run evidence (Level 4): one run fact and the records around it.

A run fact is found by its id and its ``started_at`` (the table is
partitioned by month, so the instant bounds the read to one partition). Around
it: the model gateway records with its correlation id, the trace spans with
its trace id (a flat list, longest first; the waterfall is Phase 3), the
approvals raised for it (by correlation id or workflow run) and the audit rows
with its trace id or workflow run. Every read is in the caller's tenant
session; a fact outside the caller's domains or company is not found (404),
never shown. No output text, prompt or tool argument is returned: the
evidence is ids, timings, statuses, tokens and cost.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from core.insights.explorers import jsonable
from core.insights.metrics import DEBUG_ENTRYPOINT, MetricContext, money

MAX_RECORDS = 50
MAX_SPANS = 200
# ``at`` may be off by this much (rounding in links); the read stays inside one or two partitions.
AT_TOLERANCE = timedelta(seconds=1)
# Approvals and audit rows are matched on unindexed JSON fields, so only within this window after the run started
# (an approval may be decided days later, but it is raised during the run).
RELATED_BEFORE = timedelta(minutes=5)
RELATED_AFTER = timedelta(days=2)


class EvidenceNotFoundError(LookupError):
    """No run fact with that id at that instant that this caller may see."""


async def run_evidence(ctx: MetricContext, fact_id: uuid.UUID, at: datetime) -> dict[str, Any]:
    from sqlalchemy import or_, select

    from core.insights.explore_sources import user_names
    from core.insights.metrics import agent_names
    from core.models.audit import AuditLog as A
    from core.models.hitl import HITLQueue as H
    from core.models.insights import RunFact as F
    from core.models.model_gateway_record import ModelGatewayRecord as G
    from core.models.run_span import RunSpan as S

    clauses = [
        F.tenant_id == ctx.tenant_id,
        F.id == fact_id,
        F.started_at >= at - AT_TOLERANCE,
        F.started_at <= at + AT_TOLERANCE,
        F.entrypoint != DEBUG_ENTRYPOINT,
    ]
    if ctx.company_id is not None:
        clauses.append(F.company_id == ctx.company_id)
    if ctx.domains is not None:
        clauses.append(F.domain.in_(list(ctx.domains)))
    fact = (await ctx.session.execute(select(F).where(*clauses))).scalar_one_or_none()
    if fact is None:
        raise EvidenceNotFoundError(str(fact_id))
    names = await agent_names(ctx, [fact.agent_id] if fact.agent_id else [])
    run: dict[str, Any] = {
        "id": str(fact.id),
        "run_key": fact.run_key,
        "started_at": fact.started_at,
        "agent": names.get(str(fact.agent_id)) if fact.agent_id else fact.agent_ref,
        "agent_id": str(fact.agent_id) if fact.agent_id else None,
        "agent_type": fact.agent_type,
        "agent_version": fact.agent_version,
        "domain": fact.domain,
        "entrypoint": fact.entrypoint,
        "task_type": fact.task_type,
        "use_case": fact.use_case,
        "environment": fact.environment,
        "status": fact.status,
        "error_class": fact.error_class,
        "duration_ms": fact.duration_ms,
        "confidence": fact.confidence,
        "confidence_floor": fact.confidence_floor,
        "hitl_required": fact.hitl_required,
        "outcome_validated": fact.outcome_validated,
        "tokens": {
            "input": fact.tokens_in,
            "output": fact.tokens_out,
            "cached": fact.tokens_cached,
            "total": fact.tokens_total,
        },
        "cost_usd": money(fact.cost_usd, 6),
        "provider": fact.provider,
        "model": fact.model,
        "data_quality": fact.data_quality,
        "correlation_id": fact.correlation_id,
        "trace_id": fact.trace_id,
        "workflow_run_id": str(fact.workflow_run_id) if fact.workflow_run_id else None,
        "step_id": fact.step_id,
    }
    gateway: list[dict[str, Any]] = []
    if fact.correlation_id:
        for g in (
            await ctx.session.execute(
                select(G)
                .where(G.tenant_id == ctx.tenant_id, G.correlation_id == fact.correlation_id)
                .order_by(G.created_at.asc())
                .limit(MAX_RECORDS)
            )
        ).scalars():
            gateway.append(
                {
                    "created_at": g.created_at,
                    "provider": g.provider,
                    "model": g.model,
                    "requested_model": g.requested_model,
                    "fallback_from": g.fallback_from,
                    "restricted": g.restricted,
                    "outcome": g.outcome,
                    "error_type": g.error_type,
                    "latency_ms": g.latency_ms,
                    "admission_wait_ms": g.admission_wait_ms,
                    "input_tokens": g.input_tokens,
                    "output_tokens": g.output_tokens,
                    "cost_usd": money(g.cost_usd, 6),
                    "signed": bool(g.signature),
                }
            )
    spans: list[dict[str, Any]] = []
    if fact.trace_id:
        for s in (
            await ctx.session.execute(
                select(S)
                .where(S.tenant_id == ctx.tenant_id, S.trace_id == fact.trace_id)
                .order_by(S.duration_ms.desc())
                .limit(MAX_SPANS)
            )
        ).scalars():
            spans.append(
                {
                    "name": s.name,
                    "kind": s.kind,
                    "status": s.status,
                    "started_at": s.started_at,
                    "duration_ms": s.duration_ms,
                    "span_id": s.span_id,
                    "parent_span_id": s.parent_span_id,
                }
            )
    approvals: list[dict[str, Any]] = []
    links = []
    if fact.correlation_id:
        links.append(H.context["correlation_id"].astext == fact.correlation_id)
    if fact.workflow_run_id:
        links.append(H.workflow_run_id == fact.workflow_run_id)
    if links:
        items = (
            (
                await ctx.session.execute(
                    select(H)
                    .where(
                        H.tenant_id == ctx.tenant_id,
                        H.created_at >= fact.started_at - RELATED_BEFORE,
                        H.created_at <= fact.started_at + RELATED_AFTER,
                        or_(*links),
                    )
                    .order_by(H.created_at.asc())
                    .limit(MAX_RECORDS)
                )
            )
            .scalars()
            .all()
        )
        people = await user_names(ctx, (i.decision_by for i in items if i.decision_by))
        for item in items:
            approvals.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "trigger_type": item.trigger_type,
                    "status": item.status,
                    "decision": item.decision,
                    "decided_by": people.get(str(item.decision_by), "User") if item.decision_by else None,
                    "created_at": item.created_at,
                    "decision_at": item.decision_at,
                    "link": f"/dashboard/approvals?id={item.id}",
                }
            )
    audit: list[dict[str, Any]] = []
    audit_links = []
    if fact.trace_id:
        audit_links.append(A.trace_id == fact.trace_id)
    if fact.workflow_run_id:
        audit_links.append(A.workflow_run_id == fact.workflow_run_id)
    if fact.correlation_id:
        audit_links.append(A.details["correlation_id"].astext == fact.correlation_id)
    if audit_links:
        for a in (
            await ctx.session.execute(
                select(A.created_at, A.event_type, A.action, A.outcome, A.signature)
                .where(
                    A.tenant_id == ctx.tenant_id,
                    A.created_at >= fact.started_at - RELATED_BEFORE,
                    A.created_at <= fact.started_at + RELATED_AFTER,
                    or_(*audit_links),
                )
                .order_by(A.created_at.asc())
                .limit(MAX_RECORDS)
            )
        ).all():
            audit.append(
                {
                    "created_at": a.created_at,
                    "event_type": a.event_type,
                    "action": (a.action or "")[:200],
                    "outcome": a.outcome,
                    "signed": bool(a.signature),
                }
            )
    return jsonable(
        {
            "run": run,
            "gateway_records": gateway,
            "spans": spans,
            "approvals": approvals,
            "audit": audit,
            "links": {
                **({"agent": f"/dashboard/agents/{fact.agent_id}"} if fact.agent_id else {}),
            },
            "limits": {"records": MAX_RECORDS, "spans": MAX_SPANS},
        }
    )


def parse_at(raw: str | None) -> datetime | None:
    """The ``at`` query value as an aware instant, or None when it is not an ISO timestamp with an offset."""
    if not raw:
        return None
    # A "+" in an unencoded query string arrives as a space.
    try:
        value = datetime.fromisoformat(raw.strip().replace(" ", "+").replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None
