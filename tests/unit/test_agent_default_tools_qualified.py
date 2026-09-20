# SPDX-License-Identifier: Apache-2.0
"""Ambiguous default tools name their connector (PRD §7 F-3).

A bare name registered by several connectors resolves first-wins in
connector import order, so ``create_issue`` went to GitHub for Jira-oriented
agents and ``abm``'s ``query`` went to QuickBooks although its prompt reads
Salesforce. Where the agent's prompt names the connector, the default is now
``connector:tool``; the qualified name must resolve to exactly that connector
everywhere it is used, and an unknown qualifier must never match.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _derive_default_tools
from auth.grantex_registration import _tools_to_scopes
from core.langgraph.tool_adapter import _build_tool_index, build_tools_for_agent

EXPECTED_QUALIFIED = {
    "abm": {"salesforce:query", "salesforce:search_contacts", "linkedin_ads:get_analytics"},
    "campaign_pilot": {"linkedin_ads:get_analytics"},
    "vendor_manager": {"jira:create_issue", "confluence:create_page"},
    "facilities_agent": {"jira:create_issue"},
    "contract_intelligence": {"confluence:create_page"},
    "legal_ops": {"confluence:create_page"},
    "onboarding_agent": {"confluence:create_page"},
    "ld_coordinator": {"confluence:create_page"},
    "content_factory": {"confluence:create_page"},
    "compliance_guard": {"gstn:get_compliance_notice"},
    "it_operations": {"servicenow:create_incident"},
    "email_marketing": {"sendgrid:send_email", "mailchimp:create_campaign"},
    "email_agent": {"gmail:send_email"},
}


@pytest.fixture(scope="module")
def alias_index() -> dict[str, tuple[str, str]]:
    return _build_tool_index(include_connector_aliases=True)


@pytest.mark.parametrize(("agent_type", "qualified"), sorted(EXPECTED_QUALIFIED.items()))
def test_ambiguous_defaults_name_the_connector_the_prompt_uses(agent_type, qualified):
    tools = set(_AGENT_TYPE_DEFAULT_TOOLS[agent_type])
    assert qualified <= tools
    assert not {name.split(":", 1)[1] for name in qualified} & tools, "bare duplicate left beside qualified name"


def _qualified_defaults() -> list[tuple[str, str]]:
    return sorted(
        (agent_type, tool) for agent_type, tools in _AGENT_TYPE_DEFAULT_TOOLS.items() for tool in tools if ":" in tool
    )


@pytest.mark.parametrize(("agent_type", "tool"), _qualified_defaults())
def test_every_qualified_default_resolves_to_the_connector_it_names(agent_type, tool, alias_index):
    connector, _bare = tool.split(":", 1)
    assert tool in alias_index, f"{agent_type}: {tool} is not registered"
    assert alias_index[tool][0] == connector


def test_qualified_default_survives_when_its_connector_is_linked():
    tools = _derive_default_tools("vendor_manager", "ops", ["jira", "confluence"])
    assert "jira:create_issue" in tools
    assert "confluence:create_page" in tools
    assert "search_issues" in tools


def test_qualified_default_is_dropped_when_its_connector_is_not_linked():
    tools = _derive_default_tools("vendor_manager", "ops", ["jira"])
    assert "jira:create_issue" in tools
    assert "confluence:create_page" not in tools


def test_unknown_connector_qualifier_never_matches():
    defaults = {"vendor_manager": ["nonexistent_connector:create_issue", "jira:not_a_tool", "search_issues"]}
    with patch.dict("api.v1.agents._AGENT_TYPE_DEFAULT_TOOLS", defaults):
        tools = _derive_default_tools("vendor_manager", "ops", ["jira"])
    assert tools == ["search_issues"]


def test_scopes_use_the_named_connector_not_the_first_registered_one():
    scopes = _tools_to_scopes(["jira:create_issue", "salesforce:query", "create_issue"], "ops")
    assert "tool:jira:write:create_issue" in scopes
    assert "tool:salesforce:read:query" in scopes
    # The bare name keeps its historical first-wins resolution.
    assert "tool:github:write:create_issue" in scopes


def test_scopes_for_unknown_qualifier_grant_no_connector():
    scopes = _tools_to_scopes(["nonexistent_connector:create_issue"], "ops")
    assert scopes == ["agenticorg:ops:read", "tool:agenticorg:write:nonexistent_connector:create_issue"]


def test_runtime_binds_the_named_connector():
    tools = build_tools_for_agent(["jira:create_issue", "salesforce:query", "nonexistent_connector:create_issue"])
    bound = {(t.metadata["connector"], t.metadata["tool"]) for t in tools}
    assert bound == {("jira", "create_issue"), ("salesforce", "query")}
