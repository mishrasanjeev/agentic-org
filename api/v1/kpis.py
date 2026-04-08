"""KPI endpoints -- CxO executive dashboards with real cache + DB fallback.

Each endpoint:
  1. Reads from Redis-backed KPICache (hot layer).
  2. Falls back to agent_task_results table (last 24h).
  3. Returns DEMO data with ``demo: true, stale: true`` when no real data exists.
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


# ── DEMO DATA (fallback when no real data) ─────────────────────────────

DEMO_DATA: dict[str, dict] = {
    "ceo": {
        "total_employees": 127,
        "revenue_mtd": 78_00_000,
        "active_incidents": 3,
        "pipeline_value": 1_42_00_000,
        "overall_health_score": 82,
        "departments": [
            {"name": "Finance", "agent_count": 8, "task_success_rate": 94.2,
             "pending_approvals": 7},
            {"name": "HR", "agent_count": 5, "task_success_rate": 91.8,
             "pending_approvals": 3},
            {"name": "Marketing", "agent_count": 6, "task_success_rate": 89.5,
             "pending_approvals": 4},
            {"name": "Operations", "agent_count": 7, "task_success_rate": 96.1,
             "pending_approvals": 2},
            {"name": "Legal", "agent_count": 4, "task_success_rate": 97.3,
             "pending_approvals": 1},
            {"name": "Strategy", "agent_count": 5, "task_success_rate": 93.0,
             "pending_approvals": 0},
        ],
        "recent_escalations": [
            {"id": "esc-001", "title": "Invoice >5L needs approval",
             "agent": "AP Agent", "priority": "high", "created_at": "2026-04-08T09:15:00Z"},
            {"id": "esc-002", "title": "GST-3B variance detected",
             "agent": "GST Agent", "priority": "medium", "created_at": "2026-04-08T08:30:00Z"},
            {"id": "esc-003", "title": "New vendor onboarding — KYC pending",
             "agent": "Vendor Agent", "priority": "medium", "created_at": "2026-04-07T16:45:00Z"},
            {"id": "esc-004", "title": "Payroll delta >2% from last month",
             "agent": "Payroll Agent", "priority": "low", "created_at": "2026-04-07T14:20:00Z"},
            {"id": "esc-005", "title": "Contract renewal — legal review needed",
             "agent": "Contract Agent", "priority": "high", "created_at": "2026-04-07T11:00:00Z"},
        ],
    },
    "cfo": {
        "cash_runway_months": 14.2,
        "cash_runway_trend": 1.8,
        "burn_rate": 18_50_000,
        "burn_rate_trend": -3.2,
        "dso_days": 42,
        "dso_trend": -5.1,
        "dpo_days": 38,
        "dpo_trend": 2.4,
        "ar_aging": {
            "0_30": 32_00_000, "31_60": 14_50_000,
            "61_90": 6_80_000, "90_plus": 2_10_000,
        },
        "ap_aging": {
            "0_30": 22_00_000, "31_60": 9_50_000,
            "61_90": 3_20_000, "90_plus": 80_000,
        },
        "monthly_pl": [
            {"month": "2026-01", "revenue": 68_00_000, "cogs": 19_00_000,
             "gross_margin": 49_00_000, "opex": 34_00_000, "net_income": 15_00_000},
            {"month": "2026-02", "revenue": 72_50_000, "cogs": 20_00_000,
             "gross_margin": 52_50_000, "opex": 35_50_000, "net_income": 17_00_000},
            {"month": "2026-03", "revenue": 78_00_000, "cogs": 21_50_000,
             "gross_margin": 56_50_000, "opex": 37_00_000, "net_income": 19_50_000},
        ],
        "bank_balances": [
            {"account": "HDFC Current A/c", "balance": 1_45_00_000, "currency": "INR"},
            {"account": "ICICI Savings A/c", "balance": 62_00_000, "currency": "INR"},
            {"account": "SBI FD", "balance": 50_00_000, "currency": "INR"},
            {"account": "Wise USD A/c", "balance": 48_500, "currency": "USD"},
        ],
        "tax_calendar": [
            {"filing": "GST-3B (March)", "due_date": "2026-04-20", "status": "pending"},
            {"filing": "TDS 26Q (Q4)", "due_date": "2026-05-15", "status": "upcoming"},
            {"filing": "Advance Tax Q1", "due_date": "2026-06-15", "status": "upcoming"},
            {"filing": "ROC Annual Filing", "due_date": "2026-09-30", "status": "upcoming"},
        ],
        "pending_approvals_count": 7,
    },
    "chro": {
        "total_employees": 127,
        "attrition_rate_monthly": 1.8,
        "new_joiners_mtd": 5,
        "open_positions": 12,
        "payroll_status": {
            "current_month": "2026-04",
            "processed": True,
            "pending": False,
            "total_ctc": 1_85_00_000,
        },
        "department_breakdown": [
            {"dept": "Engineering", "headcount": 42, "attrition": 2.1},
            {"dept": "Sales", "headcount": 18, "attrition": 3.2},
            {"dept": "Marketing", "headcount": 14, "attrition": 1.5},
            {"dept": "Finance", "headcount": 12, "attrition": 0.8},
            {"dept": "HR", "headcount": 8, "attrition": 0.0},
            {"dept": "Operations", "headcount": 15, "attrition": 1.2},
            {"dept": "Legal", "headcount": 6, "attrition": 0.0},
            {"dept": "Product", "headcount": 12, "attrition": 1.0},
        ],
        "recruitment_pipeline": {
            "applied": 340, "screened": 120, "interviewed": 45,
            "offered": 18, "accepted": 12,
        },
        "compliance": {
            "epfo_status": "compliant",
            "esi_status": "compliant",
            "pt_status": "compliant",
        },
        "engagement": {
            "enps_score": 42,
            "pulse_score": 7.2,
            "attrition_risk_count": 8,
        },
        "upcoming_events": [
            {"type": "holiday", "date": "2026-04-14", "description": "Ambedkar Jayanti"},
            {"type": "review", "date": "2026-04-30", "description": "Q1 Performance Reviews Due"},
            {"type": "training", "date": "2026-05-05", "description": "Compliance Training Deadline"},
        ],
    },
    "cmo": {
        "cac": 3_200,
        "cac_trend": -8.5,
        "mqls": 284,
        "mqls_trend": 12.3,
        "sqls": 67,
        "sqls_trend": 9.1,
        "pipeline_value": 1_42_00_000,
        "pipeline_trend": 15.6,
        "roas_by_channel": {
            "Google Ads": 4.2, "Meta Ads": 3.1,
            "LinkedIn": 2.8, "Organic": 7.6,
        },
        "email_performance": {
            "open_rate": 34.2, "click_rate": 4.8, "unsubscribe_rate": 0.3,
        },
        "social_engagement": {
            "Twitter": 12_400, "LinkedIn": 8_900, "Instagram": 15_600,
        },
        "website_traffic": {
            "sessions": 48_200, "users": 31_500, "bounce_rate": 42.1,
            "sessions_trend": [
                {"date": "2026-03-01", "sessions": 1_420},
                {"date": "2026-03-04", "sessions": 1_580},
                {"date": "2026-03-07", "sessions": 1_350},
                {"date": "2026-03-10", "sessions": 1_720},
                {"date": "2026-03-13", "sessions": 1_890},
                {"date": "2026-03-16", "sessions": 1_640},
                {"date": "2026-03-19", "sessions": 2_010},
                {"date": "2026-03-22", "sessions": 1_950},
                {"date": "2026-03-25", "sessions": 2_200},
                {"date": "2026-03-28", "sessions": 2_350},
            ],
        },
        "content_top_pages": [
            {"page": "/blog/ai-virtual-employees-guide", "views": 4_820, "avg_time_sec": 245},
            {"page": "/blog/automate-accounts-payable", "views": 3_150, "avg_time_sec": 198},
            {"page": "/pricing", "views": 2_900, "avg_time_sec": 130},
            {"page": "/blog/gst-compliance-automation", "views": 2_340, "avg_time_sec": 210},
            {"page": "/case-studies/enterprise-roi", "views": 1_870, "avg_time_sec": 310},
        ],
        "brand_sentiment_score": 78,
        "brand_sentiment_trend": 3.2,
        "pending_content_approvals": 4,
    },
    "coo": {
        "active_incidents": 3,
        "mttr_hours": 4.2,
        "ticket_volume_today": 47,
        "sla_compliance_pct": 94.8,
        "support_metrics": {
            "open_tickets": 23, "resolved_today": 31,
            "csat_score": 4.3, "deflection_rate": 38.5,
        },
        "vendor_metrics": {
            "active_vendors": 42, "contracts_expiring_30d": 5,
            "total_spend_mtd": 12_50_000,
        },
        "it_ops": {
            "uptime_pct": 99.94, "change_success_rate": 97.2,
            "pending_changes": 4,
        },
        "facilities": {
            "open_requests": 8, "asset_utilization_pct": 72.3,
        },
    },
    "cbo": {
        "legal": {
            "active_contracts": 34, "pending_reviews": 7,
            "nda_count": 12, "litigation_count": 1,
        },
        "risk": {
            "compliance_score": 91, "audit_findings_open": 3,
            "sanctions_screened_mtd": 245,
        },
        "corporate": {
            "next_board_meeting": "2026-06-15",
            "statutory_filings_due": 2,
            "agm_status": "scheduled",
        },
        "comms": {
            "internal_reach_pct": 78.5, "media_mentions_mtd": 14,
            "investor_queries_open": 3,
        },
    },
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

    # Final fallback: demo data
    demo = DEMO_DATA.get(role, {})
    return {
        **demo,
        "demo": True,
        "stale": True,
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
