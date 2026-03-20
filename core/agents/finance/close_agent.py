"""Month-End Close agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class CloseAgentAgent(BaseAgent):
    agent_type = "close_agent"
    domain = "finance"
    confidence_floor = 0.8
    prompt_file = "close_agent.prompt.txt"

    async def execute(self, task):
        """Execute month-end close task with domain-specific logic."""
        return await super().execute(task)
