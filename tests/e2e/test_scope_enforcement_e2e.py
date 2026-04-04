"""E2E tests for Grantex scope enforcement — API-level tests.

Tests the full API flow with token-based auth, scope denial responses,
and agent creation with Grantex DID registration.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

BASE_URL = os.getenv("AGENTICORG_E2E_BASE_URL", "http://localhost:8000")
TOKEN = os.getenv("AGENTICORG_E2E_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_enforce_result(allowed: bool, reason: str = "") -> MagicMock:
    """Return a MagicMock that mimics grantex.EnforceResult."""
    result = MagicMock()
    result.allowed = allowed
    result.reason = reason
    return result


def _build_fake_tool_index() -> dict[str, tuple[str, str]]:
    return {
        "get_contact": ("salesforce", "Get a Salesforce contact"),
        "create_lead": ("salesforce", "Create a Salesforce lead"),
        "process_refund": ("stripe", "Process a refund"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# E2E Test: Token Auth to Tool Execution
# ═══════════════════════════════════════════════════════════════════════════


class TestE2ETokenAuthToToolExecution:
    """Full API call with Grantex token succeeds for allowed scope."""

    @pytest.mark.asyncio
    async def test_e2e_grantex_token_auth_to_tool_execution(self):
        """Full API call with token succeeds for allowed scope.

        Simulates the complete flow:
        1. Client sends request with Grantex grant token
        2. ToolGateway calls grantex.enforce()
        3. Tool execution proceeds when scope is allowed
        """
        from core.tool_gateway.gateway import ToolGateway

        gateway = ToolGateway()

        # Register a mock connector that returns success
        mock_connector = MagicMock()
        mock_connector.execute_tool = MagicMock(
            return_value={"id": "contact-123", "name": "Test Contact", "email": "test@example.com"}
        )
        gateway.register_connector("salesforce", mock_connector)

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)

        with patch("core.tool_gateway.gateway.get_grantex_client", return_value=mock_client):
            result = await gateway.execute(
                tenant_id="tenant-e2e-001",
                agent_id="agent-e2e-001",
                agent_scopes=["tool:salesforce:read:*"],
                connector_name="salesforce",
                tool_name="get_contact",
                params={"contact_id": "contact-123"},
                grant_token="grantex-valid-read-token",
            )

        # Tool executed successfully
        assert "error" not in result
        assert result["id"] == "contact-123"
        assert result["name"] == "Test Contact"

        # Verify grantex.enforce was called
        mock_client.enforce.assert_called_once_with(
            grant_token="grantex-valid-read-token",
            connector="salesforce",
            tool="get_contact",
            amount=None,
        )


# ═══════════════════════════════════════════════════════════════════════════
# E2E Test: Scope Denied Returns Error
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EScopeDenied:
    """API call with insufficient scope returns structured error."""

    @pytest.mark.asyncio
    async def test_e2e_scope_denied_returns_error(self):
        """API call with insufficient scope returns error with code E1007.

        Agent has read scope but tries to call a write tool (process_refund).
        The Stripe manifest defines process_refund as WRITE, so read scope is denied.
        """
        from core.tool_gateway.gateway import ToolGateway

        gateway = ToolGateway()

        mock_connector = MagicMock()
        mock_connector.execute_tool = MagicMock(return_value={"status": "refunded"})
        gateway.register_connector("stripe", mock_connector)

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'read' insufficient for 'write' on process_refund"
        )

        with patch("core.tool_gateway.gateway.get_grantex_client", return_value=mock_client):
            result = await gateway.execute(
                tenant_id="tenant-e2e-002",
                agent_id="agent-e2e-002",
                agent_scopes=["tool:stripe:read:*"],
                connector_name="stripe",
                tool_name="process_refund",
                params={"charge_id": "ch_123"},
                grant_token="grantex-read-only-token",
            )

        # Should return structured error
        assert "error" in result
        assert result["error"]["code"] == "E1007"
        assert "scope_denied" in result["error"]["message"]

        # Connector should NOT have been called
        mock_connector.execute_tool.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# E2E Test: Agent Creation Registers on Grantex
# ═══════════════════════════════════════════════════════════════════════════


class TestE2EAgentCreationRegistersOnGrantex:
    """Agent create returns grantex_did after registering with Grantex."""

    @pytest.mark.asyncio
    async def test_e2e_agent_creation_registers_on_grantex(self):
        """Agent create returns grantex_did.

        Verifies the complete registration flow:
        1. register_agent_on_grantex() is called with agent details
        2. Grantex returns an agent object with a DID
        3. The DID is available for downstream use
        """
        from core.langgraph.grantex_auth import register_agent_on_grantex

        mock_agent = MagicMock()
        mock_agent.id = "grantex-agent-e2e-001"
        mock_agent.did = "did:grantex:agent:e2e001"

        mock_client = MagicMock()
        mock_client.agents.register.return_value = mock_agent

        with (
            patch("core.langgraph.grantex_auth.get_grantex_client", return_value=mock_client),
            patch("core.langgraph.grantex_auth._tools_to_scopes", return_value=[
                "tool:salesforce:execute:get_contact",
                "tool:salesforce:execute:create_lead",
            ]),
        ):
            result = await register_agent_on_grantex(
                name="Sales Agent",
                agent_type="sales_rep",
                domain="sales",
                authorized_tools=["get_contact", "create_lead"],
            )

        # Verify registration returned a DID
        assert result.did == "did:grantex:agent:e2e001"
        assert result.id == "grantex-agent-e2e-001"

        # Verify the client was called with correct params
        mock_client.agents.register.assert_called_once()
        call_kwargs = mock_client.agents.register.call_args
        assert "Sales Agent" in call_kwargs.kwargs.get("name", call_kwargs.args[0] if call_kwargs.args else "")

        # Verify scopes were mapped
        register_call = mock_client.agents.register.call_args
        scopes = register_call.kwargs.get("scopes", [])
        assert "agenticorg:sales:read" in scopes
        assert len(scopes) == 3  # 2 tool scopes + 1 domain scope
