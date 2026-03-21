"""Risk Sentinel agent implementation."""
from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class RiskSentinelAgent(BaseAgent):
    agent_type = "risk_sentinel"
    domain = "backoffice"
    confidence_floor = 0.95
    prompt_file = "risk_sentinel.prompt.txt"

    async def execute(self, task):
        """Execute risk sentinel task with domain-specific logic."""
        return await super().execute(task)
