"""Performance Coach agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class PerformanceCoachAgent(BaseAgent):
    agent_type = "performance_coach"
    domain = "hr"
    confidence_floor = 0.8
    prompt_file = "performance_coach.prompt.txt"

    async def execute(self, task):
        """Execute performance coach task with domain-specific logic."""
        return await super().execute(task)
