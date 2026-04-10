"""KPI endpoints -- CxO executive dashboards with real cache + DB fallback.

Each endpoint:
  1. Reads from Redis-backed KPICache (hot layer).
  2. Falls back to agent_task_results table (last 24h).
  3. Computes real metrics from agent_task_results when no cache exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.kpi_cache import KPICache

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Role-to-domain mapping for agent_task_results queries ──────────────
ROLE_DOMAIN_MAP: dict[str, list[str]] = {
    "ceo": ["finance", "hr", "marketing", "operations", "legal", "strategy"],
    "cfo": ["finance", "treasury", "tax", "compliance"],
    "chro": ["hr", "payroll", "recruitment"],
    "cmo": ["marketing", "sales", "content"],
    "coo": ["operations", "it", "support", "facilities"],
    "cbo": ["legal", "risk", "corporate", "comms"],
}


# ── Real metrics computation from agent_task_results ────────────────────


async def _compute_basic_metrics(
    tenant_id: str, role: str, domains: list[str],
) -> dict:
    """Compute real KPI metrics from agent_task_results for any CxO role.

    Returns honest zeros when no data exists — never fabricated numbers.
    """
    from core.database import get_tenant_session

    try:
        async with get_tenant_session(tenant_id) as session:
            # Aggregate stats from last 30 days
            cutoff = datetime.now(UTC) - timedelta(days=30)
            rows = (
                await session.execute(
                    text(
                        "SELECT domain, status, COUNT(*) as cnt, "
                        "       AVG(confidence) as avg_conf, "
                        "       SUM(tokens_used) as total_tokens, "
                        "       SUM(cost_usd) as total_cost, "
                        "       AVG(duration_ms) as avg_duration, "
                        "       COUNT(*) FILTER (WHERE hitl_required) as hitl_count "
                        "FROM agent_task_results "
                        "WHERE tenant_id = :tid "
                        "  AND domain = ANY(:domains) "
                        "  AND created_at > :cutoff "
                        "GROUP BY domain, status"
                    ),
                    {"tid": tenant_id, "domains": domains, "cutoff": cutoff},
                )
            ).all()

            # Build per-domain breakdown
            domain_stats: dict[str, dict] = {}
            total_tasks = 0
            total_success = 0
            total_hitl = 0
            total_cost = 0.0

            for r in rows:
                d = r.domain
                if d not in domain_stats:
                    domain_stats[d] = {
                        "total": 0, "completed": 0, "failed": 0,
                        "avg_confidence": 0.0, "hitl_count": 0,
                    }
                domain_stats[d]["total"] += r.cnt
                total_tasks += r.cnt
                if r.status == "completed":
                    domain_stats[d]["completed"] += r.cnt
                    total_success += r.cnt
                elif r.status == "failed":
                    domain_stats[d]["failed"] += r.cnt
                domain_stats[d]["avg_confidence"] = round(
                    float(r.avg_conf or 0), 3
                )
                domain_stats[d]["hitl_count"] += r.hitl_count or 0
                total_hitl += r.hitl_count or 0
                total_cost += float(r.total_cost or 0)

            # Count active agents
            agent_count_row = await session.execute(
                text(
                    "SELECT COUNT(*) FROM agents "
                    "WHERE tenant_id = :tid AND domain = ANY(:domains) "
                    "AND status IN ('active', 'shadow')"
                ),
                {"tid": tenant_id, "domains": domains},
            )
            agent_count = (agent_count_row.scalar() or 0)

            success_rate = round(
                (total_success / total_tasks * 100) if total_tasks > 0 else 0, 1
            )

            return {
                "agent_count": agent_count,
                "total_tasks_30d": total_tasks,
                "success_rate": success_rate,
                "hitl_interventions": total_hitl,
                "total_cost_usd": round(total_cost, 2),
                "domain_breakdown": [
                    {"domain": d, **s} for d, s in domain_stats.items()
                ],
            }
    except Exception:
        logging.getLogger(__name__).debug("compute_basic_metrics_failed: %s %s", tenant_id, role)
        return {
            "agent_count": 0,
            "total_tasks_30d": 0,
            "success_rate": 0,
            "hitl_interventions": 0,
            "total_cost_usd": 0,
            "domain_breakdown": [],
        }





# ── Helpers ────────────────────────────────────────────────────────────

async def _query_agent_results(
    tenant_id: str, domains: list[str], hours: int = 24, limit: int = 50
) -> list[dict]:
    """Query recent agent_task_results for the given domains."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT agent_type, domain, task_type, task_output, "
                        "       confidence, status, created_at "
                        "FROM agent_task_results "
                        "WHERE domain = ANY(:domains) "
                        "  AND created_at > :cutoff "
                        "ORDER BY created_at DESC "
                        "LIMIT :lim"
                    ),
                    {"domains": domains, "cutoff": cutoff, "lim": limit},
                )
            ).all()
            return [
                {
                    "agent_type": r.agent_type,
                    "domain": r.domain,
                    "task_type": r.task_type,
                    "task_output": r.task_output,
                    "confidence": r.confidence,
                    "status": r.status,
                    "created_at": r.created_at.isoformat()
                    if hasattr(r.created_at, "isoformat")
                    else str(r.created_at),
                }
                for r in rows
            ]
    except Exception:
        logger.debug("agent_task_results query failed", exc_info=True)
        return []


