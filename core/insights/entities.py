# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights Level 3 entity pages: one agent, workflow, connector, company, user or cost centre.

Each page has the entity's header facts, a timeline of its lifecycle from the
audit log (and, for an agent, its lifecycle events), metric cards scoped to the
entity for the period, and links to the explorers pre-filtered to it. Every
read runs in the tenant session (forced row-level security); who may open which
kind follows the explorers and views behind it, and an entity outside the
caller's reach (another tenant's, or outside the caller's domains) is not found.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from core.insights import registry
from core.insights.metrics import MetricContext, day_bounds, money

KINDS: tuple[str, ...] = ("agent", "workflow", "connector", "company", "user", "cost_centre")
TIMELINE_LIMIT = 50

# Who may open each kind (administrators open every kind).
KIND_ROLES: dict[str, frozenset[str]] = {
    "agent": frozenset({"cfo", "chro", "cmo", "coo", "developer", "domain_lead", "analyst", "auditor"}),
    "workflow": frozenset({"coo"}),
    "connector": frozenset({"coo"}),
    "company": frozenset({"cfo", "coo", "auditor"}),
    "user": frozenset({"auditor"}),
    "cost_centre": frozenset({"cfo"}),
}
RUN_CARDS = ("runs.volume", "runs.success_rate", "runs.latency", "runs.confidence")
EXPLORE = "/dashboard/insights/explore"


class EntityNotFoundError(LookupError):
    """No such entity in the caller's reach."""


class EntityForbiddenError(PermissionError):
    """The caller's role may not open this kind of entity."""


@dataclass(frozen=True)
class EntityCaller:
    role: str
    is_admin: bool
    domains: list[str] | None


def may_open(kind: str, caller: EntityCaller) -> bool:
    return caller.is_admin or caller.role in KIND_ROLES.get(kind, frozenset())


def _q(value: str) -> str:
    return quote(value, safe="")


async def _cards(ctx: MetricContext, keys: tuple[str, ...], **changes: Any) -> list[dict[str, Any]]:
    scoped = replace(ctx, cache={}, **changes)
    return [await registry.resolve(registry.METRICS[key], scoped) for key in keys]


async def _timeline(ctx: MetricContext, *clauses: Any) -> list[dict[str, Any]]:
    from sqlalchemy import or_, select

    from core.models.audit import AuditLog as A

    rows = (
        await ctx.session.execute(
            select(A.created_at, A.event_type, A.action, A.outcome, A.actor_type, A.actor_id)
            .where(A.tenant_id == ctx.tenant_id, or_(*clauses))
            .order_by(A.created_at.desc())
            .limit(TIMELINE_LIMIT)
        )
    ).all()
    return [
        {
            "at": at.isoformat() if at else None,
            "event": event,
            "action": action,
            "outcome": outcome,
            "actor_type": actor_type,
            "actor": actor,
        }
        for at, event, action, outcome, actor_type, actor in rows
    ]


def _fact(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, Decimal):
        value = float(value)
    elif isinstance(value, uuid.UUID):
        value = str(value)
    return {"key": key, "value": value}


def _period_bounds(ctx: MetricContext) -> tuple[datetime, datetime]:
    return day_bounds(ctx.period.start, ctx.zone)[0], day_bounds(ctx.period.end, ctx.zone)[1]


# ------------------------------------------------------------------ agent


