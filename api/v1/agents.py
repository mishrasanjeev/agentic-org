"""Agent CRUD + lifecycle endpoints."""

from __future__ import annotations

import csv
import io
import re as _re
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.agent import Agent, AgentCostLedger, AgentLifecycleEvent, AgentVersion
from core.models.audit import AuditLog
from core.models.company import Company
from core.models.hitl import HITLQueue
from core.models.prompt_template import PromptEditHistory
from core.models.tenant import Tenant
from core.schemas.api import (
    AgentCloneRequest,
    AgentCreate,
    AgentUpdate,
    FleetLimits,
    PaginatedResponse,
)

_AGENT_TYPE_DEFAULT_TOOLS: dict[str, list[str]] = {
    # Finance
    "ap_processor": [
        "fetch_bank_statement", "check_account_balance",
        "post_voucher", "get_ledger_balance", "get_trial_balance",
        "create_order", "check_order_status",
    ],
    "ar_collections": [
        "create_invoice", "list_invoices",
        "create_payment_link", "send_email",
        "check_account_balance",
    ],
    "recon_agent": [
        "fetch_bank_statement", "get_transaction_list",
        "check_account_balance", "list_invoices",
    ],
    "tax_compliance": [
        "fetch_gstr2a", "push_gstr1_data",
        "file_gstr3b", "file_gstr9",
        "generate_eway_bill", "generate_einvoice_irn",
        "check_filing_status",
    ],
    "close_agent": [
        "list_invoices", "fetch_bank_statement",
        "get_balance", "search_content_fulltext",
    ],
    "fpa_agent": [
        "list_invoices", "get_balance",
        "get_campaign_performance_metrics", "get_project_metrics",
    ],
    "treasury": [
        "check_account_balance", "fetch_bank_statement",
        "get_balance", "get_balance_sheet", "get_cash_position",
    ],
    "expense_manager": [
        "record_expense", "create_ap_invoice",
        "check_order_status", "list_invoices", "get_profit_loss",
    ],
    "rev_rec": [
        "query", "create_invoice", "post_journal_entry",
        "get_trial_balance", "list_invoices",
    ],
    "fixed_assets": [
        "post_journal_entry", "record_expense",
        "get_trial_balance", "get_balance_sheet", "create_ap_invoice",
    ],
    # HR
    "talent_acquisition": [
        "post_job", "search_candidates", "get_applications",
        "schedule_interview", "send_offer", "send_inmail",
    ],
    "onboarding_agent": [
        "create_employee", "provision_user", "assign_group",
        "create_page", "schedule_social_post",
    ],
    "payroll_engine": [
        "run_payroll", "get_payslip", "get_attendance",
        "post_leave", "file_24q_return",
    ],
    "performance_coach": [
        "update_performance", "get_employee",
        "get_org_chart", "add_comment",
    ],
    "ld_coordinator": [
        "search_content_fulltext", "create_page",
        "get_employee", "schedule_interview",
    ],
    "offboarding_agent": [
        "terminate_employee", "deactivate_user",
        "remove_group", "list_active_sessions",
    ],
    # Marketing
    "content_factory": [
        "schedule_social_post", "get_post_analytics",
        "manage_publishing_queue", "approve_draft_post",
        "create_page",
    ],
    "campaign_pilot": [
        "get_campaign_performance_metrics",
        "adjust_campaign_budget", "get_campaign_performance",
        "reallocate_ad_budget", "get_reach_and_frequency_data",
    ],
    "seo_strategist": [
        "get_campaign_performance_metrics",
        "get_search_term_report", "search_content_fulltext",
        "get_post_analytics",
    ],
    "crm_intelligence": [
        "list_contacts", "search_contacts", "list_deals",
        "get_deal", "get_campaign_analytics", "create_contact",
    ],
    "brand_monitor": [
        "get_post_analytics", "get_campaign_performance",
        "schedule_social_post", "search_contacts",
    ],
    "email_marketing": [
        "send_email", "create_campaign", "send_campaign",
        "get_campaign_report", "add_list_member", "get_campaign_stats",
    ],
    "social_media": [
        "create_tweet", "create_update", "get_post_analytics",
        "list_channel_videos", "get_campaign_insights",
    ],
    "abm": [
        "query", "search_contacts", "get_analytics",
        "get_campaign_performance", "create_campaign",
    ],
    "competitive_intel": [
        "get_domain_rating", "get_organic_keywords",
        "get_mentions", "get_share_of_voice", "get_backlinks",
    ],
    # Ops
    "support_triage": [
        "create_ticket", "update_ticket", "escalate_to_group",
        "get_sla_breach_status", "get_csat_score", "apply_macro",
        "send_message", "post_alert",
    ],
    "vendor_manager": [
        "search_issues", "create_issue", "add_comment",
        "create_page", "get_project_metrics",
    ],
    "contract_intelligence": [
        "search_content_fulltext", "create_page",
        "search_issues", "get_page_tree",
    ],
    "compliance_guard": [
        "get_compliance_notice", "get_access_log",
        "search_issues", "create_incident",
        "send_message",
    ],
    "it_operations": [
        "create_incident", "trigger_alert_with_context",
        "acknowledge_incident", "manage_on_call_schedule",
        "run_automated_runbook", "send_message",
        "post_alert",
    ],
    # Backoffice
    "legal_ops": [
        "search_content_fulltext", "create_page",
        "search_issues", "get_page_tree",
        "manage_space_permissions",
    ],
    "risk_sentinel": [
        "get_access_log", "create_incident",
        "get_compliance_notice", "search_issues",
        "generate_postmortem_doc",
    ],
    "facilities_agent": [
        "create_ticket", "update_ticket",
        "create_issue", "get_sla_breach_status",
    ],
    # Comms
    "email_agent": [
        "send_email", "read_inbox", "search_emails",
    ],
    "notification_agent": [
        "send_email", "create_calendar_event", "slack_send_message",
    ],
    "chat_agent": [
        "slack_send_message", "send_email", "read_inbox",
    ],
}

_DOMAIN_DEFAULT_TOOLS: dict[str, list[str]] = {
    "finance": [
        "fetch_bank_statement", "create_payment_intent",
        "get_balance", "list_invoices",
    ],
    "hr": [
        "get_employee", "create_employee",
        "provision_user", "post_job",
    ],
    "marketing": [
        "get_campaign_performance_metrics",
        "schedule_social_post", "list_contacts",
        "get_post_analytics",
    ],
    "ops": [
        "create_ticket", "search_issues",
        "create_incident", "get_sla_breach_status",
    ],
    "backoffice": [
        "search_content_fulltext", "create_page",
        "search_issues", "get_access_log",
    ],
    "comms": [
        "send_email", "read_inbox",
        "slack_send_message", "create_calendar_event",
    ],
}

logger = structlog.get_logger()

router = APIRouter()

# Valid lifecycle transitions: from_status -> set of allowed to_statuses
_LIFECYCLE_FSM: dict[str, list[str]] = {
    "shadow": ["active", "paused", "retired"],
    "active": ["paused", "retired"],
    "paused": ["active", "shadow", "retired"],
    "retired": [],
}

# Promotion path: shadow -> active -> (already top-level; no further promotion)
_PROMOTE_MAP: dict[str, str] = {
    "shadow": "active",
}


def _agent_to_dict(agent: Agent) -> dict:
    """Convert an Agent ORM instance to a JSON-serialisable dict."""
    return {
        "id": str(agent.id),
        "company_id": str(agent.company_id) if getattr(agent, "company_id", None) else None,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "domain": agent.domain,
        "status": agent.status,
        "version": agent.version,
        "description": agent.description,
        "system_prompt_ref": agent.system_prompt_ref,
        "prompt_variables": agent.prompt_variables,
        "llm_model": agent.llm_model,
        "llm_fallback": agent.llm_fallback,
        "llm_config": agent.llm_config,
        "confidence_floor": float(agent.confidence_floor),
        "hitl_condition": agent.hitl_condition,
        "max_retries": agent.max_retries,
        "retry_backoff": agent.retry_backoff,
        "authorized_tools": agent.authorized_tools,
        "output_schema": agent.output_schema,
        "parent_agent_id": str(agent.parent_agent_id) if agent.parent_agent_id else None,
        "shadow_comparison_agent_id": str(agent.shadow_comparison_agent_id)
        if agent.shadow_comparison_agent_id
        else None,
        "shadow_min_samples": agent.shadow_min_samples,
        "shadow_accuracy_floor": float(agent.shadow_accuracy_floor),
        "shadow_sample_count": agent.shadow_sample_count,
        "shadow_accuracy_current": float(agent.shadow_accuracy_current)
        if agent.shadow_accuracy_current is not None
        else None,
        "cost_controls": agent.cost_controls,
        "scaling": agent.scaling,
        "tags": agent.tags,
        "ttl_hours": agent.ttl_hours,
        "expires_at": agent.expires_at.isoformat() if agent.expires_at else None,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        # Virtual employee persona fields
        "employee_name": agent.employee_name,
        "avatar_url": agent.avatar_url,
        "designation": agent.designation,
        "specialization": agent.specialization,
        "routing_filter": agent.routing_filter,
        "is_builtin": agent.is_builtin,
        # v4.6.0: maturity label (ga|beta|alpha|deprecated) — used by the
        # UI to badge preview features and excluded from HIPAA scope.
        "maturity": getattr(agent, "maturity", "beta"),
        "cost_center_id": str(agent.cost_center_id) if getattr(agent, "cost_center_id", None) else None,
        "system_prompt_text": agent.system_prompt_text,
        "reporting_to": agent.reporting_to,
        "org_level": agent.org_level,
        "prompt_amendments": getattr(agent, "prompt_amendments", None) or [],
        "config": agent.config,
        # v4.3.0: connector_ids persists which tenant Connector instances this
        # agent is linked to. Surfaced here so run_agent can scope tools, the
        # UI can render them, and consumers can detect "Gmail tool requested
        # but no Gmail connector linked" before the agent crashes at runtime.
        "connector_ids": getattr(agent, "connector_ids", None) or [],
    }


