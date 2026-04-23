"""Celery tasks for workflow wait step and event-based resumption.

Codex 2026-04-23 re-verification blocker A: these tasks were reading
``state["steps"]`` / writing ``state["current_step"]``, but the
async engine (``workflows/engine.py``) writes
``state["step_results"]`` / ``state["waiting_step_id"]`` /
``state["status"]`` to the same Redis key. Scheduled timeouts and
external-event resumes silently did nothing because the keys didn't
match — the workflow sat in ``waiting_*`` forever.

The fix aligns this module to the engine's canonical schema. We
re-implement the engine's ``resume_from_wait`` / ``timeout_event_wait``
semantics in sync Redis so scheduled Celery workers can drive the
same state transitions the async engine uses. The delegate functions
keep the behaviour in lockstep: any subsequent engine schema change
must update both this file and ``workflows/engine.py`` together.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.tasks.celery_app import app

logger = structlog.get_logger()


def _get_redis():
    """Return a synchronous Redis client for workflow state."""
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


@app.task(name="resume_workflow_wait")
def resume_workflow_wait(run_id: str, step_id: str) -> dict:
    """Resume a workflow paused at a wait_delay or wait_for_event step.

    Called when:
    - A scheduled wait duration expires (via Celery ETA / countdown).
    - An external event matches a ``wait_for_event`` step (triggered
      by webhook).

    Writes to the same ``wfstate:{run_id}`` key the async engine uses
    and with the same schema the engine's ``resume_from_wait`` writes:

      - ``state["step_results"][step_id] = {output, status="completed"}``
      - ``state["status"]`` transitions ``waiting_* → running``
      - ``state["waiting_step_id"]`` is cleared
      - ``state["steps_completed"]`` is recomputed

    This lets the web process (async engine) and the Celery worker
    both drive the same state machine without diverging.
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
        current_status = state.get("status")

        if current_status not in ("waiting_delay", "waiting_event"):
            log.info(
                "workflow_not_waiting",
                status=current_status,
            )
            # Idempotent: the state already advanced (maybe by the
            # async resume path or the other worker). Don't overwrite.
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

        # Record completion — same schema the engine uses.
        step_results = state.setdefault("step_results", {})
        step_results[step_id] = {
            "output": {},
            "status": "completed",
            "completed_by": "resume_workflow_wait",
        }
        state["steps_completed"] = len(step_results)
        state["status"] = "running"
        state.pop("waiting_step_id", None)

        r.set(state_key, json.dumps(state), ex=172800)
        _clean_event_wait_keys(r, run_id, step_id)

        log.info("workflow_wait_resumed")
        return {
            "status": "resumed",
            "run_id": run_id,
            "step_id": step_id,
        }

    except Exception as exc:
        log.error("resume_workflow_wait_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


@app.task(name="timeout_workflow_event")
def timeout_workflow_event(run_id: str, step_id: str) -> dict:
    """Mark an event wait as timed_out and resume to the fallback path.

    Scheduled with a countdown equal to the step's ``timeout_hours``.
    Idempotent: if the event already arrived (step already in
    ``step_results``), this is a no-op.
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

        # Idempotency: step already completed (event arrived first).
        step_results = state.get("step_results", {})
        if step_id in step_results:
            log.info("event_already_received_before_timeout")
            return {"status": "already_completed"}

        # Not waiting for this step any more (manual cancel / concurrent
        # resume). Don't touch.
        if state.get("waiting_step_id") != step_id:
            log.info("step_no_longer_waiting")
            return {"status": "noop"}

        # Mark as timed out — same schema as engine.timeout_event_wait.
        step_results[step_id] = {
            "status": "timed_out",
            "output": {},
            "completed_by": "timeout_workflow_event",
        }
        state["step_results"] = step_results
        state["steps_completed"] = len(step_results)
        state["status"] = "running"
        state.pop("waiting_step_id", None)

        r.set(state_key, json.dumps(state), ex=172800)
        _clean_event_wait_keys(r, run_id, step_id)

        log.info("workflow_event_timed_out")
        return {
            "status": "timed_out",
            "run_id": run_id,
            "step_id": step_id,
        }

    except Exception as exc:
        log.error("timeout_workflow_event_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}