async def _agent(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.agent import Agent, AgentCostLedger, AgentLifecycleEvent
    from core.models.audit import AuditLog as A

    clauses = [Agent.tenant_id == ctx.tenant_id, Agent.id == entity_id]
    if caller.domains is not None:
        clauses += [Agent.domain.in_(list(caller.domains)), Agent.visibility == "tenant"]
    agent = (await ctx.session.execute(select(Agent).where(*clauses))).scalar_one_or_none()
    if agent is None:
        raise EntityNotFoundError(entity_id)
    lo, hi = _period_bounds(ctx)
    spend = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(AgentCostLedger.cost_usd), 0)).where(
                AgentCostLedger.tenant_id == ctx.tenant_id,
                AgentCostLedger.agent_id == entity_id,
                AgentCostLedger.period_date >= lo.replace(tzinfo=None),
                AgentCostLedger.period_date < hi.replace(tzinfo=None),
            )
        )
    ).scalar_one()
    events = (
        await ctx.session.execute(
            select(AgentLifecycleEvent)
            .where(AgentLifecycleEvent.tenant_id == ctx.tenant_id, AgentLifecycleEvent.agent_id == entity_id)
            .order_by(AgentLifecycleEvent.created_at.desc())
            .limit(TIMELINE_LIMIT)
        )
    ).scalars()
    lifecycle = [
        {
            "at": e.created_at.isoformat() if e.created_at else None,
            "event": "agent.lifecycle",
            "action": f"{e.from_status} -> {e.to_status}",
            "outcome": e.triggered_by,
            "actor_type": "user" if e.triggered_by_user else "system",
            "actor": str(e.triggered_by_user) if e.triggered_by_user else e.triggered_by,
        }
        for e in events
    ]
    audit = await _timeline(
        ctx,
        A.agent_id == entity_id,
        A.resource_id == str(entity_id),
        (A.actor_type == "agent") & (A.actor_id == str(entity_id)),
    )
    timeline = sorted([*lifecycle, *audit], key=lambda r: r["at"] or "", reverse=True)[:TIMELINE_LIMIT]
    agent_filter = {"agent": (str(entity_id),)}
    return {
        "title": agent.name,
        "subtitle": f"{agent.agent_type} · {agent.domain}",
        "facts": [
            _fact("status", agent.status),
            _fact("agent_type", agent.agent_type),
            _fact("domain", agent.domain),
            _fact("version", getattr(agent, "version", None)),
            _fact("created_at", agent.created_at),
            _fact("spend_usd", money(spend)),
        ],
        "cards": await _cards(ctx, RUN_CARDS, filters=agent_filter),
        "timeline": timeline,
        "links": {
            "runs": f"{EXPLORE}/runs?filter%5Bagent_id%5D={_q(str(entity_id))}",
            "approvals": f"{EXPLORE}/approvals?filter%5Bagent_id%5D={_q(str(entity_id))}",
            "quality": f"{EXPLORE}/quality?filter%5Bagent_id%5D={_q(str(entity_id))}",
            "manage": f"/dashboard/agents/{entity_id}",
        },
    }


# ------------------------------------------------------------------ workflow


async def _workflow(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.audit import AuditLog as A
    from core.models.workflow import WorkflowDefinition as D
    from core.models.workflow import WorkflowRun as W

    clauses = [D.tenant_id == ctx.tenant_id, D.id == entity_id]
    if caller.domains is not None:
        clauses.append(D.domain.in_(list(caller.domains)))
    workflow = (await ctx.session.execute(select(D).where(*clauses))).scalar_one_or_none()
    if workflow is None:
        raise EntityNotFoundError(entity_id)
    lo, hi = _period_bounds(ctx)
    run_clauses = [W.tenant_id == ctx.tenant_id, W.workflow_def_id == entity_id, W.started_at >= lo, W.started_at < hi]
    if ctx.company_id is not None:
        run_clauses.append(W.company_id == ctx.company_id)
    by_status = (
        await ctx.session.execute(select(W.status, func.count()).where(*run_clauses).group_by(W.status))
    ).all()
    durations = (
        await ctx.session.execute(
            select(func.avg(func.extract("epoch", W.completed_at - W.started_at)))
            .where(*run_clauses, W.completed_at.is_not(None))
        )
    ).scalar_one()
    counts = {str(status): int(n) for status, n in by_status}
    total = sum(counts.values())
    card = {
        "key": "workflow.runs",
        "title": "Workflow runs",
        "unit": "count",
        "value": total if total else None,
        "data_quality": "measured" if total else "no_data",
        "rows": [{"label": k, "value": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])],
        "source": "workflow_runs",
    }
    if not total:
        card["hint"] = "This workflow did not run in the period."
    return {
        "title": workflow.name,
        "subtitle": workflow.domain or "",
        "facts": [
            _fact("domain", workflow.domain),
            _fact("version", workflow.version),
            _fact("trigger", workflow.trigger_type),
            _fact("active", workflow.is_active),
            _fact("created_at", workflow.created_at),
            _fact("mean_duration_s", round(float(durations), 1) if durations is not None else None),
        ],
        "cards": [card],
        "timeline": await _timeline(ctx, A.resource_id == str(entity_id)),
        "links": {
            "workflows": f"{EXPLORE}/workflows?filter%5Bworkflow_id%5D={_q(str(entity_id))}",
            "manage": f"/dashboard/workflows/{entity_id}",
        },
    }


