"""Compliance Guard agent implementation."""

from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class ComplianceGuardAgent(BaseAgent):
    agent_type = "compliance_guard"
    domain = "ops"
    confidence_floor = 0.95
    prompt_file = "compliance_guard.prompt.txt"

    async def execute(self, task):
        """Execute compliance guard task with domain-specific logic."""
        return await super().execute(task)
