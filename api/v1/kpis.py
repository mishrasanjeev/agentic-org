"""KPI endpoints -- CxO executive dashboards with real cache + DB fallback.

Each endpoint:
  1. Reads from Redis-backed KPICache (hot layer).
  2. Falls back to agent_task_results table (last 24h).
  3. Computes real metrics from agent_task_results when no cache exists.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from api.deps import get_current_tenant
from api.route_metadata import route_meta
from core.config import is_strict_runtime_env, settings
from core.database import get_tenant_session
from core.kpi_cache import KPICache
from core.marketing.approval_review import build_cmo_approval_review_projection
from core.marketing.approval_timeouts import build_approval_timeout_risk
from core.marketing.connector_contracts import (
    build_marketing_connector_contracts,
    summarize_marketing_connector_contracts,
)
from core.marketing.connector_setup import (
    build_marketing_connector_setup,
    marketing_connector_keys,
    summarize_marketing_connector_setup,
)
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.decision_audit import build_marketing_decision_audit_projection
from core.marketing.escalation_matrix import build_marketing_escalation_projection
from core.marketing.kpi_drilldown import build_cmo_kpi_drilldown_projection
from core.marketing.kpi_schema import build_unified_cmo_kpi_projection, collect_cmo_kpi_facts
from core.marketing.pilot_proof import build_cmo_pilot_proof_projection
from core.marketing.policy_manifest import build_marketing_policy_projection
from core.marketing.report_quality import build_cmo_report_quality_projection
from core.marketing.weekly_report_pilot_proof import (
    build_weekly_marketing_report_proof_projection,
)
from core.marketing.work_queue import build_cmo_work_queue_projection
from core.marketing.workflow_activation import build_cmo_workflow_activation

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Role-to-domain mapping for agent_task_results queries ──────────────
# enterprise-gate: process-local-ok reason=static-rbac-domain-query-map
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
    company_id: str | None = None,
) -> dict:
    """Compute real KPI metrics from agent_task_results for any CxO role.

    Returns honest zeros when no data exists — never fabricated numbers.

    Codex 2026-04-22 multi-company isolation fix: previously the SQL
    ignored the ``company_id`` the endpoint accepted, so the Finance
    dashboard showed tenant-wide numbers even when the user had picked
    a specific company. Both ``agent_task_results`` and ``agents``
    carry a nullable ``company_id`` column, so we can filter honestly
    when the caller scopes the request. When ``company_id`` is None or
    the sentinel "default" (legacy meaning: no scope), we keep the
    tenant-wide aggregate so admin tooling that does not pass a real
    company id keeps working.
    """
    from core.database import get_tenant_session

    # Parse company_id; treat blanks / "default" / malformed UUIDs as
    # no-scope so the legacy tenant-wide view keeps working.
    company_uuid: str | None = None
    if company_id and company_id not in ("", "default", "all"):
        try:
            import uuid as _uuid_mod
            company_uuid = str(_uuid_mod.UUID(company_id))
        except (TypeError, ValueError):
            company_uuid = None

    params: dict[str, Any] = {
        "tid": tenant_id, "domains": domains,
    }
    if company_uuid is not None:
        params["cid"] = company_uuid

    try:
        async with get_tenant_session(tenant_id) as session:
            # Aggregate stats from last 30 days. Two literal statements
            # rather than string-concat so ruff's S608 SQLi check stays
            # satisfied (and so a reader can see at a glance that no
            # user input flows into the SQL).
            cutoff = datetime.now(UTC) - timedelta(days=30)
            q_params = {**params, "cutoff": cutoff}
            if company_uuid is not None:
                agg_sql = (
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
                    "  AND company_id = :cid "
                    "GROUP BY domain, status"
                )
            else:
                agg_sql = (
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
                )
            rows = (
                await session.execute(text(agg_sql), q_params)
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

            # Count active agents (honoring the same company scope).
            # When a company scope is active, use the with-company SQL;
            # otherwise use the plain one. This avoids string-concat
            # entirely so ruff's S608 SQLi heuristic stays happy, and
            # the two statements are obvious pure-literal strings.
            if company_uuid is not None:
                agent_count_row = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM agents "
                        "WHERE tenant_id = :tid AND domain = ANY(:domains) "
                        "AND status IN ('active', 'shadow') "
                        "AND company_id = :cid"
                    ),
                    params,
                )
            else:
                agent_count_row = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM agents "
                        "WHERE tenant_id = :tid AND domain = ANY(:domains) "
                        "AND status IN ('active', 'shadow')"
                    ),
                    params,
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
    except SQLAlchemyError:
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

# Helper: map the sentinel 'default'/'all'/'' and malformed UUIDs to
# None so the KPI SQL below honours the same legacy contract as
# _compute_basic_metrics. Keeps tenant-wide reads for admin tooling
# that doesn't pass a real company id.
def _parse_company_uuid(company_id: str | None) -> str | None:
    if company_id and company_id not in ("", "default", "all"):
        try:
            import uuid as _uuid_mod
            return str(_uuid_mod.UUID(company_id))
        except (TypeError, ValueError):
            return None
    return None


async def _query_agent_results(
    tenant_id: str,
    domains: list[str],
    hours: int = 24,
    limit: int = 50,
    company_id: str | None = None,
) -> list[dict]:
    """Query recent agent_task_results for the given domains.

    Codex 2026-04-22 release-signoff review: the helper ignored the
    caller's ``company_id`` so CEO/CFO/CMO dashboards showed tenant-
    wide rows even when the switcher was scoped. Now honours the
    same company_uuid contract as _compute_basic_metrics — unscoped
    reads keep the tenant-wide contract admin tooling expects.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    company_uuid = _parse_company_uuid(company_id)

    if company_uuid is not None:
        sql = (
            "SELECT agent_type, domain, task_type, task_output, "
            "       confidence, status, created_at "
            "FROM agent_task_results "
            "WHERE domain = ANY(:domains) "
            "  AND created_at > :cutoff "
            "  AND company_id = :cid "
            "ORDER BY created_at DESC "
            "LIMIT :lim"
        )
        params: dict[str, Any] = {
            "domains": domains, "cutoff": cutoff,
            "lim": limit, "cid": company_uuid,
        }
    else:
        sql = (
            "SELECT agent_type, domain, task_type, task_output, "
            "       confidence, status, created_at "
            "FROM agent_task_results "
            "WHERE domain = ANY(:domains) "
            "  AND created_at > :cutoff "
            "ORDER BY created_at DESC "
            "LIMIT :lim"
        )
        params = {"domains": domains, "cutoff": cutoff, "lim": limit}

    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (await session.execute(text(sql), params)).all()
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
    except SQLAlchemyError:
        logger.debug("agent_task_results query failed", exc_info=True)
        return []


