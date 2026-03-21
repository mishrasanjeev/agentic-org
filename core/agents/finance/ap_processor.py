"""AP Processor agent implementation."""
from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class ApProcessorAgent(BaseAgent):
    agent_type = "ap_processor"
    domain = "finance"
    confidence_floor = 0.88
    prompt_file = "ap_processor.prompt.txt"

    async def execute(self, task):
        """Execute ap processor task with domain-specific logic."""
        return await super().execute(task)
