"""Workflow engine — load, execute, manage workflow runs."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any
import structlog
from workflows.parser import WorkflowParser
from workflows.step_types import execute_step
from workflows.state_store import WorkflowStateStore

logger = structlog.get_logger()

class WorkflowEngine:
    def __init__(self, state_store: WorkflowStateStore):
        self.state_store = state_store
        self.parser = WorkflowParser()

    async def start_run(self, definition: dict, trigger_payload: dict | None = None) -> str:
        run_id = f"wfr_{uuid.uuid4().hex[:12]}"
        parsed = self.parser.parse(definition)
        await self.state_store.save({
            "id": run_id,
            "definition": parsed,
            "status": "running",
            "trigger_payload": trigger_payload or {},
            "steps_total": len(parsed.get("steps", [])),
            "steps_completed": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        return run_id

    async def execute_next(self, run_id: str) -> dict[str, Any]:
        state = await self.state_store.load(run_id)
        if not state:
            return {"error": "Run not found"}
        steps = state["definition"].get("steps", [])
        idx = state.get("steps_completed", 0)
        if idx >= len(steps):
            state["status"] = "completed"
            await self.state_store.save(state)
            return {"status": "completed"}
        step = steps[idx]
        result = await execute_step(step, state)
        state["steps_completed"] = idx + 1
        await self.state_store.save(state)
        return result

    async def cancel(self, run_id: str) -> None:
        state = await self.state_store.load(run_id)
        if state:
            state["status"] = "cancelled"
            await self.state_store.save(state)
