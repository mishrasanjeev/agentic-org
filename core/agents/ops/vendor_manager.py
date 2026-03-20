"""Vendor Manager agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class VendorManagerAgent(BaseAgent):
    agent_type = "vendor_manager"
    domain = "ops"
    confidence_floor = 0.88
    prompt_file = "vendor_manager.prompt.txt"

    async def execute(self, task):
        """Execute vendor manager task with domain-specific logic."""
        return await super().execute(task)
