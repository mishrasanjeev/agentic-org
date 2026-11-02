# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights explorer resolvers: agents, runs, workflows, approvals, cost, quality, connectors and the audit log.

Each resolver reads under the caller's tenant session (forced row-level
security) and the request's filters (period, company, the caller's domains).
Run-level tables (runs, approvals, workflows, audit) are filtered, sorted and
paged in SQL; per-agent tables (agents, cost, quality) and connectors are
built whole (at most ``MAX_SCAN_ROWS`` rows) and paged by
``core/insights/explorers.py``. Nothing is invented: a table with nothing to
show answers ``no_data`` with a hint.

The runs explorer reads raw ``run_facts``, so it covers at most
``MAX_RAW_DAYS`` days: for a longer period it shows the period's last 31 days
and says so.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from core.insights.explorers import MAX_SCAN_ROWS, ExploreContext, ExploreData, sql_page
from core.insights.metrics import (
    DEBUG_ENTRYPOINT,
    MAX_RAW_DAYS,
    UNATTRIBUTED,
    MetricContext,
    Period,
    _agent_scope,
    _approval_scope,
    day_bounds,
    money,
    pct,
    quality_of,
    run_data,
    totals,
)

# The production floor the promote endpoint applies to shadow accuracy (api/v1/agents.py,
# _MIN_PRODUCTION_SHADOW_ACCURACY); a unit test keeps the two equal.
MIN_PRODUCTION_SHADOW_ACCURACY = Decimal("0.600")
# How far back the agents explorer looks for each agent's last run.
IDLE_WINDOW_DAYS = 90
RUNS_LINK = "/dashboard/insights/runs"
AGENT_LINK = "/dashboard/agents"
APPROVALS_LINK = "/dashboard/approvals"
CONNECTOR_LINK = "/dashboard/connectors"
NO_DOMAIN_HINT = "No domain is assigned to you, so there is nothing to show here."


def _no_domains(ctx: ExploreContext) -> bool:
    domains = ctx.metric.domains
    return domains is not None and not domains


