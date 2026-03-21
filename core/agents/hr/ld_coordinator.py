"""L&D Coordinator agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class LdCoordinatorAgent(BaseAgent):
    agent_type = "ld_coordinator"
    domain = "hr"
    confidence_floor = 0.82
    prompt_file = "ld_coordinator.prompt.txt"

    async def execute(self, task):
        """Execute l&d coordinator task with domain-specific logic."""
        return await super().execute(task)
