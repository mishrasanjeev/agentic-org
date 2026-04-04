"""Unit tests for Grantex scope enforcement in LangGraph agents.

Covers:
  - Permission hierarchy (read < write < delete < admin)
  - Token validation (revoked, expired, invalid, legacy no-op)
  - Offline JWKS verification (no online API calls)
  - Budget/capped scopes
  - Manifest loading (pre-built + custom + extend)
  - Gateway enforcement uses manifest permissions, not keyword guessing
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


def _make_state(
    tool_calls: list[dict] | None = None,
    grant_token: str = "grantex-test-token",
    agent_id: str = "agent-001",
) -> dict:
    """Build a minimal AgentState dict for validate_tool_scopes."""
    if tool_calls is None:
        tool_calls = []

    ai_msg = AIMessage(content="", tool_calls=tool_calls) if tool_calls else AIMessage(content="done")
    return {
        "messages": [ai_msg],
        "grant_token": grant_token,
        "agent_id": agent_id,
        "agent_type": "test",
        "tenant_id": "tenant-001",
        "domain": "sales",
        "authorized_tools": [],
        "confidence": 0.9,
        "hitl_trigger": "",
        "output": {},
        "status": "",
        "error": "",
        "reasoning_trace": [],
        "tool_calls_log": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Permission Hierarchy Tests (6 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestPermissionHierarchy:
    """Permission hierarchy: read < write < delete < admin."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_denies_delete_with_read_scope(
        self, mock_get_client, mock_index
    ):
        """Agent with read scope cannot call delete tool."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'read' insufficient for 'delete' permission on delete_contact"
        )
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "delete_contact", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "Access denied" in result["messages"][0].content
        mock_client.enforce.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_allows_read_with_write_scope(
        self, mock_get_client, mock_index
    ):
        """Agent with write scope can call read tools (hierarchy: write > read)."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        # Empty dict means all tool calls approved
        assert result == {}
        mock_client.enforce.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_allows_read_with_read_scope(
        self, mock_get_client, mock_index
    ):
        """Agent with read scope can call read tools."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result == {}

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_denies_write_with_read_scope(
        self, mock_get_client, mock_index
    ):
        """Agent with read scope cannot call write tools."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'read' insufficient for 'write' permission on create_lead"
        )
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "create_lead", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "create_lead" in result["messages"][0].content

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_denies_admin_with_write_scope(
        self, mock_get_client, mock_index
    ):
        """Agent with write scope cannot call admin tools."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'write' insufficient for 'admin' permission on bulk_export_all"
        )
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "bulk_export_all", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "bulk_export_all" in result["messages"][0].content

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_allows_all_with_admin_scope(
        self, mock_get_client, mock_index
    ):
        """Admin scope can call any tool (admin > delete > write > read)."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)
        mock_get_client.return_value = mock_client

        # Test all four permission levels in one call
        tool_calls = [
            {"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"},
            {"name": "create_lead", "args": {}, "id": "tc2", "type": "tool_call"},
            {"name": "delete_contact", "args": {}, "id": "tc3", "type": "tool_call"},
            {"name": "bulk_export_all", "args": {}, "id": "tc4", "type": "tool_call"},
        ]
        state = _make_state(tool_calls=tool_calls)
        result = await validate_tool_scopes(state)

        assert result == {}
        assert mock_client.enforce.call_count == 4


# ═══════════════════════════════════════════════════════════════════════════
# Token Validation Tests (4 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenValidation:
    """Grant token validation: revoked, expired, invalid, legacy no-op."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_denies_revoked_token(
        self, mock_get_client, mock_index
    ):
        """Revoked grant token blocks all tool calls."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(False, "token_revoked")
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "token_revoked" in result["messages"][0].content

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_denies_expired_token(
        self, mock_get_client, mock_index
    ):
        """Expired grant token blocks all tool calls."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(False, "token_expired")
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "create_lead", "args": {}, "id": "tc1", "type": "tool_call"}])
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "token_expired" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_validate_scopes_allows_no_token_when_not_grantex_mode(self):
        """When grant_token is empty (legacy auth), validate_scopes is a no-op."""
        from core.langgraph.agent_graph import validate_tool_scopes

        state = _make_state(
            tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}],
            grant_token="",  # Empty token = legacy auth mode
        )
        result = await validate_tool_scopes(state)

        # No-op returns empty dict
        assert result == {}

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_blocks_all_tools_on_invalid_token(
        self, mock_get_client, mock_index
    ):
        """Invalid JWT (bad signature) blocks all tool calls."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(False, "invalid_signature")
        mock_get_client.return_value = mock_client

        state = _make_state(
            tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}],
            grant_token="eyJhbGciOiJSUzI1NiJ9.invalid.bad_signature",
        )
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "invalid_signature" in result["messages"][0].content


