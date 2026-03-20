"""Agent registry — register, discover, instantiate agents."""
from __future__ import annotations

from typing import Any, Type

from core.agents.base import BaseAgent


class AgentRegistry:
    """Central registry for all agent types."""

    _registry: dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, agent_cls: Type[BaseAgent]) -> Type[BaseAgent]:
        """Register an agent class by its agent_type."""
        cls._registry[agent_cls.agent_type] = agent_cls
        return agent_cls

    @classmethod
    def get_by_type(cls, agent_type: str) -> Type[BaseAgent] | None:
        return cls._registry.get(agent_type)

    @classmethod
    def get_by_domain(cls, domain: str) -> list[Type[BaseAgent]]:
        return [a for a in cls._registry.values() if a.domain == domain]

    @classmethod
    def all_types(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def create_from_config(cls, config: dict[str, Any]) -> BaseAgent:
        """Instantiate an agent from DB config."""
        agent_cls = cls._registry.get(config["agent_type"])
        if not agent_cls:
            raise ValueError(f"Unknown agent type: {config['agent_type']}")
        return agent_cls(
            agent_id=config["id"],
            tenant_id=config["tenant_id"],
            authorized_tools=config.get("authorized_tools", []),
            prompt_variables=config.get("prompt_variables", {}),
            hitl_condition=config.get("hitl_condition", ""),
            output_schema=config.get("output_schema"),
        )
