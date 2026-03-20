"""AR Collections agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class ArCollectionsAgent(BaseAgent):
    agent_type = "ar_collections"
    domain = "finance"
    confidence_floor = 0.85
    prompt_file = "ar_collections.prompt.txt"

    async def execute(self, task):
        """Execute ar collections task with domain-specific logic."""
        return await super().execute(task)
