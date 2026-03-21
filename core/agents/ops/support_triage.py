"""Support Triage agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class SupportTriageAgent(BaseAgent):
    agent_type = "support_triage"
    domain = "ops"
    confidence_floor = 0.85
    prompt_file = "support_triage.prompt.txt"

    async def execute(self, task):
        """Execute support triage task with domain-specific logic."""
        return await super().execute(task)
