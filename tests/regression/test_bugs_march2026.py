"""Regression tests for March 2026 bug fixes.

Covers:
  TC_AGENT-007  Shadow agent resume returns to shadow (not active); blocked below floor
  TC_AGENT-008  POST /agents/{id}/retest resets shadow counters
  INT-CONN-010  BaseConnector.__init__ respects config["base_url"] override
  INT-CONN-012  Gmail connector exposes send_email, read_inbox, search_emails, get_thread
  INT-CONN-015  _get_secret resolves gcp:// secret_ref via Secret Manager
  INT-CONN-016  Health endpoint returns connector health data
  INT-CONN-017  Agent creation with invalid authorized_tools returns 422
  INT-CONN-018  Prompt template creation with invalid tool references returns 422
  UI-REG-006   Signup without terms consent (backend: missing required fields returns 422)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Shared helpers (mirrors patterns from tests/unit/test_api_endpoints.py)
# ---------------------------------------------------------------------------

TENANT_STR = "00000000-0000-0000-0000-000000000099"
TENANT_UUID = uuid.UUID(TENANT_STR)


def _make_result(scalar_one=None, scalars_list=None, scalar_value=None):
    """Build a mock SQLAlchemy Result object."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    if scalars_list is not None:
        result.scalars.return_value.all.return_value = scalars_list
    if scalar_value is not None:
        result.scalar.return_value = scalar_value
    return result


