"""Talent Acquisition agent implementation."""
from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class TalentAcquisitionAgent(BaseAgent):
    agent_type = "talent_acquisition"
    domain = "hr"
    confidence_floor = 0.88
    prompt_file = "talent_acquisition.prompt.txt"

    async def execute(self, task):
        """Execute talent acquisition task with domain-specific logic."""
        return await super().execute(task)
