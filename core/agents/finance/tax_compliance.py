"""Tax Compliance agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class TaxComplianceAgent(BaseAgent):
    agent_type = "tax_compliance"
    domain = "finance"
    confidence_floor = 0.92
    prompt_file = "tax_compliance.prompt.txt"

    async def execute(self, task):
        """Execute tax compliance task with domain-specific logic."""
        return await super().execute(task)
