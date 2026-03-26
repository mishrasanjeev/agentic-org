"""Agent CRUD + lifecycle endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_user_domains
from core.agents.registry import AgentRegistry
from core.database import get_tenant_session
from core.models.agent import Agent, AgentCostLedger, AgentLifecycleEvent, AgentVersion
from core.models.audit import AuditLog
from core.models.hitl import HITLQueue
from core.models.prompt_template import PromptEditHistory
from core.schemas.api import (
    AgentCloneRequest,
    AgentCreate,
    AgentUpdate,
    PaginatedResponse,
)
from core.schemas.messages import (
    HITLPolicy,
    TargetAgent,
    TaskAssignment,
    TaskInput,
    TaskMetadata,
)

logger = structlog.get_logger()

router = APIRouter()

# Valid lifecycle transitions: from_status -> set of allowed to_statuses
_LIFECYCLE_FSM: dict[str, list[str]] = {
    "shadow": ["active", "paused", "retired"],
    "active": ["paused", "retired"],
    "paused": ["active", "retired"],
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
        "system_prompt_text": agent.system_prompt_text,
        "reporting_to": agent.reporting_to,
    }


# ── POST /agents ─────────────────────────────────────────────────────────────
@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, tenant_id: str = Depends(get_current_tenant)):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        agent = Agent(
            tenant_id=tid,
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
            authorized_tools=body.authorized_tools,
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
        )
        session.add(agent)
        await session.flush()  # populate agent.id

        # Create initial AgentVersion snapshot
        version_row = AgentVersion(
            tenant_id=tid,
            agent_id=agent.id,
            version=agent.version,
            system_prompt=body.system_prompt,
            authorized_tools=body.authorized_tools,
            hitl_policy=body.hitl_policy.model_dump(),
            llm_config=body.llm.model_dump(),
            confidence_floor=agent.confidence_floor,
            deployed_at=datetime.now(UTC),
        )
        session.add(version_row)

    return {
        "agent_id": str(agent.id),
        "status": agent.status,
        "version": agent.version,
        "token_issued": True,
    }


# ── GET /agents ──────────────────────────────────────────────────────────────
@router.get("/agents", response_model=PaginatedResponse)
async def list_agents(
    domain: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
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

    # 2. Ensure all agent modules are registered
    import core.agents  # noqa: F401 — triggers @AgentRegistry.register for all agents

    # 3. Instantiate agent from registry (supports both built-in and custom types)
    agent_instance = AgentRegistry.create_from_config({
        "id": agent_config["id"],
        "tenant_id": tenant_id,
        "agent_type": agent_config["agent_type"],
        "authorized_tools": agent_config.get("authorized_tools", []),
        "prompt_variables": agent_config.get("prompt_variables", {}),
        "hitl_condition": agent_config.get("hitl_condition", ""),
        "output_schema": agent_config.get("output_schema"),
        "system_prompt_text": agent_config.get("system_prompt_text"),
        "llm_model": agent_config.get("llm_model"),
        "cost_controls": agent_config.get("cost_controls"),
    })

    # 4. Build TaskAssignment from user payload
    correlation_id = f"run_{_uuid.uuid4().hex[:12]}"
    task_assignment = TaskAssignment(
        message_id=f"msg_{_uuid.uuid4().hex[:12]}",
        correlation_id=correlation_id,
        workflow_run_id=payload.get("workflow_run_id", f"adhoc_{_uuid.uuid4().hex[:8]}"),
        workflow_definition_id=payload.get("workflow_definition_id", "adhoc"),
        step_id=payload.get("step_id", "step_0"),
        step_index=payload.get("step_index", 0),
        total_steps=payload.get("total_steps", 1),
        target_agent=TargetAgent(
            agent_id=agent_config["id"],
            agent_type=agent_config["agent_type"],
            agent_token="runtime",
        ),
        task=TaskInput(
            action=payload.get("action", "process"),
            inputs=payload.get("inputs", {}),
            context=payload.get("context", {}),
        ),
        hitl_policy=HITLPolicy(
            enabled=bool(agent_config.get("hitl_condition")),
            threshold_expression=agent_config.get("hitl_condition", ""),
        ),
        metadata=TaskMetadata(priority=payload.get("priority", "normal")),
    )

    # 5a. Budget check (if cost controls configured)
    cost_controls = agent_config.get("cost_controls", {})
    monthly_cap = cost_controls.get("monthly_cost_cap_usd", 0) if cost_controls else 0
    if monthly_cap and monthly_cap > 0:
        async with get_tenant_session(tid) as session:
            from sqlalchemy import func as sqlfunc
            month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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

    # 5b. Execute
    try:
        task_result = await agent_instance.execute(task_assignment)
    except Exception as exc:
        logger.error("agent_run_error", agent_id=str(agent_id), error=str(exc))
        raise HTTPException(500, f"Agent execution failed: {exc}") from exc

    # 5c. Track cost in ledger
    if task_result.performance and task_result.performance.llm_cost_usd:
        try:
            async with get_tenant_session(tid) as session:
                ledger_entry = AgentCostLedger(
                    tenant_id=tid,
                    agent_id=agent_id,
                    period_date=datetime.now(UTC),
                    token_count=task_result.performance.llm_tokens_used or 0,
                    cost_usd=task_result.performance.llm_cost_usd,
                    task_count=1,
                )
                session.add(ledger_entry)
        except Exception as e:
            logger.warning("cost_tracking_failed", agent_id=str(agent_id), error=str(e))

    # 6. Store result in audit log
    async with get_tenant_session(tid) as session:
        audit_entry = AuditLog(
            tenant_id=tid,
            event_type="agent.run",
            actor_type="agent",
            actor_id=str(agent_id),
            agent_id=agent_id,
            resource_type="task_result",
            resource_id=task_result.message_id,
            action="execute",
            outcome=task_result.status,
            details={
                "correlation_id": correlation_id,
                "confidence": task_result.confidence,
                "reasoning_trace": task_result.reasoning_trace,
                "performance": task_result.performance.model_dump(),
                "has_hitl": task_result.hitl_request is not None,
            },
        )
        session.add(audit_entry)

    # 6b. Create HITL queue entry if HITL was triggered
    if task_result.hitl_request:
        async with get_tenant_session(tid) as session:
            hitl_entry = HITLQueue(
                tenant_id=tid,
                agent_id=agent_id,
                workflow_run_id=None,
                title=f"HITL: {agent_config['agent_type']} — {task_result.hitl_request.trigger_condition}",
                trigger_type=task_result.hitl_request.trigger_type,
                priority="high" if task_result.confidence < 0.7 else "normal",
                assignee_role=agent_config.get("domain", "admin"),
                decision_options={
                    "options": ["approve", "reject", "override"],
                    "context": task_result.output,
                },
                context={
                    "correlation_id": correlation_id,
                    "agent_type": agent_config["agent_type"],
                    "confidence": task_result.confidence,
                    "reasoning_trace": task_result.reasoning_trace,
                    "trigger": task_result.hitl_request.trigger_condition,
                },
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
            session.add(hitl_entry)

    # 7. Return the real result
    response = {
        "task_id": task_result.message_id,
        "agent_id": str(agent_id),
        "correlation_id": correlation_id,
        "status": task_result.status,
        "output": task_result.output,
        "confidence": task_result.confidence,
        "reasoning_trace": task_result.reasoning_trace,
        "performance": task_result.performance.model_dump(),
    }
    if task_result.hitl_request:
        response["hitl_request"] = task_result.hitl_request.model_dump()
    if task_result.error:
        response["error"] = task_result.error

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

        agent.status = "active"

        event = AgentLifecycleEvent(
            tenant_id=tid,
            agent_id=agent.id,
            from_status="paused",
            to_status="active",
            triggered_by="api",
            reason="Agent resumed via API",
        )
        session.add(event)

    return {"id": str(agent_id), "status": "active"}


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
        if agent.status == "shadow":
            if agent.shadow_sample_count < agent.shadow_min_samples:
                raise HTTPException(
                    409,
                    f"Shadow agent has {agent.shadow_sample_count}/{agent.shadow_min_samples} samples; "
                    f"cannot promote until minimum is met",
                )
            if (
                agent.shadow_accuracy_current is not None
                and agent.shadow_accuracy_current < agent.shadow_accuracy_floor
            ):
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
            day=1, hour=0, minute=0, second=0, microsecond=0
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