async def _count_pending_approvals(
    tenant_id: str, company_id: str | None = None,
) -> int:
    """Count pending filing approvals, optionally scoped to a company."""
    company_uuid = _parse_company_uuid(company_id)

    if company_uuid is not None:
        sql = (
            "SELECT COUNT(*) AS cnt FROM filing_approvals "
            "WHERE status = 'pending' AND company_id = :cid"
        )
        params: dict[str, Any] = {"cid": company_uuid}
    else:
        sql = (
            "SELECT COUNT(*) AS cnt FROM filing_approvals "
            "WHERE status = 'pending'"
        )
        params = {}

    try:
        async with get_tenant_session(tenant_id) as session:
            row = (await session.execute(text(sql), params)).first()
            return row.cnt if row else 0
    except SQLAlchemyError:
        logger.debug("filing_approvals count failed", exc_info=True)
        return 0


async def _get_tax_calendar(
    tenant_id: str, company_id: str | None = None,
) -> list[dict]:
    """Get upcoming compliance deadlines, optionally scoped to a company."""
    company_uuid = _parse_company_uuid(company_id)

    if company_uuid is not None:
        sql = (
            "SELECT deadline_type, filing_period, due_date, filed "
            "FROM compliance_deadlines "
            "WHERE due_date >= CURRENT_DATE AND company_id = :cid "
            "ORDER BY due_date ASC LIMIT 10"
        )
        params: dict[str, Any] = {"cid": company_uuid}
    else:
        sql = (
            "SELECT deadline_type, filing_period, due_date, filed "
            "FROM compliance_deadlines "
            "WHERE due_date >= CURRENT_DATE "
            "ORDER BY due_date ASC LIMIT 10"
        )
        params = {}

    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (await session.execute(text(sql), params)).all()
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
    except SQLAlchemyError:
        logger.debug("compliance_deadlines query failed", exc_info=True)
        return []


