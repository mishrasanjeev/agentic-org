"""Celery tasks for workflow wait, event and HITL deadline handling.

Workflow run state is durable in PostgreSQL via ``WorkflowStateStore``.
Redis is still used for best-effort event-listener cache cleanup, but these
tasks must not mutate ``wfstate:{run_id}`` directly or treat Redis listener
keys as authoritative.
"""

from __future__ import annotations

from typing import Any

import structlog

from core.tasks.async_runner import run_async
from core.tasks.celery_app import app
from workflows.event_waits import WorkflowEventWaitStore
from workflows.state_store import WorkflowStateStore

logger = structlog.get_logger()


def _state_store() -> WorkflowStateStore:
    return WorkflowStateStore()


def _event_wait_store() -> WorkflowEventWaitStore:
    return WorkflowEventWaitStore()


def _get_redis():
    """Return a synchronous Redis client for event-listener cleanup."""
    import os

    import redis

    url = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")
    return redis.from_url(url, decode_responses=True)


def _clean_event_wait_keys(r: Any, run_id: str, step_id: str) -> None:
    """Delete any wait-for-event keys for this (run, step)."""
    event_pattern = f"wfwait_event:*:{run_id}:{step_id}"
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match=event_pattern, count=100)
        if keys:
            r.delete(*keys)
        if cursor == 0:
            break


def _best_effort_clean_event_wait_keys(run_id: str, step_id: str) -> None:
    try:
        r = _get_redis()
        try:
            _clean_event_wait_keys(r, run_id, step_id)
        finally:
            close = getattr(r, "close", None)
            if close is not None:
                close()
    # enterprise-gate: broad-except-ok reason=best-effort-redis-cleanup-after-durable-state-change
    except Exception as exc:  # noqa: BLE001 - cleanup cache must not block state progress.
        logger.warning(
            "workflow_event_wait_cleanup_failed",
            run_id=run_id,
            step_id=step_id,
            error=str(exc),
        )


async def _drive_engine_and_sync(store: WorkflowStateStore, run_id: str, step_id: str, log: Any) -> dict:
    """Re-drive the engine after a durable state flip and sync the DB run.

    Without this the run is left at ``status=running`` with nobody executing
    the remaining steps (stranded run).
    """
    from workflows.engine import WorkflowEngine
    from workflows.run_sync import sync_engine_state_to_workflow_run

    engine = WorkflowEngine(store)
    engine_result = await engine.execute(run_id)
    state = await store.load(run_id) or {}
    tenant_id = state.get("tenant_id")
    workflow_run_id = state.get("workflow_run_id")
    if tenant_id and workflow_run_id:
        import uuid

        await sync_engine_state_to_workflow_run(
            tenant_id=uuid.UUID(str(tenant_id)),
            workflow_run_id=uuid.UUID(str(workflow_run_id)),
            engine_run_id=run_id,
            state=state,
        )
    else:
        log.warning("workflow_run_sync_skipped_missing_context", step_id=step_id)
    return engine_result if isinstance(engine_result, dict) else {}


async def _resume_workflow_wait_async(run_id: str, step_id: str) -> dict:
    log = logger.bind(run_id=run_id, step_id=step_id)
    store = _state_store()
    await store.init()
    try:
        state = await store.load(run_id)
        if not state:
            log.warning("workflow_state_not_found")
            return {"status": "error", "reason": "workflow_state_not_found"}

        current_status = state.get("status")
        if current_status not in ("waiting_delay", "waiting_event"):
            log.info("workflow_not_waiting", status=current_status)
            return {"status": "noop", "reason": f"current status is {current_status}"}

        waiting_step_id = state.get("waiting_step_id")
        if waiting_step_id and waiting_step_id != step_id:
            log.warning(
                "waiting_step_mismatch",
                expected=waiting_step_id,
                received=step_id,
            )
            return {
                "status": "error",
                "reason": "waiting_step_id does not match",
            }

        step_results = state.setdefault("step_results", {})
        step_results[step_id] = {
            "output": {"resumed": True, "completed_by": "resume_workflow_wait"},
            "status": "completed",
            "completed_by": "resume_workflow_wait",
        }
        state["steps_completed"] = len(step_results)
        state["status"] = "running"
        state.pop("waiting_step_id", None)

        await store.save(
            state,
            actor="celery.resume_workflow_wait",
            step_id=step_id,
            idempotency_key=f"resume_workflow_wait:{run_id}:{step_id}",
            metadata={"task": "resume_workflow_wait"},
        )

        log.info("workflow_wait_resumed")
        engine_result = await _drive_engine_and_sync(store, run_id, step_id, log)
        return {
            "status": "resumed",
            "run_id": run_id,
            "step_id": step_id,
            "engine_status": engine_result.get("status"),
        }
    finally:
        await store.close()


