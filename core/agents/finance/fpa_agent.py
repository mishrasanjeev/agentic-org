"""FP&A agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class FpaAgentAgent(BaseAgent):
    agent_type = "fpa_agent"
    domain = "finance"
    confidence_floor = 0.78
    prompt_file = "fpa_agent.prompt.txt"

    async def execute(self, task):
        """Execute fp&a task with domain-specific logic."""
        return await super().execute(task)
