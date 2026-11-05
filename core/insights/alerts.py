# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights alerts: rules over the metrics, one alert per (rule, entity) until it clears.

Every 15 minutes the sweep (``core/tasks/insights_tasks.py``) evaluates each
enabled tenant's rules over the last 7 tenant-local days (and the 7 before for
week-on-week rules) in that tenant's session, then:

- opens an alert for a condition that has none (``state`` open),
- refreshes ``last_seen_at``, severity, title and detail of one that still holds
  (an acknowledged alert stays acknowledged and is not sent again),
- resolves an alert of an evaluated rule whose condition no longer holds.

A resolved alert that recurs opens a new one. ``act`` alerts email the owner
role's active users once (``notified_at``); nothing is sent for demo-seeded
data. Owners: run health and approvals the administrator; budgets (and a
tenant-wide monthly budget the month's forecast is above), spend and pricing
the CFO; connectors and workflows the COO; an agent's alerts the head
of its domain (finance CFO, hr CHRO, marketing CMO, ops COO), else the
administrator. A caller sees the alerts its role owns; administrators see all.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

from core.insights.metrics import MetricContext, Period

logger = structlog.get_logger()

WINDOW_DAYS = 7
IDLE_DAYS = 30
MAX_ENTITY_ALERTS = 50
COST_PER_RUN_JUMP = 0.25
# A forecast this far over a tenant-wide monthly budget is ``act``; any amount over it is ``watch``.
FORECAST_ACT_RATIO = 1.2
DOMAIN_OWNERS: dict[str, str] = {
    "finance": "cfo",
    "hr": "chro",
    "marketing": "cmo",
    "ops": "coo",
    "operations": "coo",
}
RULES: tuple[str, ...] = (
    "runs.success_rate",
    "approvals.pending",
    "approvals.expired",
    "cost.budget",
    "cost.forecast_over_budget",
    "cost.per_run_jump",
    "cost.pricing_coverage",
    "connectors.unhealthy",
    "workflows.stalled",
    "agents.idle",
    "agents.promotable",
)
EXPLORE = "/dashboard/insights/explore"


@dataclass(frozen=True)
class Candidate:
    rule_key: str
    severity: str
    owner_role: str
    title: str
    entity_ref: str = ""
    metric_key: str | None = None
    domain: str | None = None
    link: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule_key, self.entity_ref)


def owner_for_domain(domain: str | None) -> str:
    return DOMAIN_OWNERS.get((domain or "").lower(), "admin")


# ------------------------------------------------------------------ rules


async def _run_health(ctx: MetricContext) -> list[Candidate]:
    from core.insights import registry

    out: list[Candidate] = []
    rate = await registry.METRICS["runs.success_rate"].resolver(ctx)
    if rate.get("data_quality") == "measured" and rate.get("value") is not None:
        state = registry.METRICS["runs.success_rate"].thresholds.state(rate["value"])  # type: ignore[union-attr]
        if state == "act":
            out.append(
                Candidate(
                    "runs.success_rate",
                    "act",
                    "admin",
                    f"Run success rate is {rate['value']}% over the last {WINDOW_DAYS} days",
                    metric_key="runs.success_rate",
                    link=f"{EXPLORE}/runs?filter%5Bstatus%5D=failed",
                    detail={"value": rate["value"]},
                )
            )
    return out


async def _approvals(ctx: MetricContext) -> list[Candidate]:
    from sqlalchemy import func, select

    from core.insights import registry
    from core.insights.metrics import pending_approvals
    from core.models.hitl import HITLQueue as H

    out: list[Candidate] = []
    count, _items, _oldest = await pending_approvals(ctx, 1)
    state = registry.METRICS["approvals.pending"].thresholds.state(count)  # type: ignore[union-attr]
    if state == "act":
        out.append(
            Candidate(
                "approvals.pending",
                "act",
                "admin",
                f"{count} approvals are waiting on a person",
                metric_key="approvals.pending",
                link=f"{EXPLORE}/approvals?filter%5Bstatus%5D=pending",
                detail={"pending": count},
            )
        )
    expired = int(
        (
            await ctx.session.execute(
                select(func.count()).where(H.tenant_id == ctx.tenant_id, H.status == "pending", H.expires_at < ctx.now)
            )
        ).scalar_one()
        or 0
    )
    if expired:
        out.append(
            Candidate(
                "approvals.expired",
                "act",
                "admin",
                f"{expired} approvals passed their expiry without a decision",
                link=f"{EXPLORE}/approvals?filter%5Bstatus%5D=expired",
                detail={"expired": expired},
            )
        )
    return out