async def _get_recent_escalations(
    tenant_id: str, limit: int = 5, company_id: str | None = None,
) -> list[dict]:
    """Get recent HITL items for CEO attention, optionally scoped."""
    company_uuid = _parse_company_uuid(company_id)

    if company_uuid is not None:
        sql = (
            "SELECT id, title, priority, status, created_at "
            "FROM hitl_queue "
            "WHERE status = 'pending' AND company_id = :cid "
            "ORDER BY CASE priority "
            "  WHEN 'critical' THEN 0 "
            "  WHEN 'high' THEN 1 "
            "  WHEN 'medium' THEN 2 "
            "  ELSE 3 END, "
            "created_at DESC "
            "LIMIT :lim"
        )
        params: dict[str, Any] = {"cid": company_uuid, "lim": limit}
    else:
        sql = (
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
        )
        params = {"lim": limit}

    try:
        async with get_tenant_session(tenant_id) as session:
            rows = (await session.execute(text(sql), params)).all()
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
    except SQLAlchemyError:
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

    # Compute structured KPIs from agent_task_results.
    # Always returns the shape the dashboards expect: agent_count,
    # total_tasks_30d, success_rate, hitl_interventions, total_cost_usd,
    # domain_breakdown[]. Never returns raw task_output dicts — that
    # produced NaN in the UI when fields like "items" or "result"
    # were passed through instead of numeric KPIs.
    domains = ROLE_DOMAIN_MAP.get(role, [])
    basic = await _compute_basic_metrics(tenant_id, role, domains, company_id)
    has_data = basic.get("total_tasks_30d", 0) > 0

    return {
        **basic,
        "demo": not has_data,
        "stale": True,
        "source": "computed",
        "cached_at": now_iso,
        "company_id": company_id,
    }


async def _load_marketing_connector_configs(
    tenant_id: str,
    company_id: str | None = None,
) -> list[Any]:
    """Load exact-scope ConnectorConfig rows for CMO readiness projections.

    ``default``/``all``/blank retain the tenant-global contract.  A malformed
    explicit company selector fails closed instead of falling back to those
    global credentials.
    """

    from core.models.connector_config import ConnectorConfig

    connector_keys = marketing_connector_keys()
    company_uuid_text = _parse_company_uuid(company_id)
    explicit_company = bool(company_id and company_id not in ("", "default", "all"))
    if explicit_company and company_uuid_text is None:
        logger.warning(
            "cmo_connector_setup_invalid_company_scope",
            extra={"tenant_id": tenant_id},
        )
        return []
    company_uuid = _uuid.UUID(company_uuid_text) if company_uuid_text else None
    try:
        async with get_tenant_session(tenant_id, company_uuid) as session:
            rows = (
                await session.execute(
                    select(ConnectorConfig).where(
                        ConnectorConfig.tenant_id == tenant_id,
                        (
                            ConnectorConfig.company_id == company_uuid
                            if company_uuid is not None
                            else ConnectorConfig.company_id.is_(None)
                        ),
                        ConnectorConfig.connector_name.in_(connector_keys)
                    )
                )
            ).scalars().all()
    except SQLAlchemyError:
        logger.debug("cmo_connector_setup_query_failed", exc_info=True)
        return []
    return list(rows)


