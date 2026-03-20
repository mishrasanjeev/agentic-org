"""Content Factory agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class ContentFactoryAgent(BaseAgent):
    agent_type = "content_factory"
    domain = "marketing"
    confidence_floor = 0.88
    prompt_file = "content_factory.prompt.txt"

    async def execute(self, task):
        """Execute content factory task with domain-specific logic."""
        return await super().execute(task)
