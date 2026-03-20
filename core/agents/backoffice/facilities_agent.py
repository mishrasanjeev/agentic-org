"""Facilities agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class FacilitiesAgentAgent(BaseAgent):
    agent_type = "facilities_agent"
    domain = "backoffice"
    confidence_floor = 0.8
    prompt_file = "facilities_agent.prompt.txt"

    async def execute(self, task):
        """Execute facilities task with domain-specific logic."""
        return await super().execute(task)
