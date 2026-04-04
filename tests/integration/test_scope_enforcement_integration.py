"""Integration tests for Grantex scope enforcement — full flow tests.

Tests the complete path from agent state creation through tool scope validation,
delegation chains, token revocation, and budget enforcement.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

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
    """Fake tool index used across tests."""
    return {
        "delete_contact": ("salesforce", "Delete a Salesforce contact"),
        "get_contact": ("salesforce", "Get a Salesforce contact"),
        "create_lead": ("salesforce", "Create a Salesforce lead"),
        "bulk_export_all": ("salesforce", "Bulk export all data"),
        "query": ("salesforce", "Run a SOQL query"),
        "process_refund": ("stripe", "Process a refund"),
        "process_payment": ("stripe", "Process a payment"),
    }


def _make_agent_state(
    tool_calls: list[dict],
    grant_token: str = "grantex-integration-token",  # noqa: S107
    agent_id: str = "agent-int-001",
    domain: str = "sales",
) -> dict:
    """Build a full AgentState dict for integration tests."""
    ai_msg = AIMessage(content="", tool_calls=tool_calls) if tool_calls else AIMessage(content="done")
    return {
        "messages": [ai_msg],
        "grant_token": grant_token,
        "agent_id": agent_id,
        "agent_type": "integration_test",
        "tenant_id": "tenant-int-001",
        "domain": domain,
        "authorized_tools": [tc["name"] for tc in tool_calls],
        "confidence": 0.9,
        "hitl_trigger": "",
        "output": {},
        "status": "",
        "error": "",
        "reasoning_trace": [],
        "tool_calls_log": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Integration Test: Full Flow — Agent Create to Tool Denial
# ═══════════════════════════════════════════════════════════════════════════


class TestFullFlowAgentToToolDenial:
    """End-to-end: create agent state with read scope, validate against write tool -> denied."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_full_flow_agent_create_to_tool_denial(
        self, mock_get_client, mock_index
    ):
        """Create agent state with read scope, run validate_tool_scopes against a write tool -> denied."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()

        # Step 1: Simulate agent registration (returns DID)
        mock_agent = MagicMock()
        mock_agent.id = "grantex-agent-123"
        mock_agent.did = "did:grantex:agent:123"

        # Step 2: Simulate enforce — read scope, write tool -> denied
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'read' insufficient for 'write' on create_lead"
        )
        mock_get_client.return_value = mock_client

        # Step 3: Build state as the agent runner would, with a write tool call
        state = _make_agent_state(
            tool_calls=[{
                "name": "create_lead",
                "args": {"first_name": "Test", "last_name": "Lead"},
                "id": "tc-int-1",
                "type": "tool_call",
            }],
            grant_token="grantex-read-only-token",
        )

        # Step 4: Run scope validation
        result = await validate_tool_scopes(state)

        # Verify full flow: denied
        assert result.get("status") == "failed"
        assert "Access denied" in result["messages"][0].content
        assert "create_lead" in result["messages"][0].content
        assert result.get("error", "").startswith("Scope denied")


# ═══════════════════════════════════════════════════════════════════════════
# Integration Test: Delegation Scope Narrowing
# ═══════════════════════════════════════════════════════════════════════════


class TestFullFlowDelegation:
    """Parent agent delegates to child with narrowed scope."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_full_flow_delegation_scope_narrowing(
        self, mock_get_client, mock_index
    ):
        """Parent has write, child has read — child cannot write.

        Simulates the delegation chain: CFO Agent (write) -> VP AP Agent (read).
        Child's delegated token only grants read, so write calls are blocked.
        """
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()

        # Parent agent: write scope — can create leads
        mock_client.enforce.side_effect = [
            # First call: parent with write scope creating a lead -> allowed
            _mock_enforce_result(True),
            # Second call: child with read scope creating a lead -> denied
            _mock_enforce_result(
                False, "delegated scope 'read' insufficient for 'write' on create_lead"
            ),
        ]
        mock_get_client.return_value = mock_client

        # Parent: write scope succeeds
        parent_state = _make_agent_state(
            tool_calls=[{"name": "create_lead", "args": {}, "id": "tc-p1", "type": "tool_call"}],
            grant_token="grantex-parent-write-token",
            agent_id="parent-cfo-agent",
        )
        parent_result = await validate_tool_scopes(parent_state)
        assert parent_result == {}  # Approved

        # Child: read-only delegated scope fails for write tool
        child_state = _make_agent_state(
            tool_calls=[{"name": "create_lead", "args": {}, "id": "tc-c1", "type": "tool_call"}],
            grant_token="grantex-child-read-token",
            agent_id="child-vp-ap-agent",
        )
        child_result = await validate_tool_scopes(child_state)
        assert child_result.get("status") == "failed"
        assert "create_lead" in child_result["messages"][0].content