async def _load_latest_weekly_report_pilot_proof(
    tenant_id: str, company_id: str | None
) -> dict[str, Any] | None:
    """Load the most recent persisted weekly-report pilot proof for the tenant.

    Returns ``None`` when no row exists or the DB query fails — the
    caller falls back to exposing only the ad-hoc CMO-PROD-1 projection.
    """

    try:
        from core.marketing.weekly_report_pilot_persistence import (
            latest_weekly_report_pilot_proof,
            serialize_persisted_proof,
            summarize_persisted_proof,
        )
    except ImportError:
        return None

    try:
        async with get_tenant_session(tenant_id) as session:
            row = await latest_weekly_report_pilot_proof(
                session,
                tenant_id=tenant_id,
                company_id=company_id if company_id and company_id != "default" else None,
            )
    except SQLAlchemyError:
        logger.debug("cmo_weekly_report_pilot_proof_query_failed", exc_info=True)
        return None
    # enterprise-gate: broad-except-ok reason=missing-table-pre-migration-returns-no-pilot-proof
    except Exception:  # noqa: BLE001
        logger.debug("cmo_weekly_report_pilot_proof_lookup_failed", exc_info=True)
        return None
    if row is None:
        return None
    return {
        "latest_weekly_report_pilot_proof": serialize_persisted_proof(row),
        "latest_weekly_report_pilot_proof_summary": summarize_persisted_proof(row),
    }