async def _count_pending_approvals(tenant_id: str) -> int:
    """Count pending filing approvals for a tenant."""
    try:
        async with get_tenant_session(tenant_id) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) AS cnt FROM filing_approvals "
                        "WHERE status = 'pending'"
                    )
                )
            ).first()
            return row.cnt if row else 0
    except Exception:
        logger.debug("filing_approvals count failed", exc_info=True)
        return 0


async def _get_tax_calendar(tenant_id: str) -> list[dict]:
    """Get upcoming compliance deadlines for a tenant."""
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT deadline_type, filing_period, due_date, filed "
                        "FROM compliance_deadlines "
                        "WHERE due_date >= CURRENT_DATE "
                        "ORDER BY due_date ASC LIMIT 10"
                    )
                )
            ).all()
            return [
                {
                    "filing": f"{r.deadline_type} ({r.filing_period})",
                    "due_date": r.due_date.isoformat()
                    if hasattr(r.due_date, "isoformat")
                    else str(r.due_date),
                    "status": "filed" if r.filed else "pending",
                }
                for r in rows
            ]
    except Exception:
        logger.debug("compliance_deadlines query failed", exc_info=True)
        return []


async def _get_recent_escalations(tenant_id: str, limit: int = 5) -> list[dict]:
    """Get recent HITL items for CEO attention."""
    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, title, priority, status, created_at "
                        "FROM hitl_queue "
                        "WHERE status = 'pending' "
                        "ORDER BY CASE priority "
                        "  WHEN 'critical' THEN 0 "
                        "  WHEN 'high' THEN 1 "
                        "  WHEN 'medium' THEN 2 "
                        "  ELSE 3 END, "
                        "created_at DESC "
                        "LIMIT :lim"
                    ),
                    {"lim": limit},
                )
            ).all()
            return [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "priority": r.priority,
                    "status": r.status,
                    "created_at": r.created_at.isoformat()
                    if hasattr(r.created_at, "isoformat")
                    else str(r.created_at),
                }
                for r in rows
            ]
    except Exception:
        logger.debug("hitl_queue query failed", exc_info=True)
        return []


async def _build_kpi_response(
    tenant_id: str,
    role: str,
    company_id: str,
) -> dict:
    """Unified KPI response builder.

    1. Try KPICache (Redis/PG).
    2. Fall back to agent_task_results.
    3. Return demo data if nothing available.
    """
    cache = KPICache()
    cached = await cache.get_all_for_role(tenant_id, role)

    now_iso = datetime.now(UTC).isoformat()

    if cached and not all(v.get("stale") for v in cached.values()):
        # Merge cached values into a flat dict
        merged = {}
        for metric_name, envelope in cached.items():
            val = envelope.get("value", envelope)
            if isinstance(val, dict):
                merged[metric_name] = val
            else:
                merged[metric_name] = val
        return {
            **merged,
            "demo": False,
            "stale": any(v.get("stale") for v in cached.values()),
            "cached_at": now_iso,
            "company_id": company_id,
        }

    # Fallback: query agent_task_results
    domains = ROLE_DOMAIN_MAP.get(role, [])
    results = await _query_agent_results(tenant_id, domains)
    if results:
        # Aggregate outputs from recent agent runs
        aggregated: dict = {}
        for r in results:
            output = r.get("task_output", {})
            if isinstance(output, dict):
                aggregated.update(output)
        if aggregated:
            return {
                **aggregated,
                "demo": False,
                "stale": True,
                "source": "agent_task_results",
                "cached_at": now_iso,
                "company_id": company_id,
            }

    # Final fallback: compute basic metrics from agent_task_results
    # (may return zeros if no agent has run yet — that's honest)
    basic = await _compute_basic_metrics(tenant_id, role, domains)
    return {
        **basic,
        "demo": not bool(results),  # True only if no data exists at all
        "stale": True,
        "source": "computed",
        "cached_at": now_iso,
        "company_id": company_id,
    }


# ── CEO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/ceo")
async def get_ceo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Cross-departmental overview for the CEO executive dashboard."""
    base = await _build_kpi_response(tenant_id, "ceo", company_id)

    # Enrich with live escalations if not demo
    if not base.get("demo"):
        escalations = await _get_recent_escalations(tenant_id, limit=5)
        if escalations:
            base["recent_escalations"] = escalations

    return base


# ── CFO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cfo")
async def get_cfo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Finance KPIs for the CFO executive dashboard."""
    base = await _build_kpi_response(tenant_id, "cfo", company_id)

    # Enrich with live data when available
    if not base.get("demo"):
        tax_cal = await _get_tax_calendar(tenant_id)
        if tax_cal:
            base["tax_calendar"] = tax_cal

        pending = await _count_pending_approvals(tenant_id)
        base["pending_approvals_count"] = pending

    return base


# ── CHRO KPIs ──────────────────────────────────────────────────────────

@router.get("/kpis/chro")
async def get_chro_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """HR metrics for the CHRO executive dashboard."""
    return await _build_kpi_response(tenant_id, "chro", company_id)


# ── CMO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cmo")
async def get_cmo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Marketing KPIs for the CMO executive dashboard."""
    return await _build_kpi_response(tenant_id, "cmo", company_id)


# ── COO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/coo")
async def get_coo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Operations metrics for the COO executive dashboard."""
    return await _build_kpi_response(tenant_id, "coo", company_id)


# ── CBO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cbo")
async def get_cbo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Business/back-office metrics for the CBO executive dashboard."""
    return await _build_kpi_response(tenant_id, "cbo", company_id)
