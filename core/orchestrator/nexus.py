"""NEXUS — central orchestrator for AgenticOrg."""

from __future__ import annotations

from typing import Any

import structlog

from core.orchestrator.checkpoint import CheckpointManager
from core.orchestrator.conflict_resolver import ConflictResolver
from core.orchestrator.task_router import TaskRouter
from core.schemas.messages import HITLRequest, TaskResult

logger = structlog.get_logger()


class NexusOrchestrator:
    """Decompose intents, route tasks, manage workflow state."""

    def __init__(self, task_router: TaskRouter, checkpoint_mgr: CheckpointManager):
        self.router = task_router
        self.checkpoint = checkpoint_mgr
        self.conflict_resolver = ConflictResolver()

    async def receive_intent(
        self, workflow_run_id: str, intent: dict[str, Any], context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Decompose intent and produce sub-task assignments."""
        sub_tasks = self.decompose(intent)
        assignments = []
        for i, task in enumerate(sub_tasks):
            assignment = await self.router.route(
                workflow_run_id=workflow_run_id,
                step_id=task["id"],
                step_index=i,
                total_steps=len(sub_tasks),
                task=task,
                context=context,
            )
            assignments.append(assignment)
        await self.checkpoint.save(
            workflow_run_id, {"assignments": list(assignments), "step": 0}
        )
        return assignments

    def decompose(self, intent: dict[str, Any]) -> list[dict[str, Any]]:
        """Decompose intent into minimum sub-tasks."""
        # Use workflow definition steps if available
        steps = intent.get("steps", [])
        if steps:
            return steps
        # Fallback: single-step
        return [{"id": "main", "action": intent.get("action", "process"), "inputs": intent}]

    async def handle_result(self, workflow_run_id: str, result: TaskResult) -> dict[str, Any]:
        """Process a TaskResult from an agent."""
        trace_msg = f"Received result for step {result.step_id}: status={result.status}"
        logger.info(trace_msg, workflow_run_id=workflow_run_id)

        # Validate output
        if result.status == "completed":
            # Check HITL at orchestrator level (PRD: agents cannot bypass HITL gate)
            hitl = self.evaluate_hitl(result)
            if hitl:
                return {"action": "hitl", "hitl_request": hitl}
            await self.checkpoint.save(workflow_run_id, {"last_completed": result.step_id})
            return {"action": "proceed", "output": result.output}

        if result.status == "hitl_triggered":
            return {"action": "hitl", "hitl_request": result.hitl_request}

        if result.status == "failed":
            return {"action": "escalate", "error": result.error, "reason": "Agent failed"}

        return {"action": "unknown", "status": result.status}

    def evaluate_hitl(self, result: TaskResult) -> HITLRequest | None:
        """Evaluate HITL at orchestrator level — agents cannot bypass this."""
        if result.confidence < 0.88:
            return result.hitl_request
        return None

    async def resolve_conflict(self, results: list[TaskResult]) -> dict[str, Any]:
        return self.conflict_resolver.resolve(results)