# ═══════════════════════════════════════════════════════════════════════════
# Integration Test: Token Revocation Stops Tools
# ═══════════════════════════════════════════════════════════════════════════


class TestFullFlowTokenRevocation:
    """Revoking a token stops all tool execution."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_full_flow_token_revocation_stops_tools(
        self, mock_get_client, mock_index
    ):
        """Invalid/revoked token stops all tools.

        Simulates: token worked initially, then was revoked mid-session.
        """
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()

        # First call: token is valid -> allowed
        # Second call: token has been revoked -> denied
        mock_client.enforce.side_effect = [
            _mock_enforce_result(True),  # Before revocation
            _mock_enforce_result(False, "token_revoked"),  # After revocation
        ]
        mock_get_client.return_value = mock_client

        # Before revocation: works fine
        state_before = _make_agent_state(
            tool_calls=[{"name": "get_contact", "args": {}, "id": "tc-r1", "type": "tool_call"}],
            grant_token="grantex-will-revoke-token",
        )
        result_before = await validate_tool_scopes(state_before)
        assert result_before == {}

        # After revocation: every tool blocked
        state_after = _make_agent_state(
            tool_calls=[{"name": "get_contact", "args": {}, "id": "tc-r2", "type": "tool_call"}],
            grant_token="grantex-will-revoke-token",
        )
        result_after = await validate_tool_scopes(state_after)
        assert result_after.get("status") == "failed"
        assert "token_revoked" in result_after["messages"][0].content


# ═══════════════════════════════════════════════════════════════════════════
# Integration Test: Budget Debit with Scope
# ═══════════════════════════════════════════════════════════════════════════


class TestFullFlowBudgetDebit:
    """Budget enforcement: over and under budget amounts."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_full_flow_budget_debit_with_scope(
        self, mock_get_client, mock_index
    ):
        """Amount over budget is blocked, under budget is allowed.

        Tests the full flow:
        1. Payment of 300K (budget 500K) -> allowed
        2. Payment of 600K (budget 500K) -> blocked
        """
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.side_effect = [
            # Under budget: allowed
            _mock_enforce_result(True),
            # Over budget: denied
            _mock_enforce_result(False, "budget_exceeded: 600000 > remaining 500000"),
        ]
        mock_get_client.return_value = mock_client

        # Under budget: 300K < 500K cap
        state_under = _make_agent_state(
            tool_calls=[{
                "name": "process_payment",
                "args": {"amount": 300000, "currency": "INR"},
                "id": "tc-b1",
                "type": "tool_call",
            }],
        )
        result_under = await validate_tool_scopes(state_under)
        assert result_under == {}

        # Over budget: 600K > 500K cap
        state_over = _make_agent_state(
            tool_calls=[{
                "name": "process_payment",
                "args": {"amount": 600000, "currency": "INR"},
                "id": "tc-b2",
                "type": "tool_call",
            }],
        )
        result_over = await validate_tool_scopes(state_over)
        assert result_over.get("status") == "failed"
        assert "budget_exceeded" in result_over["messages"][0].content
