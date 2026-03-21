"""Reconciliation agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class ReconAgentAgent(BaseAgent):
    agent_type = "recon_agent"
    domain = "finance"
    confidence_floor = 0.95
    prompt_file = "recon_agent.prompt.txt"

    async def execute(self, task):
        """Execute reconciliation task with domain-specific logic."""
        return await super().execute(task)