@app.task(name="resume_workflow_wait")
def resume_workflow_wait(run_id: str, step_id: str) -> dict:
    """Resume a workflow paused at a wait_delay or wait_for_event step."""
    try:
        result = run_async(_resume_workflow_wait_async(run_id, step_id))
        if result.get("status") in {"resumed", "noop"}:
            _best_effort_clean_event_wait_keys(run_id, step_id)
        return result
    # enterprise-gate: broad-except-ok reason=celery-boundary-returns-structured-workflow-error
    except Exception as exc:  # noqa: BLE001 - Celery task returns structured errors.
        logger.error(
            "resume_workflow_wait_failed",
            run_id=run_id,
            step_id=step_id,
            error=str(exc),
        )
        return {"status": "error", "reason": str(exc)}


async def _timeout_workflow_event_async(run_id: str, step_id: str) -> dict:
    log = logger.bind(run_id=run_id, step_id=step_id)
    event_wait_store = _event_wait_store()
    await event_wait_store.init()
    event_wait_record = None
    try:
        event_wait_record = await event_wait_store.mark_timed_out(
            engine_run_id=run_id,
            step_id=step_id,
        )
    finally:
        await event_wait_store.close()

    if event_wait_record and event_wait_record.status in {"matched", "cancelled", "expired"}:
        log.info("event_wait_no_longer_waiting", listener_status=event_wait_record.status)
        return {"status": "noop", "reason": f"listener is {event_wait_record.status}"}

    state_store = _state_store()
    await state_store.init()
    try:
        state = await state_store.load(run_id)
        if not state:
            log.warning("workflow_state_not_found")
            return {"status": "error", "reason": "workflow_state_not_found"}

        step_results = state.get("step_results", {})
        if step_id in step_results:
            log.info("event_already_received_before_timeout")
            return {"status": "already_completed"}

        if state.get("waiting_step_id") != step_id:
            log.info("step_no_longer_waiting")
            return {"status": "noop"}

        step_results[step_id] = {
            "status": "timed_out",
            "output": {},
            "completed_by": "timeout_workflow_event",
        }
        state["step_results"] = step_results
        state["steps_completed"] = len(step_results)
        state["status"] = "running"
        state.pop("waiting_step_id", None)

        await state_store.save(
            state,
            actor="celery.timeout_workflow_event",
            step_id=step_id,
            idempotency_key=f"timeout_workflow_event:{run_id}:{step_id}",
            metadata={"task": "timeout_workflow_event"},
        )

        log.info("workflow_event_timed_out")
        engine_result = await _drive_engine_and_sync(state_store, run_id, step_id, log)
        return {
            "status": "timed_out",
            "run_id": run_id,
            "step_id": step_id,
            "engine_status": engine_result.get("status"),
        }
    finally:
        await state_store.close()


@app.task(name="timeout_workflow_event")
def timeout_workflow_event(run_id: str, step_id: str) -> dict:
    """Mark an event wait as timed_out and let the engine continue later."""
    try:
        result = run_async(_timeout_workflow_event_async(run_id, step_id))
        if result.get("status") in {"timed_out", "already_completed", "noop"}:
            _best_effort_clean_event_wait_keys(run_id, step_id)
        return result
    # enterprise-gate: broad-except-ok reason=celery-boundary-returns-structured-workflow-error
    except Exception as exc:  # noqa: BLE001 - Celery task returns structured errors.
        logger.error(
            "timeout_workflow_event_failed",
            run_id=run_id,
            step_id=step_id,
            error=str(exc),
        )
        return {"status": "error", "reason": str(exc)}


