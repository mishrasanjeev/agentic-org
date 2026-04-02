"""Celery tasks for workflow wait step and event-based resumption."""

from __future__ import annotations

import json

import structlog

from core.tasks.celery_app import app

logger = structlog.get_logger()


def _get_redis():
    """Return a synchronous Redis client for workflow state."""
    import os

    import redis

    url = os.getenv("AGENTICORG_REDIS_URL", "redis://localhost:6379/1")
    return redis.from_url(url, decode_responses=True)


@app.task(name="resume_workflow_wait")
def resume_workflow_wait(run_id: str, step_id: str) -> dict:
    """Load workflow state from Redis, mark wait step complete, resume execution.

    Called when:
    - A scheduled wait duration expires (via Celery ETA / countdown).
    - An external event matches a ``wait_for_event`` step (triggered by webhook).

    Workflow state is stored at ``wfstate:{run_id}`` as a JSON hash.
    """
    log = logger.bind(run_id=run_id, step_id=step_id)
    try:
        r = _get_redis()
        state_key = f"wfstate:{run_id}"
        raw = r.get(state_key)
        if not raw:
            log.warning("workflow_state_not_found")
            return {"status": "error", "reason": "workflow_state_not_found"}

        state = json.loads(raw)
        steps = state.get("steps", {})

        if step_id not in steps:
            log.warning("step_not_found", available=list(steps.keys()))
            return {"status": "error", "reason": "step_not_found"}

        step = steps[step_id]
        if step.get("status") == "completed":
            log.info("step_already_completed")
            return {"status": "already_completed"}

        # Mark step as completed
        step["status"] = "completed"
        step["completed_by"] = "resume_workflow_wait"
        state["steps"] = steps
        state["current_step"] = step.get("next_step", None)

        r.set(state_key, json.dumps(state))

        # Clean up any event wait keys for this step
        event_pattern = f"wfwait_event:*:{run_id}:{step_id}"
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=event_pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break

        log.info("workflow_wait_resumed", next_step=state.get("current_step"))
        return {
            "status": "resumed",
            "run_id": run_id,
            "step_id": step_id,
            "next_step": state.get("current_step"),
        }

    except Exception as exc:
        log.error("resume_workflow_wait_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


@app.task(name="timeout_workflow_event")
def timeout_workflow_event(run_id: str, step_id: str) -> dict:
    """Mark an event wait as timed_out and resume to the fallback path.

    Scheduled with a countdown equal to the step's ``timeout_hours``.
    If the event already arrived (step completed), this is a no-op.
    """
    log = logger.bind(run_id=run_id, step_id=step_id)
    try:
        r = _get_redis()
        state_key = f"wfstate:{run_id}"
        raw = r.get(state_key)
        if not raw:
            log.warning("workflow_state_not_found")
            return {"status": "error", "reason": "workflow_state_not_found"}

        state = json.loads(raw)
        steps = state.get("steps", {})

        if step_id not in steps:
            log.warning("step_not_found")
            return {"status": "error", "reason": "step_not_found"}

        step = steps[step_id]

        # If event already arrived, skip timeout
        if step.get("status") == "completed":
            log.info("event_already_received_before_timeout")
            return {"status": "already_completed"}

        # Mark as timed out and route to fallback path
        step["status"] = "timed_out"
        step["timed_out"] = True
        state["steps"] = steps
        state["current_step"] = step.get("false_path", step.get("fallback_step"))

        r.set(state_key, json.dumps(state))

        # Clean up event wait keys
        event_pattern = f"wfwait_event:*:{run_id}:{step_id}"
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=event_pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break

        log.info(
            "workflow_event_timed_out",
            fallback_step=state.get("current_step"),
        )
        return {
            "status": "timed_out",
            "run_id": run_id,
            "step_id": step_id,
            "fallback_step": state.get("current_step"),
        }

    except Exception as exc:
        log.error("timeout_workflow_event_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}
