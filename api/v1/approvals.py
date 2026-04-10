"""HITL approval endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_current_user, get_user_domains, get_user_role
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.audit import AuditLog
from core.models.hitl import HITLQueue
from core.schemas.api import HITLDecision, PaginatedResponse

router = APIRouter()
_log = structlog.get_logger()

# RBAC role hierarchy — higher number = more authority.
# A user can decide on HITL items where their level >= the assignee_role's level.
_ROLE_HIERARCHY: dict[str, int] = {
    "staff": 10,
    "manager": 20,
    "auditor": 25,
    "cfo": 30,
    "chro": 30,
    "cmo": 30,
    "coo": 30,
    "cbo": 30,
    "ceo": 50,
    "admin": 100,  # admin can VIEW all but DECIDE only on assigned (see decide endpoint)
}


def _role_level(role: str) -> int:
    return _ROLE_HIERARCHY.get((role or "").lower(), 0)


def _can_decide(
    user_role: str,
    user_domains: list[str] | None,
    assignee_role: str,
    agent_domain: str | None,
) -> tuple[bool, str]:
    """Check if user can decide on a HITL item.

    Rules:
      - admin can DECIDE only on items where role matches (not blanket override)
      - For other roles: user role level must be >= assignee_role level
      - Domain match required if user has domain restriction
    """
    if not user_role:
        return False, "user has no role"
    if not assignee_role:
        return False, "HITL item has no assignee_role"

    user_lvl = _role_level(user_role)
    required_lvl = _role_level(assignee_role)

    if user_lvl == 0:
        return False, f"unknown role '{user_role}'"

    # P3.2: admin must still match the assignee role to DECIDE (not just by being admin)
    if user_role.lower() == "admin":
        # Admin can decide if they share the same level/domain as assignee
        if user_lvl < required_lvl:
            return False, f"admin level {user_lvl} insufficient for {assignee_role} ({required_lvl})"
    elif user_lvl < required_lvl:
        return False, f"role '{user_role}' (level {user_lvl}) cannot approve '{assignee_role}' (level {required_lvl})"

    # Domain check (if user has domain restriction)
    if user_domains is not None and agent_domain and agent_domain not in user_domains:
        return False, f"user not authorized for domain '{agent_domain}'"

    return True, ""


def _hitl_to_dict(item: HITLQueue) -> dict:
    return {
        "id": str(item.id),
        "workflow_run_id": str(item.workflow_run_id) if item.workflow_run_id else None,
        "agent_id": str(item.agent_id),
        "title": item.title,
        "trigger_type": item.trigger_type,
        "priority": item.priority,
        "status": item.status,
        "assignee_role": item.assignee_role,
        "decision_options": item.decision_options,
        "context": item.context,
        "decision": item.decision,
        "decision_by": str(item.decision_by) if item.decision_by else None,
        "decision_at": item.decision_at.isoformat() if item.decision_at else None,
        "decision_notes": item.decision_notes,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ── GET /approvals ───────────────────────────────────────────────────────────
@router.get("/approvals", response_model=PaginatedResponse)
async def list_approvals(
    domain: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        base = select(HITLQueue).where(HITLQueue.tenant_id == tid)
        count_base = select(func.count()).select_from(HITLQueue).where(HITLQueue.tenant_id == tid)

        # RBAC domain filtering via Agent subquery
        if user_domains is not None:
            domain_agent_ids = (
                select(Agent.id).where(Agent.domain.in_(user_domains)).scalar_subquery()
            )
            base = base.where(HITLQueue.agent_id.in_(domain_agent_ids))
            count_base = count_base.where(HITLQueue.agent_id.in_(domain_agent_ids))

        if priority:
            base = base.where(HITLQueue.priority == priority)
            count_base = count_base.where(HITLQueue.priority == priority)
        if status:
            base = base.where(HITLQueue.status == status)
            count_base = count_base.where(HITLQueue.status == status)

        # Default: show only pending items
        if not status:
            base = base.where(HITLQueue.status == "pending")
            count_base = count_base.where(HITLQueue.status == "pending")

        # Exclude expired items from the pending queue
        if status == "pending" or not status:
            now = datetime.now(UTC)
            base = base.where(
                (HITLQueue.expires_at.is_(None)) | (HITLQueue.expires_at > now)
            )
            count_base = count_base.where(
                (HITLQueue.expires_at.is_(None)) | (HITLQueue.expires_at > now)
            )

        total = (await session.execute(count_base)).scalar() or 0

        query = (
            base.order_by(HITLQueue.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
        result = await session.execute(query)
        items = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_hitl_to_dict(i) for i in items],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── Background workflow resume after HITL decision ─────────────────────────


async def _resume_workflow_bg(
    tenant_id: _uuid.UUID,
    workflow_run_id: _uuid.UUID,
    decision: dict,
) -> None:
    """Resume a workflow after HITL decision and sync remaining results to DB."""
    from core.models.workflow import StepExecution, WorkflowDefinition, WorkflowRun
    from workflows.engine import WorkflowEngine
    from workflows.state_store import WorkflowStateStore

    # Load engine_run_id and workflow definition
    async with get_tenant_session(tenant_id) as session:
        db_run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            )
        ).scalar_one_or_none()
        if not db_run:
            return
        engine_run_id = (db_run.context or {}).get("_engine_run_id")
        wf_def = (
            await session.execute(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == db_run.workflow_def_id
                )
            )
        ).scalar_one_or_none()
        definition = wf_def.definition if wf_def else None

    if not engine_run_id or not definition:
        return

    state_store = WorkflowStateStore()
    await state_store.init()
    engine = WorkflowEngine(state_store)

    try:
        # Resume executes all remaining steps (or pauses at next HITL)
        await engine.resume_from_hitl(engine_run_id, decision)

        state = await state_store.load(engine_run_id)
        if not state:
            return

        steps_def = {s["id"]: s for s in definition.get("steps", [])}

        async with get_tenant_session(tenant_id) as session:
            existing = (
                await session.execute(
                    select(StepExecution.step_id).where(
                        StepExecution.workflow_run_id == workflow_run_id
                    )
                )
            ).scalars().all()
            synced_steps = set(existing)

            db_run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
                )
            ).scalar_one()

            for step_id, step_result in state.get("step_results", {}).items():
                if step_id in synced_steps:
                    # Update existing step if status changed (e.g. waiting_hitl → completed)
                    existing_step = (
                        await session.execute(
                            select(StepExecution).where(
                                StepExecution.workflow_run_id == workflow_run_id,
                                StepExecution.step_id == step_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing_step and existing_step.status != step_result.get("status"):
                        existing_step.status = step_result.get("status", "completed")
                        existing_step.output = step_result.get("output")
                        existing_step.completed_at = datetime.now(UTC)
                    continue

                step_def = steps_def.get(step_id, {})
                agent_id = None
                raw_agent = step_def.get("agent_id")
                if raw_agent:
                    try:
                        agent_id = _uuid.UUID(str(raw_agent))
                    except (ValueError, TypeError):
                        pass

                session.add(
                    StepExecution(
                        tenant_id=tenant_id,
                        workflow_run_id=workflow_run_id,
                        step_id=step_id,
                        step_type=step_def.get("type", "agent"),
                        agent_id=agent_id,
                        status=step_result.get("status", "completed"),
                        output=step_result.get("output"),
                        confidence=step_result.get("confidence"),
                        error=(
                            {"message": step_result["error"]}
                            if step_result.get("error")
                            else None
                        ),
                        started_at=datetime.now(UTC),
                        completed_at=datetime.now(UTC),
                    )
                )
                synced_steps.add(step_id)

            db_run.steps_completed = len(synced_steps)
            db_run.status = state.get("status", "running")
            if state.get("status") in ("completed", "failed", "timed_out"):
                db_run.completed_at = datetime.now(UTC)
            if state.get("status") == "completed":
                db_run.result = state.get("step_results")

    except Exception as exc:
        _log.error("workflow_resume_failed", run_id=str(workflow_run_id), error=str(exc))
        try:
            async with get_tenant_session(tenant_id) as session:
                db_run = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
                    )
                ).scalar_one()
                db_run.status = "failed"
                db_run.error = {"message": f"Resume failed: {exc}"}
                db_run.completed_at = datetime.now(UTC)
        except Exception as inner:
            _log.error("workflow_resume_error_handler_failed", error=str(inner))
    finally:
        await state_store.close()


# ── POST /approvals/{id}/decide ─────────────────────────────────────────────
@router.post("/approvals/{hitl_id}/decide")
async def decide(
    hitl_id: UUID,
    body: HITLDecision,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_current_tenant),
    user_claims: dict = Depends(get_current_user),
    user_role: str = Depends(get_user_role),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)

    # P2.1: capture decision_by from authenticated user (always required)
    user_id_str = user_claims.get("sub") or user_claims.get("agenticorg:user_id") or ""
    user_name = user_claims.get("name") or user_claims.get("email") or "unknown"
    if not user_id_str:
        raise HTTPException(401, "Cannot identify user — missing 'sub' claim")

    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(HITLQueue).where(HITLQueue.id == hitl_id, HITLQueue.tenant_id == tid)
        )
        item = result.scalar_one_or_none()
        if not item:
            raise HTTPException(404, "HITL item not found")
        if item.status != "pending":
            raise HTTPException(409, f"HITL item already resolved with status '{item.status}'")

        # Check expiry
        if item.expires_at and datetime.now(UTC) > item.expires_at:
            raise HTTPException(410, "HITL item has expired")

        # Validate assignee_role exists (P2.1 — never allow approval without role)
        if not item.assignee_role:
            raise HTTPException(
                422,
                "HITL item has no assignee_role — cannot validate authorization",
            )

        # P1.1 + P3.2: Resolve agent domain for RBAC check
        agent_result = await session.execute(
            select(Agent.domain).where(Agent.id == item.agent_id)
        )
        agent_domain = agent_result.scalar_one_or_none()

        # P1.1: Enforce role hierarchy and domain match
        allowed, reason = _can_decide(user_role, user_domains, item.assignee_role, agent_domain)
        if not allowed:
            _log.warning(
                "hitl_decide_denied",
                hitl_id=str(hitl_id),
                user_id=user_id_str,
                user_role=user_role,
                assignee_role=item.assignee_role,
                reason=reason,
            )
            raise HTTPException(403, f"Cannot decide on this approval: {reason}")

        # Apply decision with full attribution
        try:
            user_uuid = _uuid.UUID(user_id_str)
            item.decision_by = user_uuid
        except (ValueError, TypeError):
            # Non-UUID sub claim — store None but log
            _log.warning("hitl_decide_non_uuid_user", user_id=user_id_str)

        item.decision = body.decision
        item.decision_notes = body.notes if body.notes else None
        item.decision_at = datetime.now(UTC)
        item.status = "decided"
        workflow_run_id = item.workflow_run_id

        # Audit log entry — captures who approved/rejected what
        audit = AuditLog(
            tenant_id=tid,
            event_type="hitl.decided",
            actor_type="user",
            actor_id=user_id_str,
            agent_id=item.agent_id,
            action=body.decision or "decide",
            outcome="success",
            resource_type="hitl_item",
            resource_id=str(hitl_id),
            details={
                "decision": body.decision,
                "notes": body.notes or "",
                "user_name": user_name,
                "user_role": user_role,
                "assignee_role": item.assignee_role,
                "agent_domain": agent_domain,
            },
        )
        session.add(audit)

        _log.info(
            "hitl_decided",
            hitl_id=str(hitl_id),
            user_id=user_id_str,
            user_role=user_role,
            decision=body.decision,
        )

    # Resume workflow execution in background
    if workflow_run_id:
        background_tasks.add_task(
            _resume_workflow_bg,
            tid,
            workflow_run_id,
            {"decision": body.decision, "notes": body.notes},
        )

    return {
        "hitl_id": str(hitl_id),
        "decision": body.decision,
        "status": "decided",
        "decided_by": user_id_str,
        "decided_at": item.decision_at.isoformat(),
    }
