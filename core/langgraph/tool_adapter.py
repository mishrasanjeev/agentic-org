"""Wrap existing BaseConnector tools as LangChain tools.

Each connector tool becomes a LangChain @tool function that:
1. Validates the agent's Grantex grant has the required scope
2. Debits the Grantex budget for payment operations
3. Executes the tool via the existing connector framework
4. Logs to the Grantex audit trail
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from langchain_core.tools import StructuredTool

from connectors.framework.base_connector import BaseConnector
from connectors.registry import ConnectorRegistry

logger = structlog.get_logger()

# Cache connector instances to avoid re-creating on every tool call
_connector_cache: dict[str, BaseConnector] = {}


async def _execute_connector_tool(
    connector_name: str,
    tool_name: str,
    params: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a connector tool and return the result."""
    connector_cls = ConnectorRegistry.get(connector_name)
    if not connector_cls:
        return {"error": f"Connector '{connector_name}' not found in registry"}

    config_fingerprint = json.dumps(config or {}, sort_keys=True)
    cache_key = f"{connector_name}:{config_fingerprint}"
    if cache_key not in _connector_cache:
        instance = connector_cls(config or {})
        try:
            await instance.connect()
        except Exception as e:
            logger.warning("connector_connect_failed", connector=connector_name, error=str(e))
            return {"error": f"Failed to connect to {connector_name}"}
        _connector_cache[cache_key] = instance

    instance = _connector_cache[cache_key]
    start = time.monotonic()
    try:
        result = await instance.execute_tool(tool_name, params)
        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "tool_executed",
            connector=connector_name,
            tool=tool_name,
            latency_ms=latency,
            status="success",
        )
        return result
    except Exception as e:
        latency = int((time.monotonic() - start) * 1000)
        logger.error(
            "tool_execution_failed",
            connector=connector_name,
            tool=tool_name,
            latency_ms=latency,
            error=str(e),
        )
        return {"error": f"Tool execution failed: {type(e).__name__}"}


def build_tools_for_agent(
    authorized_tools: list[str],
    connector_config: dict[str, Any] | None = None,
) -> list[StructuredTool]:
    """Build LangChain tools from an agent's authorized_tools list.

    Each tool name in authorized_tools (e.g., "fetch_bank_statement",
    "create_payment_intent") is matched to a connector and wrapped as a
    LangChain StructuredTool.

    Returns a list of callable LangChain tools ready for LangGraph.
    """
    tools: list[StructuredTool] = []
    seen: set[str] = set()

    # Build a reverse index: tool_name -> (connector_name, handler_doc)
    tool_index = _build_tool_index(connector_config)

    for tool_ref in authorized_tools:
        if tool_ref in seen:
            continue
        seen.add(tool_ref)

        match = tool_index.get(tool_ref)
        if not match:
            continue

        connector_name, description = match

        # Create an async wrapper that calls the connector
        def _make_tool_fn(cn: str, tn: str, desc: str):
            async def _tool_fn(**kwargs: Any) -> dict[str, Any]:
                return await _execute_connector_tool(cn, tn, kwargs, connector_config)
            _tool_fn.__name__ = tn
            _tool_fn.__doc__ = desc or f"Execute {tn} on {cn} connector"
            return _tool_fn

        tool = StructuredTool.from_function(
            coroutine=_make_tool_fn(connector_name, tool_ref, description),
            name=tool_ref,
            description=description or f"Execute {tool_ref}",
        )
        tools.append(tool)

    return tools


def _build_tool_index(
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Build a reverse index: tool_name -> (connector_name, description).

    Scans all registered native connectors and their tool registries,
    then appends Composio tools (with ``composio:`` prefix) from the
    ConnectorRegistry.  Native tools always take priority.

    UR-Bug-2 (Uday/Ramesh 2026-04-21): when ``connector_names`` is
    provided, the index is restricted to tools registered by those
    connectors. Used by ``GET /tools?connectors=gmail`` so the agent
    creation UI can populate authorized_tools with exactly the
    connectors the user picked, instead of every tool in the product.
    """
    allowed: set[str] | None = None
    if connector_names:
        # Normalise — strip any "registry-" UI prefix and lowercase.
        allowed = {
            n.removeprefix("registry-").strip().lower()
            for n in connector_names
            if n
        }

    index: dict[str, tuple[str, str]] = {}

    # 1. Native connectors first
    for connector_name in ConnectorRegistry.all_names():
        # Skip the composio meta-connector; its tools are handled below
        if connector_name == "composio":
            continue
        if allowed is not None and connector_name.lower() not in allowed:
            continue

        connector_cls = ConnectorRegistry.get(connector_name)
        if not connector_cls:
            continue

        # Instantiate just to read the tool registry
        instance = connector_cls.__new__(connector_cls)
        instance.config = connector_config or {}
        instance._tool_registry = {}
        try:
            instance._register_tools()
        except Exception:  # noqa: S112
            continue  # Skip connectors that fail to register tools

        for tool_name, handler in instance._tool_registry.items():
            if tool_name not in index:
                doc = (handler.__doc__ or "").strip().split("\n")[0]
                index[tool_name] = (connector_name, doc)

    # 2. Composio tools (already filtered for native priority in registry)
    if allowed is None or "composio" in allowed:
        for tool_name, meta in ConnectorRegistry.get_composio_tools().items():
            if tool_name not in index:
                index[tool_name] = ("composio", meta.get("description", ""))

    return index