# ------------------------------------------------------------------ connector


async def _connector(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import JSONB

    from core.models.agent import Agent
    from core.models.audit import AuditLog as A
    from core.models.connector import Connector
    from core.models.connector_config import ConnectorConfig as C

    connector = (
        await ctx.session.execute(
            select(Connector).where(Connector.tenant_id == ctx.tenant_id, Connector.id == entity_id)
        )
    ).scalar_one_or_none()
    if connector is None:
        raise EntityNotFoundError(entity_id)
    configs = (
        await ctx.session.execute(
            select(C.company_id, C.health_status, C.last_health_check, C.status).where(
                C.tenant_id == ctx.tenant_id, C.connector_name == connector.name
            )
        )
    ).all()
    agents = (
        await ctx.session.execute(
            select(Agent.id, Agent.name).where(
                Agent.tenant_id == ctx.tenant_id,
                Agent.connector_ids.op("@>")(cast([str(entity_id)], JSONB)),
            )
        )
    ).all()
    health = [
        {
            "label": str(company or "tenant"),
            "health": health_status or "unknown",
            "status": status,
            "checked_at": checked.isoformat() if checked else None,
        }
        for company, health_status, checked, status in configs
    ]
    unhealthy = sum(1 for row in health if row["health"] == "unhealthy")
    card = {
        "key": "connector.health",
        "title": "Connector health",
        "unit": "count",
        "value": unhealthy if health else None,
        "state": ("act" if unhealthy else "ok") if health else None,
        "data_quality": "measured" if health else "no_data",
        "rows": health,
        "source": "connector_configs",
    }
    if not health:
        card["hint"] = "This connector is not configured for any company yet."
    return {
        "title": connector.name,
        "subtitle": connector.category,
        "facts": [
            _fact("category", connector.category),
            _fact("auth_type", connector.auth_type),
            _fact("status", connector.status),
            _fact("health_check_at", connector.health_check_at),
            _fact("agents", len(agents)),
            _fact("created_at", connector.created_at),
        ],
        "cards": [card],
        "agents": [{"id": str(agent_id), "name": name} for agent_id, name in agents],
        "timeline": await _timeline(ctx, A.resource_id == str(entity_id)),
        "links": {
            "connectors": f"{EXPLORE}/connectors?filter%5Bconnector%5D={_q(connector.name)}",
            "manage": f"/dashboard/connectors/{entity_id}",
        },
    }


# ------------------------------------------------------------------ company


async def _company(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.audit import AuditLog as A
    from core.models.company import Company
    from core.models.compliance_deadline import ComplianceDeadline as D

    company = (
        await ctx.session.execute(select(Company).where(Company.tenant_id == ctx.tenant_id, Company.id == entity_id))
    ).scalar_one_or_none()
    if company is None:
        raise EntityNotFoundError(entity_id)
    today = ctx.today
    overdue = (
        await ctx.session.execute(
            select(func.count()).where(
                D.tenant_id == ctx.tenant_id, D.company_id == entity_id, D.filed.is_(False), D.due_date < today
            )
        )
    ).scalar_one()
    return {
        "title": company.name,
        "subtitle": company.industry or "",
        "facts": [
            _fact("active", company.is_active),
            _fact("industry", company.industry),
            _fact("state_code", company.state_code),
            _fact("health_score", company.client_health_score),
            _fact("overdue_deadlines", int(overdue or 0)),
            _fact("created_at", company.created_at),
        ],
        "cards": await _cards(ctx, ("runs.volume", "runs.success_rate", "cost.spend"), company_id=entity_id),
        "timeline": await _timeline(ctx, A.company_id == entity_id, A.resource_id == str(entity_id)),
        "links": {
            "runs": f"{EXPLORE}/runs?company_id={_q(str(entity_id))}",
            "manage": f"/dashboard/companies/{entity_id}",
        },
    }


# ------------------------------------------------------------------ user


async def _user(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.audit import AuditLog as A
    from core.models.hitl import HITLQueue as H
    from core.models.user import User

    user = (
        await ctx.session.execute(select(User).where(User.tenant_id == ctx.tenant_id, User.id == entity_id))
    ).scalar_one_or_none()
    if user is None:
        raise EntityNotFoundError(entity_id)
    lo, hi = _period_bounds(ctx)
    decided = (
        await ctx.session.execute(
            select(H.decision, func.count())
            .where(H.tenant_id == ctx.tenant_id, H.decision_by == entity_id, H.decision_at >= lo, H.decision_at < hi)
            .group_by(H.decision)
        )
    ).all()
    counts = {str(decision or "none"): int(n) for decision, n in decided}
    total = sum(counts.values())
    card = {
        "key": "user.decisions",
        "title": "Approvals decided",
        "unit": "count",
        "value": total if total else None,
        "data_quality": "measured" if total else "no_data",
        "rows": [{"label": k, "value": v} for k, v in sorted(counts.items())],
        "source": "hitl_queue",
    }
    if not total:
        card["hint"] = "This person decided no approval in the period."
    return {
        "title": user.name or user.email,
        "subtitle": user.role,
        "facts": [
            _fact("role", user.role),
            _fact("domain", user.domain),
            _fact("status", user.status),
            _fact("mfa_enabled", user.mfa_enabled),
            _fact("last_login_at", user.last_login_at),
        ],
        "cards": [card],
        "timeline": await _timeline(ctx, A.actor_id == str(entity_id), A.resource_id == str(entity_id)),
        # The approvals and audit explorers do not filter by person; the timeline is this person's audit trail.
        "links": {"approvals": f"{EXPLORE}/approvals", "audit": f"{EXPLORE}/audit"},
    }


# ------------------------------------------------------------------ cost centre


async def _cost_centre(ctx: MetricContext, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    from sqlalchemy import func, select

    from core.models.agent import Agent, AgentCostLedger
    from core.models.organization import CostCenter

    centre = (
        await ctx.session.execute(
            select(CostCenter).where(CostCenter.tenant_id == ctx.tenant_id, CostCenter.id == entity_id)
        )
    ).scalar_one_or_none()
    if centre is None:
        raise EntityNotFoundError(entity_id)
    agents = (
        await ctx.session.execute(
            select(Agent.id, Agent.name).where(Agent.tenant_id == ctx.tenant_id, Agent.cost_center_id == entity_id)
        )
    ).all()
    agent_ids = [agent_id for agent_id, _ in agents]
    lo, hi = _period_bounds(ctx)
    spend = Decimal("0")
    if agent_ids:
        spend = (
            await ctx.session.execute(
                select(func.coalesce(func.sum(AgentCostLedger.cost_usd), 0)).where(
                    AgentCostLedger.tenant_id == ctx.tenant_id,
                    AgentCostLedger.agent_id.in_(agent_ids),
                    AgentCostLedger.period_date >= lo.replace(tzinfo=None),
                    AgentCostLedger.period_date < hi.replace(tzinfo=None),
                )
            )
        ).scalar_one()
    cards = (
        await _cards(ctx, ("runs.volume", "runs.success_rate"), filters={"agent": tuple(str(a) for a in agent_ids)})
        if agent_ids
        else []
    )
    budget = centre.budget_limit
    return {
        "title": centre.name,
        "subtitle": centre.code,
        "facts": [
            _fact("code", centre.code),
            _fact("agents", len(agent_ids)),
            _fact("spend_usd", money(spend)),
            _fact("budget_limit", budget),
            _fact("budget_used_pct", round(float(spend) / float(budget) * 100, 1) if budget else None),
            _fact("fiscal_year", centre.fiscal_year),
        ],
        "cards": cards,
        "agents": [{"id": str(agent_id), "name": name} for agent_id, name in agents],
        "timeline": [],
        "hint": None if agent_ids else "No agent is assigned to this cost centre.",
        "links": {"cost": f"{EXPLORE}/cost"},
    }


_RESOLVERS = {
    "agent": _agent,
    "workflow": _workflow,
    "connector": _connector,
    "company": _company,
    "user": _user,
    "cost_centre": _cost_centre,
}


async def entity(ctx: MetricContext, kind: str, entity_id: uuid.UUID, caller: EntityCaller) -> dict[str, Any]:
    """The entity page for ``kind``/``entity_id``; raises EntityForbiddenError or EntityNotFoundError."""
    if kind not in _RESOLVERS:
        raise EntityNotFoundError(kind)
    if not may_open(kind, caller):
        raise EntityForbiddenError(kind)
    page = await _RESOLVERS[kind](ctx, entity_id, caller)
    return {"kind": kind, "id": str(entity_id), "period": ctx.period.as_dict(), **page}


# ------------------------------------------------------------------ search (Ctrl/Cmd-K)

SEARCH_LIMIT = 8
SEARCH_MIN = 2
SEARCH_MAX = 64


async def search(ctx: MetricContext, text: str, caller: EntityCaller) -> list[dict[str, Any]]:
    """Agents, workflows, connectors, companies, users and cost centres whose name matches ``text``, and a run by id.

    Each kind is searched only when the caller may open it; agents and workflows keep to the caller's domains.
    """
    from sqlalchemy import or_, select

    from core.models.agent import Agent
    from core.models.company import Company
    from core.models.connector import Connector
    from core.models.insights import RunFact
    from core.models.organization import CostCenter
    from core.models.user import User
    from core.models.workflow import WorkflowDefinition

    needle = text.strip()
    if not (SEARCH_MIN <= len(needle) <= SEARCH_MAX):
        return []
    # LIKE wildcards in the search text match themselves (escaped with a backslash).
    pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    results: list[dict[str, Any]] = []

    async def add(kind: str, rows: Any, label: Any) -> None:
        for row in rows:
            entity_id, title, detail = label(row)
            results.append({"kind": kind, "id": str(entity_id), "title": title, "detail": detail})

    def limit(query: Any) -> Any:
        return query.limit(SEARCH_LIMIT)

    if may_open("agent", caller):
        q = select(Agent.id, Agent.name, Agent.domain).where(
            Agent.tenant_id == ctx.tenant_id, Agent.name.ilike(pattern, escape="\\")
        )
        if caller.domains is not None:
            q = q.where(Agent.domain.in_(list(caller.domains)), Agent.visibility == "tenant")
        await add("agent", (await ctx.session.execute(limit(q.order_by(Agent.name)))).all(), lambda r: r)
    if may_open("workflow", caller):
        q = select(WorkflowDefinition.id, WorkflowDefinition.name, WorkflowDefinition.domain).where(
            WorkflowDefinition.tenant_id == ctx.tenant_id, WorkflowDefinition.name.ilike(pattern, escape="\\")
        )
        if caller.domains is not None:
            q = q.where(WorkflowDefinition.domain.in_(list(caller.domains)))
        found = (await ctx.session.execute(limit(q.order_by(WorkflowDefinition.name)))).all()
        await add("workflow", found, lambda r: r)
    if may_open("connector", caller):
        q = select(Connector.id, Connector.name, Connector.category).where(
            Connector.tenant_id == ctx.tenant_id, Connector.name.ilike(pattern, escape="\\")
        )
        await add("connector", (await ctx.session.execute(limit(q.order_by(Connector.name)))).all(), lambda r: r)
    if may_open("company", caller):
        q = select(Company.id, Company.name, Company.industry).where(
            Company.tenant_id == ctx.tenant_id, Company.name.ilike(pattern, escape="\\")
        )
        await add("company", (await ctx.session.execute(limit(q.order_by(Company.name)))).all(), lambda r: r)
    if may_open("user", caller):
        q = select(User.id, User.name, User.role).where(
            User.tenant_id == ctx.tenant_id,
            or_(User.name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\")),
        )
        await add(
            "user",
            (await ctx.session.execute(limit(q.order_by(User.name)))).all(),
            lambda r: (r[0], r[1] or "", r[2]),
        )
    if may_open("cost_centre", caller):
        q = select(CostCenter.id, CostCenter.name, CostCenter.code).where(
            CostCenter.tenant_id == ctx.tenant_id,
            or_(CostCenter.name.ilike(pattern, escape="\\"), CostCenter.code.ilike(pattern, escape="\\")),
        )
        await add("cost_centre", (await ctx.session.execute(limit(q.order_by(CostCenter.name)))).all(), lambda r: r)
    try:
        run_id = uuid.UUID(needle)
    except ValueError:
        run_id = None
    if run_id is not None:
        q = select(RunFact.id, RunFact.agent_type, RunFact.started_at, RunFact.domain).where(
            RunFact.tenant_id == ctx.tenant_id, RunFact.id == run_id
        )
        if caller.domains is not None:
            q = q.where(RunFact.domain.in_(list(caller.domains)))
        for fact_id, agent_type, started_at, _domain in (await ctx.session.execute(q.limit(1))).all():
            results.append(
                {
                    "kind": "run",
                    "id": str(fact_id),
                    "title": agent_type or "run",
                    "detail": started_at.isoformat() if started_at else None,
                    "at": started_at.isoformat() if started_at else None,
                }
            )
    return results
