"""Workflow endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.workflow import StepExecution, WorkflowDefinition, WorkflowRun
from core.schemas.api import PaginatedResponse, WorkflowCreate, WorkflowRunTrigger

router = APIRouter()


def _wf_to_dict(wf: WorkflowDefinition) -> dict:
    return {
        "id": str(wf.id),
        "name": wf.name,
        "version": wf.version,
        "description": wf.description,
        "domain": wf.domain,
        "trigger_type": wf.trigger_type,
        "trigger_config": wf.trigger_config,
        "is_active": wf.is_active,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
    }


def _step_to_dict(step: StepExecution) -> dict:
    return {
        "id": str(step.id),
        "step_id": step.step_id,
        "step_type": step.step_type,
        "agent_id": str(step.agent_id) if step.agent_id else None,
        "status": step.status,
        "input": step.input,
        "output": step.output,
        "confidence": float(step.confidence) if step.confidence is not None else None,
        "error": step.error,
        "retry_count": step.retry_count,
        "latency_ms": step.latency_ms,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
    }


def _run_to_dict(run: WorkflowRun, include_steps: bool = False) -> dict:
    d = {
        "run_id": str(run.id),
        "workflow_def_id": str(run.workflow_def_id),
        "status": run.status,
        "trigger_payload": run.trigger_payload,
        "context": run.context,
        "result": run.result,
        "error": run.error,
        "steps_total": run.steps_total,
        "steps_completed": run.steps_completed,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if include_steps and run.steps:
        d["steps"] = [_step_to_dict(s) for s in run.steps]
    return d


# ── GET /workflows ───────────────────────────────────────────────────────────
@router.get("/workflows", response_model=PaginatedResponse)
async def list_workflows(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    tenant_id: str = Depends(get_current_tenant),
    user_domains: list[str] | None = Depends(get_user_domains),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        count_q = (
            select(func.count())
            .select_from(WorkflowDefinition)
            .where(WorkflowDefinition.tenant_id == tid)
        )

        query = select(WorkflowDefinition).where(WorkflowDefinition.tenant_id == tid)

        # RBAC domain filtering
        if user_domains is not None:
            query = query.where(WorkflowDefinition.domain.in_(user_domains))
            count_q = count_q.where(WorkflowDefinition.domain.in_(user_domains))

        total = (await session.execute(count_q)).scalar() or 0

        query = (
            query.order_by(WorkflowDefinition.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await session.execute(query)
        workflows = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_wf_to_dict(wf) for wf in workflows],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── GET /workflows/{wf_id} ──────────────────────────────────────────────────
@router.get("/workflows/{wf_id}")
async def get_workflow(
    wf_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == wf_id, WorkflowDefinition.tenant_id == tid
            )
        )
        wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _wf_to_dict(wf)


# ── POST /workflows ─────────────────────────────────────────────────────────
@router.post("/workflows", status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        wf = WorkflowDefinition(
            tenant_id=tid,
            name=body.name,
            version=body.version,
            description=body.description,
            domain=body.domain,
            definition=body.definition,
            trigger_type=body.trigger_type,
            trigger_config=body.trigger_config,
            is_active=True,
        )
        session.add(wf)
        await session.flush()

    return {
        "workflow_id": str(wf.id),
        "name": wf.name,
        "version": wf.version,
    }


# ── POST /workflows/{wf_id}/run ─────────────────────────────────────────────
@router.post("/workflows/{wf_id}/run")
async def run_workflow(
    wf_id: UUID,
    body: WorkflowRunTrigger | None = None,
    tenant_id: str = Depends(get_current_tenant),
):
    if body is None:
        body = WorkflowRunTrigger()
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Verify the workflow definition exists
        wf_result = await session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == wf_id,
                WorkflowDefinition.tenant_id == tid,
            )
        )
        wf = wf_result.scalar_one_or_none()
        if not wf:
            raise HTTPException(404, "Workflow definition not found")
        if not wf.is_active:
            raise HTTPException(409, "Workflow definition is inactive")

        # Count steps from definition
        steps_list = wf.definition.get("steps", [])
        steps_total = len(steps_list) if isinstance(steps_list, list) else None

        run = WorkflowRun(
            tenant_id=tid,
            workflow_def_id=wf.id,
            status="running",
            trigger_payload=body.payload,
            context={},
            steps_total=steps_total,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()

    return {
        "run_id": str(run.id),
        "workflow_def_id": str(wf_id),
        "status": "running",
        "started_at": run.started_at.isoformat() if run.started_at else None,
    }


# ── GET /workflows/runs/{run_id} ────────────────────────────────────────────
@router.get("/workflows/runs/{run_id}")
async def get_workflow_run(
    run_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(WorkflowRun)
            .options(selectinload(WorkflowRun.steps))
            .where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == tid)
        )
        run = result.scalar_one_or_none()

    if not run:
        raise HTTPException(404, "Workflow run not found")

    return _run_to_dict(run, include_steps=True)