async def _timeout_workflow_hitl_async(run_id: str, step_id: str) -> dict:
    """Enforce the HITL deadline for ``step_id`` of engine run ``run_id``.

    If the run is still waiting on this step:

    * with an ``approval_timeout_policy`` whose outcome is ``auto_escalate``
      (see ``core.marketing.approval_timeouts``) the step is escalated once —
      the pending HITL rows are reassigned to ``escalation_role``, the
      deadline is extended by the policy SLA and a new timeout is queued;
    * otherwise the step becomes ``timed_out``, the run fails with
      ``error.code=hitl_timeout``, the HITL rows flip to ``expired`` and the
      DB run is synced.
    """
    from datetime import UTC, datetime, timedelta

    from workflows.run_sync import (
        expire_pending_hitl_items,
        schedule_hitl_timeout,
        sync_engine_state_to_workflow_run,
    )

    log = logger.bind(run_id=run_id, step_id=step_id)
    store = _state_store()
    await store.init()
    try:
        state = await store.load(run_id)
        if not state:
            log.warning("workflow_state_not_found")
            return {"status": "error", "reason": "workflow_state_not_found"}
        if state.get("status") != "waiting_hitl" or state.get("waiting_step_id") != step_id:
            log.info("hitl_step_no_longer_waiting", status=state.get("status"))
            return {"status": "noop", "reason": f"current status is {state.get('status')}"}

        step_results = state.setdefault("step_results", {})
        step_result = step_results.get(step_id) or {}
        output = dict(step_result.get("output") or {}) if isinstance(step_result.get("output"), dict) else {}
        now = datetime.now(UTC)

        tenant_id = state.get("tenant_id")
        workflow_run_id = state.get("workflow_run_id")
        tenant_uuid = workflow_run_uuid = None
        if tenant_id and workflow_run_id:
            import uuid

            tenant_uuid = uuid.UUID(str(tenant_id))
            workflow_run_uuid = uuid.UUID(str(workflow_run_id))

        policy = output.get("approval_timeout_policy")
        escalation_role = str(policy.get("escalation_role") or "") if isinstance(policy, dict) else ""
        if (
            isinstance(policy, dict)
            and policy.get("timeout_outcome") == "auto_escalate"
            and escalation_role
            and not output.get("hitl_escalated")
        ):
            sla_hours = float(policy.get("default_sla_hours") or output.get("timeout_hours") or 4)
            new_expires_at = now + timedelta(hours=sla_hours)
            output.update(
                {
                    "hitl_escalated": True,
                    "hitl_escalated_at": now.isoformat(),
                    "assignee_role": escalation_role,
                    "escalated_from_role": step_result.get("output", {}).get("assignee_role"),
                }
            )
            step_results[step_id] = {**step_result, "output": output}
            await store.save(
                state,
                actor="celery.timeout_workflow_hitl",
                step_id=step_id,
                idempotency_key=f"timeout_workflow_hitl:escalate:{run_id}:{step_id}",
                metadata={"task": "timeout_workflow_hitl", "event": "hitl_escalated"},
            )
            if tenant_uuid and workflow_run_uuid:
                await expire_pending_hitl_items(
                    tenant_id=tenant_uuid,
                    workflow_run_id=workflow_run_uuid,
                    step_id=step_id,
                    new_status="pending",
                    assignee_role=escalation_role,
                    expires_at=new_expires_at,
                )
            schedule_hitl_timeout(run_id, step_id, new_expires_at)
            log.info("workflow_hitl_escalated", escalation_role=escalation_role)
            return {
                "status": "escalated",
                "run_id": run_id,
                "step_id": step_id,
                "escalation_role": escalation_role,
                "expires_at": new_expires_at.isoformat(),
            }

        error = {
            "code": "hitl_timeout",
            "message": f"Step '{step_id}' timed out waiting for a human decision",
        }
        step_results[step_id] = {
            **step_result,
            "status": "timed_out",
            "error": error,
            "completed_by": "timeout_workflow_hitl",
        }
        state["steps_completed"] = len(step_results)
        state["status"] = "failed"
        state["error"] = error
        state["completed_at"] = now.isoformat()
        state.pop("waiting_step_id", None)
        await store.save(
            state,
            actor="celery.timeout_workflow_hitl",
            step_id=step_id,
            idempotency_key=f"timeout_workflow_hitl:{run_id}:{step_id}",
            metadata={"task": "timeout_workflow_hitl", "event": "hitl_timed_out"},
        )
        log.info("workflow_hitl_timed_out")

        if tenant_uuid and workflow_run_uuid:
            await expire_pending_hitl_items(
                tenant_id=tenant_uuid,
                workflow_run_id=workflow_run_uuid,
                step_id=step_id,
            )
            await sync_engine_state_to_workflow_run(
                tenant_id=tenant_uuid,
                workflow_run_id=workflow_run_uuid,
                engine_run_id=run_id,
                state=state,
            )
        else:
            log.warning("workflow_run_sync_skipped_missing_context")
        return {"status": "timed_out", "run_id": run_id, "step_id": step_id}
    finally:
        await store.close()


@app.task(name="timeout_workflow_hitl")
def timeout_workflow_hitl(run_id: str, step_id: str) -> dict:
    """Fail (or escalate) a workflow whose HITL step passed its deadline."""
    try:
        return run_async(_timeout_workflow_hitl_async(run_id, step_id))
    # enterprise-gate: broad-except-ok reason=celery-boundary-returns-structured-workflow-error
    except Exception as exc:  # noqa: BLE001 - Celery task returns structured errors.
        logger.error(
            "timeout_workflow_hitl_failed",
            run_id=run_id,
            step_id=step_id,
            error=str(exc),
        )
        return {"status": "error", "reason": str(exc)}
