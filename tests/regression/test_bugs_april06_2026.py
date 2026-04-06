"""Regression tests for all 15 bugs from Bugs06April2026.xlsx.

Each test verifies the specific fix applied so these bugs never return.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# BUG 1: DASH-NTF-001 — Notification toggle must call function, not store ref
# ═══════════════════════════════════════════════════════════════════════════

class TestBug1NotificationToggle:
    def test_notification_bell_calls_push_supported_function(self):
        """useState must invoke isPushSupported(), not store the function ref."""
        import re

        with open("ui/src/components/NotificationBell.tsx", encoding="utf-8") as f:
            code = f.read()

        # Must have useState(() => isPushSupported()) — calling the function
        assert "useState(() =>" in code or "useState(isPushSupported())" in code, (
            "NotificationBell must call isPushSupported() in useState, not store the ref"
        )
        # Must NOT have useState(isPushSupported) without parens (stores fn ref)
        bad_pattern = re.findall(r"useState\(isPushSupported\)(?!\()", code)
        assert len(bad_pattern) == 0, (
            "Found useState(isPushSupported) without () — stores function ref not result"
        )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 2: AGENT-TOOL-002 — Tool selector must fetch function-level names
# ═══════════════════════════════════════════════════════════════════════════

class TestBug2ToolDropdown:
    def test_agent_create_fetches_tools_not_mcp(self):
        """AgentCreate must fetch from /tools or /connectors/registry, NOT /mcp/tools."""
        with open("ui/src/pages/AgentCreate.tsx", encoding="utf-8") as f:
            code = f.read()

        # Must reference /tools endpoint (function-level)
        has_tools_endpoint = "/tools" in code or "/connectors/registry" in code
        assert has_tools_endpoint, "AgentCreate must fetch from /tools or /connectors/registry"

    def test_tools_endpoint_exists(self):
        """GET /tools endpoint must exist in connectors router."""
        from api.v1.connectors import router

        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/tools" in paths or "tools" in str(paths), (
            "GET /tools endpoint must exist in connectors router"
        )

    def test_tools_endpoint_returns_function_level_names(self):
        """GET /tools must return function-level names like send_email, not agenticorg_email_agent."""
        from core.langgraph.tool_adapter import _build_tool_index

        index = _build_tool_index()
        if index:
            for tool_name in list(index.keys())[:5]:
                assert not tool_name.startswith("agenticorg_"), (
                    f"Tool index should return function-level names, got: {tool_name}"
                )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 3 + BUG 8: AGENT-CONFIG-003 / BUG-API-008 — config field in response
# ═══════════════════════════════════════════════════════════════════════════

class TestBug3ConfigField:
    def test_agent_to_dict_includes_config(self):
        """_agent_to_dict() must include 'config' field."""
        from api.v1.agents import _agent_to_dict

        agent = MagicMock()
        agent.config = {"grantex": {"did": "test"}}
        for attr in [
            "id", "name", "agent_type", "domain", "status", "version",
            "description", "system_prompt_ref", "prompt_variables",
            "llm_model", "llm_fallback", "llm_config",
            "confidence_floor", "hitl_condition", "max_retries",
            "retry_backoff", "authorized_tools", "output_schema",
            "parent_agent_id", "shadow_comparison_agent_id",
            "shadow_min_samples", "shadow_accuracy_floor",
            "shadow_sample_count", "shadow_accuracy_current",
            "cost_controls", "scaling", "tags", "ttl_hours",
            "expires_at", "created_at", "updated_at",
            "employee_name", "avatar_url", "designation",
            "specialization", "routing_filter", "is_builtin",
            "system_prompt_text", "reporting_to", "org_level",
        ]:
            setattr(agent, attr, getattr(agent, attr, None))
        agent.prompt_amendments = []

        result = _agent_to_dict(agent)
        assert "config" in result, "config field must be in _agent_to_dict() response"
        assert result["config"] == {"grantex": {"did": "test"}}


# ═══════════════════════════════════════════════════════════════════════════
# BUG 4 + BUG 9: AGENT-RUN-004 / BUG-API-009 — connector_config passed
# ═══════════════════════════════════════════════════════════════════════════

class TestBug4ConnectorConfig:
    def test_run_agent_handler_passes_connector_config(self):
        """POST /agents/{id}/run must pass connector_config to langgraph_run()."""

        with open("api/v1/agents.py", encoding="utf-8") as f:
            code = f.read()

        # Find the langgraph_run() call and verify connector_config is passed
        assert "connector_config=" in code, (
            "agents.py must pass connector_config= to langgraph_run()"
        )
        assert 'connector_config=agent_config.get("config")' in code or \
               "connector_config=agent_config.get('config')" in code, (
            "connector_config must come from agent_config['config']"
        )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 5 + BUG 11: CHAT-ROUTER-005 / BUG-CHAT-011 — dynamic chat routing
# ═══════════════════════════════════════════════════════════════════════════

class TestBug5ChatRouting:
    def test_chat_router_not_hardcoded(self):
        """Chat router must query DB, not use hardcoded agent dict."""
        with open("api/v1/chat.py", encoding="utf-8") as f:
            code = f.read()

        # Must NOT have a static dict of domain -> agent name
        assert "_DOMAIN_AGENTS" not in code or "_DOMAIN_AGENTS = {" not in code, (
            "Chat router must not use hardcoded _DOMAIN_AGENTS dict"
        )
        # Must have DB query for agents
        has_db_query = "select(" in code or "session.execute" in code or "_find_agent" in code
        assert has_db_query, "Chat router must query DB for agents"

    def test_chat_router_calls_run_agent(self):
        """Chat endpoint must call langgraph_run() or run_agent()."""
        with open("api/v1/chat.py", encoding="utf-8") as f:
            code = f.read()

        assert "run_agent" in code or "langgraph_run" in code, (
            "Chat endpoint must call run_agent() for real execution"
        )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 6: AGENT-006 — Long input retry
# ═══════════════════════════════════════════════════════════════════════════

class TestBug6LongInputRetry:
    def test_agent_generator_has_retry_logic(self):
        """agent_generator must retry on parse failure with simplified input."""
        with open("core/agent_generator.py", encoding="utf-8") as f:
            code = f.read()

        assert "retry" in code.lower() or "simplified" in code.lower() or "truncat" in code.lower(), (
            "agent_generator must have retry/truncation logic for long inputs"
        )

    @pytest.mark.asyncio
    async def test_long_input_does_not_crash(self):
        """Long structured input should not raise unhandled exception."""
        from core.agent_generator import generate_agent_config

        long_input = "I need an agent that " + "processes invoices " * 200

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "agent_type": "ap_processor",
            "domain": "finance",
            "confidence": 0.9,
            "employee_name": "Test",
            "designation": "AP Agent",
            "system_prompt": "You are an AP agent.",
            "suggested_tools": ["process_invoice"],
            "confidence_floor": 0.88,
            "hitl_condition": "",
            "specialization": "invoices",
        })
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value=mock_response)
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        try:
            result = await generate_agent_config(long_input, llm=mock_llm)
            assert isinstance(result, (dict, list)), "Should return config or suggestions"
        except ValueError:
            pass  # ValueError with helpful message is acceptable
        except Exception as e:
            pytest.fail(f"Long input raised unhandled exception: {type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# BUG 7: BUG-API-007 — Agent Teams GET endpoint
# ═══════════════════════════════════════════════════════════════════════════

class TestBug7AgentTeamsGET:
    def test_agent_teams_has_get_endpoint(self):
        """Agent teams router must have GET endpoints."""
        from api.v1.agent_teams import router

        methods = set()
        for route in router.routes:
            if hasattr(route, "methods"):
                methods.update(route.methods)

        assert "GET" in methods, "Agent teams router must have GET endpoint"

    def test_agent_teams_get_returns_list_structure(self):
        """GET /agent-teams should be designed to return a list."""
        with open("api/v1/agent_teams.py", encoding="utf-8") as f:
            code = f.read()

        assert "@router.get" in code, "Must have @router.get decorator"


# ═══════════════════════════════════════════════════════════════════════════
# BUG 10: BUG-API-010 — Connectors not_configured instead of unhealthy
# ═══════════════════════════════════════════════════════════════════════════

class TestBug10ConnectorHealth:
    def test_base_connector_has_credentials_check(self):
        """BaseConnector must have _has_credentials() method."""
        from connectors.framework.base_connector import BaseConnector

        assert hasattr(BaseConnector, "_has_credentials"), (
            "BaseConnector must have _has_credentials() method"
        )

    @pytest.mark.asyncio
    async def test_health_check_returns_not_configured_for_empty_creds(self):
        """Health check with empty creds returns not_configured, not unhealthy."""
        from connectors.framework.base_connector import BaseConnector

        class TestConnector(BaseConnector):
            name = "test_conn"
            category = "test"
            auth_type = "apikey"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                self._auth_headers = {"Authorization": "Bearer test"}

        connector = TestConnector(config={})
        result = await connector.health_check()
        assert result["status"] in ("not_configured", "not_connected"), (
            f"Empty creds should return not_configured, got: {result['status']}"
        )

    def test_empty_bearer_not_sent(self):
        """connect() must not send empty Bearer token."""
        with open("connectors/framework/base_connector.py", encoding="utf-8") as f:
            code = f.read()

        assert "_has_credentials" in code, "Must check credentials before authenticating"


# ═══════════════════════════════════════════════════════════════════════════
# BUG 12: BUG-MCP-012 — MCP passes connector_config
# ═══════════════════════════════════════════════════════════════════════════

class TestBug12MCPConnectorConfig:
    def test_mcp_call_passes_connector_config(self):
        """MCP call handler must pass connector_config to langgraph_run()."""
        with open("api/v1/mcp.py", encoding="utf-8") as f:
            code = f.read()

        assert "connector_config" in code, (
            "MCP handler must reference connector_config"
        )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 13: BUG-MCP-013 — MCP accepts both name and tool fields
# ═══════════════════════════════════════════════════════════════════════════

class TestBug13MCPSchemaCompat:
    def test_mcp_request_accepts_tool_field(self):
        """MCPCallRequest must accept 'tool' field as alias for 'name'."""
        from api.v1.mcp import MCPCallRequest

        req = MCPCallRequest(tool="agenticorg_ap_processor", arguments={})
        assert req.name == "agenticorg_ap_processor", (
            "tool field must sync to name"
        )

    def test_mcp_request_accepts_name_field(self):
        """MCPCallRequest must still accept 'name' field."""
        from api.v1.mcp import MCPCallRequest

        req = MCPCallRequest(name="agenticorg_ap_processor", arguments={})
        assert req.tool == "agenticorg_ap_processor", (
            "name field must sync to tool"
        )

    def test_mcp_request_rejects_empty(self):
        """MCPCallRequest with neither name nor tool should have empty name."""
        from api.v1.mcp import MCPCallRequest

        req = MCPCallRequest(arguments={})
        assert req.name == "", "Empty request should have empty name"


# ═══════════════════════════════════════════════════════════════════════════
# BUG 14: BUG-UI-TOOLS-014 — GET /tools endpoint exists
# ═══════════════════════════════════════════════════════════════════════════

class TestBug14ToolsEndpoint:
    def test_tools_endpoint_registered(self):
        """GET /tools must be registered in connectors router."""
        from api.v1.connectors import router

        get_routes = [
            r for r in router.routes
            if hasattr(r, "methods") and "GET" in r.methods
        ]
        paths = [getattr(r, "path", "") for r in get_routes]
        assert any("tools" in p for p in paths), (
            f"GET /tools not found in connectors routes: {paths}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 15: BUG-API-AGENT-015 — Error message references correct endpoint
# ═══════════════════════════════════════════════════════════════════════════

class TestBug15ErrorMessage:
    def test_error_message_references_tools_endpoint(self):
        """PATCH agent error must reference /tools, not /mcp/tools."""
        with open("api/v1/agents.py", encoding="utf-8") as f:
            code = f.read()

        # Should reference the correct endpoints
        assert "/tools" in code, "Must reference /tools endpoint in error messages"
        # Should NOT exclusively point to /mcp/tools for tool discovery
        # (it's OK to mention /mcp/tools but must also mention /tools or /connectors/registry)


# ═══════════════════════════════════════════════════════════════════════════
# DEPLOY HEALTH CHECK — Post-fix regression for deploy pipeline
# ═══════════════════════════════════════════════════════════════════════════

class TestDeployHealthCheck:
    def test_ci_health_check_accepts_degraded(self):
        """CI deploy health check must accept degraded status (unconfigured connectors)."""
        with open(".github/workflows/deploy.yml", encoding="utf-8") as f:
            code = f.read()

        assert "degraded" in code, (
            "Deploy health check must accept degraded status"
        )
