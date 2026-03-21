"""Payroll Engine agent implementation."""
from __future__ import annotations

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry


@AgentRegistry.register
class PayrollEngineAgent(BaseAgent):
    agent_type = "payroll_engine"
    domain = "hr"
    confidence_floor = 0.99
    prompt_file = "payroll_engine.prompt.txt"

    async def execute(self, task):
        """Execute payroll engine task with domain-specific logic."""
        return await super().execute(task)