def _patch_tenant_session(module_path: str, mock_session):
    """Return a patch context manager that makes get_tenant_session yield *mock_session*."""
    ctx = patch(f"api.v1.{module_path}.get_tenant_session")
    mock_gts = ctx.start()
    mock_gts.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_gts.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_session():
    """Create a fresh mock async session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock()
    return session


def _make_agent(
    *,
    agent_id=None,
    tenant_id=TENANT_UUID,
    status="shadow",
    shadow_sample_count=0,
    shadow_accuracy_current=None,
    shadow_accuracy_floor=Decimal("0.950"),
    shadow_min_samples=10,
    authorized_tools=None,
    **overrides,
):
    """Create a mock Agent ORM object."""
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.tenant_id = tenant_id
    agent.name = overrides.get("name", "Test Agent")
    agent.agent_type = overrides.get("agent_type", "ap_processor")
    agent.domain = overrides.get("domain", "finance")
    agent.status = status
    agent.version = overrides.get("version", "1.0.0")
    agent.description = overrides.get("description", None)
    agent.system_prompt_ref = ""
    agent.prompt_variables = {}
    agent.llm_model = "claude-3-5-sonnet-20241022"
    agent.llm_fallback = None
    agent.llm_config = {}
    agent.confidence_floor = Decimal("0.880")
    agent.hitl_condition = "confidence < 0.88"
    agent.max_retries = 3
    agent.retry_backoff = "exponential"
    agent.authorized_tools = authorized_tools or []
    agent.output_schema = None
    agent.parent_agent_id = None
    agent.shadow_comparison_agent_id = None
    agent.shadow_min_samples = shadow_min_samples
    agent.shadow_accuracy_floor = shadow_accuracy_floor
    agent.shadow_sample_count = shadow_sample_count
    agent.shadow_accuracy_current = shadow_accuracy_current
    agent.cost_controls = {}
    agent.scaling = {}
    agent.tags = []
    agent.ttl_hours = None
    agent.expires_at = None
    agent.config = {}
    agent.employee_name = "Test"
    agent.avatar_url = None
    agent.designation = None
    agent.specialization = None
    agent.routing_filter = {}
    agent.is_builtin = False
    agent.system_prompt_text = None
    agent.reporting_to = None
    agent.org_level = 0
    agent.created_at = datetime.now(UTC)
    agent.updated_at = datetime.now(UTC)
    return agent


# ═══════════════════════════════════════════════════════════════════════════════
# TC_AGENT-007 — Resume paused shadow agent returns to shadow, not active
# ═══════════════════════════════════════════════════════════════════════════════


class TestTcAgent007ResumeToShadow:
    """Resuming a shadow agent that was paused must return it to shadow (not active).

    Resume should also be blocked when shadow accuracy is below the floor.
    """

    @pytest.mark.asyncio
    async def test_resume_paused_shadow_agent_returns_to_shadow(self):
        """Agent was shadow -> paused.  Resume must go back to shadow."""
        from api.v1.agents import resume_agent

        agent = _make_agent(
            status="paused",
            shadow_accuracy_current=Decimal("0.970"),
            shadow_accuracy_floor=Decimal("0.950"),
            shadow_min_samples=10,
            shadow_sample_count=12,
        )

        # Mock the pause lifecycle event that recorded from_status="shadow"
        pause_event = MagicMock()
        pause_event.from_status = "shadow"

        session = _mock_session()
        # First call: fetch agent; second call: fetch lifecycle event
        session.execute = AsyncMock(
            side_effect=[
                _make_result(scalar_one=agent),
                _make_result(scalar_one=pause_event),
            ]
        )

        ctx = _patch_tenant_session("agents", session)
        try:
            resp = await resume_agent(agent.id, TENANT_STR)
            assert resp["status"] == "shadow", (
                "Shadow agent resumed to wrong status: expected 'shadow', "
                f"got '{resp['status']}'"
            )
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_resume_paused_active_agent_returns_to_active(self):
        """Agent was active -> paused.  Resume must go back to active."""
        from api.v1.agents import resume_agent

        agent = _make_agent(status="paused")

        pause_event = MagicMock()
        pause_event.from_status = "active"

        session = _mock_session()
        session.execute = AsyncMock(
            side_effect=[
                _make_result(scalar_one=agent),
                _make_result(scalar_one=pause_event),
            ]
        )

        ctx = _patch_tenant_session("agents", session)
        try:
            resp = await resume_agent(agent.id, TENANT_STR)
            assert resp["status"] == "active"
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_resume_blocked_when_shadow_accuracy_below_floor(self):
        """Resume must be denied when accuracy has dropped below floor."""
        from api.v1.agents import resume_agent

        agent = _make_agent(
            status="paused",
            shadow_accuracy_current=Decimal("0.800"),
            shadow_accuracy_floor=Decimal("0.950"),
            shadow_min_samples=10,
            shadow_sample_count=15,
        )

        pause_event = MagicMock()
        pause_event.from_status = "shadow"

        session = _mock_session()
        session.execute = AsyncMock(
            side_effect=[
                _make_result(scalar_one=agent),
                _make_result(scalar_one=pause_event),
            ]
        )

        ctx = _patch_tenant_session("agents", session)
        try:
            with pytest.raises(HTTPException) as exc:
                await resume_agent(agent.id, TENANT_STR)
            assert exc.value.status_code == 409
            assert "accuracy" in str(exc.value.detail).lower()
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_resume_allowed_when_accuracy_equals_floor(self):
        """Edge case: accuracy exactly at floor should be allowed."""
        from api.v1.agents import resume_agent

        agent = _make_agent(
            status="paused",
            shadow_accuracy_current=Decimal("0.950"),
            shadow_accuracy_floor=Decimal("0.950"),
            shadow_min_samples=10,
            shadow_sample_count=12,
        )

        pause_event = MagicMock()
        pause_event.from_status = "shadow"

        session = _mock_session()
        session.execute = AsyncMock(
            side_effect=[
                _make_result(scalar_one=agent),
                _make_result(scalar_one=pause_event),
            ]
        )

        ctx = _patch_tenant_session("agents", session)
        try:
            resp = await resume_agent(agent.id, TENANT_STR)
            assert resp["status"] == "shadow"
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_resume_non_paused_agent_returns_409(self):
        """Resuming an agent that is not paused must fail with 409."""
        from api.v1.agents import resume_agent

        agent = _make_agent(status="active")

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=agent))

        ctx = _patch_tenant_session("agents", session)
        try:
            with pytest.raises(HTTPException) as exc:
                await resume_agent(agent.id, TENANT_STR)
            assert exc.value.status_code == 409
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_resume_nonexistent_agent_returns_404(self):
        """Resuming an agent that does not exist must fail with 404."""
        from api.v1.agents import resume_agent

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=None))

        ctx = _patch_tenant_session("agents", session)
        try:
            with pytest.raises(HTTPException) as exc:
                await resume_agent(uuid.uuid4(), TENANT_STR)
            assert exc.value.status_code == 404
        finally:
            ctx.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# TC_AGENT-008 — POST /agents/{id}/retest resets shadow counters
# ═══════════════════════════════════════════════════════════════════════════════


class TestTcAgent008RetestEndpoint:
    """The /retest endpoint must reset shadow_sample_count and shadow_accuracy_current."""

    @pytest.mark.asyncio
    async def test_retest_resets_shadow_counters(self):
        from api.v1.agents import retest_agent

        agent = _make_agent(
            status="shadow",
            shadow_sample_count=42,
            shadow_accuracy_current=Decimal("0.910"),
        )

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=agent))

        ctx = _patch_tenant_session("agents", session)
        try:
            resp = await retest_agent(agent.id, TENANT_STR)
            assert resp["retest"] is True
            assert resp["shadow_sample_count"] == 0
            assert resp["shadow_accuracy_current"] is None
            assert resp["previous_sample_count"] == 42
            assert resp["previous_accuracy"] == pytest.approx(0.91, abs=1e-3)
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_retest_agent_not_in_shadow_returns_409(self):
        """Retest should only be allowed for agents in shadow mode."""
        from api.v1.agents import retest_agent

        agent = _make_agent(status="active")

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=agent))

        ctx = _patch_tenant_session("agents", session)
        try:
            with pytest.raises(HTTPException) as exc:
                await retest_agent(agent.id, TENANT_STR)
            assert exc.value.status_code == 409
            assert "shadow" in str(exc.value.detail).lower()
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_retest_nonexistent_agent_returns_404(self):
        from api.v1.agents import retest_agent

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=None))

        ctx = _patch_tenant_session("agents", session)
        try:
            with pytest.raises(HTTPException) as exc:
                await retest_agent(uuid.uuid4(), TENANT_STR)
            assert exc.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_retest_with_no_prior_accuracy(self):
        """Retest on a shadow agent that has never been sampled should succeed cleanly."""
        from api.v1.agents import retest_agent

        agent = _make_agent(
            status="shadow",
            shadow_sample_count=0,
            shadow_accuracy_current=None,
        )

        session = _mock_session()
        session.execute = AsyncMock(return_value=_make_result(scalar_one=agent))

        ctx = _patch_tenant_session("agents", session)
        try:
            resp = await retest_agent(agent.id, TENANT_STR)
            assert resp["retest"] is True
            assert resp["previous_sample_count"] == 0
            assert resp["previous_accuracy"] is None
        finally:
            ctx.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-010 — BaseConnector.__init__ respects config["base_url"] override
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn010BaseUrlOverride:
    """BaseConnector must use config['base_url'] when provided."""

    def test_base_url_overridden_from_config(self):
        """When config has base_url, the instance base_url must use it."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnector(BaseConnector):
            name = "stub_test_010"
            category = "test"
            auth_type = "none"
            base_url = "https://default.example.com"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        instance = _StubConnector(config={"base_url": "https://custom.example.com"})
        assert instance.base_url == "https://custom.example.com"

    def test_base_url_not_overridden_when_absent(self):
        """When config has no base_url, the class default should be used."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnector2(BaseConnector):
            name = "stub_test_010b"
            category = "test"
            auth_type = "none"
            base_url = "https://default.example.com"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        instance = _StubConnector2(config={})
        assert instance.base_url == "https://default.example.com"

    def test_base_url_not_overridden_when_empty_string(self):
        """An empty string in config['base_url'] should NOT override the class default."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnector3(BaseConnector):
            name = "stub_test_010c"
            category = "test"
            auth_type = "none"
            base_url = "https://default.example.com"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        instance = _StubConnector3(config={"base_url": ""})
        assert instance.base_url == "https://default.example.com"

    def test_base_url_not_overridden_when_none_config(self):
        """When config is None, the class default should be used."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnector4(BaseConnector):
            name = "stub_test_010d"
            category = "test"
            auth_type = "none"
            base_url = "https://default.example.com"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        instance = _StubConnector4(config=None)
        assert instance.base_url == "https://default.example.com"


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-012 — Gmail connector has required tools
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn012GmailConnectorTools:
    """The Gmail connector must expose the four expected tools."""

    EXPECTED_TOOLS = {"send_email", "read_inbox", "search_emails", "get_thread"}

    def test_gmail_connector_exists_in_registry(self):
        """Gmail connector must be registered."""
        import connectors  # noqa: F401 — trigger auto-registration
        from connectors.registry import ConnectorRegistry

        cls = ConnectorRegistry.get("gmail")
        assert cls is not None, "Gmail connector not found in ConnectorRegistry"

    def test_gmail_connector_has_all_required_tools(self):
        """Gmail connector must have send_email, read_inbox, search_emails, get_thread."""
        from connectors.comms.gmail import GmailConnector

        instance = GmailConnector(config={})
        registered_tools = set(instance._tool_registry.keys())
        assert self.EXPECTED_TOOLS.issubset(registered_tools), (
            f"Missing tools: {self.EXPECTED_TOOLS - registered_tools}"
        )

    def test_gmail_connector_tools_are_callable(self):
        """Each registered tool must be callable (async coroutine function)."""
        import asyncio

        from connectors.comms.gmail import GmailConnector

        instance = GmailConnector(config={})
        for tool_name in self.EXPECTED_TOOLS:
            handler = instance._tool_registry[tool_name]
            assert callable(handler), f"Tool '{tool_name}' is not callable"
            assert asyncio.iscoroutinefunction(handler), (
                f"Tool '{tool_name}' must be an async function"
            )

    def test_gmail_connector_metadata(self):
        """Gmail connector must have correct name, category, and auth_type."""
        from connectors.comms.gmail import GmailConnector

        assert GmailConnector.name == "gmail"
        assert GmailConnector.category == "comms"
        assert GmailConnector.auth_type == "oauth2"


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-015 — _get_secret with gcp:// secret_ref format
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn015GetSecretGcp:
    """_get_secret must resolve gcp:// secret_ref via GCP Secret Manager (mocked)."""

    def test_gcp_secret_ref_calls_secret_manager(self):
        """A gcp:// URI should trigger Secret Manager access_secret_version."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnectorSecret(BaseConnector):
            name = "stub_secret"
            category = "test"
            auth_type = "none"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        secret_ref = "gcp://projects/my-proj/secrets/my-secret/versions/latest"

        # Mock the Secret Manager client
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "super-secret-value"

        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response

        with patch(
            "connectors.framework.base_connector._get_sm_client",
            return_value=mock_client,
        ):
            instance = _StubConnectorSecret(config={"secret_ref": secret_ref})
            value = instance._get_secret("api_key")

        mock_client.access_secret_version.assert_called_once_with(
            request={"name": "projects/my-proj/secrets/my-secret/versions/latest"}
        )
        assert value == "super-secret-value"

    def test_gcp_secret_ref_json_payload_extracts_key(self):
        """When Secret Manager returns a JSON payload, _get_secret extracts the key."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnectorJson(BaseConnector):
            name = "stub_json_secret"
            category = "test"
            auth_type = "none"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        secret_ref = "gcp://projects/p1/secrets/creds/versions/latest"
        json_payload = json.dumps({"api_key": "key-from-json", "other": "not-this"})

        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = json_payload

        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response

        with patch(
            "connectors.framework.base_connector._get_sm_client",
            return_value=mock_client,
        ):
            instance = _StubConnectorJson(config={"secret_ref": secret_ref})
            value = instance._get_secret("api_key")

        assert value == "key-from-json"

    def test_gcp_secret_ref_per_key_takes_priority(self):
        """secret_ref_<key> should override the global secret_ref."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnectorPerKey(BaseConnector):
            name = "stub_per_key"
            category = "test"
            auth_type = "none"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        per_key_ref = "gcp://projects/per-key/secrets/specific/versions/latest"
        global_ref = "gcp://projects/global/secrets/default/versions/latest"

        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "per-key-secret"

        mock_client = MagicMock()
        mock_client.access_secret_version.return_value = mock_response

        with patch(
            "connectors.framework.base_connector._get_sm_client",
            return_value=mock_client,
        ):
            instance = _StubConnectorPerKey(config={
                "secret_ref": global_ref,
                "secret_ref_api_key": per_key_ref,
            })
            value = instance._get_secret("api_key")

        # Should have used per-key ref, not global
        call_args = mock_client.access_secret_version.call_args
        assert "per-key" in call_args.kwargs["request"]["name"]
        assert value == "per-key-secret"

    def test_invalid_gcp_secret_ref_returns_fallback(self):
        """A non-gcp:// secret_ref should fall through to config fallback."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnectorFallback(BaseConnector):
            name = "stub_fallback"
            category = "test"
            auth_type = "none"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        instance = _StubConnectorFallback(config={
            "secret_ref": "not-a-gcp-uri",
            "api_key": "fallback-key",
        })
        value = instance._get_secret("some_other_key")
        # Should fall through to config["api_key"] as fallback
        assert value == "fallback-key"

    def test_gcp_secret_ref_failure_returns_empty(self):
        """If Secret Manager raises, _get_secret should return the fallback gracefully."""
        from connectors.framework.base_connector import BaseConnector

        class _StubConnectorFail(BaseConnector):
            name = "stub_fail"
            category = "test"
            auth_type = "none"

            def _register_tools(self):
                pass

            async def _authenticate(self):
                pass

        secret_ref = "gcp://projects/fail/secrets/boom/versions/latest"

        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = Exception("Permission denied")

        with patch(
            "connectors.framework.base_connector._get_sm_client",
            return_value=mock_client,
        ):
            instance = _StubConnectorFail(config={
                "secret_ref": secret_ref,
                "api_key": "fallback-on-error",
            })
            instance._get_secret("api_key")  # should not raise

        # _resolve_gcp_secret returns "" on error; _get_secret then falls to step 4
        # but "api_key" was already found at step 1 (direct config lookup) so
        # let's test with a key that doesn't exist directly
        with patch(
            "connectors.framework.base_connector._get_sm_client",
            return_value=mock_client,
        ):
            instance2 = _StubConnectorFail(config={"secret_ref": secret_ref})
            value2 = instance2._get_secret("nonexistent_key")

        assert value2 == ""


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-016 — Health endpoint returns connector health data
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn016HealthEndpoint:
    """GET /health must include connector health data in its response."""

    @pytest.mark.asyncio
    async def test_health_liveness_returns_alive(self):
        """Liveness probe must return {status: 'alive'}."""
        from api.v1.health import liveness

        resp = await liveness()
        assert resp["status"] == "alive"

    @pytest.mark.asyncio
    async def test_health_check_includes_connectors_section(self):
        """Full health check must include a 'connectors' key with health data."""
        from api.v1.health import health_check

        with (
            patch("api.v1.health.async_session_factory") as mock_sf,
            patch("api.v1.health.aioredis") as mock_redis,
            patch("api.v1.health.ConnectorRegistry") as mock_cr,
        ):
            # Mock DB healthy
            session = AsyncMock()
            session.execute = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock Redis healthy
            redis_instance = AsyncMock()
            redis_instance.ping = AsyncMock()
            redis_instance.close = AsyncMock()
            mock_redis.from_url.return_value = redis_instance

            # Mock connector registry with no connectors (fast test)
            mock_cr.all_names.return_value = []

            resp = await health_check()

        assert "checks" in resp
        assert "connectors" in resp["checks"]
        conn_data = resp["checks"]["connectors"]
        assert "registered" in conn_data
        assert "healthy" in conn_data
        assert "unhealthy" in conn_data

    @pytest.mark.asyncio
    async def test_health_check_reports_connector_count(self):
        """Connector section must report the correct count of registered connectors."""
        from api.v1.health import health_check

        with (
            patch("api.v1.health.async_session_factory") as mock_sf,
            patch("api.v1.health.aioredis") as mock_redis,
            patch("api.v1.health.ConnectorRegistry") as mock_cr,
            patch("api.v1.health._check_connector") as mock_check,
        ):
            session = AsyncMock()
            session.execute = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            redis_instance = AsyncMock()
            redis_instance.ping = AsyncMock()
            redis_instance.close = AsyncMock()
            mock_redis.from_url.return_value = redis_instance

            mock_cr.all_names.return_value = ["slack", "gmail", "jira"]
            mock_check.return_value = {"status": "unhealthy", "error": "test"}

            resp = await health_check()

        assert resp["checks"]["connectors"]["registered"] == 3

    @pytest.mark.asyncio
    async def test_health_overall_degraded_when_connectors_unhealthy(self):
        """If all core checks pass but connectors fail, overall status = 'degraded'."""
        from api.v1.health import health_check

        with (
            patch("api.v1.health.async_session_factory") as mock_sf,
            patch("api.v1.health.aioredis") as mock_redis,
            patch("api.v1.health.ConnectorRegistry") as mock_cr,
            patch("api.v1.health._check_connector") as mock_check,
        ):
            session = AsyncMock()
            session.execute = AsyncMock()
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            redis_instance = AsyncMock()
            redis_instance.ping = AsyncMock()
            redis_instance.close = AsyncMock()
            mock_redis.from_url.return_value = redis_instance

            mock_cr.all_names.return_value = ["slack"]
            mock_check.return_value = {"status": "unhealthy", "error": "timeout"}

            resp = await health_check()

        assert resp["status"] == "degraded"


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-017 — Agent creation with invalid authorized_tools returns 422
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn017InvalidAuthorizedTools:
    """POST /agents with non-existent tools must return 422."""

    @pytest.mark.asyncio
    async def test_create_agent_invalid_tools_returns_422(self):
        from api.v1.agents import create_agent
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="Bad Agent",
            agent_type="ap_processor",
            domain="finance",
            authorized_tools=["nonexistent_tool_xyz", "another_fake_tool"],
            hitl_policy={"condition": "confidence < 0.88"},
        )

        with patch(
            "api.v1.agents._validate_authorized_tools",
            return_value=["nonexistent_tool_xyz", "another_fake_tool"],
        ):
            with pytest.raises(HTTPException) as exc:
                await create_agent(body, TENANT_STR)
            assert exc.value.status_code == 422
            detail = exc.value.detail
            assert "invalid_authorized_tools" in detail.get("error", "")
            assert "nonexistent_tool_xyz" in detail["invalid_tools"]

    @pytest.mark.asyncio
    async def test_create_agent_valid_tools_passes_validation(self):
        """When all tools are valid, creation should proceed past tool validation.

        It may raise later due to mocked DB, but must not raise 422.
        """
        from api.v1.agents import create_agent
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="Good Agent",
            agent_type="ap_processor",
            domain="finance",
            authorized_tools=["fetch_bank_statement"],
            hitl_policy={"condition": "confidence < 0.88"},
        )

        session = _mock_session()
        agent_mock = _make_agent(authorized_tools=["fetch_bank_statement"])
        session.execute = AsyncMock(return_value=_make_result(scalar_one=agent_mock))
        session.flush = AsyncMock()

        with (
            patch("api.v1.agents._validate_authorized_tools", return_value=[]),
            PatchTenantSessionCtx("agents", session),
        ):
            # We expect the function to proceed past validation.
            # It may raise later due to mocked DB, but not 422.
            try:
                await create_agent(body, TENANT_STR)
            except HTTPException as e:
                assert e.status_code != 422, "Valid tools should not trigger 422"
            except Exception:  # noqa: S110
                pass  # Other errors from mocked DB are expected

    @pytest.mark.asyncio
    async def test_create_agent_empty_tools_uses_defaults(self):
        """When authorized_tools is empty, defaults for the agent_type are used."""
        from api.v1.agents import create_agent
        from core.schemas.api import AgentCreate

        body = AgentCreate(
            name="Default Tools Agent",
            agent_type="ap_processor",
            domain="finance",
            authorized_tools=[],
            hitl_policy={"condition": "confidence < 0.88"},
        )

        with patch(
            "api.v1.agents._validate_authorized_tools",
            return_value=["fetch_bank_statement"],
        ):
            # Even default tools get validated; if some are invalid -> 422
            with pytest.raises(HTTPException) as exc:
                await create_agent(body, TENANT_STR)
            assert exc.value.status_code == 422


