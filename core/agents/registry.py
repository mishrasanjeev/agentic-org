"""Agent registry — register, discover, instantiate agents."""

from __future__ import annotations

from typing import Any

from core.agents.base import BaseAgent


class AgentRegistry:
    """Central registry for all agent types.

    Built-in agents register via @AgentRegistry.register decorator at import time.
    Custom agents (user-created via UI) have no Python class and fall back to BaseAgent.
    """

    _registry: dict[str, type[BaseAgent]] = {}

    @classmethod
    def register(cls, agent_cls: type[BaseAgent]) -> type[BaseAgent]:
        """Register an agent class by its agent_type."""
        cls._registry[agent_cls.agent_type] = agent_cls
        return agent_cls

    @classmethod
    def get_by_type(cls, agent_type: str) -> type[BaseAgent] | None:
        return cls._registry.get(agent_type)

    @classmethod
    def has_type(cls, agent_type: str) -> bool:
        return agent_type in cls._registry

    @classmethod
    def get_by_domain(cls, domain: str) -> list[type[BaseAgent]]:
        return [a for a in cls._registry.values() if a.domain == domain]

    @classmethod
    def all_types(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def create_from_config(cls, config: dict[str, Any]) -> BaseAgent:
        """Instantiate an agent from DB config.

        For registered types: uses the registered class (built-in agents).
        For custom types: falls back to BaseAgent with inline prompt from DB.
        """
        agent_cls = cls._registry.get(config["agent_type"])

        if agent_cls is None:
            # Custom agent type — no Python class, use BaseAgent directly
            agent_cls = BaseAgent

        instance = agent_cls(
            agent_id=config["id"],
            tenant_id=config["tenant_id"],
            authorized_tools=config.get("authorized_tools", []),
            prompt_variables=config.get("prompt_variables", {}),
            hitl_condition=config.get("hitl_condition", ""),
            output_schema=config.get("output_schema"),
            tool_gateway=config.get("tool_gateway"),
            llm_model=config.get("llm_model"),
            cost_controls=config.get("cost_controls"),
        )

        # Override prompt if inline text provided (custom agents or persona-modified)
        system_prompt_text = config.get("system_prompt_text")
        if system_prompt_text:
            resolved = cls._resolve_template(
                system_prompt_text, config.get("prompt_variables", {})
            )
            instance._system_prompt = resolved

        return instance

    @staticmethod
    def _resolve_template(template: str, variables: dict[str, str]) -> str:
        """Replace {{key}} placeholders with variable values."""
        for key, val in variables.items():
            template = template.replace("{{" + key + "}}", str(val))
        return template
