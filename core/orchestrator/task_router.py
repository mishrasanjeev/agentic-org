"""Route tasks to the most capable agent."""
from __future__ import annotations

import uuid


class TaskRouter:
    async def route(self, workflow_run_id, step_id, step_index, total_steps, task, context):
        agent_type = task.get("agent", task.get("agent_type", ""))
        return {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "step_index": step_index,
            "total_steps": total_steps,
            "target_agent_type": agent_type,
            "task": task,
            "context": context,
        }
