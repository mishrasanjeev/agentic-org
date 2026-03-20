"""Connector registry — register and discover connectors."""
from __future__ import annotations
from typing import Any
from connectors.framework.base_connector import BaseConnector

class ConnectorRegistry:
    _connectors: dict[str, type[BaseConnector]] = {}

    @classmethod
    def register(cls, connector_cls: type[BaseConnector]) -> None:
        cls._connectors[connector_cls.name] = connector_cls

    @classmethod
    def get(cls, name: str) -> type[BaseConnector] | None:
        return cls._connectors.get(name)

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._connectors.keys())

    @classmethod
    def by_category(cls, category: str) -> list[type[BaseConnector]]:
        return [c for c in cls._connectors.values() if c.category == category]
