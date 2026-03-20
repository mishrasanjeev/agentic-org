"""IT Operations agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class ItOperationsAgent(BaseAgent):
    agent_type = "it_operations"
    domain = "ops"
    confidence_floor = 0.88
    prompt_file = "it_operations.prompt.txt"

    async def execute(self, task):
        """Execute it operations task with domain-specific logic."""
        return await super().execute(task)
