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
from workflows.state_store import WorkflowStateStore
from workflows.step_types import execute_step

logger = structlog.get_logger()


class WorkflowTimeoutError(Exception):
    """Raised when a workflow exceeds its configured timeout_hours."""


class WorkflowEngine:
    """Execute workflow definitions with dependency resolution, retry, timeout, and HITL support."""

    def __init__(self, state_store: WorkflowStateStore):
        self.state_store = state_store
        self.parser = WorkflowParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_run(self, definition: dict, trigger_payload: dict | None = None) -> str:
        """Parse a workflow definition, persist initial state, and return the run_id."""
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
        }
        await self.state_store.save(state)
        logger.info("workflow_run_started", run_id=run_id)
        return run_id

    async def execute(self, run_id: str) -> dict[str, Any]:
        """Drive the workflow to completion (or pause on HITL / timeout / error).

        Steps are executed in topological order respecting ``depends_on``.
        After each step the state is checkpointed.
        """
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
            # Skip steps already completed (supports resumption after checkpoint).
            if step_id in state.get("step_results", {}):
                continue

            # ---- timeout check ----
            if timeout_hours is not None:
                try:
                    self._check_timeout(state, timeout_hours)
                except WorkflowTimeoutError:
                    state["status"] = "timed_out"
                    await self.state_store.save(state)
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
                await self.state_store.save(state)
                continue

            # ---- build context from prior step outputs for condition resolution ----
            context = self._build_context(state)

            # ---- execute the step (with retry if configured) ----
            try:
                result = await self._execute_with_retry(step, state, context)
            except Exception as exc:
                state["step_results"][step_id] = {
                    "output": None,
                    "status": "failed",
                    "confidence": None,
                    "error": str(exc),
                }
                state["status"] = "failed"
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(state)
                logger.error("workflow_step_failed", run_id=run_id, step_id=step_id, error=str(exc))
                return {"status": "failed", "step_results": state["step_results"]}

            # ---- record result ----
            state["step_results"][step_id] = {
                "output": result.get("output", result),
                "status": result.get("status", "completed"),
                "confidence": result.get("confidence"),
            }
            state["steps_completed"] = len(state["step_results"])

            # ---- handle condition branching ----
            if step.get("type") == "condition":
                branch_target = self._resolve_condition_branch(step, result, context)
                if branch_target and branch_target in step_index:
                    # Inject the branch target into step_results context so downstream
                    # dependency checks pass if needed; the main loop will reach the
                    # target in topological order.  We also mark skipped branches.
                    state["step_results"][step_id]["branch_target"] = branch_target

            # ---- handle HITL pause ----
            if step.get("type") == "human_in_loop" or result.get("status") == "waiting_hitl":
                state["status"] = "waiting_hitl"
                state["waiting_step_id"] = step_id
                await self.state_store.save(state)
                logger.info("workflow_waiting_hitl", run_id=run_id, step_id=step_id)
                return {"status": "waiting_hitl", "step_results": state["step_results"]}

            # ---- handle wait/delay pause ----
            if result.get("status") == "waiting_delay":
                state["status"] = "waiting_delay"
                state["waiting_step_id"] = step_id
                await self.state_store.save(state)
                logger.info("workflow_waiting_delay", run_id=run_id, step_id=step_id, resume_at=result.get("resume_at"))
                return {"status": "waiting_delay", "step_results": state["step_results"]}

            # ---- handle event wait pause ----
            if result.get("status") == "waiting_event":
                state["status"] = "waiting_event"
                state["waiting_step_id"] = step_id
                await self.state_store.save(state)
                evt = result.get("event_type")
                logger.info("workflow_waiting_event", run_id=run_id, step_id=step_id, event_type=evt)
                return {"status": "waiting_event", "step_results": state["step_results"]}

            # ---- checkpoint ----
            await self.state_store.save(state)
            logger.debug("step_checkpointed", run_id=run_id, step_id=step_id)

        # All steps done.
        state["status"] = "completed"
        state["completed_at"] = datetime.now(UTC).isoformat()
        await self.state_store.save(state)
        logger.info("workflow_completed", run_id=run_id)
        return {"status": "completed", "step_results": state["step_results"]}

    async def execute_next(self, run_id: str) -> dict[str, Any]:
        """Legacy single-step execution preserved for backward compatibility.

        Executes just the next eligible step in topological order, then returns.
        """
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
                    await self.state_store.save(state)
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
                await self.state_store.save(state)
                continue

            context = self._build_context(state)

            try:
                result = await self._execute_with_retry(step, state, context)
            except Exception as exc:
                state["step_results"][step_id] = {
                    "output": None,
                    "status": "failed",
                    "confidence": None,
                    "error": str(exc),
                }
                state["status"] = "failed"
                state["steps_completed"] = len(state["step_results"])
                await self.state_store.save(state)
                return {"status": "failed", "step_id": step_id, "error": str(exc)}

            state["step_results"][step_id] = {
                "output": result.get("output", result),
                "status": result.get("status", "completed"),
                "confidence": result.get("confidence"),
            }
            state["steps_completed"] = len(state["step_results"])

            if step.get("type") == "condition":
                branch_target = self._resolve_condition_branch(step, result, context)
                if branch_target:
                    state["step_results"][step_id]["branch_target"] = branch_target

            if step.get("type") == "human_in_loop" or result.get("status") == "waiting_hitl":
                state["status"] = "waiting_hitl"
                state["waiting_step_id"] = step_id
                await self.state_store.save(state)
                return {"status": "waiting_hitl", "step_id": step_id}

            await self.state_store.save(state)
            return result

        # All steps executed.
        state["status"] = "completed"
        state["completed_at"] = datetime.now(UTC).isoformat()
        await self.state_store.save(state)
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

        # Record the HITL decision as the step's completed output.
        state["step_results"][waiting_step_id] = {
            "output": decision,
            "status": "completed",
            "confidence": decision.get("confidence"),
        }
        state["steps_completed"] = len(state["step_results"])
        state["status"] = "running"
        state.pop("waiting_step_id", None)
        await self.state_store.save(state)

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
        await self.state_store.save(state)

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
        await self.state_store.save(state)

        logger.info("workflow_event_timed_out", run_id=run_id, step_id=step_id)
        return await self.execute(run_id)

    async def cancel(self, run_id: str) -> None:
        """Cancel a running workflow."""
        state = await self.state_store.load(run_id)
        if state:
            state["status"] = "cancelled"
            state["cancelled_at"] = datetime.now(UTC).isoformat()
            await self.state_store.save(state)
            logger.info("workflow_cancelled", run_id=run_id)

    # ------------------------------------------------------------------
    # Dependency graph helpers
    # ------------------------------------------------------------------

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
        number of retry attempts.
        """
        on_failure = step.get("on_failure", "")
        max_retries = self._parse_retry_count(on_failure)

        # Inject the built context into state so step handlers can use it.
        state_with_context = {**state, "context": context}

        if max_retries > 0:
            return await retry_with_backoff(
                func=lambda: execute_step(step, state_with_context),
                max_retries=max_retries,
            )

        return await execute_step(step, state_with_context)

    @staticmethod
    def _parse_retry_count(on_failure: str) -> int:
        """Extract the retry count from an ``on_failure`` directive like ``retry(3)``."""
        if not on_failure:
            return 0
        match = re.match(r"retry\((\d+)\)", on_failure.strip())
        if match:
            return int(match.group(1))
        return 0