def forecast_in_usd(forecast: dict[str, Any]) -> float | None:
    """The month's forecast point in USD (budgets are USD), or ``None`` when it has none or cannot be converted."""
    value = forecast.get("value")
    if value is None or forecast.get("data_quality") == "no_data":
        return None
    currency = str(forecast.get("currency") or "USD").upper()
    if currency == "USD":
        return float(value)
    rate = (forecast.get("conversion") or {}).get("rate_to_inr")
    if currency == "INR" and rate:
        try:
            per_usd = float(rate)
        except (TypeError, ValueError):
            return None
        return float(value) / per_usd if per_usd > 0 else None
    return None


async def _forecast_over_budget(ctx: MetricContext, budgets: list[dict[str, Any]]) -> list[Candidate]:
    """Tenant-wide monthly budgets the month's spend forecast is above (the forecast is tenant-wide only)."""
    from core.insights import registry

    monthly = [b for b in budgets if b["period"] == "monthly" and b.get("tenant_wide")]
    if not monthly or ctx.company_id is not None:
        return []
    forecast = await registry.METRICS["cost.forecast"].resolver(ctx)
    point = forecast_in_usd(forecast)
    if point is None:
        return []
    out: list[Candidate] = []
    for budget in monthly:
        threshold = float(budget["threshold"] or 0)
        if threshold <= 0 or point <= threshold:
            continue
        ratio = point / threshold
        out.append(
            Candidate(
                "cost.forecast_over_budget",
                "act" if ratio >= FORECAST_ACT_RATIO else "watch",
                "cfo",
                f"This month's spend is forecast to reach ${point:,.2f} against the "
                f"${threshold:,.2f} monthly budget {budget['name']}",
                entity_ref=str(budget["id"]),
                metric_key="cost.forecast",
                link="/dashboard/insights/budgets",
                detail={
                    "forecast_usd": round(point, 2),
                    "threshold_usd": round(threshold, 2),
                    "forecast_pct": round(ratio * 100, 1),
                    "data_quality": "estimated",
                },
            )
        )
    return out


async def _cost(ctx: MetricContext) -> list[Candidate]:
    from core.insights import metrics_cost, registry

    out: list[Candidate] = []
    budgets = await metrics_cost.budget_rows(ctx)
    for budget in budgets:
        if budget["state"] not in ("warn", "breached"):
            continue
        severity = "act" if budget["state"] == "breached" else "watch"
        verb = "is over" if severity == "act" else "is near"
        out.append(
            Candidate(
                "cost.budget",
                severity,
                "cfo",
                f"{budget['name']} {verb} its {budget['period']} budget ({budget['used_pct']}% used)",
                entity_ref=str(budget["id"]),
                metric_key="cost.budgets",
                link="/dashboard/insights/explore/cost",
                detail={"used_pct": budget["used_pct"], "period": budget["period"]},
            )
        )
    out.extend(await _forecast_over_budget(ctx, budgets))
    per_run = await registry.METRICS["cost.per_run"].resolver(ctx)
    value, previous = per_run.get("value"), per_run.get("compare_value")
    if value is not None and previous and float(previous) > 0:
        jump = (float(value) - float(previous)) / float(previous)
        if jump > COST_PER_RUN_JUMP:
            out.append(
                Candidate(
                    "cost.per_run_jump",
                    "watch",
                    "cfo",
                    f"Cost per run rose {round(jump * 100)}% week on week",
                    metric_key="cost.per_run",
                    link=f"{EXPLORE}/cost?sort=change&order=desc",
                    detail={"value": value, "previous": previous, "currency": per_run.get("currency")},
                )
            )
    coverage = await registry.METRICS["cost.data_quality"].resolver(ctx)
    if coverage.get("value") is not None:
        state = registry.METRICS["cost.data_quality"].thresholds.state(coverage["value"])  # type: ignore[union-attr]
        if state == "act":
            out.append(
                Candidate(
                    "cost.pricing_coverage",
                    "act",
                    "cfo",
                    f"Only {coverage['value']}% of metered usage was priced",
                    metric_key="cost.data_quality",
                    link="/dashboard/insights/explore/cost",
                    detail={"value": coverage["value"]},
                )
            )
    return out


