"""Workflow endpoints."""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from api.deps import get_current_tenant, get_user_domains
from core.database import get_tenant_session
from core.models.workflow import StepExecution, WorkflowDefinition, WorkflowRun
from core.schemas.api import PaginatedResponse, WorkflowCreate, WorkflowRunTrigger

router = APIRouter()
_log = structlog.get_logger()


# ── NL-to-Workflow schemas ──────────────────────────────────────────────────


class WorkflowGenerateRequest(BaseModel):
    """Request body for POST /workflows/generate."""

    description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Plain English description of the workflow to generate",
    )
    deploy: bool = Field(
        default=False,
        description="If true, automatically create and activate the workflow",
    )


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
    if include_steps:
        d["steps"] = [_step_to_dict(s) for s in (run.steps or [])]
    return d


# ── POST /workflows/generate ─────────────────────────────────────────────────
@router.post("/workflows/generate")
async def generate_workflow_endpoint(
    body: WorkflowGenerateRequest,
    tenant_id: str = Depends(get_current_tenant),
):
    """Generate a workflow from a plain English description using LLM.

    Optionally deploys the workflow immediately if ``deploy`` is True.
    Returns the generated definition, deployment status, and workflow ID.
    """
    from core.workflow_generator import generate_workflow

    try:
        definition = await generate_workflow(body.description, tenant_id)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None

    workflow_id: str | None = None
    deployed = False

    if body.deploy:
        tid = _uuid.UUID(tenant_id)
        async with get_tenant_session(tid) as session:
            wf = WorkflowDefinition(
                tenant_id=tid,
                name=definition.get("name", "Generated Workflow"),
                version=definition.get("version", "1.0"),
                description=definition.get("description", ""),
                domain=definition.get("domain", "ops"),
                definition=definition,
                trigger_type=definition.get("trigger_type", "manual"),
                trigger_config=definition.get("trigger_config", {}),
                is_active=True,
            )
            session.add(wf)
            await session.flush()
            workflow_id = str(wf.id)
            deployed = True

        _log.info(
            "workflow_generated_and_deployed",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
        )

    return {
        "workflow": definition,
        "deployed": deployed,
        "workflow_id": workflow_id,
    }


