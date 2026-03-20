"""Legal Ops agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class LegalOpsAgent(BaseAgent):
    agent_type = "legal_ops"
    domain = "backoffice"
    confidence_floor = 0.9
    prompt_file = "legal_ops.prompt.txt"

    async def execute(self, task):
        """Execute legal ops task with domain-specific logic."""
        return await super().execute(task)