async def _operations(ctx: MetricContext) -> list[Candidate]:
    from sqlalchemy import func, select

    from core.models.connector_config import ConnectorConfig
    from core.models.workflow import WorkflowRun

    out: list[Candidate] = []
    rows = await ctx.session.execute(
        select(ConnectorConfig.connector_name, func.count())
        .where(ConnectorConfig.tenant_id == ctx.tenant_id, ConnectorConfig.health_status == "unhealthy")
        .group_by(ConnectorConfig.connector_name)
        .order_by(ConnectorConfig.connector_name)
        .limit(MAX_ENTITY_ALERTS)
    )
    for name, companies in rows:
        out.append(
            Candidate(
                "connectors.unhealthy",
                "act",
                "coo",
                f"Connector {name} is unhealthy",
                entity_ref=str(name),
                link=f"{EXPLORE}/connectors?filter%5Bhealth%5D=unhealthy",
                detail={"companies": int(companies)},
            )
        )
    stalled = int(
        (
            await ctx.session.execute(
                select(func.count()).where(
                    WorkflowRun.tenant_id == ctx.tenant_id,
                    WorkflowRun.completed_at.is_(None),
                    WorkflowRun.status.notin_(("completed", "failed", "cancelled", "rejected", "timed_out")),
                    WorkflowRun.timeout_at.is_not(None),
                    WorkflowRun.timeout_at < ctx.now,
                )
            )
        ).scalar_one()
        or 0
    )
    if stalled:
        out.append(
            Candidate(
                "workflows.stalled",
                "act",
                "coo",
                f"{stalled} workflow runs are past their timeout and still open",
                link=f"{EXPLORE}/workflows?filter%5Bpast_timeout%5D=true",
                detail={"stalled": stalled},
            )
        )
    return out


async def _agents(ctx: MetricContext) -> list[Candidate]:
    from sqlalchemy import select

    from core.insights.explore_sources import shadow_gate
    from core.models.agent import Agent
    from core.models.insights import RunFact as F

    out: list[Candidate] = []
    since = ctx.now - timedelta(days=IDLE_DAYS)
    recent = (
        select(F.agent_id)
        .where(F.tenant_id == ctx.tenant_id, F.started_at >= since, F.agent_id.is_not(None))
        .distinct()
        .scalar_subquery()
    )
    idle = (
        await ctx.session.execute(
            select(Agent.id, Agent.name, Agent.domain)
            .where(
                Agent.tenant_id == ctx.tenant_id,
                Agent.status == "active",
                Agent.created_at < since,
                Agent.id.notin_(recent),
            )
            .order_by(Agent.name)
            .limit(MAX_ENTITY_ALERTS)
        )
    ).all()
    for agent_id, name, domain in idle:
        out.append(
            Candidate(
                "agents.idle",
                "watch",
                owner_for_domain(domain),
                f"{name} has not run for {IDLE_DAYS} days",
                entity_ref=str(agent_id),
                domain=domain,
                link=f"/dashboard/agents/{agent_id}",
                detail={"days": IDLE_DAYS},
            )
        )
    shadows = (
        await ctx.session.execute(
            select(Agent).where(Agent.tenant_id == ctx.tenant_id, Agent.status == "shadow").limit(MAX_ENTITY_ALERTS * 4)
        )
    ).scalars()
    promotable = 0
    for agent in shadows:
        ok, _reason = shadow_gate(agent)
        if not ok or promotable >= MAX_ENTITY_ALERTS:
            continue
        promotable += 1
        out.append(
            Candidate(
                "agents.promotable",
                "watch",
                owner_for_domain(agent.domain),
                f"{agent.name} passes its shadow checks and can be promoted",
                entity_ref=str(agent.id),
                domain=agent.domain,
                link=f"/dashboard/agents/{agent.id}",
            )
        )
    return out


