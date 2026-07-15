"""Tests for A2A and MCP endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from core.commerce.discovery_gate import COMMERCE_PUBLIC_DISCOVERY_ENV


def _payload_contains(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_payload_contains(item, needle) for item in value.values())
    if isinstance(value, list | tuple | set):
        return any(_payload_contains(item, needle) for item in value)
    return isinstance(value, str) and needle in value


class TestA2AAgentCard:
    @pytest.mark.asyncio
    async def test_agent_card_returns_skills(self, monkeypatch):
        from api.v1.a2a import agent_card

        monkeypatch.delenv(COMMERCE_PUBLIC_DISCOVERY_ENV, raising=False)
        card = await agent_card()
        assert card["name"] == "AgenticOrg Agent Platform"
        assert card["protocol"] == "a2a/1.0"
        assert card["capabilities"]["tasks"] is True
        assert card["authentication"]["scheme"] == "grantex"
        assert len(card["skills"]) == 36  # Commerce discovery is disabled by default.
        assert "commerce_sales_agent" not in {skill["id"] for skill in card["skills"]}
        assert not _payload_contains(card["skills"], "grantex_commerce:")

    @pytest.mark.asyncio
    async def test_agent_card_skills_have_required_fields(self, monkeypatch):
        from api.v1.a2a import agent_card

        monkeypatch.delenv(COMMERCE_PUBLIC_DISCOVERY_ENV, raising=False)
        card = await agent_card()
        for skill in card["skills"]:
            assert "id" in skill
            assert "name" in skill
            assert "domain" in skill
            assert "tools" in skill
            assert "inputSchema" in skill

    @pytest.mark.asyncio
    async def test_list_available_agents(self, monkeypatch):
        from api.v1.a2a import list_available_agents

        monkeypatch.delenv(COMMERCE_PUBLIC_DISCOVERY_ENV, raising=False)
        result = await list_available_agents()
        assert len(result["agents"]) == 36
        assert "commerce_sales_agent" not in {agent["id"] for agent in result["agents"]}
        assert not _payload_contains(result["agents"], "grantex_commerce:")

    @pytest.mark.asyncio
    async def test_agent_card_exposes_commerce_when_public_discovery_enabled(self, monkeypatch):
        from api.v1.a2a import agent_card, list_available_agents

        monkeypatch.setenv(COMMERCE_PUBLIC_DISCOVERY_ENV, "true")
        card = await agent_card()
        agents = await list_available_agents()

        assert "commerce_sales_agent" in {skill["id"] for skill in card["skills"]}
        assert "commerce_sales_agent" in {agent["id"] for agent in agents["agents"]}
        assert _payload_contains(card["skills"], "grantex_commerce:")
        assert _payload_contains(agents["agents"], "grantex_commerce:")

    @pytest.mark.asyncio
    async def test_invalid_public_discovery_value_hides_commerce(self, monkeypatch):
        from api.v1.a2a import list_available_agents

        monkeypatch.setenv(COMMERCE_PUBLIC_DISCOVERY_ENV, "maybe")
        result = await list_available_agents()
        assert "commerce_sales_agent" not in {agent["id"] for agent in result["agents"]}
        assert not _payload_contains(result["agents"], "grantex_commerce:")


class TestA2ATask:
    @pytest.mark.asyncio
    async def test_create_task_unknown_agent_type(self):
        from api.v1.a2a import A2ATaskRequest, create_task

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()
        with pytest.raises(HTTPException) as exc:
            await create_task(
                A2ATaskRequest(agent_type="nonexistent_agent", inputs={"x": 1}),
                request,
                "00000000-0000-0000-0000-000000000001",
            )
        assert exc.value.status_code == 400

    # test_get_task_not_found: moved to
    # tests/integration/test_db_api_endpoints.py::TestA2ATaskIntegration
    # (it hits real postgres-backed a2a_tasks).


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_registered_agents(self, monkeypatch):
        from api.v1.mcp import list_tools

        monkeypatch.delenv(COMMERCE_PUBLIC_DISCOVERY_ENV, raising=False)
        result = await list_tools()
        assert len(result["tools"]) == 36  # Commerce discovery is disabled by default.
        assert "agenticorg_commerce_sales_agent" not in {tool["name"] for tool in result["tools"]}
        assert not _payload_contains(result["tools"], "grantex_commerce:")

    @pytest.mark.asyncio
    async def test_tool_names_prefixed(self, monkeypatch):
        from api.v1.mcp import list_tools

        monkeypatch.delenv(COMMERCE_PUBLIC_DISCOVERY_ENV, raising=False)
        result = await list_tools()
        for tool in result["tools"]:
            assert tool["name"].startswith("agenticorg_")
            assert "inputSchema" in tool
            assert "description" in tool

    @pytest.mark.asyncio
    async def test_list_tools_exposes_commerce_when_public_discovery_enabled(self, monkeypatch):
        from api.v1.mcp import list_tools

        monkeypatch.setenv(COMMERCE_PUBLIC_DISCOVERY_ENV, "enabled")
        result = await list_tools()
        names = {tool["name"] for tool in result["tools"]}
        assert "agenticorg_commerce_sales_agent" in names

    @pytest.mark.asyncio
    async def test_call_unknown_tool_returns_404(self):
        from api.v1.mcp import MCPCallRequest, call_tool

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()
        with pytest.raises(HTTPException) as exc:
            await call_tool(
                MCPCallRequest(name="agenticorg_nonexistent", arguments={}),
                request,
                "00000000-0000-0000-0000-000000000001",
            )
        # PR-A changed unknown tools from 400 → 404 with a structured body.
        # See docs/mcp-product-model.md.
        assert exc.value.status_code == 404
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail.get("error") == "unknown_tool"
        assert exc.value.detail.get("supported_prefix") == "agenticorg_"

    @pytest.mark.asyncio
    async def test_call_invalid_prefix_returns_404(self):
        from api.v1.mcp import MCPCallRequest, call_tool

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()
        with pytest.raises(HTTPException) as exc:
            await call_tool(
                MCPCallRequest(name="some_other_tool", arguments={}),
                request,
                "00000000-0000-0000-0000-000000000001",
            )
        # PR-A: unknown prefix → 404 unknown_tool (was 400).
        assert exc.value.status_code == 404
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail.get("error") == "unknown_tool"
        assert exc.value.detail.get("name") == "some_other_tool"

    @pytest.mark.asyncio
    async def test_call_connector_resolution_failure_returns_error(self, monkeypatch):
        import api.v1.mcp as mcp
        from api.v1 import agents
        from api.v1.mcp import MCPCallRequest, call_tool
        from tests.company_scope import (
            TEST_COMPANY_ID,
            TEST_TENANT_ID,
            owned_company_validator,
        )

        agent_type = next(iter(agents._AGENT_TYPE_DEFAULT_TOOLS))

        async def _fail_resolve(*args, **kwargs):  # noqa: ARG001
            raise RuntimeError("connector store unavailable")

        monkeypatch.setattr(mcp, "_load_agent_prompt", lambda _agent_type: "prompt")
        monkeypatch.setattr(agents, "_resolve_agent_connector_ids_for_type", _fail_resolve)
        monkeypatch.setattr(
            agents,
            "_require_company_for_tenant",
            owned_company_validator(),
        )

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()

        result = await call_tool(
            MCPCallRequest(
                name=f"agenticorg_{agent_type}",
                arguments={"inputs": {}},
                company_id=str(TEST_COMPANY_ID),
            ),
            request,
            str(TEST_TENANT_ID),
        )

        assert result["isError"] is True
        assert result["error"] == "RuntimeError"
        assert "Connector configuration unavailable" in result["content"][0]["text"]


class TestA2AHelpers:
    def test_domain_map_covers_all_agents(self):
        from api.v1.a2a import _DOMAIN_MAP
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        for agent_type in _AGENT_TYPE_DEFAULT_TOOLS:
            assert agent_type in _DOMAIN_MAP, f"{agent_type} missing from A2A _DOMAIN_MAP"

    def test_mcp_domain_map_covers_all_agents(self):
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS
        from api.v1.mcp import _DOMAIN_MAP

        for agent_type in _AGENT_TYPE_DEFAULT_TOOLS:
            assert agent_type in _DOMAIN_MAP, f"{agent_type} missing from MCP _DOMAIN_MAP"
