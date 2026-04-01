"""Tests for A2A and MCP endpoints."""

from __future__ import annotations

import pytest
from fastapi import HTTPException


class TestA2AAgentCard:
    @pytest.mark.asyncio
    async def test_agent_card_returns_skills(self):
        from api.v1.a2a import agent_card

        card = await agent_card()
        assert card["name"] == "AgenticOrg Agent Platform"
        assert card["protocol"] == "a2a/1.0"
        assert card["capabilities"]["tasks"] is True
        assert card["authentication"]["scheme"] == "grantex"
        assert len(card["skills"]) == 28  # 25 original + 3 comms (email, notification, chat)

    @pytest.mark.asyncio
    async def test_agent_card_skills_have_required_fields(self):
        from api.v1.a2a import agent_card

        card = await agent_card()
        for skill in card["skills"]:
            assert "id" in skill
            assert "name" in skill
            assert "domain" in skill
            assert "tools" in skill
            assert "inputSchema" in skill

    @pytest.mark.asyncio
    async def test_list_available_agents(self):
        from api.v1.a2a import list_available_agents

        result = await list_available_agents()
        assert len(result["agents"]) == 28


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

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        from api.v1.a2a import get_task

        with pytest.raises(HTTPException) as exc:
            await get_task("nonexistent_task_id")
        assert exc.value.status_code == 404


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_25(self):
        from api.v1.mcp import list_tools

        result = await list_tools()
        assert len(result["tools"]) == 28

    @pytest.mark.asyncio
    async def test_tool_names_prefixed(self):
        from api.v1.mcp import list_tools

        result = await list_tools()
        for tool in result["tools"]:
            assert tool["name"].startswith("agenticorg_")
            assert "inputSchema" in tool
            assert "description" in tool

    @pytest.mark.asyncio
    async def test_call_unknown_tool_returns_400(self):
        from api.v1.mcp import MCPCallRequest, call_tool

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()
        with pytest.raises(HTTPException) as exc:
            await call_tool(
                MCPCallRequest(name="agenticorg_nonexistent", arguments={}),
                request,
                "00000000-0000-0000-0000-000000000001",
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_call_invalid_prefix_returns_400(self):
        from api.v1.mcp import MCPCallRequest, call_tool

        request = type("R", (), {"state": type("S", (), {"grant_token": ""})()})()
        with pytest.raises(HTTPException) as exc:
            await call_tool(
                MCPCallRequest(name="some_other_tool", arguments={}),
                request,
                "00000000-0000-0000-0000-000000000001",
            )
        assert exc.value.status_code == 400


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