async def evaluate(ctx: MetricContext) -> list[Candidate]:
    """Every rule's current alerts for the tenant of ``ctx`` (a system context: every domain)."""
    out: list[Candidate] = []
    for rule in (_run_health, _approvals, _cost, _operations, _agents):
        out.extend(await rule(ctx))
    # One alert per (rule, entity): the first wins.
    seen: dict[tuple[str, str], Candidate] = {}
    for candidate in out:
        seen.setdefault(candidate.key, candidate)
    return list(seen.values())


def window(today: Any) -> tuple[Period, Period]:
    """The last ``WINDOW_DAYS`` tenant-local days (today included) and the same number before them."""
    current = Period(today - timedelta(days=WINDOW_DAYS - 1), today, "day")
    previous = Period(current.start - timedelta(days=WINDOW_DAYS), current.start - timedelta(days=1), "day")
    return current, previous


# ------------------------------------------------------------------ open, refresh, resolve


@dataclass
class Applied:
    opened: int = 0
    refreshed: int = 0
    resolved: int = 0
    to_notify: list[uuid.UUID] = field(default_factory=list)


async def apply(
    session: Any, tenant_id: uuid.UUID, candidates: Iterable[Candidate], now: datetime, rules: Iterable[str] = RULES
) -> Applied:
    """Bring the tenant's live alerts in line with ``candidates`` for the evaluated ``rules``."""
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert

    from core.models.insights import InsightsAlert as A

    result = Applied()
    wanted = {c.key: c for c in candidates}
    evaluated = set(rules)
    live = {
        (a.rule_key, a.entity_ref): a
        for a in (
            await session.execute(select(A).where(A.tenant_id == tenant_id, A.state != "resolved").with_for_update())
        ).scalars()
    }
    for key, alert in live.items():
        candidate = wanted.get(key)
        if candidate is None:
            if alert.rule_key in evaluated:
                alert.state = "resolved"
                alert.resolved_at = now
                result.resolved += 1
            continue
        alert.last_seen_at = now
        alert.severity = candidate.severity
        alert.title = candidate.title[:300]
        alert.detail = candidate.detail
        alert.deep_link = candidate.link
        alert.owner_role = candidate.owner_role
        alert.domain = candidate.domain
        result.refreshed += 1
        if alert.severity == "act" and alert.state == "open" and alert.notified_at is None:
            result.to_notify.append(alert.id)
    for key, candidate in wanted.items():
        if key in live:
            continue
        new_id = uuid.uuid4()
        inserted = await session.execute(
            insert(A)
            .values(
                id=new_id,
                tenant_id=tenant_id,
                rule_key=candidate.rule_key,
                metric_key=candidate.metric_key,
                entity_ref=candidate.entity_ref,
                severity=candidate.severity,
                owner_role=candidate.owner_role,
                domain=candidate.domain,
                title=candidate.title[:300],
                detail=candidate.detail,
                deep_link=candidate.link,
                state="open",
                fired_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[A.tenant_id, A.rule_key, A.entity_ref], index_where=text("state <> 'resolved'")
            )
            .returning(A.id)
        )
        if inserted.scalar_one_or_none() is not None:
            result.opened += 1
            if candidate.severity == "act":
                result.to_notify.append(new_id)
    await session.flush()
    return result


# ------------------------------------------------------------------ who sees what


def visible_clause(model: Any, role: str, *, is_admin: bool) -> Any:
    """Administrators see every alert; any other role the alerts it owns."""
    from sqlalchemy import true

    if is_admin:
        return true()
    return model.owner_role == (role or "").lower()


