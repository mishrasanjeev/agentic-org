"""Contract Intelligence agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class ContractIntelligenceAgent(BaseAgent):
    agent_type = "contract_intelligence"
    domain = "ops"
    confidence_floor = 0.82
    prompt_file = "contract_intelligence.prompt.txt"

    async def execute(self, task):
        """Execute contract intelligence task with domain-specific logic."""
        return await super().execute(task)
