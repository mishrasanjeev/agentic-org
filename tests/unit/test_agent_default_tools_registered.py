# SPDX-License-Identifier: Apache-2.0
"""Default tool lists name only tools a connector registers (PRD §7 F-3).

A default naming a tool no connector registers is never bound at run time,
yet it is advertised to the tool picker, MCP/A2A discovery and the agent's
prompt. Such names were removed; this keeps them out.
"""

from __future__ import annotations

import pytest

from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _DOMAIN_DEFAULT_TOOLS
from core.langgraph.tool_adapter import _build_tool_index


@pytest.fixture(scope="module")
def registered() -> set[str]:
    return set(_build_tool_index(include_connector_aliases=True))


def _entries() -> list[tuple[str, str]]:
    entries = [(f"agent_type:{k}", tool) for k, tools in _AGENT_TYPE_DEFAULT_TOOLS.items() for tool in tools]
    entries += [(f"domain:{k}", tool) for k, tools in _DOMAIN_DEFAULT_TOOLS.items() for tool in tools]
    return entries


@pytest.mark.parametrize(("owner", "tool"), _entries())
def test_every_default_tool_is_registered(owner, tool, registered):
    assert tool in registered, f"{owner} default tool {tool!r} is not registered by any connector"


@pytest.mark.parametrize(
    "tool",
    [
        "get_post_analytics",
        "schedule_social_post",
        "slack_send_message",
        "search_content_fulltext",
        "create_calendar_event",
        "get_sla_breach_status",
    ],
)
def test_known_unregistered_names_are_gone(tool, registered):
    assert tool not in registered
    assert all(tool not in tools for tools in _AGENT_TYPE_DEFAULT_TOOLS.values())
    assert all(tool not in tools for tools in _DOMAIN_DEFAULT_TOOLS.values())


def test_seo_strategist_keeps_its_agent_type_with_no_default_tools():
    assert _AGENT_TYPE_DEFAULT_TOOLS["seo_strategist"] == []
