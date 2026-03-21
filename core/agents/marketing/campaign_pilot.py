"""Campaign Pilot agent implementation."""
from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class CampaignPilotAgent(BaseAgent):
    agent_type = "campaign_pilot"
    domain = "marketing"
    confidence_floor = 0.85
    prompt_file = "campaign_pilot.prompt.txt"

    async def execute(self, task):
        """Execute campaign pilot task with domain-specific logic."""
        return await super().execute(task)
