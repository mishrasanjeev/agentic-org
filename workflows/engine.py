"""Workflow engine — dependency-aware execution with retry, timeout, HITL, and checkpointing."""

from __future__ import annotations

import re
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

import structlog

from workflows.parser import WorkflowParser
from workflows.retry import retry_with_backoff
from workflows.state_store import StaleStateError, WorkflowStateStore
from workflows.step_results import (
    ALLOWED_STEP_STATUSES,
    UnknownStepStatusError,
    failure_result,
)
from workflows.step_types import execute_step

try:
    from workflows.replanner import (
        MAX_REPLAN_ATTEMPTS,
        ReplanError,
        build_replan_event,
        replan_workflow,
    )

    _HAS_REPLANNER = True
except ImportError:
    _HAS_REPLANNER = False

logger = structlog.get_logger()


class WorkflowTimeoutError(Exception):
    """Raised when a workflow exceeds its configured timeout_hours."""


class StepFailedError(Exception):
    """Internal: carries a ``status == "failed"`` step result through ``retry_with_backoff``."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(str(result.get("error") or "step failed"))
        self.result = result


_RETRY_DIRECTIVE_RE = re.compile(r"retry\((\d+)\)")
_READ_ONLY_ACTION_PREFIXES = (
    "analy",
    "audit",
    "check",
    "classif",
    "compare",
    "describe",
    "detect",
    "estimate",
    "evaluate",
    "extract",
    "fetch",
    "find",
    "forecast",
    "get",
    "identify",
    "list",
    "lookup",
    "monitor",
    "plan",
    "query",
    "rank",
    "read",
    "recommend",
    "reconcile",
    "research",
    "retrieve",
    "review",
    "score",
    "search",
    "summar",
    "validate",
    "verify",
)
_WRITE_ACTION_HINTS = (
    "activate",
    "approve",
    "cancel",
    "charge",
    "create",
    "delete",
    "deploy",
    "disburse",
    "execute",
    "file",
    "initiate",
    "launch",
    "mutate",
    "notify",
    "pay",
    "post",
    "publish",
    "queue",
    "refund",
    "release",
    "remove",
    "schedule",
    "send",
    "set_",
    "setup",
    "spend",
    "submit",
    "sync",
    "transfer",
    "update",
    "upload",
    "upsert",
    "write",
)


class WorkflowEngine:
    """Execute workflow definitions with dependency resolution, retry, timeout, and HITL support."""

    def __init__(self, state_store: WorkflowStateStore):
        self.state_store = state_store
        self.parser = WorkflowParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_run(
        self,
        definition: dict,
        trigger_payload: dict | None = None,
        *,
        tenant_id: str | None = None,
        workflow_run_id: str | None = None,
        caller_grant: dict[str, str] | None = None,
    ) -> str:
        """Parse a workflow definition, persist initial state, and return the run_id.

        ``caller_grant`` (``auth.run_grants.CallerGrant.marker``) records that a
        caller Grantex token started the run; the token itself is never stored.
        """
        run_id = f"wfr_{uuid.uuid4().hex[:12]}"
        parsed = self.parser.parse(definition)
        state = {
            "id": run_id,
            "definition": parsed,
            "status": "running",
            "trigger_payload": trigger_payload or {},
            "steps_total": len(parsed.get("steps", [])),
            "steps_completed": 0,
            "step_results": {},
            "started_at": datetime.now(UTC).isoformat(),
            "replan_count": 0,
            "replan_history": [],
        }
        if tenant_id:
            state["tenant_id"] = tenant_id
        if workflow_run_id:
            state["workflow_run_id"] = workflow_run_id
        if caller_grant:
            from auth.run_grants import CALLER_GRANT_KEY

            state[CALLER_GRANT_KEY] = dict(caller_grant)
        await self.state_store.save(
            state,
            actor="workflow_engine",
            metadata={"event": "run_started"},
        )
        logger.info("workflow_run_started", run_id=run_id)
        return run_id

    async def execute(self, run_id: str) -> dict[str, Any]:
        """Drive the workflow to completion (or pause on HITL / timeout / error).

        Steps are executed in topological order respecting ``depends_on``.
        After each step the state is checkpointed. A checkpoint rejected as
        stale means a concurrent writer (``cancel``, a timeout task) already
        moved the run on; its status is reported instead of being overwritten.
        """
        try:
            return await self._execute_unguarded(run_id)
        except StaleStateError as exc:
            return await self._stale_state_result(run_id, exc)

    async def _execute_unguarded(self, run_id: str) -> dict[str, Any]:
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}

        if state["status"] not in ("running",):
            return {"status": state["status"], "step_results": state.get("step_results", {})}

        steps = state["definition"].get("steps", [])
        step_index = self._build_step_index(steps)
        execution_order = self._topological_sort(steps)
        timeout_hours = state["definition"].get("timeout_hours")
        ran_step = False

        for step_id in execution_order:
            # Skip steps already completed (supports resumption after checkpoint).
            if step_id in state.get("step_results", {}):
                continue

            # ---- re-read the durable status: cancel() may have won during the last step ----
            # (the state loaded above is fresh for the first step of this call)
            if ran_step:
                live_status = await self._live_status(run_id)
                if live_status not in (None, "running"):
                    logger.info(
                        "workflow_stopped_by_status_change",
                        run_id=run_id,
                        step_id=step_id,
                        status=live_status,
                    )
                    return {"status": live_status, "step_results": state["step_results"]}

            # ---- timeout check ----
            if timeout_hours is not None:
                try:
                    self._check_timeout(state, timeout_hours)
                except WorkflowTimeoutError:
                    state["status"] = "timed_out"
                    await self.state_store.save(
                        state,
                        actor="workflow_engine",
                        step_id=step_id,
                        metadata={"event": "workflow_timed_out"},
                    )
                    logger.warning("workflow_timed_out", run_id=run_id, step_id=step_id)
                    return {"status": "timed_out", "step_results": state["step_results"]}

            step = step_index[step_id]

            # ---- evaluate depends_on — all deps must have succeeded ----
            dep_failure = self._check_dependencies(step, state)
            if dep_failure:
                state["step_results"][step_id] = {
                    "output": None,
                    "status": "skipped",
                    "confidence": None,
                    "reason": dep_failure,
                }
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_skipped", "reason": dep_failure},
                )
                continue

            # ---- build context from prior step outputs for condition resolution ----
            context = self._build_context(state)

            # ---- execute the step (with retry if configured) ----
            ran_step = True
            try:
                result = await self._execute_with_retry(step, state, context)
            # enterprise-gate: broad-except-ok reason=step-boundary-marks-durable-workflow-failed
            except Exception as exc:
                # ---- dynamic re-planning ----
                replan_enabled = (
                    _HAS_REPLANNER
                    and state["definition"].get("replan_on_failure") is True
                    and state.get("replan_count", 0) < MAX_REPLAN_ATTEMPTS
                )
                if replan_enabled:
                    replan_result = await self._attempt_replan(
                        state, run_id, step_id, step, steps, execution_order, str(exc),
                    )
                    if replan_result is not None:
                        # Re-planning succeeded — restart execution with updated steps
                        return await self.execute(run_id)

                state["step_results"][step_id] = {
                    "output": None,
                    "status": "failed",
                    "confidence": None,
                    "error": str(exc),
                }
                state["status"] = "failed"
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_failed", "error": str(exc)},
                )
                logger.error("workflow_step_failed", run_id=run_id, step_id=step_id, error=str(exc))
                return {"status": "failed", "step_results": state["step_results"]}

            # ---- record result ----
            result = self._normalize_step_result(step, result)
            state["step_results"][step_id] = self._state_result_from_step_result(result)
            state["steps_completed"] = len(state["step_results"])

            if result.get("status") == "failed" and not self._step_allows_failure(step):
                state["status"] = "failed"
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_failed", "error": result.get("error")},
                )
                logger.error(
                    "workflow_step_failed",
                    run_id=run_id,
                    step_id=step_id,
                    error=result.get("error"),
                )
                return {"status": "failed", "step_results": state["step_results"]}

            # ---- handle condition branching ----
            if step.get("type") == "condition":
                branch_target = self._resolve_condition_branch(step, result, context)
                if branch_target and branch_target in step_index:
                    # Inject the branch target into step_results context so downstream
                    # dependency checks pass if needed; the main loop will reach the
                    # target in topological order.  We also mark skipped branches.
                    state["step_results"][step_id]["branch_target"] = branch_target
                self._skip_branch_not_taken(step, branch_target, state)

            # ---- handle HITL pause ----
            if step.get("type") == "human_in_loop" or result.get("status") == "waiting_hitl":
                state["status"] = "waiting_hitl"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_hitl"},
                )
                logger.info("workflow_waiting_hitl", run_id=run_id, step_id=step_id)
                return {"status": "waiting_hitl", "step_results": state["step_results"]}

            # ---- handle wait/delay pause ----
            if result.get("status") == "waiting_delay":
                state["status"] = "waiting_delay"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_delay", "resume_at": result.get("resume_at")},
                )
                logger.info("workflow_waiting_delay", run_id=run_id, step_id=step_id, resume_at=result.get("resume_at"))
                return {"status": "waiting_delay", "step_results": state["step_results"]}

            # ---- handle event wait pause ----
            if result.get("status") == "waiting_event":
                state["status"] = "waiting_event"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_event", "event_type": result.get("event_type")},
                )
                evt = result.get("event_type")
                logger.info("workflow_waiting_event", run_id=run_id, step_id=step_id, event_type=evt)
                return {"status": "waiting_event", "step_results": state["step_results"]}

            # ---- checkpoint ----
            await self.state_store.save(
                state,
                actor="workflow_engine",
                step_id=step_id,
                metadata={"event": "step_checkpointed"},
            )
            logger.debug("step_checkpointed", run_id=run_id, step_id=step_id)

        # All steps done.
        state["status"] = "completed"
        state["completed_at"] = datetime.now(UTC).isoformat()
        await self.state_store.save(
            state,
            actor="workflow_engine",
            metadata={"event": "workflow_completed"},
        )
        logger.info("workflow_completed", run_id=run_id)
        return {"status": "completed", "step_results": state["step_results"]}

    async def execute_next(self, run_id: str) -> dict[str, Any]:
        """Legacy single-step execution preserved for backward compatibility.

        Executes just the next eligible step in topological order, then returns.
        """
        try:
            return await self._execute_next_unguarded(run_id)
        except StaleStateError as exc:
            return await self._stale_state_result(run_id, exc)

    async def _execute_next_unguarded(self, run_id: str) -> dict[str, Any]:
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}

        if state["status"] not in ("running",):
            return {"status": state["status"], "step_results": state.get("step_results", {})}

        steps = state["definition"].get("steps", [])
        step_index = self._build_step_index(steps)
        execution_order = self._topological_sort(steps)
        timeout_hours = state["definition"].get("timeout_hours")

        for step_id in execution_order:
            if step_id in state.get("step_results", {}):
                continue

            if timeout_hours is not None:
                try:
                    self._check_timeout(state, timeout_hours)
                except WorkflowTimeoutError:
                    state["status"] = "timed_out"
                    await self.state_store.save(
                        state,
                        actor="workflow_engine",
                        step_id=step_id,
                        metadata={"event": "workflow_timed_out"},
                    )
                    return {"status": "timed_out"}

            step = step_index[step_id]
            dep_failure = self._check_dependencies(step, state)
            if dep_failure:
                state["step_results"][step_id] = {
                    "output": None,
                    "status": "skipped",
                    "confidence": None,
                    "reason": dep_failure,
                }
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_skipped", "reason": dep_failure},
                )
                continue

            context = self._build_context(state)

            try:
                result = await self._execute_with_retry(step, state, context)
            # enterprise-gate: broad-except-ok reason=legacy-step-boundary-marks-durable-workflow-failed
            except Exception as exc:
                state["step_results"][step_id] = {
                    "output": None,
                    "status": "failed",
                    "confidence": None,
                    "error": str(exc),
                }
                state["status"] = "failed"
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_failed", "error": str(exc)},
                )
                return {"status": "failed", "step_id": step_id, "error": str(exc)}

            result = self._normalize_step_result(step, result)
            state["step_results"][step_id] = self._state_result_from_step_result(result)
            state["steps_completed"] = len(state["step_results"])

            if result.get("status") == "failed" and not self._step_allows_failure(step):
                state["status"] = "failed"
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "step_failed", "error": result.get("error")},
                )
                return {"status": "failed", "step_id": step_id, "error": result.get("error")}

            if step.get("type") == "condition":
                branch_target = self._resolve_condition_branch(step, result, context)
                if branch_target:
                    state["step_results"][step_id]["branch_target"] = branch_target
                self._skip_branch_not_taken(step, branch_target, state)

            if step.get("type") == "human_in_loop" or result.get("status") == "waiting_hitl":
                state["status"] = "waiting_hitl"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_hitl"},
                )
                return {"status": "waiting_hitl", "step_id": step_id}

            if result.get("status") == "waiting_delay":
                state["status"] = "waiting_delay"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_delay", "resume_at": result.get("resume_at")},
                )
                return {"status": "waiting_delay", "step_id": step_id}

            if result.get("status") == "waiting_event":
                state["status"] = "waiting_event"
                state["waiting_step_id"] = step_id
                await self.state_store.save(
                    state,
                    actor="workflow_engine",
                    step_id=step_id,
                    metadata={"event": "waiting_event", "event_type": result.get("event_type")},
                )
                return {"status": "waiting_event", "step_id": step_id}

            await self.state_store.save(
                state,
                actor="workflow_engine",
                step_id=step_id,
                metadata={"event": "step_checkpointed"},
            )
            return result

        # All steps executed.
        state["status"] = "completed"
        state["completed_at"] = datetime.now(UTC).isoformat()
        await self.state_store.save(
            state,
            actor="workflow_engine",
            metadata={"event": "workflow_completed"},
        )
        return {"status": "completed", "step_results": state["step_results"]}

    async def resume_from_hitl(self, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        """Resume a workflow paused at a human-in-the-loop step.

        ``decision`` is stored as the HITL step's output, then execution continues.
        """
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}
        if state["status"] != "waiting_hitl":
            return {"error": f"Run is not waiting for HITL, current status: {state['status']}"}

        waiting_step_id = state.get("waiting_step_id")
        if not waiting_step_id:
            return {"error": "No waiting step recorded"}

        if self._is_rejection(decision):
            # A rejection is terminal: record it on the HITL step, fail the
            # run, and never execute dependent steps.
            state["step_results"][waiting_step_id] = {
                "output": decision,
                "status": "rejected",
                "confidence": decision.get("confidence"),
                "error": {"code": "hitl_rejected", "message": "Rejected by human reviewer"},
            }
            state["steps_completed"] = len(state["step_results"])
            state["status"] = "failed"
            state["error"] = {
                "code": "hitl_rejected",
                "message": f"Step '{waiting_step_id}' was rejected by a human reviewer",
            }
            state["completed_at"] = datetime.now(UTC).isoformat()
            state.pop("waiting_step_id", None)
            await self.state_store.save(
                state,
                actor="workflow_engine.hitl_resume",
                step_id=waiting_step_id,
                metadata={"event": "hitl_rejected"},
            )
            logger.info("workflow_hitl_rejected", run_id=run_id, step_id=waiting_step_id)
            return {"status": "failed", "step_results": state["step_results"]}

        # Record the HITL decision on the step. A dedicated human_in_loop step
        # has no output of its own, so the decision *is* its output. Any other
        # step (e.g. an agent that escalated for approval) keeps the work it
        # produced before the pause; the decision is attached alongside it.
        prior = state["step_results"].get(waiting_step_id) or {}
        prior_output = prior.get("output")
        if self._step_type_for(state, waiting_step_id) == "human_in_loop" or prior_output in (None, {}, ""):
            state["step_results"][waiting_step_id] = {
                "output": decision,
                "status": "completed",
                "confidence": decision.get("confidence"),
            }
        else:
            merged_output = dict(prior_output) if isinstance(prior_output, dict) else {"result": prior_output}
            merged_output["hitl_decision"] = decision
            prior_confidence = prior.get("confidence")
            state["step_results"][waiting_step_id] = {
                "output": merged_output,
                "status": "completed",
                "confidence": prior_confidence if prior_confidence is not None else decision.get("confidence"),
            }
        state["steps_completed"] = len(state["step_results"])
        state["status"] = "running"
        state.pop("waiting_step_id", None)
        await self.state_store.save(
            state,
            actor="workflow_engine.hitl_resume",
            step_id=waiting_step_id,
            metadata={"event": "hitl_resumed"},
        )

        logger.info("workflow_hitl_resumed", run_id=run_id, step_id=waiting_step_id)

        # Continue executing remaining steps.
        return await self.execute(run_id)

    async def resume_from_wait(self, run_id: str, step_result: dict[str, Any] | None = None) -> dict[str, Any]:
        """Resume a workflow paused at a wait/delay step."""
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}
        if state["status"] not in ("waiting_delay", "waiting_event"):
            return {"error": f"Run is not waiting, current status: {state['status']}"}

        waiting_step_id = state.get("waiting_step_id")
        if not waiting_step_id:
            return {"error": "No waiting step recorded"}

        state["step_results"][waiting_step_id] = {
            "output": step_result or {},
            "status": "completed",
        }
        state["steps_completed"] = len(state["step_results"])
        state["status"] = "running"
        state.pop("waiting_step_id", None)
        await self.state_store.save(
            state,
            actor="workflow_engine.wait_resume",
            step_id=waiting_step_id,
            metadata={"event": "wait_resumed"},
        )

        logger.info("workflow_wait_resumed", run_id=run_id, step_id=waiting_step_id)
        return await self.execute(run_id)

    async def resume_from_event(self, run_id: str, event_data: dict[str, Any]) -> dict[str, Any]:
        """Resume a workflow paused at a wait_for_event step."""
        return await self.resume_from_wait(run_id, step_result={"event": event_data, "status": "event_received"})

    async def timeout_event_wait(self, run_id: str, step_id: str) -> dict[str, Any]:
        """Handle timeout for a wait_for_event step."""
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}
        if state["status"] != "waiting_event" or state.get("waiting_step_id") != step_id:
            return {"error": "Step is no longer waiting"}

        state["step_results"][step_id] = {"status": "timed_out", "output": {}}
        state["steps_completed"] = len(state["step_results"])
        state["status"] = "running"
        state.pop("waiting_step_id", None)
        await self.state_store.save(
            state,
            actor="workflow_engine.event_timeout",
            step_id=step_id,
            metadata={"event": "event_wait_timed_out"},
        )

        logger.info("workflow_event_timed_out", run_id=run_id, step_id=step_id)
        return await self.execute(run_id)

    async def cancel(self, run_id: str) -> None:
        """Cancel a running workflow."""
        state = await self.state_store.load(run_id)
        if state:
            state["status"] = "cancelled"
            state["cancelled_at"] = datetime.now(UTC).isoformat()
            await self.state_store.save(
                state,
                actor="workflow_engine.cancel",
                metadata={"event": "workflow_cancelled"},
            )
            logger.info("workflow_cancelled", run_id=run_id)

    # ------------------------------------------------------------------
    # Dynamic re-planning
    # ------------------------------------------------------------------

    async def _attempt_replan(
        self,
        state: dict,
        run_id: str,
        failed_step_id: str,
        failed_step: dict,
        all_steps: list[dict],
        execution_order: list[str],
        error_msg: str,
    ) -> list[dict] | None:
        """Attempt to re-plan the workflow after a step failure.

        Returns the new steps list on success, or None if re-planning failed.
        """
        if not _HAS_REPLANNER:
            return None

        replan_count = state.get("replan_count", 0) + 1
        logger.info(
            "workflow_replan_attempt",
            run_id=run_id,
            step_id=failed_step_id,
            attempt=replan_count,
        )

        # Build completed steps context (steps that succeeded before this failure)
        completed_steps = []
        for sid, sresult in state.get("step_results", {}).items():
            completed_steps.append({"id": sid, **sresult})

        # Build failed step context
        failed_context = {
            "id": failed_step_id,
            "error": error_msg,
            **{k: v for k, v in failed_step.items() if k != "id"},
        }

        # Identify remaining steps (not yet executed and not the failed step)
        executed_ids = set(state.get("step_results", {}).keys()) | {failed_step_id}
        step_index = self._build_step_index(all_steps)
        remaining_steps = [
            step_index[sid] for sid in execution_order
            if sid not in executed_ids and sid in step_index
        ]

        try:
            new_steps = await replan_workflow(
                original_definition=state["definition"],
                completed_steps=completed_steps,
                failed_step=failed_context,
                remaining_steps=remaining_steps,
            )
        # enterprise-gate: broad-except-ok reason=replan-boundary-falls-back-to-original-step-failure
        except (ReplanError, Exception) as exc:
            logger.warning(
                "workflow_replan_failed",
                run_id=run_id,
                step_id=failed_step_id,
                error=str(exc),
            )
            return None

        # Record the replan event
        event = build_replan_event(replan_count, failed_step_id, error_msg, new_steps)
        state.setdefault("replan_history", []).append(event)
        state["replan_count"] = replan_count

        # Mark the failed step as replanned (not failed — it was handled)
        state["step_results"][failed_step_id] = {
            "output": None,
            "status": "replanned",
            "confidence": None,
            "error": error_msg,
            "replanned": True,
        }
        state["steps_completed"] = len(state["step_results"])

        # Replace remaining steps in the definition with the replanned ones
        # Keep completed steps + the replanned marker, append new steps
        completed_step_defs = [
            s for s in all_steps if s["id"] in state.get("step_results", {})
        ]
        # Mark new steps as replanned for UI display
        for ns in new_steps:
            ns["replanned"] = True

        state["definition"]["steps"] = completed_step_defs + new_steps
        state["steps_total"] = len(state["definition"]["steps"])

        await self.state_store.save(
            state,
            actor="workflow_engine.replan",
            step_id=failed_step_id,
            metadata={"event": "workflow_replanned", "error": error_msg},
        )

        logger.info(
            "workflow_replanned_successfully",
            run_id=run_id,
            failed_step=failed_step_id,
            new_step_count=len(new_steps),
            replan_count=replan_count,
        )

        return new_steps

    # ------------------------------------------------------------------
    # Dependency graph helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_step_result(step: dict, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return failure_result(
                step_id=step["id"],
                step_type=step.get("type", "agent"),
                failure=UnknownStepStatusError(step_id=step["id"], status=None),
                output=result,
            )

        status = result.get("status")
        if status not in ALLOWED_STEP_STATUSES:
            return failure_result(
                step_id=step["id"],
                step_type=step.get("type", "agent"),
                failure=UnknownStepStatusError(
                    step_id=step["id"],
                    status=str(status) if status is not None else None,
                ),
                output=result,
            )
        return result

    @staticmethod
    def _state_result_from_step_result(result: dict[str, Any]) -> dict[str, Any]:
        state_result = {
            "output": result.get("output", result),
            "status": result["status"],
            "confidence": result.get("confidence"),
        }
        if result.get("error"):
            state_result["error"] = result["error"]
        if result.get("stubbed"):  # enterprise-gate: stub-ok reason=transparent-relaxed-env-marker
            state_result["stubbed"] = True  # enterprise-gate: stub-ok reason=transparent-relaxed-env-marker
            state_result["reason"] = result.get("reason")
            state_result["code"] = result.get("code")
            if result.get("connector"):
                state_result["connector"] = result.get("connector")
            if result.get("agent"):
                state_result["agent"] = result.get("agent")
            if result.get("action"):
                state_result["action"] = result.get("action")
        return state_result

    async def _live_status(self, run_id: str) -> str | None:
        latest = await self.state_store.load(run_id)
        return latest.get("status") if latest else None

    async def _stale_state_result(self, run_id: str, exc: StaleStateError) -> dict[str, Any]:
        """A concurrent writer won the race; report its status, never overwrite it."""
        latest = await self.state_store.load(run_id) or {}
        status = latest.get("status", "unknown")
        logger.warning(
            "workflow_state_stale_write_rejected",
            run_id=run_id,
            status=status,
            expected_version=exc.expected,
            actual_version=exc.actual,
        )
        return {"status": status, "step_results": latest.get("step_results", {})}

    @staticmethod
    def _step_type_for(state: dict[str, Any], step_id: str) -> str:
        for step in (state.get("definition") or {}).get("steps", []) or []:
            if isinstance(step, dict) and step.get("id") == step_id:
                return str(step.get("type") or "agent").strip().lower()
        return "agent"

    @staticmethod
    def _is_rejection(decision: dict[str, Any]) -> bool:
        raw = decision.get("decision") if isinstance(decision, dict) else None
        return str(raw or "").strip().lower() in {"reject", "rejected", "deny", "denied"}

    @staticmethod
    def _skip_branch_not_taken(step: dict, branch_target: str | None, state: dict) -> None:
        """Mark the condition path that was not selected as skipped.

        Downstream steps that depend on the skipped path are then skipped by
        ``_check_dependencies`` (a skipped dependency is not ``completed``).
        """
        for path_key in ("true_path", "false_path"):
            other = step.get(path_key)
            if not other or other == branch_target or other in state["step_results"]:
                continue
            state["step_results"][other] = {
                "output": None,
                "status": "skipped",
                "confidence": None,
                "reason": "branch_not_taken",
            }
        state["steps_completed"] = len(state["step_results"])

    @staticmethod
    def _step_allows_failure(step: dict) -> bool:
        """Return True when a failed step must not fail the run.

        ``on_failure`` may combine a retry directive with a fallback, e.g.
        ``"retry(3)"`` (fail the run once retries are exhausted) or
        ``"retry(3) then continue"`` / ``"retry(3), ignore"`` (continue after
        the retries are exhausted). A bare ``retry(N)`` never allows failure.
        """
        on_failure = str(step.get("on_failure", "")).strip().lower()
        fallback = _RETRY_DIRECTIVE_RE.sub("", on_failure)
        fallback_tokens = {token for token in re.split(r"[^a-z_]+", fallback) if token}
        return bool(
            step.get("optional") is True
            or step.get("allow_failure") is True
            or fallback_tokens & {"continue", "ignore", "optional"}
        )

    @staticmethod
    def _build_step_index(steps: list[dict]) -> dict[str, dict]:
        """Return a mapping from step_id -> step definition."""
        return {step["id"]: step for step in steps}

    @staticmethod
    def _topological_sort(steps: list[dict]) -> list[str]:
        """Kahn's algorithm — returns step IDs in a valid execution order.

        Steps with no dependencies come first; ties are broken by definition order
        so behaviour is deterministic.
        """
        step_ids = [s["id"] for s in steps]
        step_set = set(step_ids)
        graph: dict[str, list[str]] = {s["id"]: [] for s in steps}
        in_degree: dict[str, int] = {s["id"]: 0 for s in steps}

        for step in steps:
            for dep in step.get("depends_on", []):
                if dep in step_set:
                    graph[dep].append(step["id"])
                    in_degree[step["id"]] += 1

        # Seed queue with zero-in-degree nodes in definition order.
        queue: deque[str] = deque()
        for sid in step_ids:
            if in_degree[sid] == 0:
                queue.append(sid)

        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbour in graph[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(order) != len(step_ids):
            raise ValueError("Circular dependency detected in workflow steps")

        return order

    # ------------------------------------------------------------------
    # Timeout
    # ------------------------------------------------------------------

    @staticmethod
    def _check_timeout(state: dict, timeout_hours: float) -> None:
        """Raise ``WorkflowTimeoutError`` if the run has exceeded *timeout_hours*."""
        started_at = datetime.fromisoformat(state["started_at"])
        elapsed = (datetime.now(UTC) - started_at).total_seconds()
        if elapsed > timeout_hours * 3600:
            raise WorkflowTimeoutError(
                f"Workflow exceeded timeout of {timeout_hours}h (elapsed {elapsed / 3600:.2f}h)"
            )

    # ------------------------------------------------------------------
    # Dependency checking
    # ------------------------------------------------------------------

    @staticmethod
    def _check_dependencies(step: dict, state: dict) -> str | None:
        """Return an error message if any dependency has not succeeded, else None."""
        step_results = state.get("step_results", {})
        for dep_id in step.get("depends_on", []):
            dep_result = step_results.get(dep_id)
            if dep_result is None:
                return f"Dependency '{dep_id}' has not been executed"
            if dep_result.get("status") == "skipped" and dep_result.get("reason") == "branch_not_taken":
                return "branch_not_taken"
            if dep_result.get("status") not in ("completed",):
                return f"Dependency '{dep_id}' did not complete successfully (status={dep_result.get('status')})"
        return None

    # ------------------------------------------------------------------
    # Context builder — makes prior step outputs available to conditions
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(state: dict) -> dict[str, Any]:
        """Build a flat context dict from step_results so conditions can reference
        ``{step_id}.output.{field}`` via the condition evaluator's dot-path resolver.
        """
        context: dict[str, Any] = {}
        # Copy trigger payload into context root.
        context.update(state.get("trigger_payload", {}))
        # Add each completed step's results keyed by step_id.
        for step_id, result in state.get("step_results", {}).items():
            context[step_id] = result
        return context

    # ------------------------------------------------------------------
    # Condition branching
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_condition_branch(step: dict, result: dict, context: dict) -> str | None:
        """Given a condition step's execution result, return the branch target step_id."""
        condition_result = result.get("result", False)
        if condition_result:
            return step.get("true_path")
        return step.get("false_path")

    # ------------------------------------------------------------------
    # Step execution with retry
    # ------------------------------------------------------------------

    async def _execute_with_retry(self, step: dict, state: dict, context: dict) -> dict[str, Any]:
        """Execute a step, optionally wrapping in retry_with_backoff.

        The step may declare ``on_failure: "retry(N)"`` where *N* is the max
        number of retry attempts. Step handlers return failures as
        ``{"status": "failed"}`` dicts rather than raising, so a failed
        result is re-raised as :class:`StepFailedError` inside the retry loop
        and unwrapped back into the final result once retries are spent.

        Retry rule (a retry must never duplicate a side effect):

        * ``connector_tool`` steps (and ``agent`` steps that resolve to a
          connector tool) retry when the tool is read-only by name
          (``get_*``/``list_*``/``fetch_*``/...) or the step carries an
          ``idempotency_key``;
        * ``agent`` steps retry only when ``action`` is read-only or the step
          carries an ``idempotency_key`` (the key is forwarded to the connector
          gateway, which dedupes the replay);
        * ``http`` steps retry only with method ``GET``;
        * ``notify`` steps and writes without an idempotency key are never
          retried — ``retry(N)`` on them is ignored and the first failure
          stands.
        """
        on_failure = step.get("on_failure", "")
        max_retries = self._parse_retry_count(on_failure)

        # Inject the built context into state so step handlers can use it.
        state_with_context = {**state, "context": context, "_state_store": self.state_store}

        if max_retries > 0 and self._step_is_retryable(step):

            async def _attempt() -> dict[str, Any]:
                result = await execute_step(step, state_with_context)
                if isinstance(result, dict) and result.get("status") == "failed":
                    raise StepFailedError(result)
                return result

            try:
                return await retry_with_backoff(func=_attempt, max_retries=max_retries)
            except StepFailedError as exc:
                final = dict(exc.result)
                final["retry_attempts"] = max_retries
                return final

        if max_retries > 0:
            logger.info(
                "workflow_retry_directive_ignored_non_idempotent",
                step_id=step.get("id"),
                step_type=step.get("type", "agent"),
            )

        return await execute_step(step, state_with_context)

    @staticmethod
    def _is_read_only_name(name: Any) -> bool:
        normalized = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized:
            return False
        if any(hint in normalized for hint in _WRITE_ACTION_HINTS):
            return False
        return normalized.startswith(_READ_ONLY_ACTION_PREFIXES)

    @classmethod
    def _step_is_retryable(cls, step: dict) -> bool:
        """Apply the retry rule documented on ``_execute_with_retry``."""
        from workflows.step_types import _connector_tool_ref_from_step

        step_type = str(step.get("type", "agent")).strip().lower()
        if step_type in {"notify", "sub_workflow", "human_in_loop", "wait", "wait_for_event"}:
            return False
        if step_type == "http":
            return str(step.get("method", "GET")).strip().upper() == "GET"

        connector, tool = _connector_tool_ref_from_step(step, allow_action_tool=step_type == "connector_tool")
        if step_type == "connector_tool" or (step_type == "agent" and connector and tool):
            if step.get("idempotency_key"):
                return True
            return cls._is_read_only_name(tool)
        if step_type == "agent":
            if step.get("idempotency_key"):
                return True
            return cls._is_read_only_name(step.get("action", "process"))
        return False

    @staticmethod
    def _parse_retry_count(on_failure: str) -> int:
        """Extract the retry count from an ``on_failure`` directive like ``retry(3)``."""
        if not on_failure:
            return 0
        match = _RETRY_DIRECTIVE_RE.match(str(on_failure).strip())
        if match:
            return int(match.group(1))
        return 0
