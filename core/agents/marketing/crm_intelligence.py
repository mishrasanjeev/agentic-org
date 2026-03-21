"""CRM Intelligence agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class CrmIntelligenceAgent(BaseAgent):
    agent_type = "crm_intelligence"
    domain = "marketing"
    confidence_floor = 0.88
    prompt_file = "crm_intelligence.prompt.txt"

    async def execute(self, task):
        """Execute crm intelligence task with domain-specific logic."""
        return await super().execute(task)