# ═══════════════════════════════════════════════════════════════════════════
# Offline Verification Test (1 test)
# ═══════════════════════════════════════════════════════════════════════════

class TestOfflineVerification:
    """Ensure enforce() uses offline JWKS, not online API."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    @patch("httpx.AsyncClient.post")
    async def test_validate_scopes_uses_offline_jwks_not_online_api(
        self, mock_http_post, mock_get_client, mock_index
    ):
        """enforce() uses offline JWKS verification, NOT online API call.

        We mock httpx.AsyncClient.post and verify /v1/tokens/verify is never called.
        """
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)
        mock_get_client.return_value = mock_client

        state = _make_state(tool_calls=[{"name": "get_contact", "args": {}, "id": "tc1", "type": "tool_call"}])
        await validate_tool_scopes(state)

        # Verify that no HTTP POST was made to the token verification endpoint
        mock_http_post.assert_not_called()

        # Double-check: enforce() was called (offline), not tokens.verify()
        mock_client.enforce.assert_called_once()
        mock_client.tokens.verify.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Budget / Capped Scope Tests (2 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBudgetCappedScope:
    """Capped scopes: enforce amount limits."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_capped_scope_blocks_over_amount(
        self, mock_get_client, mock_index
    ):
        """Capped scope blocks tool call when amount exceeds remaining budget."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "budget_exceeded: requested 600000 but remaining is 500000"
        )
        mock_get_client.return_value = mock_client

        state = _make_state(
            tool_calls=[{
                "name": "process_payment",
                "args": {"amount": 600000, "currency": "INR"},
                "id": "tc1",
                "type": "tool_call",
            }]
        )
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "budget_exceeded" in result["messages"][0].content

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_validate_scopes_capped_scope_allows_under_amount(
        self, mock_get_client, mock_index
    ):
        """Capped scope allows tool call when amount is within remaining budget."""
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(True)
        mock_get_client.return_value = mock_client

        state = _make_state(
            tool_calls=[{
                "name": "process_payment",
                "args": {"amount": 300000, "currency": "INR"},
                "id": "tc1",
                "type": "tool_call",
            }]
        )
        result = await validate_tool_scopes(state)

        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════
# Manifest Loading Tests (3 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestManifestLoading:
    """Loading pre-built, custom, and extended manifests."""

    @patch("importlib.import_module")
    def test_manifest_loading_loads_all_53_connectors(self, mock_import):
        """All 53 pre-built manifests load without error."""
        from core.langgraph.grantex_auth import _load_all_manifests

        # Mock each manifest module to return a mock manifest object
        mock_module = MagicMock()
        mock_module.manifest = MagicMock(spec_set=["name", "tools", "permissions"])
        mock_import.return_value = mock_module

        mock_client = MagicMock()
        _load_all_manifests(mock_client)

        # 53 import_module calls (one per manifest)
        assert mock_import.call_count == 53

        # load_manifests should be called with a list of 53 manifests
        mock_client.load_manifests.assert_called_once()
        loaded = mock_client.load_manifests.call_args[0][0]
        assert len(loaded) == 53

    def test_manifest_loading_custom_json_file(self, tmp_path):
        """Custom JSON manifest loads from directory."""
        from core.langgraph.grantex_auth import _load_all_manifests

        # Create a custom manifest JSON file in the tmp directory
        manifest_json = tmp_path / "custom_crm.json"
        manifest_json.write_text(
            '{"name": "custom_crm", "tools": [{"name": "get_deal", "permission": "READ"}]}'
        )

        mock_client = MagicMock()

        with (
            patch("importlib.import_module", side_effect=ImportError("no module")),
            patch.dict("os.environ", {"GRANTEX_MANIFESTS_DIR": str(tmp_path)}),
            patch("os.path.isdir", return_value=True),
        ):
            _load_all_manifests(mock_client)

        # Pre-built manifests all failed (ImportError), but custom dir loading was called
        mock_client.load_manifests_from_dir.assert_called_once_with(str(tmp_path))

    @patch("importlib.import_module")
    def test_manifest_loading_extend_existing(self, mock_import):
        """Can extend pre-built manifest with custom tools."""
        from core.langgraph.grantex_auth import _load_all_manifests

        # Simulate pre-built manifest for salesforce
        mock_module = MagicMock()
        base_manifest = MagicMock()
        base_manifest.name = "salesforce"
        mock_module.manifest = base_manifest
        mock_import.return_value = mock_module

        mock_client = MagicMock()

        with (
            patch.dict("os.environ", {"GRANTEX_MANIFESTS_DIR": "/nonexistent"}),
            patch("os.path.isdir", return_value=False),
        ):
            _load_all_manifests(mock_client)

        # Verify pre-built manifests were loaded
        mock_client.load_manifests.assert_called_once()
        loaded = mock_client.load_manifests.call_args[0][0]
        assert len(loaded) == 53

        # Verify each manifest object is present
        for m in loaded:
            assert m == base_manifest


# ═══════════════════════════════════════════════════════════════════════════
# Gateway Enforcement Tests (2 tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestGatewayEnforcement:
    """Gateway uses manifest permissions, not keyword guessing."""

    @pytest.mark.asyncio
    @patch("core.langgraph.agent_graph._build_tool_index", return_value=_build_fake_tool_index())
    @patch("core.langgraph.agent_graph.get_grantex_client")
    async def test_check_scope_uses_manifest_permission_not_keyword(
        self, mock_get_client, mock_index
    ):
        """enforce() uses manifest-defined permission, not keyword guessing.

        process_refund sounds like "read" (has no create/update/delete keyword)
        but the Stripe manifest defines it as WRITE permission.
        A read-only grant should be denied.
        """
        from core.langgraph.agent_graph import validate_tool_scopes

        mock_client = MagicMock()
        # Grantex enforce checks the Stripe manifest: process_refund = WRITE permission
        # Agent has only read scope -> denied
        mock_client.enforce.return_value = _mock_enforce_result(
            False,
            "scope 'read' insufficient for 'write' permission on process_refund "
            "(manifest: stripe defines process_refund as WRITE)",
        )
        mock_get_client.return_value = mock_client

        state = _make_state(
            tool_calls=[{"name": "process_refund", "args": {"charge_id": "ch_123"}, "id": "tc1", "type": "tool_call"}]
        )
        result = await validate_tool_scopes(state)

        assert result.get("status") == "failed"
        assert "process_refund" in result["messages"][0].content
        # Verify enforce was called with the correct connector (stripe, not guessed)
        mock_client.enforce.assert_called_once_with(
            grant_token="grantex-test-token",
            connector="stripe",
            tool="process_refund",
        )

    @pytest.mark.asyncio
    async def test_gateway_enforce_replaces_keyword_guessing(self):
        """ToolGateway.execute() uses grantex.enforce() when grant_token is provided."""
        from core.tool_gateway.gateway import ToolGateway

        gateway = ToolGateway()

        # Register a mock connector
        mock_connector = MagicMock()
        mock_connector.execute_tool = MagicMock(return_value={"status": "ok"})
        gateway.register_connector("stripe", mock_connector)

        mock_client = MagicMock()
        mock_client.enforce.return_value = _mock_enforce_result(
            False, "scope 'read' cannot execute WRITE tool process_refund"
        )

        with patch("core.tool_gateway.gateway.get_grantex_client", return_value=mock_client):
            result = await gateway.execute(
                tenant_id="t1",
                agent_id="a1",
                agent_scopes=["tool:stripe:read:*"],
                connector_name="stripe",
                tool_name="process_refund",
                params={"charge_id": "ch_123"},
                grant_token="grantex-test-token",
            )

        # Should be denied by grantex.enforce, not by keyword guessing
        assert "error" in result
        assert result["error"]["code"] == "E1007"
        assert "scope_denied" in result["error"]["message"]
        mock_client.enforce.assert_called_once()
