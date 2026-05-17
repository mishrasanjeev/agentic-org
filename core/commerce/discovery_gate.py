"""Fail-closed public discovery gate for commerce metadata."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence

from core.commerce.sales_guardrails import COMMERCE_AGENT_TYPE

COMMERCE_PUBLIC_DISCOVERY_ENV = "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


def is_commerce_public_discovery_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return true only for explicit safe true values.

    Missing, empty, malformed, and ambiguous values all keep public commerce
    discovery disabled.
    """
    source = os.environ if environ is None else environ
    value = source.get(COMMERCE_PUBLIC_DISCOVERY_ENV, "")
    return value.strip().lower() in _TRUE_VALUES


def iter_public_discovery_agent_tools(
    agent_tools: Mapping[str, Sequence[str]],
) -> Iterator[tuple[str, Sequence[str]]]:
    """Yield agent tool mappings allowed in public MCP/A2A discovery."""
    commerce_enabled = is_commerce_public_discovery_enabled()
    for agent_type, tools in agent_tools.items():
        if agent_type == COMMERCE_AGENT_TYPE and not commerce_enabled:
            continue
        yield agent_type, tools