# ── GET /workflows ───────────────────────────────────────────────────────────
@router.get("/workflows", response_model=PaginatedResponse)
async def list_workflows(
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
    # Validate definition structure
    if not body.definition or not isinstance(body.definition.get("steps"), list):
        raise HTTPException(
            400, "Workflow definition must contain a 'steps' array"
        )
    if len(body.definition["steps"]) == 0:
        raise HTTPException(400, "Workflow must have at least one step")

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


# ── DELETE /workflows/{wf_id} ────────────────────────────────────────────────
@router.delete("/workflows/{wf_id}")
async def delete_workflow(
    wf_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Soft-delete a workflow by setting is_active=False."""
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == wf_id,
                WorkflowDefinition.tenant_id == tid,
            )
        )
        wf = result.scalar_one_or_none()
        if not wf:
            raise HTTPException(404, "Workflow not found")
        wf.is_active = False
        await session.commit()
    return {"status": "deleted", "workflow_id": str(wf_id)}


# ── Background workflow execution ──────────────────────────────────────────


async def _execute_workflow_bg(
    tenant_id: _uuid.UUID,
    run_id: _uuid.UUID,
    definition: dict,
    trigger_payload: dict | None,
) -> None:
    """Execute workflow steps in background and sync each result to the DB."""
    from core.models.agent import Agent
    from core.models.hitl import HITLQueue
    from workflows.engine import WorkflowEngine
    from workflows.state_store import WorkflowStateStore

    state_store = WorkflowStateStore()
    await state_store.init()
    engine = WorkflowEngine(state_store)

    try:
        engine_run_id = await engine.start_run(definition, trigger_payload)

        # Persist engine_run_id so HITL resume can find it later
        async with get_tenant_session(tenant_id) as session:
            db_run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            db_run.context = {**(db_run.context or {}), "_engine_run_id": engine_run_id}

        steps_def = {s["id"]: s for s in definition.get("steps", [])}
        synced_steps: set[str] = set()

        while True:
            await engine.execute_next(engine_run_id)

            state = await state_store.load(engine_run_id)
            if not state:
                break

            # ---- sync new step results to DB ----
            async with get_tenant_session(tenant_id) as session:
                db_run = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                ).scalar_one()

                for step_id, step_result in state.get("step_results", {}).items():
                    if step_id in synced_steps:
                        continue

                    step_def = steps_def.get(step_id, {})
                    agent_id = None
                    raw_agent = step_def.get("agent_id")
                    if raw_agent:
                        try:
                            agent_id = _uuid.UUID(str(raw_agent))
                        except (ValueError, TypeError):
                            agent_id = None

                    step_status = step_result.get("status", "completed")
                    session.add(
                        StepExecution(
                            tenant_id=tenant_id,
                            workflow_run_id=run_id,
                            step_id=step_id,
                            step_type=step_def.get("type", "agent"),
                            agent_id=agent_id,
                            status=step_status,
                            output=step_result.get("output"),
                            confidence=step_result.get("confidence"),
                            error=(
                                {"message": step_result["error"]}
                                if step_result.get("error")
                                else None
                            ),
                            started_at=datetime.now(UTC),
                            completed_at=(
                                datetime.now(UTC)
                                if step_status != "waiting_hitl"
                                else None
                            ),
                        )
                    )
                    synced_steps.add(step_id)

                    # Create HITLQueue entry for approval steps
                    if step_status == "waiting_hitl":
                        timeout_h = step_def.get("timeout_hours", 4)
                        hitl_agent_id = agent_id
                        if not hitl_agent_id:
                            hitl_agent_id = (
                                await session.execute(
                                    select(Agent.id)
                                    .where(Agent.tenant_id == tenant_id)
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                        if hitl_agent_id:
                            session.add(
                                HITLQueue(
                                    tenant_id=tenant_id,
                                    workflow_run_id=run_id,
                                    agent_id=hitl_agent_id,
                                    title=f"Approval required: {step_def.get('title', step_id)}",
                                    trigger_type="workflow_step",
                                    priority=step_def.get("priority", "normal"),
                                    assignee_role=step_result.get(
                                        "assignee_role",
                                        step_def.get("assignee_role", "admin"),
                                    ),
                                    decision_options=step_def.get(
                                        "decision_options",
                                        {"options": ["approve", "reject"]},
                                    ),
                                    context={
                                        "workflow_run_id": str(run_id),
                                        "step_id": step_id,
                                        "engine_run_id": engine_run_id,
                                    },
                                    expires_at=datetime.now(UTC)
                                    + timedelta(hours=timeout_h),
                                )
                            )

                db_run.steps_completed = len(synced_steps)
                db_run.status = state.get("status", "running")
                if state.get("status") in ("completed", "failed", "timed_out"):
                    db_run.completed_at = datetime.now(UTC)
                if state.get("status") == "completed":
                    db_run.result = state.get("step_results")

            if state.get("status") in (
                "completed",
                "failed",
                "waiting_hitl",
                "timed_out",
                "cancelled",
            ):
                break

    except Exception as exc:
        _log.error("workflow_bg_failed", run_id=str(run_id), error=str(exc))
        try:
            async with get_tenant_session(tenant_id) as session:
                db_run = (
                    await session.execute(
                        select(WorkflowRun).where(WorkflowRun.id == run_id)
                    )
                ).scalar_one()
                db_run.status = "failed"
                db_run.error = {"message": str(exc)}
                db_run.completed_at = datetime.now(UTC)
        except Exception as inner:
            _log.error("workflow_bg_error_handler_failed", error=str(inner))
    finally:
        await state_store.close()


# ── POST /workflows/{wf_id}/run ─────────────────────────────────────────────
@router.post("/workflows/{wf_id}/run")
async def run_workflow(
    wf_id: UUID,
    background_tasks: BackgroundTasks,
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

        definition = wf.definition

        # Count steps from definition
        steps_list = definition.get("steps", [])
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

    # Execute workflow steps in the background
    background_tasks.add_task(
        _execute_workflow_bg, tid, run.id, definition, body.payload
    )

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
