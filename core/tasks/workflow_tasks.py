"""Celery tasks for workflow wait step and event-based resumption.

Workflow run state is durable in PostgreSQL via ``WorkflowStateStore``.
Redis is still used for best-effort event-listener cache cleanup, but these
tasks must not mutate ``wfstate:{run_id}`` directly or treat Redis listener
keys as authoritative.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

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
        return {
            "status": "resumed",
            "run_id": run_id,
            "step_id": step_id,
        }
    finally:
        await store.close()


@app.task(name="resume_workflow_wait")
def resume_workflow_wait(run_id: str, step_id: str) -> dict:
    """Resume a workflow paused at a wait_delay or wait_for_event step."""
    try:
        result = asyncio.run(_resume_workflow_wait_async(run_id, step_id))
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
        return {
            "status": "timed_out",
            "run_id": run_id,
            "step_id": step_id,
        }
    finally:
        await state_store.close()


@app.task(name="timeout_workflow_event")
def timeout_workflow_event(run_id: str, step_id: str) -> dict:
    """Mark an event wait as timed_out and let the engine continue later."""
    try:
        result = asyncio.run(_timeout_workflow_event_async(run_id, step_id))
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
