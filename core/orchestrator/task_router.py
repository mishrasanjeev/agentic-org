"""Route tasks to the most capable agent instance."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID


class TaskRouter:
    """Route workflow tasks to specific agent instances.

    When multiple agents share the same agent_type, resolves the best
    instance using routing_filter, specialization, and availability.
    """

    async def route(
        self, workflow_run_id, step_id, step_index, total_steps, task, context
    ):
        agent_type = task.get("agent", task.get("agent_type", ""))
        target_agent_id = task.get("agent_id")
        routing_context = task.get("routing_context", {})

        return {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "workflow_run_id": workflow_run_id,
            "step_id": step_id,
            "step_index": step_index,
            "total_steps": total_steps,
            "target_agent_type": agent_type,
            "target_agent_id": target_agent_id,
            "routing_context": routing_context,
            "task": task,
            "context": context,
        }

    @staticmethod
    async def resolve_agent_instance(
        tenant_id: UUID,
        agent_type: str,
        routing_context: dict[str, Any],
        session,
    ) -> UUID | None:
        """Find the best agent instance when multiple share a type.

        Resolution order:
        1. Exact routing_filter match (e.g., region=APAC)
        2. Specialization keyword match
        3. First active agent of that type (fallback)
        """
        from sqlalchemy import select

        from core.models.agent import Agent

        query = (
            select(Agent)
            .where(
                Agent.tenant_id == tenant_id,
                Agent.agent_type == agent_type,
                Agent.status == "active",
            )
            .order_by(Agent.created_at.asc())
        )
        result = await session.execute(query)
        candidates = result.scalars().all()

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].id

        # Try routing_filter match
        if routing_context:
            for agent in candidates:
                if agent.routing_filter:
                    if all(
                        agent.routing_filter.get(k) == v
                        for k, v in routing_context.items()
                        if k in agent.routing_filter
                    ):
                        return agent.id

        # Try specialization keyword match
        task_description = routing_context.get("description", "").lower()
        if task_description:
            for agent in candidates:
                if agent.specialization and agent.specialization.lower() in task_description:
                    return agent.id

        # Fallback: first active agent
        return candidates[0].id
