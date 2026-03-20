"""Onboarding agent implementation."""
from __future__ import annotations
from typing import Any
from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

@AgentRegistry.register
class OnboardingAgentAgent(BaseAgent):
    agent_type = "onboarding_agent"
    domain = "hr"
    confidence_floor = 0.95
    prompt_file = "onboarding_agent.prompt.txt"

    async def execute(self, task):
        """Execute onboarding task with domain-specific logic."""
        return await super().execute(task)