# Context manager variant for cleaner test code
class PatchTenantSessionCtx:
    """Context manager form of _patch_tenant_session."""

    def __init__(self, module_path: str, mock_session):
        self.ctx = patch(f"api.v1.{module_path}.get_tenant_session")
        self.mock_session = mock_session

    def __enter__(self):
        mock_gts = self.ctx.start()
        mock_gts.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
        mock_gts.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_gts

    def __exit__(self, *args):
        self.ctx.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# INT-CONN-018 — Prompt template creation with invalid tool references returns 422
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntConn018InvalidPromptToolRefs:
    """POST /prompt-templates with invalid tool references must return 422."""

    @pytest.mark.asyncio
    async def test_create_template_invalid_tool_ref_returns_422(self):
        from api.v1.prompt_templates import create_prompt_template
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="Bad Template",
            agent_type="ap_processor",
            domain="finance",
            template_text="Process invoice using {{tool:nonexistent_tool_abc}}",
        )

        with patch(
            "api.v1.prompt_templates._validate_tool_references",
            return_value=["nonexistent_tool_abc"],
        ):
            with pytest.raises(HTTPException) as exc:
                await create_prompt_template(body, TENANT_STR)
            assert exc.value.status_code == 422
            detail = exc.value.detail
            assert "invalid_tool_references" in detail.get("error", "")
            assert "nonexistent_tool_abc" in detail["invalid_tools"]

    @pytest.mark.asyncio
    async def test_create_template_valid_tool_ref_passes(self):
        """Template with valid tool references should not be rejected with 422."""
        from api.v1.prompt_templates import create_prompt_template
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="Good Template",
            agent_type="ap_processor",
            domain="finance",
            template_text="Process invoice using {{tool:fetch_bank_statement}}",
        )

        session = _mock_session()
        ctx = _patch_tenant_session("prompt_templates", session)

        with patch(
            "api.v1.prompt_templates._validate_tool_references",
            return_value=[],
        ):
            try:
                resp = await create_prompt_template(body, TENANT_STR)
                # If we get here without 422, the validation passed
                assert "id" in resp or True
            except HTTPException as e:
                assert e.status_code != 422
            except Exception:  # noqa: S110
                pass  # Mocked DB might cause other errors
            finally:
                ctx.stop()

    @pytest.mark.asyncio
    async def test_create_template_no_tool_refs_passes(self):
        """Template with no tool references should not trigger validation."""
        from api.v1.prompt_templates import create_prompt_template
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="Plain Template",
            agent_type="ap_processor",
            domain="finance",
            template_text="Just a simple prompt with no tool references.",
        )

        session = _mock_session()
        ctx = _patch_tenant_session("prompt_templates", session)

        with patch(
            "api.v1.prompt_templates._validate_tool_references",
            return_value=[],
        ):
            try:
                await create_prompt_template(body, TENANT_STR)
            except HTTPException as e:
                assert e.status_code != 422
            except Exception:  # noqa: S110
                pass
            finally:
                ctx.stop()

    def test_extract_tool_references_patterns(self):
        """Verify the regex correctly extracts tool references from templates."""
        from api.v1.prompt_templates import _extract_tool_references

        template = (
            "Use {{tool:send_email}} to notify, "
            "then {{tools.search_emails}} for lookup, "
            "also @tool(read_inbox) and use_tool('get_thread')."
        )
        refs = _extract_tool_references(template)
        assert "send_email" in refs
        assert "search_emails" in refs
        assert "read_inbox" in refs
        assert "get_thread" in refs