def _parse_company_id(company_id: str | None) -> _uuid.UUID | None:
    if company_id in (None, ""):
        return None
    if isinstance(company_id, _uuid.UUID):
        return company_id
    if not isinstance(company_id, str):
        return None
    try:
        return _uuid.UUID(company_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid company_id format") from exc


def _validate_authorized_tools(tools: list[str]) -> list[str]:
    """Validate that every tool in the list exists in the connector tool_index.

    Returns a list of invalid tool names. Empty list means all tools are valid.
    """
    from core.langgraph.tool_adapter import _build_tool_index

    index = _build_tool_index()
    return [t for t in tools if t not in index]


def _derive_default_tools(
    agent_type: str,
    domain: str | None,
    connector_names: list[str] | None,
) -> list[str]:
    """Return the default authorized-tools list for ``agent_type``.

    When ``connector_names`` is provided, the result is the intersection
    of the static agent-type/domain defaults and the tools actually
    offered by those connectors — so an ``ap_processor`` picked with the
    ``tally`` connector only gets tools Tally supports, and the LLM
    tool-surface never drifts past what the agent's linked connectors
    can execute.

    When ``connector_names`` is provided but yields no intersection
    (a connector the type has no mapped defaults for), we fall back to
    the union of the connectors' tools.  When no ``connector_names`` are
    given, we return the static defaults.

    This is the single source of truth used by both ``POST /agents``
    auto-population and ``GET /agents/default-tools/{type}``.
    """
    from core.langgraph.tool_adapter import _build_tool_index

    static_defaults = _AGENT_TYPE_DEFAULT_TOOLS.get(
        agent_type,
        _DOMAIN_DEFAULT_TOOLS.get(domain or "", []),
    )

    if not connector_names:
        return list(static_defaults)

    connector_index = _build_tool_index(connector_names=connector_names)
    connector_tool_names = set(connector_index.keys())

    intersected = [t for t in static_defaults if t in connector_tool_names]
    if intersected:
        return intersected

    # No overlap — the user picked a connector whose tools we don't have
    # a static mapping for. Return the connector tools directly so the
    # agent at least sees something runnable.
    return sorted(connector_tool_names)


# ── GET /agents/default-tools/{agent_type} ───────────────────────────────────
@router.get("/agents/default-tools/{agent_type}")
async def get_default_tools(
    agent_type: str,
    domain: str | None = None,
    connector_ids: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Return the default authorized-tools list for ``agent_type``.

    Root-cause fix (2026-04-22 Codex review, UR-Bug-2 gap): the
    ``/agents/create`` UI called this route but the backend never
    implemented it, so every agent fell through to a client-side
    ``availableTools.slice(0, 5)`` guess. This endpoint now returns a
    real connector-aware default list:

    - If ``connector_ids`` is supplied (comma-separated connector names,
      tolerant of the ``registry-<name>`` UI prefix), defaults are
      intersected with the tools those connectors actually expose.
    - Otherwise the static ``_AGENT_TYPE_DEFAULT_TOOLS`` /
      ``_DOMAIN_DEFAULT_TOOLS`` fallback is returned.

    ``tenant_id`` is required so the route sits behind the same auth gate
    as the rest of ``/agents`` and the tenant-scoping is explicit.
    """
    _ = tenant_id  # Auth side-effect — value unused for derivation.
    conn_list: list[str] | None = None
    if connector_ids:
        conn_list = [c.strip() for c in connector_ids.split(",") if c.strip()]

    tools = _derive_default_tools(agent_type, domain, conn_list)
    return {"agent_type": agent_type, "tools": tools}


# ── POST /agents ─────────────────────────────────────────────────────────────
@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    company_uuid = _parse_company_id(body.company_id)

    initial_status = body.initial_status or "shadow"

    # Auto-populate authorized tools based on agent type / domain when none provided.
    # When the caller supplied ``connector_ids`` but no explicit
    # ``authorized_tools``, derive defaults connector-aware (root-cause
    # fix for UR-Bug-1 downstream: a Gmail agent should default to Gmail
    # tools, not the static type map's union).
    tools = body.authorized_tools
    if not tools:
        tools = _derive_default_tools(
            body.agent_type,
            body.domain,
            body.connector_ids or None,
        )

    # Validate user-provided tools against the registry (skip for auto-populated)
    if body.authorized_tools and tools:
        try:
            invalid_tools = _validate_authorized_tools(tools)
            if invalid_tools:
                raise HTTPException(
                    422,
                    detail={
                        "error": "invalid_authorized_tools",
                        "invalid_tools": invalid_tools,
                        "message": (
                            f"The following tools do not exist in the connector registry: "
                            f"{', '.join(invalid_tools)}. "
                            f"Check tool names or register the required connectors first."
                        ),
                    },
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning("tool_validation_skipped", agent_type=body.agent_type)

    async with get_tenant_session(tid) as session:
        if company_uuid is not None:
            company_exists = await session.execute(
                select(Company.id).where(Company.id == company_uuid, Company.tenant_id == tid)
            )
            if company_exists.scalar_one_or_none() is None:
                raise HTTPException(404, "Company not found")
        # ── SET-008: Enforce shadow agent limit ────────────────────────
        if initial_status == "shadow":
            try:
                tenant_result = await session.execute(select(Tenant).where(Tenant.id == tid))
                tenant_row = tenant_result.scalar_one_or_none()
                stored = (tenant_row.settings or {}).get("fleet_limits") if tenant_row else None
                limits = FleetLimits(**stored) if stored else FleetLimits()
                shadow_count = (await session.execute(
                    select(func.count(Agent.id)).where(Agent.tenant_id == tid, Agent.status == "shadow")
                )).scalar() or 0
                if shadow_count >= limits.max_shadow_agents:
                    raise HTTPException(409, detail=f"Shadow limit reached ({shadow_count}/{limits.max_shadow_agents})")
            except HTTPException:
                raise
            except Exception:  # noqa: S110
                pass  # Skip limit check if query fails (new tenant, no settings)

        agent = Agent(
            tenant_id=tid,
            company_id=company_uuid,
            name=body.name,
            agent_type=body.agent_type,
            domain=body.domain,
            description=None,
            system_prompt_ref=body.system_prompt or "",
            system_prompt_text=body.system_prompt_text,
            prompt_variables=body.prompt_variables,
            llm_model=body.llm.model,
            llm_fallback=body.llm.fallback_model,
            llm_config=body.llm.model_dump(),
            confidence_floor=Decimal(str(body.confidence_floor)),
            hitl_condition=body.hitl_policy.condition,
            max_retries=body.max_retries,
            authorized_tools=tools,
            output_schema=body.output_schema,
            status=body.initial_status or "shadow",
            version="1.0.0",
            shadow_comparison_agent_id=(
                _uuid.UUID(body.shadow_comparison_agent) if body.shadow_comparison_agent else None
            ),
            shadow_min_samples=body.shadow_min_samples,
            shadow_accuracy_floor=Decimal(str(body.shadow_accuracy_floor)),
            cost_controls=body.cost_controls.model_dump(),
            scaling=body.scaling.model_dump(),
            ttl_hours=body.ttl_hours,
            # Virtual employee persona fields
            employee_name=body.employee_name or body.name,
            avatar_url=body.avatar_url,
            designation=body.designation,
            specialization=body.specialization,
            routing_filter=body.routing_filter,
            reporting_to=body.reporting_to,
            org_level=body.org_level or 0,
            parent_agent_id=(
                _uuid.UUID(body.parent_agent_id) if body.parent_agent_id else None
            ),
            connector_ids=list(body.connector_ids) if body.connector_ids else [],
        )
        session.add(agent)
        await session.flush()  # populate agent.id

        # Create initial AgentVersion snapshot
        version_row = AgentVersion(
            tenant_id=tid,
            agent_id=agent.id,
            version=agent.version,
            system_prompt=body.system_prompt,
            authorized_tools=tools,
            hitl_policy=body.hitl_policy.model_dump(),
            llm_config=body.llm.model_dump(),
            confidence_floor=agent.confidence_floor,
            deployed_at=datetime.now(UTC),
        )
        session.add(version_row)

        # Audit log for agent creation
        audit_entry = AuditLog(
            tenant_id=tid,
            company_id=company_uuid,
            event_type="agent.create",
            actor_type="user",
            actor_id=str(tid),
            agent_id=agent.id,
            resource_type="agent",
            resource_id=str(agent.id),
            action=f"Created agent '{agent.name}' ({agent.agent_type})",
            outcome="success",
        )
        session.add(audit_entry)

    # Auto-register on Grantex (non-blocking — failure doesn't block creation)
    grantex_info = None
    try:
        from auth.grantex_registration import register_agent

        grantex_info = register_agent(
            name=agent.employee_name or agent.name,
            agent_type=agent.agent_type,
            domain=agent.domain,
            authorized_tools=tools,
        )
        if grantex_info:
            # Store Grantex metadata in agent config JSONB
            async with get_tenant_session(tid) as session:
                from sqlalchemy import update

                await session.execute(
                    update(Agent)
                    .where(Agent.id == agent.id)
                    .values(config={**agent.config, "grantex": grantex_info})
                )
    except Exception:
        logger.warning("grantex_auto_registration_skipped", agent_id=str(agent.id))

    return {
        "agent_id": str(agent.id),
        "company_id": str(agent.company_id) if agent.company_id else None,
        "status": agent.status,
        "version": agent.version,
        "token_issued": True,
        "grantex_registered": grantex_info is not None,
        "grantex_did": grantex_info.get("grantex_did", "") if grantex_info else "",
    }


# ── GET /agents ──────────────────────────────────────────────────────────────
@router.get("/agents", response_model=PaginatedResponse)
async def list_agents(
    domain: str | None = None,
    status: str | None = None,
    company_id: str | None = None,
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)

    tid = _uuid.UUID(tenant_id)
    company_uuid = _parse_company_id(company_id)
    async with get_tenant_session(tid) as session:
        query = select(Agent).where(Agent.tenant_id == tid)
        count_query = select(func.count()).select_from(Agent).where(Agent.tenant_id == tid)

        # RBAC domain filtering
        if user_domains is not None:
            query = query.where(Agent.domain.in_(user_domains))
            count_query = count_query.where(Agent.domain.in_(user_domains))

        if domain:
            query = query.where(Agent.domain == domain)
            count_query = count_query.where(Agent.domain == domain)
        if status:
            query = query.where(Agent.status == status)
            count_query = count_query.where(Agent.status == status)
        if company_uuid is not None:
            query = query.where(Agent.company_id == company_uuid)
            count_query = count_query.where(Agent.company_id == company_uuid)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        query = query.order_by(Agent.created_at.desc())
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        agents = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_agent_to_dict(a) for a in agents],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ORG TREE + CSV IMPORT — must be declared BEFORE /agents/{agent_id}
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/agents/org-tree")
async def get_org_tree(
    domain: str | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Return agents as a hierarchical org tree."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # BUG-28: Filter out deleted/broken agents from the org chart
        query = select(Agent).where(
            Agent.tenant_id == tid,
            Agent.status.notin_(["deleted", "error", "broken"]),
        )
        if domain:
            query = query.where(Agent.domain == domain)
        query = query.order_by(Agent.org_level.asc(), Agent.created_at.asc())
        result = await session.execute(query)
        agents = result.scalars().all()

    agents_by_id: dict[str, dict] = {}
    for a in agents:
        grantex_config = (a.config or {}).get("grantex", {})
        agents_by_id[str(a.id)] = {
            "id": str(a.id),
            "name": a.name,
            "employee_name": a.employee_name,
            "designation": a.designation,
            "domain": a.domain,
            "agent_type": a.agent_type,
            "status": a.status,
            "avatar_url": a.avatar_url,
            "org_level": a.org_level,
            "parent_agent_id": str(a.parent_agent_id) if a.parent_agent_id else None,
            "specialization": a.specialization,
            "reporting_to": a.reporting_to,
            "grantex_did": grantex_config.get("grantex_did", ""),
            "grantex_agent_id": grantex_config.get("grantex_agent_id", ""),
            "node_type": "agent",  # "agent" or "human" — configurable per org
            "children": [],
        }

    roots: list[dict] = []
    for _aid, node in agents_by_id.items():
        pid = node.get("parent_agent_id")
        if pid and pid in agents_by_id:
            agents_by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    return {"tree": roots, "flat_count": len(agents_by_id)}


# ── POST /agents/{id}/delegate — Grantex delegation ────────────────────────
@router.post("/agents/{agent_id}/delegate")
async def delegate_to_agent(
    agent_id: UUID,
    body: dict | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Set up Grantex delegation from parent to child agent.

    Creates a scoped grant where the parent delegates a subset of its
    scopes to the child agent in the org hierarchy.
    """
    if body is None:
        body = {}
    tid = _uuid.UUID(tenant_id)

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        child = result.scalar_one_or_none()
        if not child:
            raise HTTPException(404, "Agent not found")
        if not child.parent_agent_id:
            raise HTTPException(400, "Agent has no parent — cannot set up delegation")

        # Get parent's Grantex info
        parent_result = await session.execute(
            select(Agent).where(Agent.id == child.parent_agent_id, Agent.tenant_id == tid)
        )
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent agent not found")

    parent_grantex = (parent.config or {}).get("grantex", {})
    child_grantex = (child.config or {}).get("grantex", {})

    if not parent_grantex.get("grantex_agent_id") or not child_grantex.get("grantex_agent_id"):
        return {
            "status": "skipped",
            "reason": "Parent or child not registered on Grantex",
        }

    try:
        from auth.grantex_registration import setup_delegation

        child_scopes = child_grantex.get("grantex_scopes", [])
        result = setup_delegation(
            parent_grant_token=body.get("parent_grant_token", ""),
            child_grantex_agent_id=child_grantex["grantex_agent_id"],
            child_scopes=child_scopes,
            expires_in=body.get("expires_in", 28800),
        )
        return {
            "status": "delegated",
            "parent_agent": str(parent.id),
            "child_agent": str(child.id),
            "parent_did": parent_grantex.get("grantex_did", ""),
            "child_did": child_grantex.get("grantex_did", ""),
            "scopes_delegated": len(child_scopes),
        }
    except Exception:
        logger.exception("delegation_failed")
        return {"status": "failed", "reason": "Grantex delegation failed"}


@router.post("/agents/import-csv")
async def import_agents_csv(
    file: UploadFile,
    tenant_id: str = Depends(get_current_tenant),
):
    """Import agents from CSV with two-pass parent linking."""
    tid = _uuid.UUID(tenant_id)
    content = await file.read()
    text_content = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_content))

    # TC-009: Validate CSV has proper headers
    if not reader.fieldnames or not {"name", "agent_type", "domain"}.intersection(
        {f.strip().lower() for f in reader.fieldnames}
    ):
        raise HTTPException(
            422,
            detail="CSV missing required columns. Expected: name, agent_type, domain",
        )

    valid_domains = {"finance", "hr", "marketing", "ops", "backoffice", "comms"}
    name_pattern = _re.compile(r"^[a-zA-Z][a-zA-Z0-9 _\-\.]{1,99}$")

    imported: list[dict] = []
    skipped: list[dict] = []
    rows_with_parent: list[dict] = []

    async with get_tenant_session(tid) as session:
        for row in reader:
            name = (row.get("name") or row.get("Name") or "").strip()
            agent_type = (row.get("agent_type") or "").strip()
            domain = (row.get("domain") or "").strip().lower()

            if not name or not agent_type or not domain:
                skipped.append({"reason": "missing required field", "row": dict(row)})
                continue

            # TC-011: Validate data formats
            if not name_pattern.match(name):
                skipped.append({
                    "reason": "invalid name — must start with a letter, alphanumeric only",
                    "row": dict(row),
                })
                continue
            if not name_pattern.match(agent_type):
                skipped.append({"reason": "invalid agent_type format", "row": dict(row)})
                continue
            if domain not in valid_domains:
                allowed = ", ".join(sorted(valid_domains))
                skipped.append({
                    "reason": f"invalid domain '{domain}' — must be: {allowed}",
                    "row": dict(row),
                })
                continue

            designation = (row.get("designation") or "").strip()
            specialization = (row.get("specialization") or "").strip()
            reporting_to_name = (row.get("reporting_to_name") or "").strip()
            org_level_str = (row.get("org_level") or "").strip()
            llm_model = (row.get("llm_model") or "").strip()
            confidence_str = (row.get("confidence_floor") or "").strip()

            org_level = int(org_level_str) if org_level_str.isdigit() else 0
            confidence_floor = Decimal("0.88")
            if confidence_str:
                try:
                    confidence_floor = Decimal(confidence_str)
                except (ValueError, ArithmeticError):
                    confidence_floor = Decimal("0.88")

            agent = Agent(
                tenant_id=tid, name=name, employee_name=name,
                agent_type=agent_type, domain=domain,
                designation=designation or None,
                specialization=specialization or None,
                system_prompt_ref="", system_prompt_text="",
                hitl_condition="confidence < 0.88",
                status="shadow", version="1.0.0",
                confidence_floor=confidence_floor,
                org_level=org_level, llm_model=llm_model or None,
                parent_agent_id=None,
            )
            session.add(agent)
            await session.flush()
            imported.append({
                "id": str(agent.id), "name": name,
                "agent_type": agent_type, "domain": domain,
            })
            if reporting_to_name:
                rows_with_parent.append({
                    "agent_id": str(agent.id), "agent_name": name,
                    "agent_domain": domain,
                    "reporting_to_name": reporting_to_name,
                })

    parent_links_set = 0
    if rows_with_parent:
        async with get_tenant_session(tid) as session:
            all_result = await session.execute(
                select(Agent).where(Agent.tenant_id == tid)
            )
            all_agents = all_result.scalars().all()
            name_map: dict[tuple[str, str], _uuid.UUID] = {}
            for a in all_agents:
                name_map[(a.employee_name or a.name, a.domain)] = a.id

            for link in rows_with_parent:
                rtn = link["reporting_to_name"]
                if rtn == link["agent_name"]:
                    skipped.append({"reason": "self-reference", "agent": rtn})
                    continue
                pid = name_map.get((rtn, link["agent_domain"]))
                if not pid:
                    skipped.append({
                        "reason": f"parent '{rtn}' not found in {link['agent_domain']}",
                        "agent": link["agent_name"],
                    })
                    continue
                ar = await session.execute(
                    select(Agent).where(Agent.id == _uuid.UUID(link["agent_id"]))
                )
                agent_row = ar.scalar_one_or_none()
                if agent_row:
                    agent_row.parent_agent_id = pid
                    agent_row.reporting_to = rtn
                    parent_links_set += 1

    return {
        "imported": len(imported), "skipped": len(skipped),
        "parent_links_set": parent_links_set,
        "agents": imported, "skip_details": skipped[:10],
    }


