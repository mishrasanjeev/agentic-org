"""Sync durable engine state back to the tenant ``WorkflowRun`` row.

Shared by the HITL resume path (``api.v1.approvals._resume_workflow_bg``)
and the Celery wait/timeout resumption tasks so every path that re-drives
``WorkflowEngine.execute`` reflects step results, HITL queue entries, the
run status and A/B variant outcomes the same way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()


async def record_ab_outcome_if_terminal(db_run: Any) -> None:
    """Record the A/B variant outcome once, only when the run is terminal.

    Idempotent: a marker is written into ``db_run.context`` so a run that
    pauses and resumes several times is counted exactly once.
    """
    from api.v1.workflows import TERMINAL_WORKFLOW_STATUSES
    from core.workflow_ab import record_outcome

    if db_run is None or db_run.status not in TERMINAL_WORKFLOW_STATUSES:
        return
    context = dict(db_run.context or {})
    ab = context.get("ab") or {}
    variant_id = ab.get("variant_id")
    if not variant_id or ab.get("outcome_recorded"):
        return
    await record_outcome(uuid.UUID(str(variant_id)), success=db_run.status == "completed")
    context["ab"] = {**ab, "outcome_recorded": True}
    db_run.context = context


def hitl_timeout_hours(step_result: dict[str, Any], step_def: dict[str, Any]) -> float:
    """Resolve the HITL SLA for a waiting step.

    The engine result (``output.timeout_hours``) wins because it already
    applied the approval timeout policy; the definition value is the
    fallback and 4h the default.
    """
    output = step_result.get("output") if isinstance(step_result, dict) else None
    candidates = (
        output.get("timeout_hours") if isinstance(output, dict) else None,
        step_result.get("timeout_hours") if isinstance(step_result, dict) else None,
        step_def.get("timeout_hours"),
    )
    for raw in candidates:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 4.0


def schedule_hitl_timeout(engine_run_id: str, step_id: str, expires_at: datetime) -> bool:
    """Queue ``timeout_workflow_hitl`` for ``expires_at``.

    Scheduling failure is logged and returns False; the HITL item still
    exists with its ``expires_at`` so the approvals list and ``/decide``
    enforce the deadline, and an operator can re-run the timeout task.
    """
    try:
        from core.tasks.workflow_tasks import timeout_workflow_hitl

        timeout_workflow_hitl.apply_async(args=[engine_run_id, step_id], eta=expires_at)
        return True
    # enterprise-gate: broad-except-ok reason=hitl-timeout-scheduling-failure-leaves-durable-expires-at
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "workflow_hitl_timeout_schedule_failed",
            run_id=engine_run_id,
            step_id=step_id,
            error=str(exc),
        )
        return False


async def expire_pending_hitl_items(
    *,
    tenant_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    step_id: str,
    new_status: str = "expired",
    assignee_role: str | None = None,
    expires_at: datetime | None = None,
) -> int:
    """Flip the pending HITL queue rows for one workflow step.

    ``new_status="expired"`` closes them on timeout; passing ``assignee_role``
    and ``expires_at`` with ``new_status="pending"`` escalates them instead.
    Returns the number of rows changed.
    """
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.hitl import HITLQueue

    changed = 0
    async with get_tenant_session(tenant_id) as session:
        rows = (
            await session.execute(
                select(HITLQueue).where(
                    HITLQueue.tenant_id == tenant_id,
                    HITLQueue.workflow_run_id == workflow_run_id,
                    HITLQueue.status == "pending",
                )
            )
        ).scalars().all()
        for item in rows:
            context = item.context if isinstance(item.context, dict) else {}
            if str(context.get("step_id") or "") != str(step_id):
                continue
            item.status = new_status
            if assignee_role:
                item.assignee_role = assignee_role
            if expires_at is not None:
                item.expires_at = expires_at
            if new_status == "expired":
                item.decision_at = datetime.now(UTC)
                item.decision_notes = "Expired: no decision before the approval deadline."
            changed += 1
    return changed


async def sync_engine_state_to_workflow_run(
    *,
    tenant_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    engine_run_id: str,
    state: dict[str, Any],
    definition: dict[str, Any] | None = None,
) -> None:
    """Persist engine ``state`` (step results + run status) to the DB run."""
    from sqlalchemy import select

    from api.v1.workflows import (
        TERMINAL_WORKFLOW_STATUSES,
        _run_steps_completed,
        _run_steps_total,
        _upsert_step_execution,
    )
    from core.database import get_tenant_session
    from core.models.agent import Agent
    from core.models.hitl import HITLQueue
    from core.models.workflow import WorkflowRun

    definition = definition or state.get("definition") or {}
    steps_def = {s["id"]: s for s in definition.get("steps", [])}

    async with get_tenant_session(tenant_id) as session:
        db_run = (
            await session.execute(select(WorkflowRun).where(WorkflowRun.id == workflow_run_id))
        ).scalar_one_or_none()
        if db_run is None:
            logger.warning(
                "workflow_run_sync_missing_db_run",
                workflow_run_id=str(workflow_run_id),
                engine_run_id=engine_run_id,
            )
            return

        for step_id, step_result in state.get("step_results", {}).items():
            step_def = steps_def.get(step_id, {})
            step_row, created = await _upsert_step_execution(
                session,
                tenant_id=tenant_id,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                step_result=step_result,
                step_def=step_def,
            )

            if created and step_row.status == "waiting_hitl":
                timeout_h = hitl_timeout_hours(step_result, step_def)
                hitl_agent_id = step_row.agent_id
                if not hitl_agent_id:
                    hitl_agent_id = (
                        await session.execute(
                            select(Agent.id).where(Agent.tenant_id == tenant_id).limit(1)
                        )
                    ).scalar_one_or_none()
                if hitl_agent_id:
                    expires_at = datetime.now(UTC) + timedelta(hours=timeout_h)
                    hitl_item = HITLQueue(
                            tenant_id=tenant_id,
                            workflow_run_id=workflow_run_id,
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
                                "workflow_run_id": str(workflow_run_id),
                                "step_id": step_id,
                                "engine_run_id": engine_run_id,
                            },
                            expires_at=expires_at,
                    )
                    session.add(hitl_item)
                    schedule_hitl_timeout(engine_run_id, step_id, expires_at)
                    from core.push.sender import notify_approval_created

                    await notify_approval_created(str(tenant_id), item_id=str(hitl_item.id), action=step_id)

        db_run.steps_completed = _run_steps_completed(state)
        db_run.steps_total = _run_steps_total(state, db_run.steps_total)
        db_run.status = state.get("status", "running")
        if state.get("status") in TERMINAL_WORKFLOW_STATUSES:
            db_run.completed_at = datetime.now(UTC)
        if state.get("status") == "completed":
            db_run.result = state.get("step_results")
        if state.get("status") == "failed" and isinstance(state.get("error"), dict):
            db_run.error = state["error"]

        try:
            await record_ab_outcome_if_terminal(db_run)
        # enterprise-gate: broad-except-ok reason=ab-outcome-recording-is-best-effort-after-run-terminal
        except Exception:  # noqa: BLE001 - A/B bookkeeping must not fail the run sync.
            logger.debug("workflow_ab_record_outcome_skipped", run_id=str(workflow_run_id))
