"""HITL approval endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.hitl import HITLQueue
from core.schemas.api import HITLDecision, PaginatedResponse

router = APIRouter()
_log = structlog.get_logger()


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
):
    tid = _uuid.UUID(tenant_id)
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

        item.decision = body.decision
        item.decision_notes = body.notes if body.notes else None
        item.decision_at = datetime.now(UTC)
        item.status = "decided"
        workflow_run_id = item.workflow_run_id

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
        "decided_at": item.decision_at.isoformat(),
    }
