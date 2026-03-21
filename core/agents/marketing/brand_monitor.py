"""Brand Monitor agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class BrandMonitorAgent(BaseAgent):
    agent_type = "brand_monitor"
    domain = "marketing"
    confidence_floor = 0.85
    prompt_file = "brand_monitor.prompt.txt"

    async def execute(self, task):
        """Execute brand monitor task with domain-specific logic."""
        return await super().execute(task)