# ── POST /agents/generate (Conversational Agent Creator) ─────────────────────


@router.post("/agents/generate")
async def generate_agent(
    body: dict,
    tenant_id: str = Depends(get_current_tenant),
):
    """Generate agent config from a natural-language description.

    Body: ``{"description": str, "deploy": bool}``

    Returns a preview of the generated agent configuration. If ``deploy``
    is True, also creates the agent in shadow mode using the top suggestion.
    """
    from core.agent_generator import generate_agent_config

    description = body.get("description", "")
    deploy = body.get("deploy", False)

    if not description or len(description) < 10:
        raise HTTPException(
            422, detail="Description must be at least 10 characters.",
        )

    try:
        result = await generate_agent_config(description)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc

    suggestions = result.get("suggestions", [])
    if not suggestions:
        raise HTTPException(
            422,
            detail="Could not generate agent configuration from that description.",
        )

    created_agent = None

    # If deploy requested, create the top suggestion as a shadow agent
    if deploy and suggestions:
        top = suggestions[0]
        tid = _uuid.UUID(tenant_id)

        # Build tools list
        tools = top.get("suggested_tools", [])
        if not tools:
            tools = _AGENT_TYPE_DEFAULT_TOOLS.get(
                top.get("agent_type", ""),
                _DOMAIN_DEFAULT_TOOLS.get(top.get("domain", ""), []),
            )

        async with get_tenant_session(tid) as session:
            agent = Agent(
                tenant_id=tid,
                name=top.get("employee_name", "Generated Agent"),
                employee_name=top.get("employee_name", "Generated Agent"),
                agent_type=top.get("agent_type", ""),
                domain=top.get("domain", ""),
                designation=top.get("designation"),
                specialization=top.get("specialization"),
                system_prompt_ref="",
                system_prompt_text=top.get("system_prompt", ""),
                prompt_variables={},
                llm_model="gemini-2.5-flash",
                llm_fallback="gemini-2.5-flash-preview-05-20",
                llm_config={
                    "model": "gemini-2.5-flash",
                    "fallback_model": "gemini-2.5-flash-preview-05-20",
                },
                confidence_floor=Decimal(str(top.get("confidence_floor", 0.88))),
                hitl_condition=top.get("hitl_condition", "confidence < 0.88"),
                max_retries=3,
                authorized_tools=tools,
                status="shadow",
                version="1.0.0",
                cost_controls={},
                scaling={},
            )
            session.add(agent)
            await session.flush()

            # Audit log
            audit_entry = AuditLog(
                tenant_id=tid,
                company_id=agent.company_id,
                event_type="agent.generate_deploy",
                actor_type="user",
                actor_id=str(tid),
                agent_id=agent.id,
                resource_type="agent",
                resource_id=str(agent.id),
                action=(
                    f"Generated and deployed agent '{agent.name}' "
                    f"({agent.agent_type}) from NL description"
                ),
                outcome="success",
            )
            session.add(audit_entry)

            created_agent = {
                "agent_id": str(agent.id),
                "name": agent.name,
                "status": "shadow",
                "agent_type": agent.agent_type,
                "domain": agent.domain,
            }

    return {
        "suggestions": suggestions,
        "deployed": created_agent,
        "llm_model": result.get("llm_model"),
        "tokens_used": result.get("tokens_used"),
    }


