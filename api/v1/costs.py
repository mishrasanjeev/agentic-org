"""Cost dashboard API — aggregated LLM/infra spend per tenant.

Endpoints:
  GET /api/v1/costs/summary?period=daily|weekly|monthly[&company_id=&cost_center_id=]
  GET /api/v1/costs/trend?days=30
  GET /api/v1/costs/top-agents?limit=10
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from api.deps import get_current_tenant
from core.database import get_tenant_session

logger = structlog.get_logger()
router = APIRouter(prefix="/costs", tags=["Costs"])


class CostPoint(BaseModel):
    date: str
    cost_usd: float
    tasks: int


class CostSummary(BaseModel):
    tenant_id: str
    period: str
    start: datetime
    end: datetime
    total_usd: float
    task_count: int
    by_domain: dict[str, float]
    by_agent: dict[str, float]


class AgentCostRow(BaseModel):
    agent_id: str
    agent_type: str
    domain: str
    total_usd: float
    task_count: int


@router.get("/summary", response_model=CostSummary)
async def summary(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    company_id: uuid.UUID | None = None,
    cost_center_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> CostSummary:
    tid = uuid.UUID(tenant_id)
    now = datetime.now(UTC)
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Build the WHERE clause dynamically.
    where = ["tenant_id = :tid", "created_at >= :since"]
    params: dict = {"tid": str(tid), "since": start}
    if company_id is not None:
        where.append("company_id = :cid")
        params["cid"] = str(company_id)
    if cost_center_id is not None:
        where.append(
            "agent_id IN (SELECT id FROM agents WHERE cost_center_id = :ccid)"
        )
        params["ccid"] = str(cost_center_id)
    where_sql = " AND ".join(where)

    async with get_tenant_session(tid) as session:
        # Totals / grouped aggregates.  The WHERE clause is built from a
        # hard-coded whitelist above (tenant, company, cost center) — no
        # user input reaches the SQL string.
        total_sql = f"SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM agent_task_results WHERE {where_sql}"  # noqa: S608
        total_q = text(total_sql)
        row = (await session.execute(total_q, params)).first()
        total_usd = float(row[0] or 0)
        task_count = int(row[1] or 0)

        # By domain
        domain_prefix = "SELECT domain, COALESCE(SUM(cost_usd), 0) FROM agent_task_results WHERE "
        domain_q = text(domain_prefix + where_sql + " GROUP BY domain")  # noqa: S608
        by_domain = {
            (r[0] or "unknown"): float(r[1] or 0)
            for r in (await session.execute(domain_q, params)).all()
        }

        # By agent (top 20)
        agent_prefix = "SELECT agent_type, COALESCE(SUM(cost_usd), 0) FROM agent_task_results WHERE "
        agent_suffix = " GROUP BY agent_type ORDER BY 2 DESC LIMIT 20"
        agent_q = text(agent_prefix + where_sql + agent_suffix)  # noqa: S608
        by_agent = {
            (r[0] or "unknown"): float(r[1] or 0)
            for r in (await session.execute(agent_q, params)).all()
        }

    return CostSummary(
        tenant_id=str(tid),
        period=period,
        start=start,
        end=now,
        total_usd=round(total_usd, 4),
        task_count=task_count,
        by_domain={k: round(v, 4) for k, v in by_domain.items()},
        by_agent={k: round(v, 4) for k, v in by_agent.items()},
    )


@router.get("/trend", response_model=list[CostPoint])
async def trend(
    days: int = Query(30, ge=1, le=365),
    tenant_id: str = Depends(get_current_tenant),
) -> list[CostPoint]:
    """Daily cost buckets for the trailing N days."""
    tid = uuid.UUID(tenant_id)
    start = datetime.now(UTC) - timedelta(days=days)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            text(
                "SELECT DATE(created_at) AS d, "
                "       COALESCE(SUM(cost_usd), 0) AS total, "
                "       COUNT(*) AS cnt "
                "FROM agent_task_results "
                "WHERE tenant_id = :tid AND created_at >= :since "
                "GROUP BY d ORDER BY d"
            ),
            {"tid": str(tid), "since": start},
        )
        rows = result.all()

    return [
        CostPoint(date=str(r[0]), cost_usd=float(r[1] or 0), tasks=int(r[2] or 0))
        for r in rows
    ]


@router.get("/top-agents", response_model=list[AgentCostRow])
async def top_agents(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=100),
    tenant_id: str = Depends(get_current_tenant),
) -> list[AgentCostRow]:
    tid = uuid.UUID(tenant_id)
    start = datetime.now(UTC) - timedelta(days=days)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            text(
                "SELECT agent_id, agent_type, domain, "
                "       COALESCE(SUM(cost_usd), 0) AS total, "
                "       COUNT(*) AS cnt "
                "FROM agent_task_results "
                "WHERE tenant_id = :tid AND created_at >= :since "
                "GROUP BY agent_id, agent_type, domain "
                "ORDER BY total DESC LIMIT :lim"
            ),
            {"tid": str(tid), "since": start, "lim": limit},
        )
        rows = result.all()

    return [
        AgentCostRow(
            agent_id=str(r[0]),
            agent_type=str(r[1]),
            domain=str(r[2]),
            total_usd=round(float(r[3] or 0), 4),
            task_count=int(r[4] or 0),
        )
        for r in rows
    ]
