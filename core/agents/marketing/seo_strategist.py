"""SEO Strategist agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class SeoStrategistAgent(BaseAgent):
    agent_type = "seo_strategist"
    domain = "marketing"
    confidence_floor = 0.9
    prompt_file = "seo_strategist.prompt.txt"

    async def execute(self, task):
        """Execute seo strategist task with domain-specific logic."""
        return await super().execute(task)