def _uuid_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _uuids(values: Iterable[Any]) -> list[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for value in values:
        try:
            out.add(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def run_link(fact_id: Any, started_at: datetime | None) -> str:
    """The run evidence page of one run fact; ``at`` lets the lookup prune month partitions."""
    from urllib.parse import quote

    at = started_at.isoformat() if started_at is not None else ""
    return f"{RUNS_LINK}/{fact_id}?at={quote(at, safe='')}"


async def user_names(ctx: MetricContext, ids: Iterable[Any]) -> dict[str, str]:
    """Display names of this tenant's users by id text. Emails are never shown: a user without a name is 'User'."""
    from sqlalchemy import select

    from core.models.user import User

    wanted = _uuids(ids)
    if not wanted:
        return {}
    rows = await ctx.session.execute(
        select(User.id, User.name).where(User.tenant_id == ctx.tenant_id, User.id.in_(wanted))
    )
    return {str(user_id): (name or "User") for user_id, name in rows}


def _actor_label(raw: Any, names: dict[str, str]) -> str | None:
    if raw is None:
        return None
    text = str(raw)
    return names.get(text, "User" if _uuids([text]) else text)


# ------------------------------------------------------------------ shadow promotion gate


def shadow_gate(agent: Any) -> tuple[bool, str]:
    """Whether a shadow agent passes the promote endpoint's shadow checks, and why not.

    Mirrors ``POST /agents/{id}/promote`` (scored samples, accuracy computed, accuracy at
    the floor, human evidence). Promotion also checks connector readiness and, for a
    changed prompt, that someone other than its author activates it; those are checked
    when promoting.
    """
    from core.feedback.shadow_learning import promotion_evidence_gate, scored_shadow_sample_count

    minimum = int(getattr(agent, "shadow_min_samples", 0) or 0)
    if minimum <= 0:
        return True, "Shadow sample checks are turned off for this agent."
    scored = scored_shadow_sample_count(agent)
    if scored < minimum:
        total = int(getattr(agent, "shadow_sample_count", 0) or 0)
        return False, f"{scored}/{minimum} scored samples ({total} total); the minimum is not met yet."
    accuracy = getattr(agent, "shadow_accuracy_current", None)
    if accuracy is None:
        return False, "Shadow accuracy is not computed yet; run shadow samples first."
    floor = max(Decimal(str(getattr(agent, "shadow_accuracy_floor", None) or "0")), MIN_PRODUCTION_SHADOW_ACCURACY)
    if Decimal(str(accuracy)) < floor:
        return False, f"Shadow accuracy {accuracy} is below the floor {floor}."
    evidence = promotion_evidence_gate(agent)
    if not evidence["ok"]:
        return False, str(evidence["reason"])
    return True, "Passes the shadow checks; promotion also checks connectors and prompt approval."


# ------------------------------------------------------------------ agents


async def _last_runs(ctx: MetricContext) -> dict[str, datetime]:
    from sqlalchemy import func, select

    from core.models.insights import RunFact as F

    lo = ctx.now - timedelta(days=IDLE_WINDOW_DAYS)
    rows = await ctx.session.execute(
        select(F.agent_id, func.max(F.started_at))
        .where(
            F.tenant_id == ctx.tenant_id, F.started_at >= lo, F.entrypoint != DEBUG_ENTRYPOINT, F.agent_id.is_not(None)
        )
        .group_by(F.agent_id)
    )
    return {str(agent_id): started for agent_id, started in rows if started is not None}


def _p95(t: dict[str, Any]) -> float | None:
    from core.insights.buckets import percentile_from_buckets

    return percentile_from_buckets(t["buckets"], 0.95)


async def explore_agents(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import select

    from core.insights.metrics_cost import cost_basis, daily_spend
    from core.models.agent import Agent

    m = ctx.metric
    if _no_domains(ctx):
        return ExploreData([], "agents", "no_data", hint=NO_DOMAIN_HINT)
    agents = (
        await m.session.execute(
            select(
                Agent.id,
                Agent.name,
                Agent.agent_type,
                Agent.domain,
                Agent.status,
                Agent.created_at,
                Agent.shadow_min_samples,
                Agent.shadow_sample_count,
                Agent.shadow_scored_sample_count,
                Agent.shadow_accuracy_current,
                Agent.shadow_accuracy_floor,
                Agent.shadow_feedback_count,
                Agent.shadow_human_confidence_current,
                Agent.prompt_amendments,
            )
            .where(*_agent_scope(m, Agent))
            .order_by(Agent.name)
            .limit(MAX_SCAN_ROWS)
        )
    ).all()
    if not agents:
        return ExploreData(
            [], "agents", "no_data", hint="No agents yet. Create or install agents from the Agents page."
        )
    runs = await run_data(m, m.period, group="agent")
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runs.rows:
        by_agent[row["grp"]].append(row)
    basis = await cost_basis(m)
    spend = await daily_spend(m, m.period.start, m.period.end, group="agent")
    spend_by_agent: dict[str, Decimal] = defaultdict(Decimal)
    for (_day, grp), amount in spend.amounts.items():
        spend_by_agent[grp] += amount
    last_runs = await _last_runs(m)
    rows: list[dict[str, Any]] = []
    for agent in agents:
        agent_id = str(agent.id)
        t = totals(by_agent.get(agent_id, []))
        last = last_runs.get(agent_id)
        since = last or agent.created_at
        idle = max(0, (m.now - since).days) if since is not None else None
        if last is None and idle is not None:
            idle = min(idle, IDLE_WINDOW_DAYS)
        eligible: bool | None = None
        reason: str | None = None
        if agent.status == "shadow":
            eligible, reason = shadow_gate(agent)
        amendments = agent.prompt_amendments if isinstance(agent.prompt_amendments, list) else []
        accuracy = agent.shadow_accuracy_current
        floor = max(Decimal(str(agent.shadow_accuracy_floor or "0")), MIN_PRODUCTION_SHADOW_ACCURACY)
        rows.append(
            {
                "agent": agent.name,
                "agent_id": agent_id,
                "agent_type": agent.agent_type,
                "domain": agent.domain,
                "status": agent.status,
                "runs": int(t["volume"]),
                "success_rate": pct(t["completed"], t["completed"] + t["failed"]),
                "p95_ms": _p95(t) if t["duration_n"] else None,
                "spend": money(spend_by_agent[agent_id]) if agent_id in spend_by_agent else None,
                "last_run_at": last,
                "idle_days": idle,
                "shadow_samples": int(agent.shadow_scored_sample_count or agent.shadow_sample_count or 0),
                "shadow_required": int(agent.shadow_min_samples or 0),
                "shadow_accuracy": round(float(accuracy) * 100, 1) if accuracy is not None else None,
                "accuracy_floor": round(float(floor) * 100, 1),
                "promotion_eligible": eligible,
                "promotion_reason": reason,
                "learned_rules": len(amendments),
                "links": {
                    "agent": f"{AGENT_LINK}/{agent_id}",
                    "runs": f"/dashboard/insights/explore/runs?filter[agent_id]={agent_id}",
                },
            }
        )
    quality = runs.quality() if runs.rows else "measured"
    hint_parts = [
        f"Idle days count from the last run in the last {IDLE_WINDOW_DAYS} days, or from when the agent was created; "
        f"{IDLE_WINDOW_DAYS} means {IDLE_WINDOW_DAYS} or more."
    ]
    if basis.hint:
        hint_parts.append(basis.hint)
    return ExploreData(
        rows,
        f"agents+{runs.source}+{spend.source}",
        quality if quality != "no_data" else "measured",
        hint=" ".join(hint_parts),
        summary={"currency": basis.currency, "agents": len(rows)},
    )


# ------------------------------------------------------------------ runs


def _runs_window(ctx: MetricContext) -> tuple[Period, str | None]:
    period = ctx.period
    if period.days <= MAX_RAW_DAYS:
        return period, None
    start = period.end - timedelta(days=MAX_RAW_DAYS - 1)
    hint = (
        f"Run rows are read one by one, so this table covers the last {MAX_RAW_DAYS} days of the period "
        f"({start.isoformat()} to {period.end.isoformat()}). Pick a shorter range to see earlier runs."
    )
    return Period(start, period.end, period.granularity), hint


async def explore_runs(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import String, cast, select

    from core.insights.metrics import agent_names
    from core.models.insights import RunFact as F

    m = ctx.metric
    if _no_domains(ctx):
        return ExploreData([], "run_facts", "no_data", hint=NO_DOMAIN_HINT, paged=True, total=0)
    window, hint = _runs_window(m)
    lo = day_bounds(window.start, m.zone)[0]
    hi = day_bounds(window.end, m.zone)[1]
    clauses = [F.tenant_id == m.tenant_id, F.entrypoint != DEBUG_ENTRYPOINT, F.started_at >= lo, F.started_at < hi]
    if m.company_id is not None:
        clauses.append(F.company_id == m.company_id)
    if m.domains is not None:
        clauses.append(F.domain.in_(list(m.domains)))
    expressions = {
        "started_at": F.started_at,
        "agent_id": cast(F.agent_id, String),
        "agent_type": F.agent_type,
        "domain": F.domain,
        "task_type": F.task_type,
        "entrypoint": F.entrypoint,
        "status": F.status,
        "duration_ms": F.duration_ms,
        "tokens_total": F.tokens_total,
        "cost_usd": F.cost_usd,
        "hitl_required": F.hitl_required,
        "error_class": F.error_class,
        "data_quality": F.data_quality,
    }
    statement = select(F).where(*clauses)
    found, total = await sql_page(m.session, statement, ctx.query, expressions, F.id)
    facts = [row[0] for row in found]
    names = await agent_names(m, (f.agent_id for f in facts if f.agent_id is not None))
    rows = [
        {
            "started_at": f.started_at,
            "agent": names.get(str(f.agent_id)) if f.agent_id else f.agent_ref,
            "agent_id": _uuid_text(f.agent_id),
            "agent_type": f.agent_type,
            "domain": f.domain,
            "task_type": f.task_type,
            "entrypoint": f.entrypoint,
            "status": f.status,
            "duration_ms": f.duration_ms,
            "tokens_total": f.tokens_total,
            "cost_usd": money(f.cost_usd, 6),
            "hitl_required": f.hitl_required,
            "error_class": f.error_class,
            "data_quality": f.data_quality,
            "links": {
                "run": run_link(f.id, f.started_at),
                **({"agent": f"{AGENT_LINK}/{f.agent_id}"} if f.agent_id else {}),
            },
        }
        for f in facts
    ]
    quality = quality_of(f.data_quality for f in facts) if facts else ("no_data" if not total else "measured")
    if not total:
        hint = hint or "No agent runs match. Runs are recorded as agents run; widen the date range or clear filters."
    return ExploreData(rows, "run_facts", quality, paged=True, total=total, hint=hint)


# ------------------------------------------------------------------ workflows


async def explore_workflows(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import String, cast, extract, func, select

    from core.models.workflow import WorkflowDefinition as D
    from core.models.workflow import WorkflowRun as W

    m = ctx.metric
    if _no_domains(ctx):
        return ExploreData([], "workflow_runs", "no_data", hint=NO_DOMAIN_HINT, paged=True, total=0)
    lo = day_bounds(m.period.start, m.zone)[0]
    hi = day_bounds(m.period.end, m.zone)[1]
    started = func.coalesce(W.started_at, W.created_at)
    duration = extract("epoch", W.completed_at - W.started_at) * 1000
    clauses = [W.tenant_id == m.tenant_id, started >= lo, started < hi]
    if m.company_id is not None:
        clauses.append(W.company_id == m.company_id)
    if m.domains is not None:
        clauses.append(D.domain.in_(list(m.domains)))
    timed_out = (W.status == "timed_out") | ((W.timeout_at.is_not(None)) & (W.completed_at > W.timeout_at))
    expressions = {
        "started_at": started,
        "workflow": D.name,
        "workflow_id": cast(D.id, String),
        "domain": D.domain,
        "status": W.status,
        "duration_ms": duration,
        "steps_completed": W.steps_completed,
        "steps_total": W.steps_total,
        "past_timeout": timed_out,
    }
    statement = (
        select(
            W.id,
            D.id,
            D.name,
            D.domain,
            W.status,
            started.label("started"),
            W.completed_at,
            W.timeout_at,
            W.steps_completed,
            W.steps_total,
            duration.label("duration"),
            timed_out.label("late"),
        )
        .join(D, D.id == W.workflow_def_id)
        .where(*clauses)
    )
    found, total = await sql_page(m.session, statement, ctx.query, expressions, W.id)
    rows = [
        {
            "started_at": r.started,
            "workflow": r.name,
            "workflow_id": str(r[1]),
            "domain": r.domain,
            "status": r.status,
            "duration_ms": round(float(r.duration)) if r.duration is not None else None,
            "steps_completed": r.steps_completed,
            "steps_total": r.steps_total,
            "past_timeout": bool(r.late),
            "links": {"workflow_run": f"/dashboard/workflows/{r[1]}/runs/{r[0]}"},
        }
        for r in found
    ]
    hint = None if total else "No workflow runs started in this period."
    return ExploreData(
        rows,
        "workflow_runs+workflow_definitions",
        "measured" if total else "no_data",
        paged=True,
        total=total,
        hint=hint,
    )


# ------------------------------------------------------------------ approvals


def _effective_status(status: str, expires_at: datetime | None, now: datetime) -> str:
    if status == "pending" and expires_at is not None and now > expires_at:
        return "expired"
    return status


async def explore_approvals(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import String, case, cast, extract, select

    from core.insights.metrics import agent_names

    m = ctx.metric
    if _no_domains(ctx):
        return ExploreData([], "hitl_queue", "no_data", hint=NO_DOMAIN_HINT, paged=True, total=0)
    hitl, clauses = _approval_scope(m)
    lo = day_bounds(m.period.start, m.zone)[0]
    hi = day_bounds(m.period.end, m.zone)[1]
    clauses = [*clauses, hitl.created_at >= lo, hitl.created_at < hi]
    status = case(
        ((hitl.status == "pending") & hitl.expires_at.is_not(None) & (hitl.expires_at < m.now), "expired"),
        else_=hitl.status,
    )
    cycle = extract("epoch", hitl.decision_at - hitl.created_at) * 1000
    expressions = {
        "created_at": hitl.created_at,
        "title": hitl.title,
        "agent_id": cast(hitl.agent_id, String),
        "trigger_type": hitl.trigger_type,
        "priority": hitl.priority,
        "assignee_role": hitl.assignee_role,
        "status": status,
        "decision": hitl.decision,
        "decision_at": hitl.decision_at,
        "cycle_time_ms": cycle,
        "expires_at": hitl.expires_at,
    }
    statement = select(hitl, cycle.label("cycle")).where(*clauses)
    found, total = await sql_page(m.session, statement, ctx.query, expressions, hitl.id)
    items = [(r[0], r.cycle) for r in found]
    names = await agent_names(m, (i.agent_id for i, _ in items if i.agent_id is not None))
    people = await user_names(m, (i.decision_by for i, _ in items if i.decision_by))
    rows = []
    for item, cycle_ms in items:
        rows.append(
            {
                "created_at": item.created_at,
                "title": item.title,
                "agent": names.get(str(item.agent_id)) if item.agent_id else None,
                "agent_id": _uuid_text(item.agent_id),
                "trigger_type": item.trigger_type,
                "priority": item.priority,
                "assignee_role": item.assignee_role,
                "status": _effective_status(item.status, item.expires_at, m.now),
                "decision": item.decision,
                "decided_by": _actor_label(item.decision_by, people),
                "decision_at": item.decision_at,
                "cycle_time_ms": round(float(cycle_ms)) if cycle_ms is not None else None,
                "expires_at": item.expires_at,
                "links": {
                    "approval": f"{APPROVALS_LINK}?id={item.id}",
                    **({"agent": f"{AGENT_LINK}/{item.agent_id}"} if item.agent_id else {}),
                },
            }
        )
    hint = None if total else "No approval was requested in this period."
    return ExploreData(rows, "hitl_queue", "measured" if total else "no_data", paged=True, total=total, hint=hint)


# ------------------------------------------------------------------ cost


def decompose(
    prior: dict[str, tuple[Decimal, Decimal]], current: dict[str, tuple[Decimal, Decimal]]
) -> dict[str, Decimal]:
    """A spend change split into volume, price and mix, over items keyed by what is priced.

    ``prior`` and ``current`` map an item (a model and unit, or the agent itself) to
    (quantity, amount). For an item in both periods with a quantity: volume is
    Δquantity × prior unit price and price is prior quantity × Δunit price. Mix is the
    rest of the change: the interaction term, and items that appeared or disappeared
    (a new model, a usage type the agent did not use before).
    """
    zero = Decimal("0")
    volume = zero
    price = zero
    change = zero
    for key in set(prior) | set(current):
        q0, a0 = prior.get(key, (zero, zero))
        q1, a1 = current.get(key, (zero, zero))
        change += a1 - a0
        if q0 > 0 and q1 > 0:
            p0 = a0 / q0
            p1 = a1 / q1
            volume += (q1 - q0) * p0
            price += q0 * (p1 - p0)
    return {"change": change, "volume": volume, "price": price, "mix": change - volume - price}


def driver_of(parts: dict[str, Decimal]) -> str:
    """The part that moved spend most, in absolute terms; ``none`` when nothing changed."""
    candidates = {name: abs(parts[name]) for name in ("volume", "price", "mix")}
    name, size = max(candidates.items(), key=lambda item: item[1])
    return name if size > 0 else "none"


async def _metered_items(
    ctx: MetricContext, period: Period, basis: Any
) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
    """Per agent: (provider, model, unit) → (quantity, amount in the display currency), from the rollups."""
    from sqlalchemy import String, cast, func, select

    from core.insights.metrics_cost import NATIVE, ROLLUPS
    from core.models.spend_usage import SpendUsageRollup as R

    rows = await ctx.session.execute(
        select(
            cast(R.agent_id, String),
            R.provider,
            R.model,
            R.unit,
            func.coalesce(func.sum(R.quantity), 0),
            func.coalesce(func.sum(R.amount_inr), 0),
        )
        .where(R.tenant_id == ctx.tenant_id, R.day >= period.start, R.day <= period.end)
        .group_by(R.agent_id, R.provider, R.model, R.unit)
    )
    out: dict[str, dict[str, tuple[Decimal, Decimal]]] = defaultdict(dict)
    for agent_id, provider, model, unit, quantity, amount in rows:
        key = f"{provider or '-'}|{model or '-'}|{unit or '-'}"
        out[agent_id or UNATTRIBUTED][key] = (
            Decimal(quantity or 0),
            basis.shown(Decimal(amount or 0), NATIVE[ROLLUPS]),
        )
    return out


async def explore_cost(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import select

    from core.insights.metrics import previous_period
    from core.insights.metrics_cost import ROLLUPS, cost_basis, daily_spend
    from core.models.agent import Agent

    m = ctx.metric
    if not ctx.tenant_cost and _no_domains(ctx):
        return ExploreData([], "spend", "no_data", hint=NO_DOMAIN_HINT)
    compare = m.compare or previous_period(m.period)
    basis = await cost_basis(m)
    current = await daily_spend(m, m.period.start, m.period.end, group="agent")
    prior = await daily_spend(m, compare.start, compare.end, group="agent")
    if not current.amounts and not prior.amounts:
        from core.insights.metrics_cost import NO_SPEND_HINT

        return ExploreData([], current.source, "no_data", hint=basis.hint or NO_SPEND_HINT)
    # Which agents the caller may see: every agent for administrators and the CFO, else their domains' agents.
    agent_filter = [Agent.tenant_id == m.tenant_id] if ctx.tenant_cost else _agent_scope(m, Agent)
    if ctx.tenant_cost and m.company_id is not None:
        agent_filter.append(Agent.company_id == m.company_id)
    agents = {
        str(a.id): a
        for a in (
            await m.session.execute(
                select(Agent.id, Agent.name, Agent.domain).where(*agent_filter).limit(MAX_SCAN_ROWS)
            )
        ).all()
    }
    metered = ROLLUPS in current.sources and ROLLUPS in prior.sources and not current.mixed and not prior.mixed

    def per_agent(spend: Any) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(Decimal)
        for (_day, grp), amount in spend.amounts.items():
            out[grp] += amount
        return out

    now_spend = per_agent(current)
    then_spend = per_agent(prior)
    items_now: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    items_then: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    runs_now: dict[str, int] = {}
    runs_then: dict[str, int] = {}
    if metered:
        items_now = await _metered_items(m, m.period, basis)
        items_then = await _metered_items(m, compare, basis)
    for period, target in ((m.period, runs_now), (compare, runs_then)):
        data = await run_data(m, period, group="agent", domain_filter=False)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in data.rows:
            grouped[row["grp"]].append(row)
        for grp, agent_rows in grouped.items():
            target[grp] = int(totals(agent_rows)["volume"])
    rows: list[dict[str, Any]] = []
    for agent_id in set(now_spend) | set(then_spend):
        agent = agents.get(agent_id)
        if agent is None and not (ctx.tenant_cost and agent_id == UNATTRIBUTED):
            continue  # not an agent this caller may see
        a1 = now_spend.get(agent_id, Decimal("0"))
        a0 = then_spend.get(agent_id, Decimal("0"))
        q1 = Decimal(runs_now.get(agent_id, 0))
        q0 = Decimal(runs_then.get(agent_id, 0))
        if metered and (agent_id in items_now or agent_id in items_then):
            parts = decompose(items_then.get(agent_id, {}), items_now.get(agent_id, {}))
            method = "metered"
        else:
            parts = decompose({"runs": (q0, a0)}, {"runs": (q1, a1)})
            method = "runs"
        rows.append(
            {
                "agent": agent.name if agent is not None else "Unattributed",
                "agent_id": agent_id if agent is not None else None,
                "domain": agent.domain if agent is not None else None,
                "spend": money(a1),
                "prior_spend": money(a0),
                "change": money(a1 - a0),
                "change_pct": pct(float(a1 - a0), float(a0)) if a0 else None,
                "volume_effect": money(parts["volume"]),
                "price_effect": money(parts["price"]),
                "mix_effect": money(parts["mix"]),
                "driver": driver_of(parts),
                "basis": method,
                "runs": int(q1),
                "cost_per_run": money(a1 / q1, 6) if q1 else None,
                "links": {
                    **({"agent": f"{AGENT_LINK}/{agent_id}"} if agent is not None else {}),
                    **(
                        {"runs": f"/dashboard/insights/explore/runs?filter[agent_id]={agent_id}"}
                        if agent is not None
                        else {}
                    ),
                },
            }
        )
    hint = (
        "Change compares the period with the same number of days just before it. Volume is the change in "
        "quantity at the earlier unit price, price the change in unit price on the earlier quantity, and mix "
        "the rest (new or dropped models and usage types, and both moving at once). "
    )
    hint += (
        "Quantities are metered units per model."
        if metered
        else "Without metered usage for both periods, runs stand in for quantity and spend per run for price."
    )
    if basis.hint:
        hint += " " + basis.hint
    quality = "mixed" if (current.mixed or prior.mixed) else "measured"
    return ExploreData(
        rows,
        "+".join(sorted(current.sources | prior.sources)) or current.source,
        quality,
        hint=hint,
        summary={
            "currency": basis.currency,
            "spend": money(sum(now_spend.values(), Decimal("0"))) if ctx.tenant_cost else None,
            "compare": compare.range_dict(),
        },
    )


# ------------------------------------------------------------------ quality and learning


async def explore_quality(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import func, select

    from core.models.agent import Agent
    from core.models.feedback import AgentFeedback as Fb

    m = ctx.metric
    if _no_domains(ctx):
        return ExploreData([], "agents+agent_feedback", "no_data", hint=NO_DOMAIN_HINT)
    agents = (
        await m.session.execute(
            select(Agent.id, Agent.name, Agent.domain, Agent.status, Agent.prompt_amendments)
            .where(*_agent_scope(m, Agent))
            .limit(MAX_SCAN_ROWS)
        )
    ).all()
    lo = day_bounds(m.period.start, m.zone)[0]
    hi = day_bounds(m.period.end, m.zone)[1]
    ids = [a.id for a in agents]
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unapplied: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    if ids:
        found = await m.session.execute(
            select(Fb.agent_id, Fb.feedback_type, func.count(), func.count().filter(Fb.applied_at.is_(None)))
            .where(Fb.tenant_id == m.tenant_id, Fb.agent_id.in_(ids), Fb.created_at >= lo, Fb.created_at < hi)
            .group_by(Fb.agent_id, Fb.feedback_type)
        )
        for agent_id, kind, count, not_applied in found:
            counts[str(agent_id)][str(kind)] += int(count)
            unapplied[str(agent_id)] += int(not_applied)
            by_type[str(kind)] += int(count)
    rows: list[dict[str, Any]] = []
    for agent in agents:
        amendments = agent.prompt_amendments if isinstance(agent.prompt_amendments, list) else []
        agent_id = str(agent.id)
        c = counts.get(agent_id, {})
        negative = int(c.get("thumbs_down", 0)) + int(c.get("correction", 0))
        for index, rule in enumerate(amendments):
            rows.append(
                {
                    "agent": agent.name,
                    "agent_id": agent_id,
                    "domain": agent.domain,
                    "status": agent.status,
                    "rule_number": index + 1,
                    "rule": str(rule)[:500],
                    "rules_on_agent": len(amendments),
                    "feedback": sum(c.values()),
                    "negative_feedback": negative,
                    "unapplied_feedback": unapplied.get(agent_id, 0),
                    "links": {"agent": f"{AGENT_LINK}/{agent_id}"},
                    "actions": {"revoke": {"agent_id": agent_id, "index": index}},
                }
            )
    hint = None
    if not rows:
        hint = (
            "No learned rules yet. A rule is proposed when feedback on an agent is analysed and applied; "
            "feedback counts for the period are in the summary."
        )
    return ExploreData(
        rows,
        "agents+agent_feedback",
        "measured" if rows or by_type else "no_data",
        hint=hint,
        summary={
            "feedback_by_type": dict(sorted(by_type.items())),
            "agents_with_rules": len({r["agent_id"] for r in rows}),
        },
    )


# ------------------------------------------------------------------ connectors


_HEALTH_ORDER = {"unhealthy": 3, "degraded": 2, "unknown": 1, "healthy": 0}


async def explore_connectors(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import select

    from core.models.agent import Agent
    from core.models.connector import Connector
    from core.models.connector_config import ConnectorConfig

    m = ctx.metric
    connectors = (
        await m.session.execute(
            select(Connector.id, Connector.name, Connector.category, Connector.status, Connector.health_check_at)
            .where(Connector.tenant_id == m.tenant_id)
            .order_by(Connector.name)
            .limit(MAX_SCAN_ROWS)
        )
    ).all()
    if not connectors:
        return ExploreData(
            [], "connectors", "no_data", hint="No connectors are set up. Add one from the Connectors page."
        )
    config_query = select(
        ConnectorConfig.connector_name,
        ConnectorConfig.company_id,
        ConnectorConfig.status,
        ConnectorConfig.health_status,
        ConnectorConfig.last_health_check,
        ConnectorConfig.sync_error,
    ).where(ConnectorConfig.tenant_id == m.tenant_id)
    if m.company_id is not None:
        config_query = config_query.where(ConnectorConfig.company_id == m.company_id)
    configs: dict[str, list[Any]] = defaultdict(list)
    for row in (await m.session.execute(config_query)).all():
        configs[str(row.connector_name)].append(row)
    dependents: dict[str, int] = defaultdict(int)
    agent_rows = (
        await m.session.execute(
            select(Agent.connector_ids)
            .where(Agent.tenant_id == m.tenant_id, Agent.status != "deleted")
            .limit(MAX_SCAN_ROWS)
        )
    ).all()
    for (ids,) in agent_rows:
        for cid in ids or []:
            dependents[str(cid)] += 1
    rows: list[dict[str, Any]] = []
    for c in connectors:
        found = configs.get(str(c.name), [])
        health = None
        last_check = c.health_check_at
        for cfg in found:
            state = str(cfg.health_status or "unknown")
            if health is None or _HEALTH_ORDER.get(state, 1) > _HEALTH_ORDER.get(health, 1):
                health = state
            if cfg.last_health_check is not None and (last_check is None or cfg.last_health_check > last_check):
                last_check = cfg.last_health_check
        rows.append(
            {
                "connector": c.name,
                "category": c.category,
                "status": c.status,
                "health": health or "not_configured",
                "configured_companies": len({str(cfg.company_id) for cfg in found}),
                "sync_failing": any(cfg.sync_error for cfg in found),
                "last_health_check": last_check,
                "dependent_agents": dependents.get(str(c.id), 0),
                "links": {"connector": f"{CONNECTOR_LINK}/{c.id}"},
            }
        )
    return ExploreData(
        rows,
        "connectors+connector_configs",
        "measured",
        hint=(
            "Call counts, success rate and latency per connector need tool-call records, which are not "
            "written yet; health comes from each connector's last health check."
        ),
    )


# ------------------------------------------------------------------ audit log


async def explore_audit(ctx: ExploreContext) -> ExploreData:
    from sqlalchemy import func, select

    from core.models.audit import AuditLog as A

    m = ctx.metric
    lo = day_bounds(m.period.start, m.zone)[0]
    hi = day_bounds(m.period.end, m.zone)[1]
    category = func.split_part(A.event_type, ".", 1)
    clauses = [A.tenant_id == m.tenant_id, A.created_at >= lo, A.created_at < hi]
    if m.company_id is not None:
        clauses.append(A.company_id == m.company_id)
    expressions = {
        "created_at": A.created_at,
        "category": category,
        "event_type": A.event_type,
        "actor_type": A.actor_type,
        "resource_type": A.resource_type,
        "action": A.action,
        "outcome": A.outcome,
    }
    statement = select(
        A.id,
        A.created_at,
        category.label("category"),
        A.event_type,
        A.actor_type,
        A.actor_id,
        A.resource_type,
        A.resource_id,
        A.action,
        A.outcome,
        A.agent_id,
        A.signature,
    ).where(*clauses)
    found, total = await sql_page(m.session, statement, ctx.query, expressions, A.id)
    people = await user_names(m, (r.actor_id for r in found if r.actor_type == "user"))
    rows = [
        {
            "created_at": r.created_at,
            "category": r.category,
            "event_type": r.event_type,
            "actor_type": r.actor_type,
            "actor": _actor_label(r.actor_id, people) if r.actor_type == "user" else r.actor_id,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "action": r.action[:200] if r.action else r.action,
            "outcome": r.outcome,
            "signed": bool(r.signature),
            "links": {**({"agent": f"{AGENT_LINK}/{r.agent_id}"} if r.agent_id else {})},
        }
        for r in found
    ]
    hint = (
        "Audited actions: approvals, agent and policy changes, exports, data requests and more. Sign-in "
        "failures, lockouts and rate-limit denials are not in the audit log yet."
    )
    if not total:
        hint = "Nothing was audited in this period. " + hint
    return ExploreData(rows, "audit_log", "measured" if total else "no_data", paged=True, total=total, hint=hint)