async def open_alerts(
    session: Any, tenant_id: uuid.UUID, role: str, *, is_admin: bool, limit: int = 20
) -> list[dict[str, Any]]:
    """The caller's unresolved, unacknowledged alerts, most severe and newest first."""
    from sqlalchemy import case, select

    from core.models.insights import InsightsAlert as A

    rows = (
        await session.execute(
            select(A)
            .where(A.tenant_id == tenant_id, A.state == "open", visible_clause(A, role, is_admin=is_admin))
            .order_by(case((A.severity == "act", 0), else_=1), A.fired_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [alert_dict(a) for a in rows]


def alert_dict(a: Any) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "rule_key": a.rule_key,
        "metric_key": a.metric_key,
        "severity": a.severity,
        "owner_role": a.owner_role,
        "title": a.title,
        "detail": a.detail or {},
        "link": a.deep_link,
        "state": a.state,
        "fired_at": a.fired_at.isoformat() if a.fired_at else None,
        "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
        "acked_at": a.acked_at.isoformat() if a.acked_at else None,
    }


# ------------------------------------------------------------------ email


def _body(tenant_name: str, alerts: list[Any], base_url: str) -> str:
    from html import escape

    items = "".join(
        f"<li><strong>{escape(a.title)}</strong>"
        + (f' — <a href="{escape(base_url + a.deep_link)}">open</a>' if a.deep_link else "")
        + "</li>"
        for a in alerts
    )
    return (
        f"<p>Insights for {escape(tenant_name)} needs you:</p><ul>{items}</ul>"
        "<p>Acknowledge an alert on the Insights page so it is not sent again. Alerts resolve on their own once "
        "the condition clears.</p>"
    )


async def notify(tenant_id: uuid.UUID, alert_ids: list[uuid.UUID], now: datetime) -> int:
    """Email each ``act`` alert's owner role once (its active users), then mark the alerts notified."""
    if not alert_ids:
        return 0
    from sqlalchemy import select

    from core.config import settings
    from core.database import get_tenant_session
    from core.email import send_email
    from core.models.insights import InsightsAlert as A
    from core.models.tenant import Tenant
    from core.models.user import User

    async with get_tenant_session(tenant_id) as session:
        alerts = list(
            (
                await session.execute(
                    select(A).where(
                        A.tenant_id == tenant_id, A.id.in_(alert_ids), A.notified_at.is_(None), A.state == "open"
                    )
                )
            ).scalars()
        )
        if not alerts:
            return 0
        tenant_name = (
            await session.execute(select(Tenant.name).where(Tenant.id == tenant_id))
        ).scalar_one_or_none() or "your organisation"
        roles = sorted({a.owner_role for a in alerts})
        users = (
            await session.execute(
                select(User.email, User.role).where(
                    User.tenant_id == tenant_id, User.status == "active", User.role.in_([*roles, "admin"])
                )
            )
        ).all()
    base_url = str(getattr(settings, "app_base_url", "") or "https://app.agenticorg.ai").rstrip("/")
    sent = 0
    for role in roles:
        mine = [a for a in alerts if a.owner_role == role]
        recipients = sorted({email for email, user_role in users if user_role == role and email})
        if not recipients and role != "admin":
            # Nobody holds the owner role: the administrators get it.
            recipients = sorted({email for email, user_role in users if user_role == "admin" and email})
        subject = f"Insights: {mine[0].title}" if len(mine) == 1 else f"Insights: {len(mine)} items need you"
        html = _body(tenant_name, mine, base_url)
        for email in recipients:
            if await asyncio.to_thread(send_email, email, subject[:200], html):
                sent += 1
    async with get_tenant_session(tenant_id) as session:
        from sqlalchemy import update

        await session.execute(
            update(A).where(A.tenant_id == tenant_id, A.id.in_([a.id for a in alerts])).values(notified_at=now)
        )
    logger.info("insights_alerts_notified", alerts=len(alerts), emails=sent)
    return sent