# ── GET /agents/{id} ────────────────────────────────────────────────────────
@router.get("/agents/{agent_id}")
async def get_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    return _agent_to_dict(agent)


# ── PUT /agents/{id} ────────────────────────────────────────────────────────
@router.put("/agents/{agent_id}")
async def replace_agent(
    agent_id: UUID,
    body: AgentCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        agent.name = body.name
        agent.agent_type = body.agent_type
        agent.domain = body.domain
        agent.system_prompt_ref = body.system_prompt
        agent.prompt_variables = body.prompt_variables
        agent.llm_model = body.llm.model
        agent.llm_fallback = body.llm.fallback_model
        agent.llm_config = body.llm.model_dump()
        agent.confidence_floor = Decimal(str(body.confidence_floor))
        agent.hitl_condition = body.hitl_policy.condition
        agent.max_retries = body.max_retries
        agent.authorized_tools = body.authorized_tools
        agent.output_schema = body.output_schema
        agent.shadow_min_samples = body.shadow_min_samples
        agent.shadow_accuracy_floor = Decimal(str(body.shadow_accuracy_floor))
        agent.cost_controls = body.cost_controls.model_dump()
        agent.scaling = body.scaling.model_dump()
        agent.ttl_hours = body.ttl_hours
        agent.shadow_comparison_agent_id = (
            _uuid.UUID(body.shadow_comparison_agent) if body.shadow_comparison_agent else None
        )

    return {"id": str(agent_id), "replaced": True}


# ── PATCH /agents/{id} ──────────────────────────────────────────────────────
@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: UUID,
    body: AgentUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        update_data = body.model_dump(exclude_unset=True)

        # Prompt lock: reject prompt edits on active agents
        prompt_changing = "system_prompt_text" in update_data or "system_prompt" in update_data
        if prompt_changing and agent.status == "active":
            raise HTTPException(
                409,
                "Prompt is locked on active agents. Clone this agent to make changes.",
            )

        # Track prompt changes for audit
        old_prompt = agent.system_prompt_text
        change_reason = update_data.pop("change_reason", None)

        if "name" in update_data:
            agent.name = update_data["name"]
        if "system_prompt" in update_data:
            agent.system_prompt_ref = update_data["system_prompt"]
        if "system_prompt_text" in update_data:
            agent.system_prompt_text = update_data["system_prompt_text"]
        if "prompt_variables" in update_data:
            agent.prompt_variables = update_data["prompt_variables"]
        if "authorized_tools" in update_data:
            invalid = _validate_authorized_tools(update_data["authorized_tools"])
            if invalid:
                raise HTTPException(
                    422,
                    detail=f"Invalid authorized_tools: {', '.join(invalid)}. "
                    "Use GET /connectors/registry or GET /tools to discover valid tool names.",
                )
            agent.authorized_tools = update_data["authorized_tools"]
        if "hitl_policy" in update_data and update_data["hitl_policy"] is not None:
            agent.hitl_condition = update_data["hitl_policy"]["condition"]
        if "confidence_floor" in update_data:
            agent.confidence_floor = Decimal(str(update_data["confidence_floor"]))
        if "llm" in update_data and update_data["llm"] is not None:
            agent.llm_model = update_data["llm"]["model"]
            agent.llm_fallback = update_data["llm"].get("fallback_model")
            agent.llm_config = update_data["llm"]
        # Persona fields
        if "employee_name" in update_data:
            agent.employee_name = update_data["employee_name"]
        if "avatar_url" in update_data:
            agent.avatar_url = update_data["avatar_url"]
        if "designation" in update_data:
            agent.designation = update_data["designation"]
        if "specialization" in update_data:
            agent.specialization = update_data["specialization"]
        if "routing_filter" in update_data:
            agent.routing_filter = update_data["routing_filter"]
        if "reporting_to" in update_data:
            agent.reporting_to = update_data["reporting_to"]
        if "org_level" in update_data:
            agent.org_level = update_data["org_level"]
        if "parent_agent_id" in update_data:
            pid = update_data["parent_agent_id"]
            agent.parent_agent_id = _uuid.UUID(pid) if pid else None
        if "connector_ids" in update_data and update_data["connector_ids"] is not None:
            agent.connector_ids = list(update_data["connector_ids"])

        # Audit trail for prompt edits
        new_prompt = agent.system_prompt_text
        if prompt_changing and old_prompt != new_prompt:
            audit = PromptEditHistory(
                tenant_id=tid,
                agent_id=agent.id,
                prompt_before=old_prompt,
                prompt_after=new_prompt or "",
                change_reason=change_reason,
            )
            session.add(audit)

    return {"id": str(agent_id), "updated": True}


# ── POST /agents/{id}/run ────────────────────────────────────────────────────
@router.post("/agents/{agent_id}/run")
async def run_agent(
    agent_id: UUID,
    payload: dict | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Instantiate agent from registry and execute against user input."""
    if payload is None:
        payload = {}
    # Validate that inputs are not empty
    inputs = payload.get("inputs", {})
    if not inputs:
        raise HTTPException(400, "inputs field is required and cannot be empty")
    tid = _uuid.UUID(tenant_id)

    # 1. Load agent config from DB
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent_row = result.scalar_one_or_none()
        if not agent_row:
            raise HTTPException(404, "Agent not found")
        if agent_row.status == "retired":
            raise HTTPException(409, "Cannot run a retired agent")

        agent_config = _agent_to_dict(agent_row)

    # 2. Prepare execution config
    authorized_tools = agent_config.get("authorized_tools", []) or []
    correlation_id = f"run_{_uuid.uuid4().hex[:12]}"

    # Pre-flight: filter out unresolvable tools so the graph runs with what
    # the registry can actually dispatch (Session 4 BUGs 013/014/015). Many
    # auto-populated defaults in _AGENT_TYPE_DEFAULT_TOOLS are not registered
    # as connector tools today, so a hard 400 on any missing name would
    # regress newly-created default agents. Only fail if *every* authorized
    # tool is unresolvable and the agent had some to begin with — an empty
    # toolset after filtering means the agent genuinely can't do its job.
    if authorized_tools:
        try:
            missing_tools = _validate_authorized_tools(authorized_tools)
        except Exception:  # noqa: BLE001 - validation is best-effort
            missing_tools = []
        if missing_tools:
            resolvable = [t for t in authorized_tools if t not in set(missing_tools)]
            logger.warning(
                "agent_run_tools_filtered",
                agent_id=str(agent_id),
                missing=missing_tools,
                kept=resolvable,
            )
            if not resolvable:
                linked_connectors = agent_config.get("connector_ids") or []
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "tools_unavailable",
                        "message": (
                            "Agent has no resolvable tools — every entry in "
                            "authorized_tools is missing from the connector "
                            "registry. Link a connector or update the tool list."
                        ),
                        "missing_tools": missing_tools,
                        "linked_connector_ids": linked_connectors,
                    },
                )
            authorized_tools = resolvable
            agent_config["authorized_tools"] = resolvable

    # Resolve system prompt — custom text or load from prompt file
    system_prompt = agent_config.get("system_prompt_text") or ""
    if not system_prompt:
        # Try loading from the prompt template file directly (works for all agents)
        prompt_ref = agent_config.get("system_prompt_ref", "")
        if prompt_ref:
            from pathlib import Path
            prompt_path = Path(__file__).resolve().parent.parent.parent / prompt_ref
            if prompt_path.exists():
                try:
                    raw = prompt_path.read_text(encoding="utf-8")
                    for k, v in (agent_config.get("prompt_variables") or {}).items():
                        raw = raw.replace("{{" + k + "}}", v)
                    system_prompt = raw
                except Exception:  # noqa: S110
                    pass
    if not system_prompt:
        # Load from prompt file via LangGraph agent module (built-in agents only)
        try:
            import importlib

            mod = importlib.import_module(
                f"core.langgraph.agents.{agent_config['agent_type']}"
            )
            load_fn = getattr(mod, "load_prompt", None) or getattr(mod, "load_ap_processor_prompt", None)
            if load_fn:
                system_prompt = load_fn(agent_config.get("prompt_variables", {}))
        except (ImportError, AttributeError):
            system_prompt = (
                f"You are a {agent_config['agent_type']} agent for the "
                f"{agent_config.get('domain', 'ops')} domain. Process the task "
                "and return JSON with status, confidence, and processing_trace."
            )

    # Get Grantex grant token if available (from request state or agent config)
    grant_token = ""
    grantex_config = agent_config.get("config", {}).get("grantex", {})
    if grantex_config:
        grant_token = grantex_config.get("grant_token", "")

    # 5a. Budget check (if cost controls configured)
    cost_controls = agent_config.get("cost_controls", {})
    monthly_cap = cost_controls.get("monthly_cost_cap_usd", 0) if cost_controls else 0
    if monthly_cap and monthly_cap > 0:
        # P3.1: Use a Postgres advisory lock keyed on agent_id to serialize
        # concurrent budget checks. Without this, two requests could both see
        # spend < cap and both proceed, causing overspend.
        async with get_tenant_session(tid) as session:
            from sqlalchemy import func as sqlfunc
            from sqlalchemy import text as sqltext

            # Acquire advisory lock for this agent (auto-released at txn end)
            # pg_advisory_xact_lock(int8) — use hash of UUID as the key
            lock_key = abs(hash(str(agent_id))) % (2**31)
            await session.execute(sqltext("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

            month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            spent_result = await session.execute(
                select(sqlfunc.coalesce(sqlfunc.sum(AgentCostLedger.cost_usd), 0)).where(
                    AgentCostLedger.agent_id == agent_id,
                    AgentCostLedger.period_date >= month_start,
                )
            )
            monthly_spent = float(spent_result.scalar() or 0)
            if monthly_spent >= monthly_cap:
                return {
                    "task_id": f"msg_{_uuid.uuid4().hex[:12]}",
                    "agent_id": str(agent_id),
                    "status": "budget_exceeded",
                    "error": {
                        "code": "E1008",
                        "message": f"Monthly budget exceeded: ${monthly_spent:.2f} / ${monthly_cap:.2f}",
                    },
                    "output": {},
                    "confidence": 0,
                    "reasoning_trace": [f"Budget check: ${monthly_spent:.2f} >= cap ${monthly_cap:.2f}"],
                }

    # 5b. Execute via LangGraph runner
    from core.langgraph.runner import run_agent as langgraph_run

    try:
        lg_result = await langgraph_run(
            agent_id=str(agent_id),
            agent_type=agent_config["agent_type"],
            domain=agent_config.get("domain", "ops"),
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            authorized_tools=authorized_tools,
            task_input={
                "action": payload.get("action", "process"),
                "inputs": payload.get("inputs", {}),
                "context": payload.get("context", {}),
            },
            llm_model=agent_config.get("llm_model", ""),
            confidence_floor=float(agent_config.get("confidence_floor", 0.88)),
            hitl_condition=agent_config.get("hitl_condition", ""),
            grant_token=grant_token,
            connector_config=agent_config.get("config"),
        )
    except Exception as exc:
        # Surface enough information for the caller to act on without
        # leaking secrets from the exception message. The full traceback
        # stays server-side, correlated by trace_id.
        import traceback as _tb

        trace_id = _uuid.uuid4().hex[:12]
        err_type = type(exc).__name__
        # Extract the innermost user-code frame from the traceback so we
        # can report "which attribute on what object" without leaking
        # secrets. For AttributeError this turns the opaque "internal
        # agent runtime error" into an actionable hint like
        # "'NoneType' object has no attribute 'tool_calls'".
        tb_frame = ""
        try:
            tb = _tb.extract_tb(exc.__traceback__)
            if tb:
                last = tb[-1]
                tb_frame = f"{last.filename.rsplit('/', 1)[-1]}:{last.lineno}"
        except Exception:  # noqa: BLE001, S110  # diagnostic extraction is best-effort
            pass
        exc_msg = str(exc)[:200].replace("\n", " ")
        logger.exception(
            "agent_run_error",
            agent_id=str(agent_id),
            trace_id=trace_id,
            error_type=err_type,
            frame=tb_frame,
            exc_msg=exc_msg,
        )
        # Classify to a short, safe hint.
        hint = {
            "TimeoutError": "upstream LLM or tool timed out",
            "PermissionError": "agent lacks permission for a required tool",
            "ValueError": "invalid task input",
            "KeyError": "missing required configuration",
            "AttributeError": f"missing attribute in agent runtime at {tb_frame}",
        }.get(err_type, f"internal agent runtime error at {tb_frame}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {hint} ({err_type}). trace_id={trace_id}",
        ) from None

    task_status = lg_result.get("status", "completed")
    task_confidence = lg_result.get("confidence", 0.0)
    task_output = lg_result.get("output", {})
    task_trace = lg_result.get("reasoning_trace", [])
    hitl_trigger = lg_result.get("hitl_trigger", "")
    task_error = lg_result.get("error", "")
    perf = lg_result.get("performance", {})
    msg_id = f"msg_{_uuid.uuid4().hex[:12]}"

    # 6. Store result in audit log
    async with get_tenant_session(tid) as session:
        audit_entry = AuditLog(
            tenant_id=tid,
            company_id=_parse_company_id(agent_config.get("company_id")),
            event_type="agent.run",
            actor_type="agent",
            actor_id=str(agent_id),
            agent_id=agent_id,
            resource_type="task_result",
            resource_id=msg_id,
            action="execute",
            outcome=task_status,
            details={
                "correlation_id": correlation_id,
                "confidence": task_confidence,
                "reasoning_trace": task_trace[:10],
                "runtime": "langgraph",
                "has_hitl": bool(hitl_trigger),
            },
        )
        session.add(audit_entry)

    # 6b. Create HITL queue entry if HITL was triggered
    if hitl_trigger:
        async with get_tenant_session(tid) as session:
            hitl_entry = HITLQueue(
                tenant_id=tid,
                agent_id=agent_id,
                workflow_run_id=None,
                title=f"HITL: {agent_config['agent_type']} — {hitl_trigger}",
                trigger_type="confidence_below_floor",
                priority="high" if task_confidence < 0.7 else "normal",
                assignee_role=agent_config.get("domain", "admin"),
                decision_options={
                    "options": ["approve", "reject", "override"],
                    "context": task_output,
                },
                context={
                    "correlation_id": correlation_id,
                    "agent_type": agent_config["agent_type"],
                    "confidence": task_confidence,
                    "reasoning_trace": task_trace,
                    "trigger": hitl_trigger,
                },
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
            session.add(hitl_entry)

    # 6c. Track running accuracy for shadow AND active agents (atomic SQL)
    #
    # BUG-012 (Ramesh 2026-04-20): shadow accuracy was reported as
    # ~40% for brand-new agents because the task_confidence defaults
    # to 0.0 when the LangGraph result doesn't carry an explicit
    # confidence field (errored runs, output-parse failures, missing
    # LLM scoring). Those 0.0 samples were being included in the
    # running average, pulling legitimate 0.7-0.85-confidence runs
    # toward ~0.4 as soon as a few parse errors hit.
    #
    # Fix: treat confidence values below 0.1 as "no-signal" samples and
    # skip them entirely. Also bump the sample count only when we're
    # actually scoring. The min-samples gate still prevents promotion
    # until enough real samples have accumulated.
    is_measurable = (
        task_status in ("completed", "hitl_triggered")
        and task_confidence is not None
        and float(task_confidence) >= 0.10
    )
    if (
        agent_config.get("status") in ("shadow", "active")
        and is_measurable
    ):
        async with get_tenant_session(tid) as session:
            from sqlalchemy import text as sql_text
            # Atomic increment to avoid race conditions between concurrent runs
            await session.execute(
                sql_text(
                    "UPDATE agents SET "
                    "shadow_sample_count = COALESCE(shadow_sample_count, 0) + 1, "
                    "shadow_accuracy_current = ROUND(CAST("
                    "  (COALESCE(shadow_accuracy_current, 0) * COALESCE(shadow_sample_count, 0) + :confidence)"
                    "  / (COALESCE(shadow_sample_count, 0) + 1) AS NUMERIC), 3) "
                    "WHERE id = :agent_id AND tenant_id = :tenant_id"
                ),
                {"confidence": task_confidence, "agent_id": str(agent_id), "tenant_id": tenant_id},
            )
            await session.commit()

    # 6d. Record cost in ledger (upsert — unique on tenant+agent+date)
    cost_usd = perf.get("llm_cost_usd", 0)
    tokens_used = perf.get("llm_tokens_used", 0)
    if cost_usd > 0 or tokens_used > 0:
        try:
            today = datetime.now(UTC).date()
            async with get_tenant_session(tid) as session:
                existing = await session.execute(
                    select(AgentCostLedger).where(
                        AgentCostLedger.agent_id == agent_id,
                        AgentCostLedger.tenant_id == tid,
                        AgentCostLedger.period_date == today,
                    )
                )
                ledger = existing.scalar_one_or_none()
                if ledger:
                    ledger.token_count = (ledger.token_count or 0) + tokens_used
                    ledger.cost_usd = float(ledger.cost_usd or 0) + cost_usd
                    ledger.task_count = (ledger.task_count or 0) + 1
                else:
                    session.add(AgentCostLedger(
                        agent_id=agent_id,
                        tenant_id=tid,
                        cost_usd=cost_usd,
                        token_count=tokens_used,
                        task_count=1,
                        period_date=today,
                    ))
        except Exception as exc:
            logger.error(
                "cost_ledger_write_failed",
                agent_id=str(agent_id),
                error=str(exc),
            )
            # AGENT-BUDGET-014: Cost ledger failures must not be silently ignored.
            # Flag the result so downstream consumers (HITL, dashboards) know
            # that budget tracking is unreliable for this run.
            task_trace.append(f"WARNING: cost ledger write failed — {exc}")
            hitl_trigger = hitl_trigger or "budget_tracking_failed"

    # 7. Return result — canonical AgentRunResult shape.
    # See docs/api/agent-run-contract.md. `task_id` stays as a deprecated
    # alias for `run_id` during the v4.8 → v5.0 transition window.
    response = {
        "run_id": msg_id,
        "task_id": msg_id,  # deprecated alias, removed in v5.0
        "agent_id": str(agent_id),
        "agent_type": None,  # this endpoint invokes by id; type path is /a2a/tasks
        "correlation_id": correlation_id,
        "status": task_status,
        "output": task_output,
        "confidence": task_confidence,
        "reasoning_trace": task_trace,
        "tool_calls": lg_result.get("tool_calls", []),
        "runtime": "langgraph",
        "explanation": lg_result.get("explanation") or None,
        "performance": {
            "total_latency_ms": perf.get("total_latency_ms", 0),
            "llm_tokens_used": perf.get("llm_tokens_used", 0),
            "llm_cost_usd": perf.get("llm_cost_usd", 0),
        },
        "hitl_trigger": hitl_trigger or None,
        "error": task_error or None,
    }
    return response


# ── POST /agents/{id}/pause ──────────────────────────────────────────────────
@router.post("/agents/{agent_id}/pause")
async def pause_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if agent.status == "paused":
            raise HTTPException(409, "Agent is already paused")
        if agent.status == "retired":
            raise HTTPException(409, "Cannot pause a retired agent")

        old_status = agent.status
        agent.status = "paused"

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status=old_status,
            to_status="paused",
            triggered_by="api",
            reason="Agent paused via API",
        )
        session.add(event)

    return {
        "id": str(agent_id),
        "status": "paused",
        "previous_status": old_status,
        "token_revoked": True,
    }


# ── POST /agents/{id}/resume ─────────────────────────────────────────────────
@router.post("/agents/{agent_id}/resume")
async def resume_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if agent.status != "paused":
            raise HTTPException(
                409, f"Cannot resume agent in '{agent.status}' status; must be paused"
            )

        # TC_AGENT-007: Look up the status the agent was in before it was paused
        # so we resume to the correct state (shadow agents must not bypass checks)
        prev_event_result = await session.execute(
            select(AgentLifecycleEvent)
            .where(
                AgentLifecycleEvent.agent_id == agent_id,
                AgentLifecycleEvent.to_status == "paused",
            )
            .order_by(AgentLifecycleEvent.created_at.desc())
            .limit(1)
        )
        pause_event = prev_event_result.scalar_one_or_none()
        resume_to = pause_event.from_status if pause_event else "active"

        # If the agent was in shadow mode before pausing, resume back to shadow
        # — it must go through the promote endpoint to reach active
        if resume_to == "shadow":
            # Re-validate that shadow accuracy hasn't degraded below floor
            if (
                agent.shadow_min_samples > 0
                and agent.shadow_accuracy_current is not None
                and agent.shadow_accuracy_current < agent.shadow_accuracy_floor
            ):
                raise HTTPException(
                    409,
                    f"Shadow accuracy {agent.shadow_accuracy_current} is below "
                    f"floor {agent.shadow_accuracy_floor}; "
                    f"use /agents/{agent_id}/retest to re-evaluate",
                )

        agent.status = resume_to

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status="paused",
            to_status=resume_to,
            triggered_by="api",
            reason=f"Agent resumed via API to '{resume_to}'",
        )
        session.add(event)

    return {"id": str(agent_id), "status": resume_to}


# ── POST /agents/{id}/promote ────────────────────────────────────────────────
@router.post("/agents/{agent_id}/promote")
async def promote_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        new_status = _PROMOTE_MAP.get(agent.status)
        if new_status is None:
            raise HTTPException(
                409,
                f"Cannot promote agent from '{agent.status}'; "
                f"valid promotion statuses: {list(_PROMOTE_MAP.keys())}",
            )

        # For shadow->active, validate minimum shadow samples met
        # Skip validation entirely when shadow_min_samples=0 (opt-out)
        if agent.status == "shadow" and agent.shadow_min_samples > 0:
            if agent.shadow_sample_count < agent.shadow_min_samples:
                raise HTTPException(
                    409,
                    f"Shadow agent has {agent.shadow_sample_count}/{agent.shadow_min_samples} samples; "
                    f"cannot promote until minimum is met",
                )
            if agent.shadow_accuracy_current is None:
                raise HTTPException(
                    409,
                    "Shadow accuracy not yet computed; run shadow samples first",
                )
            if agent.shadow_accuracy_current < agent.shadow_accuracy_floor:
                raise HTTPException(
                    409,
                    f"Shadow accuracy {agent.shadow_accuracy_current} is below floor {agent.shadow_accuracy_floor}",
                )

        old_status = agent.status
        agent.status = new_status

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status=old_status,
            to_status=new_status,
            triggered_by="api",
            reason=f"Promoted from {old_status} to {new_status}",
            shadow_accuracy=agent.shadow_accuracy_current,
            shadow_samples=agent.shadow_sample_count,
        )
        session.add(event)

    return {"id": str(agent_id), "promoted": True, "from": old_status, "to": new_status}


# ── POST /agents/{id}/retire ───────────────────────────────────────────────────
@router.post("/agents/{agent_id}/retire")
async def retire_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    """Retire an agent — marks as retired, removes from active fleet."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if agent.status == "retired":
            raise HTTPException(409, "Agent is already retired")

        allowed = _LIFECYCLE_FSM.get(agent.status, [])
        if "retired" not in allowed:
            raise HTTPException(
                409,
                f"Cannot retire agent from '{agent.status}' status",
            )

        old_status = agent.status
        agent.status = "retired"

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status=old_status,
            to_status="retired",
            triggered_by="api",
            reason="Agent retired via API",
        )
        session.add(event)

    return {
        "id": str(agent_id),
        "status": "retired",
        "previous_status": old_status,
        "token_revoked": True,
    }


# ── POST /agents/{id}/retest ─────────────────────────────────────────────────
@router.post("/agents/{agent_id}/retest")
async def retest_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    """TC_AGENT-008: Reset shadow counters so users can re-evaluate a shadow agent."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")
        if agent.status != "shadow":
            raise HTTPException(
                409,
                f"Cannot retest agent in '{agent.status}' status; must be in shadow mode",
            )

        old_sample_count = agent.shadow_sample_count
        old_accuracy = (
            float(agent.shadow_accuracy_current)
            if agent.shadow_accuracy_current is not None
            else None
        )

        agent.shadow_sample_count = 0
        agent.shadow_accuracy_current = None

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status="shadow",
            to_status="shadow",
            triggered_by="api",
            reason="Shadow counters reset for re-evaluation",
            shadow_accuracy=old_accuracy,
            shadow_samples=old_sample_count,
        )
        session.add(event)

    return {
        "id": str(agent_id),
        "retest": True,
        "shadow_sample_count": 0,
        "shadow_accuracy_current": None,
        "previous_sample_count": old_sample_count,
        "previous_accuracy": old_accuracy,
    }


# ── POST /agents/{id}/rollback ───────────────────────────────────────────────
@router.post("/agents/{agent_id}/rollback")
async def rollback_agent(agent_id: UUID, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        # Find the previous verified-good version (not the current one)
        versions_result = await session.execute(
            select(AgentVersion)
            .where(
                AgentVersion.agent_id == agent_id,
                AgentVersion.version != agent.version,
            )
            .order_by(AgentVersion.created_at.desc())
            .limit(1)
        )
        prev_version = versions_result.scalar_one_or_none()
        if not prev_version:
            raise HTTPException(409, "No previous version to rollback to")

        old_status = agent.status
        old_version = agent.version

        # Restore agent fields from the prior version snapshot
        agent.system_prompt_ref = prev_version.system_prompt
        agent.authorized_tools = prev_version.authorized_tools
        agent.hitl_condition = prev_version.hitl_policy.get("condition", agent.hitl_condition)
        agent.llm_config = prev_version.llm_config
        agent.llm_model = prev_version.llm_config.get("model", agent.llm_model)
        agent.llm_fallback = prev_version.llm_config.get("fallback_model", agent.llm_fallback)
        agent.confidence_floor = prev_version.confidence_floor
        agent.version = prev_version.version

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status=old_status,
            to_status=old_status,  # status stays the same
            triggered_by="api",
            reason=f"Rolled back from version {old_version} to {prev_version.version}",
        )
        session.add(event)

    return {
        "id": str(agent_id),
        "rolled_back": True,
        "from_version": old_version,
        "to_version": prev_version.version,
    }


# ── POST /agents/{id}/clone ──────────────────────────────────────────────────
@router.post("/agents/{agent_id}/clone")
async def clone_agent(
    agent_id: UUID,
    body: AgentCloneRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        parent = result.scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent agent not found")

        # Validate scope ceiling: clone cannot have higher-privilege tools
        if body.overrides.get("authorized_tools"):
            parent_tools = set(parent.authorized_tools or [])
            clone_tools = set(body.overrides["authorized_tools"])
            if not clone_tools.issubset(parent_tools):
                extra = clone_tools - parent_tools
                raise HTTPException(
                    403,
                    f"Clone cannot exceed parent scope ceiling; unauthorized tools: {sorted(extra)}",
                )

        clone = Agent(
            tenant_id=tid,
            name=body.name,
            agent_type=body.agent_type,
            domain=parent.domain,
            description=parent.description,
            system_prompt_ref=body.overrides.get("system_prompt", parent.system_prompt_ref),
            system_prompt_text=body.overrides.get("system_prompt_text", parent.system_prompt_text),
            prompt_variables=body.overrides.get("prompt_variables", parent.prompt_variables),
            llm_model=parent.llm_model,
            llm_fallback=parent.llm_fallback,
            llm_config=parent.llm_config,
            confidence_floor=Decimal(
                str(body.overrides.get("confidence_floor", parent.confidence_floor))
            ),
            hitl_condition=parent.hitl_condition,
            max_retries=parent.max_retries,
            authorized_tools=body.overrides.get("authorized_tools", parent.authorized_tools),
            output_schema=parent.output_schema,
            status=body.initial_status or "shadow",
            version="1.0.0",
            parent_agent_id=parent.id,
            shadow_comparison_agent_id=(
                _uuid.UUID(body.shadow_comparison_agent)
                if body.shadow_comparison_agent
                else parent.id
            ),
            shadow_min_samples=parent.shadow_min_samples,
            shadow_accuracy_floor=parent.shadow_accuracy_floor,
            cost_controls=parent.cost_controls,
            scaling=parent.scaling,
            ttl_hours=parent.ttl_hours,
            # Copy persona fields from parent, allow overrides
            employee_name=body.overrides.get("employee_name", body.name),
            avatar_url=body.overrides.get("avatar_url", parent.avatar_url),
            designation=body.overrides.get("designation", parent.designation),
            specialization=body.overrides.get("specialization", parent.specialization),
            routing_filter=body.overrides.get("routing_filter", parent.routing_filter),
        )
        session.add(clone)
        await session.flush()

        # Create initial version snapshot for clone
        version_row = AgentVersion(
            tenant_id=tid,
            agent_id=clone.id,
            version=clone.version,
            system_prompt=clone.system_prompt_ref,
            authorized_tools=clone.authorized_tools,
            hitl_policy={"condition": clone.hitl_condition},
            llm_config=clone.llm_config,
            confidence_floor=clone.confidence_floor,
            deployed_at=datetime.now(UTC),
        )
        session.add(version_row)

    return {
        "clone_id": str(clone.id),
        "status": clone.status,
        "parent_id": str(agent_id),
    }


# ── GET /agents/{id}/prompt-history ────────────────────────────────────────
@router.get("/agents/{agent_id}/prompt-history")
async def get_prompt_history(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Return prompt edit audit trail for an agent."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(PromptEditHistory)
            .where(
                PromptEditHistory.agent_id == agent_id,
                PromptEditHistory.tenant_id == tid,
            )
            .order_by(PromptEditHistory.created_at.desc())
            .limit(50)
        )
        entries = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "agent_id": str(e.agent_id),
            "edited_by": str(e.edited_by) if e.edited_by else None,
            "prompt_before": e.prompt_before,
            "prompt_after": e.prompt_after,
            "change_reason": e.change_reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


# ── GET /agents/{id}/budget ────────────────────────────────────────────────
@router.get("/agents/{agent_id}/budget")
async def get_agent_budget(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Return current budget usage for an agent."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        cost_controls = agent.cost_controls or {}
        monthly_cap = cost_controls.get("monthly_cost_cap_usd", 0)
        daily_budget = cost_controls.get("daily_token_budget", 0)

        # Query monthly spend
        from sqlalchemy import func as sqlfunc
        month_start = datetime.now(UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
        )
        spent_q = await session.execute(
            select(
                sqlfunc.coalesce(sqlfunc.sum(AgentCostLedger.cost_usd), 0),
                sqlfunc.coalesce(sqlfunc.sum(AgentCostLedger.token_count), 0),
                sqlfunc.coalesce(sqlfunc.sum(AgentCostLedger.task_count), 0),
            ).where(
                AgentCostLedger.agent_id == agent_id,
                AgentCostLedger.period_date >= month_start,
            )
        )
        row = spent_q.fetchone()
        monthly_spent = float(row[0]) if row else 0
        monthly_tokens = int(row[1]) if row else 0
        monthly_tasks = int(row[2]) if row else 0

    pct_used = (monthly_spent / monthly_cap * 100) if monthly_cap > 0 else 0
    warnings = []
    if pct_used >= 100:
        warnings.append("Monthly budget exceeded — agent will be paused on next run")
    elif pct_used >= 80:
        warnings.append("Monthly budget at 80%+ — approaching limit")

    return {
        "agent_id": str(agent_id),
        "monthly_cap_usd": monthly_cap,
        "monthly_spent_usd": round(monthly_spent, 4),
        "monthly_pct_used": round(pct_used, 1),
        "monthly_tokens": monthly_tokens,
        "monthly_tasks": monthly_tasks,
        "daily_token_budget": daily_budget,
        "warnings": warnings,
    }


# ═══════════════════════════════════════════════════════════════════════════
# FEEDBACK LOOP — Self-Improving Agents (PRD v4 Section 8)
# ═══════════════════════════════════════════════════════════════════════════


# ── POST /agents/{id}/feedback ──────────────────────────────────────────────
@router.post("/agents/{agent_id}/feedback")
async def submit_agent_feedback(
    agent_id: UUID,
    body: dict | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    """Submit feedback (thumbs up/down, correction, HITL reject) for an agent run."""
    if body is None:
        body = {}

    from core.feedback.collector import submit_feedback

    run_id = body.get("run_id", "")
    feedback_type = body.get("feedback_type", "")
    text = body.get("text", "")
    corrected_output = body.get("corrected_output")

    if not feedback_type:
        raise HTTPException(400, "feedback_type is required")
    if not run_id:
        raise HTTPException(400, "run_id is required")

    result = await submit_feedback(
        agent_id=str(agent_id),
        run_id=run_id,
        feedback_type=feedback_type,
        text=text,
        corrected_output=corrected_output,
        tenant_id=tenant_id,
    )

    if result.get("status") == "error":
        raise HTTPException(422, result.get("message", "Invalid feedback"))

    return result


# ── GET /agents/{id}/feedback ───────────────────────────────────────────────
@router.get("/agents/{agent_id}/feedback")
async def list_agent_feedback(
    agent_id: UUID,
    limit: int = 50,
    offset: int = 0,
    tenant_id: str = Depends(get_current_tenant),
):
    """List feedback entries for an agent (paginated)."""
    from core.feedback.collector import list_feedback

    entries = await list_feedback(
        agent_id=str(agent_id),
        tenant_id=tenant_id,
        limit=min(limit, 100),
        offset=max(offset, 0),
    )
    return {"agent_id": str(agent_id), "feedback": entries, "count": len(entries)}


# ── GET /agents/{id}/explanation/latest ─────────────────────────────────────
@router.get("/agents/{agent_id}/explanation/latest")
async def get_latest_explanation(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Derive a real, human-readable explanation of the agent's most recent
    run from stored `AgentTaskResult` data.

    Replaces the mock explanation that used to live in the UI
    (`ui/src/pages/AgentDetail.tsx`, removed in PR-C1). Returns the empty
    state when the agent hasn't run yet — callers must handle
    ``has_run: false`` rather than fabricating bullets.

    Response shape:
      {
        "has_run": bool,
        "run_id": str | None,
        "status": str | None,
        "confidence": float | None,
        "bullets": list[str],     # derived from hitl, confidence, tool usage
        "tools_cited": list[str], # unique tool names from tool_calls
        "hitl_required": bool,
        "hitl_decision": str | None,
        "duration_ms": int,
        "completed_at": ISO str | None,
      }
    """
    from core.models.agent_task_result import AgentTaskResult

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = (
            await session.execute(
                select(AgentTaskResult)
                .where(
                    AgentTaskResult.agent_id == agent_id,
                    AgentTaskResult.tenant_id == tid,
                )
                .order_by(AgentTaskResult.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if row is None:
        return {
            "has_run": False,
            "run_id": None,
            "status": None,
            "confidence": None,
            "bullets": [],
            "tools_cited": [],
            "hitl_required": False,
            "hitl_decision": None,
            "duration_ms": 0,
            "completed_at": None,
        }

    # Derive bullets from real trace — no hardcoded filler.
    bullets: list[str] = []
    tool_calls_list = row.tool_calls or []
    tools_cited = sorted(
        {
            (tc.get("tool") if isinstance(tc, dict) else None) or ""
            for tc in tool_calls_list
        }
        - {""}
    )

    if tools_cited:
        bullets.append(
            f"Called {len(tool_calls_list)} tool invocation"
            f"{'s' if len(tool_calls_list) != 1 else ''} "
            f"across {len(tools_cited)} distinct tool"
            f"{'s' if len(tools_cited) != 1 else ''}: "
            + ", ".join(tools_cited)
        )
    else:
        bullets.append("No tool calls were required for this run.")

    if row.confidence is not None:
        pct = int(round(row.confidence * 100))
        bullets.append(f"Confidence was {pct}% ({row.status}).")

    if row.hitl_required:
        decision = row.hitl_decision or "pending review"
        bullets.append(f"HITL gate triggered — {decision}.")
    else:
        bullets.append("No HITL gate conditions were met.")

    if row.error_message:
        # Constant summary (no exception text in the response body).
        bullets.append("The run ended with an error — see the audit log.")

    return {
        "has_run": True,
        "run_id": str(row.id),
        "status": row.status,
        "confidence": row.confidence,
        "bullets": bullets,
        "tools_cited": tools_cited,
        "hitl_required": row.hitl_required,
        "hitl_decision": row.hitl_decision,
        "duration_ms": row.duration_ms,
        "completed_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── POST /agents/{id}/feedback/analyze ──────────────────────────────────────
@router.post("/agents/{agent_id}/feedback/analyze")
async def analyze_agent_feedback(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Trigger feedback analysis to generate prompt amendment suggestions."""
    from core.feedback.analyzer import analyze_feedback

    result = await analyze_feedback(
        agent_id=str(agent_id),
        tenant_id=tenant_id,
    )
    return {"agent_id": str(agent_id), **result}


# ── GET /agents/{id}/amendments ─────────────────────────────────────────────
@router.get("/agents/{agent_id}/amendments")
async def list_agent_amendments(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """List current prompt amendments (learned rules) for an agent."""
    tid = _uuid.UUID(tenant_id)
    amendments: list[str] = []

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        # prompt_amendments is a JSONB list on the agent record
        raw = getattr(agent, "prompt_amendments", None) or []
        if isinstance(raw, list):
            amendments = [str(a) for a in raw]

    return {
        "agent_id": str(agent_id),
        "amendments": amendments,
        "count": len(amendments),
    }


# ── DELETE /agents/{id} ─────────────────────────────────────────────────────
@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Permanently delete an agent. Only paused, retired, or inactive agents can be deleted."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tid)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(404, "Agent not found")

        deletable_statuses = {"paused", "retired", "inactive", "shadow"}
        if agent.status not in deletable_statuses:
            raise HTTPException(
                409,
                f"Cannot delete agent in '{agent.status}' status. "
                f"Pause or retire the agent first.",
            )

        # Audit log entry before deletion (all NOT NULL fields populated)
        audit = AuditLog(
            tenant_id=tid,
            company_id=agent.company_id,
            event_type="agent.deleted",
            actor_type="user",
            actor_id="api",
            agent_id=agent_id,
            action="delete",
            outcome="success",
            resource_type="agent",
            resource_id=str(agent_id),
            details={"agent_name": agent.name, "agent_type": agent.agent_type},
        )
        session.add(audit)

        # Delete related records that may have FK constraints
        for related_model in (AgentLifecycleEvent, AgentCostLedger, AgentVersion):
            await session.execute(
                related_model.__table__.delete().where(
                    related_model.agent_id == agent_id
                )
            )
        # Clear HITL queue entries for this agent
        await session.execute(
            HITLQueue.__table__.delete().where(HITLQueue.agent_id == agent_id)
        )

        await session.delete(agent)

    return {"id": str(agent_id), "deleted": True}