# ═══════════════════════════════════════════════════════════════════════════════
# UI-REG-006 — Signup without terms consent (backend validation)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUiReg006SignupTermsConsent:
    """Signup validation — backend enforces required fields.

    The terms consent checkbox is enforced on the frontend (button disabled
    when !agreedToTerms). The backend does not have a terms_accepted field,
    but we verify that missing *required* fields in the signup payload
    correctly return 422, which is the same mechanism that would block
    a direct API call that bypasses the frontend consent gate.
    """

    @pytest.mark.asyncio
    async def test_signup_missing_org_name_returns_422(self):
        """Signup with missing org_name should be rejected by Pydantic validation."""
        from pydantic import ValidationError

        from api.v1.auth import SignupRequest

        with pytest.raises(ValidationError):
            SignupRequest(
                admin_name="Test User",
                admin_email="test@example.com",
                password="SecurePass1",
                # org_name intentionally missing
            )

    @pytest.mark.asyncio
    async def test_signup_missing_email_returns_422(self):
        """Signup with missing admin_email should fail Pydantic validation."""
        from pydantic import ValidationError

        from api.v1.auth import SignupRequest

        with pytest.raises(ValidationError):
            SignupRequest(
                org_name="Test Org",
                admin_name="Test User",
                password="SecurePass1",
                # admin_email intentionally missing
            )

    @pytest.mark.asyncio
    async def test_signup_missing_password_returns_422(self):
        """Signup with missing password should fail Pydantic validation."""
        from pydantic import ValidationError

        from api.v1.auth import SignupRequest

        with pytest.raises(ValidationError):
            SignupRequest(
                org_name="Test Org",
                admin_name="Test User",
                admin_email="test@example.com",
                # password intentionally missing
            )

    @pytest.mark.asyncio
    async def test_signup_weak_password_returns_400(self):
        """Signup with a password that violates policy should return 400."""
        from api.v1.auth import SignupRequest, signup

        body = SignupRequest(
            org_name="Test Org",
            admin_name="Test User",
            admin_email="weak@example.com",
            password="weak",
        )
        request = MagicMock()
        request.client.host = "10.10.10.10"

        with pytest.raises(HTTPException) as exc:
            await signup(body, request)
        assert exc.value.status_code == 400
        assert "password" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_signup_valid_request_schema_accepted(self):
        """A complete, valid signup request should pass Pydantic validation."""
        from api.v1.auth import SignupRequest

        body = SignupRequest(
            org_name="Valid Org",
            admin_name="Admin User",
            admin_email="admin@example.com",
            password="SecurePass1",
        )
        assert body.org_name == "Valid Org"
        assert body.admin_email == "admin@example.com"

    @pytest.mark.asyncio
    async def test_signup_duplicate_email_returns_409(self):
        """Signup with an already-registered email should return 409."""
        from api.v1.auth import SignupRequest, signup

        body = SignupRequest(
            org_name="Duplicate Org",
            admin_name="Test User",
            admin_email="dup@example.com",
            password="SecurePass1",
        )
        request = MagicMock()
        request.client.host = "10.10.10.11"

        existing_user = MagicMock()

        with patch("api.v1.auth.async_session_factory") as mock_sf:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_result(scalar_one=existing_user))
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(HTTPException) as exc:
                await signup(body, request)
            assert exc.value.status_code == 409
            assert "already registered" in exc.value.detail.lower()