async def _load_marketing_connector_setup(
    tenant_id: str,
    company_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load CMO connector setup states from existing ConnectorConfig rows."""

    rows = await _load_marketing_connector_configs(tenant_id, company_id)
    return build_marketing_connector_setup(rows)


async def _load_cmo_approval_timeout_risk(
    tenant_id: str,
    company_id: str | None = None,
) -> dict[str, Any]:
    """Project pending marketing HITL approvals into timeout risk state."""

    from core.models.agent import Agent
    from core.models.hitl import HITLQueue

    company_uuid = _parse_company_uuid(company_id)
    try:
        async with get_tenant_session(tenant_id) as session:
            query = (
                select(HITLQueue, Agent)
                .join(Agent, Agent.id == HITLQueue.agent_id)
                .where(
                    HITLQueue.tenant_id == tenant_id,
                    HITLQueue.status == "pending",
                    Agent.domain.in_(["marketing", "content", "sales"]),
                )
            )
            if company_uuid is not None:
                query = query.where(Agent.company_id == company_uuid)
            rows = (await session.execute(query)).all()
    except SQLAlchemyError:
        logger.debug("cmo_approval_timeout_risk_query_failed", exc_info=True)
        return build_approval_timeout_risk([])

    approvals: list[dict[str, Any]] = []
    for item, agent in rows:
        context = item.context if isinstance(item.context, dict) else {}
        approvals.append(
            {
                **context,
                "approval_id": str(item.id),
                "title": item.title,
                "workflow_run_id": str(item.workflow_run_id) if item.workflow_run_id else None,
                "workflow_id": context.get("workflow_id"),
                "step_id": context.get("step_id"),
                "action": (
                    context.get("approval_action")
                    or context.get("action")
                    or context.get("blocked_action")
                    or item.trigger_type
                ),
                "approval_type": context.get("approval_type"),
                "requested_approver": context.get("requested_approver"),
                "requested_approver_role": item.assignee_role,
                "agent_id": str(agent.id),
                "agent_type": agent.agent_type,
                "assignee_role": item.assignee_role,
                "decision_options": item.decision_options,
                "status": item.status,
                "created_at": item.created_at,
                "due_at": item.expires_at,
            }
        )
    risk = build_approval_timeout_risk(approvals)
    risk["approval_records"] = approvals
    return risk


def _attach_approval_review_refs(
    approval_timeout_risk: dict[str, Any],
    approval_reviews: list[dict[str, Any]],
) -> None:
    """Link timeout-risk decisions to their richer CMO approval review rows."""

    by_approval_id = {
        str(review.get("approval_id")): review
        for review in approval_reviews
        if review.get("approval_id")
    }
    for decision in approval_timeout_risk.get("approval_timeout_decisions") or []:
        if not isinstance(decision, dict):
            continue
        review = by_approval_id.get(str(decision.get("approval_id")))
        if not review:
            continue
        decision["approval_review_id"] = review.get("approval_review_id")
        decision["approval_review_status"] = review.get("status")


def _apply_cmo_production_data_policy(
    base: dict[str, Any],
    connector_summary: dict[str, int | bool | str],
    kpi_readiness: dict[str, Any] | None = None,
    connector_contract_summary: dict[str, Any] | None = None,
    *,
    strict_runtime: bool,
) -> dict[str, Any]:
    """Prevent strict-runtime CMO paths from presenting demo KPI fallback."""

    if not strict_runtime:
        return base

    readiness = str(connector_summary.get("readiness") or "")
    kpi_status = str((kpi_readiness or {}).get("status") or "")
    contract_readiness = str((connector_contract_summary or {}).get("readiness") or "")
    mock_or_test_double = int((connector_contract_summary or {}).get("mock_or_test_double") or 0)
    blocked_by_readiness = kpi_status == "blocked" or contract_readiness == "blocked" or mock_or_test_double > 0
    degraded_by_readiness = kpi_status == "degraded" or contract_readiness == "degraded"

    if base.get("demo") or blocked_by_readiness:
        data_coverage_status = "blocked_mapping_or_backfill"
        if base.get("demo"):
            data_coverage_status = "blocked_setup" if readiness != "ready" else "empty_real_data"
        return {
            **base,
            "demo": False,
            "demo_suppressed": bool(base.get("demo")),
            "production_data_blocked": True,
            "kpi_confidence_status": "blocked",
            "data_coverage_status": data_coverage_status,
            "source": "empty_real_tenant" if base.get("demo") else base.get("source"),
            "message": (
                "CMO KPI readiness is blocked for this production tenant. "
                "Configure connectors, complete field mappings, and finish "
                "historical backfill with production connector contracts before "
                "treating these KPIs as production-ready."
            ),
        }

    if degraded_by_readiness:
        return {
            **base,
            "production_data_blocked": False,
            "kpi_confidence_status": "degraded",
            "data_coverage_status": "degraded_mapping_or_backfill",
            "message": (
                "CMO KPI readiness is degraded because mappings or historical "
                "backfill or connector contracts are incomplete. Treat KPI "
                "values as directional."
            ),
        }

    return {
        **base,
        "production_data_blocked": False,
        "kpi_confidence_status": "ready",
    }


async def _build_cmo_kpi_response(tenant_id: str, company_id: str) -> dict[str, Any]:
    base = await _build_kpi_response(tenant_id, "cmo", company_id)
    connector_configs = await _load_marketing_connector_configs(tenant_id, company_id)
    connector_setup = build_marketing_connector_setup(connector_configs)
    connector_summary = summarize_marketing_connector_setup(connector_setup)
    connector_contracts = build_marketing_connector_contracts(
        connector_setup,
        connector_configs,
    )
    connector_contract_summary = summarize_marketing_connector_contracts(connector_contracts)
    data_readiness = build_marketing_data_readiness(
        connector_setup,
        connector_configs,
        connector_contracts=connector_contracts,
    )
    workflow_activation = build_cmo_workflow_activation(
        connector_setup,
        data_readiness,
        connector_configs,
        connector_contracts=connector_contracts,
    )
    policy_projection = build_marketing_policy_projection(connector_configs)
    escalation_projection = build_marketing_escalation_projection(connector_configs)
    decision_audit_projection = build_marketing_decision_audit_projection(connector_configs)
    unified_kpi_projection = build_unified_cmo_kpi_projection(
        connector_setup=connector_setup,
        data_readiness=data_readiness,
        connector_contracts=connector_contracts,
        connector_configs=connector_configs,
    )
    approval_timeout_risk = await _load_cmo_approval_timeout_risk(tenant_id, company_id)
    approval_review_projection = build_cmo_approval_review_projection(
        approval_timeout_risk.get("approval_records") or [],
        connector_contracts=connector_contracts,
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        decision_audit_projection=decision_audit_projection,
        approval_timeout_risk=approval_timeout_risk,
    )
    _attach_approval_review_refs(
        approval_timeout_risk,
        approval_review_projection["cmo_approval_reviews"],
    )
    base = _apply_cmo_production_data_policy(
        base,
        connector_summary,
        data_readiness["kpi_readiness"],
        connector_contract_summary,
        strict_runtime=is_strict_runtime_env(settings.env),
    )
    report_quality_projection = build_cmo_report_quality_projection(
        kpi_results=unified_kpi_projection["unified_cmo_kpi_results"],
        reconciliation_checks=unified_kpi_projection["cmo_kpi_reconciliation_checks"],
        connector_setup=connector_setup,
        data_readiness=data_readiness,
        connector_contracts=connector_contracts,
        workflow_activation=workflow_activation,
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        decision_audit_projection=decision_audit_projection,
        source_context=base,
        production_tenant=is_strict_runtime_env(settings.env),
    )
    work_queue_projection = build_cmo_work_queue_projection(
        approval_timeout_risk=approval_timeout_risk,
        escalation_projection=escalation_projection,
        connector_setup=connector_setup,
        connector_contracts=connector_contracts,
        data_readiness=data_readiness,
        workflow_activation=workflow_activation,
        policy_projection=policy_projection,
        decision_audit_projection=decision_audit_projection,
        source_context={**base, **approval_review_projection},
        kpi_results=unified_kpi_projection["unified_cmo_kpi_results"],
        reconciliation_checks=unified_kpi_projection["cmo_kpi_reconciliation_checks"],
        report_quality_gates=report_quality_projection["report_quality_gates"],
    )
    kpi_source_facts = collect_cmo_kpi_facts(connector_configs=connector_configs)
    kpi_drilldown_projection = build_cmo_kpi_drilldown_projection(
        kpi_schema=unified_kpi_projection["unified_cmo_kpi_schema"],
        kpi_results=unified_kpi_projection["unified_cmo_kpi_results"],
        reconciliation_checks=unified_kpi_projection["cmo_kpi_reconciliation_checks"],
        connector_setup=connector_setup,
        data_readiness=data_readiness,
        connector_contracts=connector_contracts,
        work_queue=work_queue_projection["cmo_work_queue"],
        report_quality_gates=report_quality_projection["report_quality_gates"],
        source_data=kpi_source_facts,
        source_context=base,
    )
    weekly_report_proof_projection = build_weekly_marketing_report_proof_projection(
        {
            "tenant_id": tenant_id,
            "company_id": company_id,
            "environment_type": base.get("cmo_pilot_environment_type")
            or base.get("pilot_environment_type")
            or base.get("environment_type"),
            "connector_evidence": connector_setup,
            "mapping_evidence": data_readiness.get("field_mapping_status") or [],
            "backfill_evidence": data_readiness.get("backfill_status") or [],
            "kpi_results": unified_kpi_projection["unified_cmo_kpi_results"],
            "reconciliation_checks": unified_kpi_projection["cmo_kpi_reconciliation_checks"],
            "report_quality_gates": report_quality_projection["report_quality_gates"],
            "report_artifact_refs": base.get("weekly_report_artifact_refs")
            or base.get("report_artifact_refs")
            or [],
            "decision_audit_refs": base.get("weekly_report_audit_refs")
            or base.get("decision_audit_refs")
            or [],
            "source_refs": base.get("weekly_report_source_refs") or [],
            "source_context": base,
        },
    )
    pilot_proof_projection = build_cmo_pilot_proof_projection(
        tenant_id=tenant_id,
        company_id=company_id,
        source_context=base,
        connector_setup=connector_setup,
        connector_contracts=connector_contracts,
        data_readiness=data_readiness,
        workflow_activation=workflow_activation,
        workflow_lint_results=base.get("workflow_lint_results") or base.get("marketing_workflow_lint_results") or [],
        policy_projection=policy_projection,
        escalation_projection=escalation_projection,
        approval_timeout_risk=approval_timeout_risk,
        external_write_results=base.get("external_write_results") or base.get("marketing_external_write_results") or [],
        decision_audit_projection=decision_audit_projection,
        kpi_schema=unified_kpi_projection["unified_cmo_kpi_schema"],
        kpi_results=unified_kpi_projection["unified_cmo_kpi_results"],
        reconciliation_checks=unified_kpi_projection["cmo_kpi_reconciliation_checks"],
        report_quality_gates=report_quality_projection["report_quality_gates"],
        work_queue=work_queue_projection["cmo_work_queue"],
        kpi_drilldowns=kpi_drilldown_projection["cmo_kpi_drilldowns"],
        approval_review_projection=approval_review_projection,
        agent_contracts=base.get("marketing_agent_contracts") or base.get("agent_contracts") or [],
        scenario_evidence=base.get("cmo_scenario_evidence") or [],
        chaos_evidence=base.get("cmo_chaos_evidence") or [],
    )
    latest_weekly_report_persisted = await _load_latest_weekly_report_pilot_proof(
        tenant_id, company_id
    )
    response: dict[str, Any] = {
        **base,
        "connector_setup": connector_setup,
        "connector_setup_summary": connector_summary,
        "connector_contracts": connector_contracts,
        "connector_contract_summary": connector_contract_summary,
        **data_readiness,
        **policy_projection,
        **escalation_projection,
        **decision_audit_projection,
        **unified_kpi_projection,
        **report_quality_projection,
        **approval_review_projection,
        **work_queue_projection,
        **kpi_drilldown_projection,
        **pilot_proof_projection,
        **weekly_report_proof_projection,
        **workflow_activation,
        "approval_timeout_risk": approval_timeout_risk,
    }
    if latest_weekly_report_persisted is not None:
        response.update(latest_weekly_report_persisted)
    return response


# ── CEO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/ceo")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.ceo",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.ceo.read",
)
async def get_ceo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Cross-departmental overview for the CEO executive dashboard."""
    base = await _build_kpi_response(tenant_id, "ceo", company_id)

    # Enrich with live escalations if not demo
    if not base.get("demo"):
        escalations = await _get_recent_escalations(
            tenant_id, limit=5, company_id=company_id,
        )
        if escalations:
            base["recent_escalations"] = escalations

    return base


# ── CFO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cfo")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.cfo",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.cfo.read",
)
async def get_cfo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Finance KPIs for the CFO executive dashboard."""
    base = await _build_kpi_response(tenant_id, "cfo", company_id)

    # Enrich with live data when available — all helpers honour the
    # same company scope so the CFO board stays consistent with the
    # company switcher the UI sent.
    if not base.get("demo"):
        tax_cal = await _get_tax_calendar(tenant_id, company_id=company_id)
        if tax_cal:
            base["tax_calendar"] = tax_cal

        pending = await _count_pending_approvals(
            tenant_id, company_id=company_id,
        )
        base["pending_approvals_count"] = pending

    return base


# ── CHRO KPIs ──────────────────────────────────────────────────────────

@router.get("/kpis/chro")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.chro",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.chro.read",
)
async def get_chro_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """HR metrics for the CHRO executive dashboard."""
    return await _build_kpi_response(tenant_id, "chro", company_id)


# ── CMO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cmo")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.cmo",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.cmo.read",
)
async def get_cmo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Marketing KPIs for the CMO executive dashboard."""
    return await _build_cmo_kpi_response(tenant_id, company_id)


# ── COO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/coo")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.coo",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.coo.read",
)
async def get_coo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Operations metrics for the COO executive dashboard."""
    return await _build_kpi_response(tenant_id, "coo", company_id)


# ── CBO KPIs ───────────────────────────────────────────────────────────

@router.get("/kpis/cbo")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="kpis.executive.sensitive.cbo",
    rate_limit="business-metrics-read",
    idempotency="read-through-cache",
    audit_event="kpis.cbo.read",
)
async def get_cbo_kpis(
    tenant_id: str = Depends(get_current_tenant),
    company_id: str = Query("default", description="Multi-company selector"),
):
    """Business/back-office metrics for the CBO executive dashboard."""
    return await _build_kpi_response(tenant_id, "cbo", company_id)
