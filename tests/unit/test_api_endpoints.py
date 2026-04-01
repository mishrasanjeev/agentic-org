"""Unit tests for all API v1 endpoint modules.

Tests every endpoint function with mocked DB sessions.
Target: 80+ test functions covering happy paths, empty/not-found, and edge cases.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TENANT_STR = "00000000-0000-0000-0000-000000000001"
TENANT_UUID = uuid.UUID(TENANT_STR)


@pytest.fixture
def tenant_id():
    return TENANT_STR


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock()
    return session


def _patch_tenant_session(module_path: str, mock_session):
    """Return a patch context manager that makes get_tenant_session yield *mock_session*."""
    ctx = patch(f"api.v1.{module_path}.get_tenant_session")
    mock_gts = ctx.start()
    mock_gts.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_gts.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_result(scalar_one=None, scalars_list=None, scalar_value=None):
    """Build a mock SQLAlchemy Result object."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    if scalars_list is not None:
        result.scalars.return_value.all.return_value = scalars_list
    if scalar_value is not None:
        result.scalar.return_value = scalar_value
    return result


# ============================================================================
# 1. health.py — health_check() and liveness()
# ============================================================================

class TestHealthEndpoints:

    @pytest.mark.asyncio
    async def test_liveness_returns_alive(self):
        from api.v1.health import liveness
        resp = await liveness()
        assert resp == {"status": "alive"}

    @pytest.mark.asyncio
    async def test_liveness_has_no_side_effects(self):
        from api.v1.health import liveness
        resp1 = await liveness()
        resp2 = await liveness()
        assert resp1 == resp2

    @pytest.mark.asyncio
    async def test_health_check_all_healthy(self):
        from api.v1.health import health_check

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch("api.v1.health.async_session_factory") as mock_sf, \
             patch("api.v1.health.aioredis") as mock_aioredis, \
             patch("api.v1.health.settings") as mock_settings, \
             patch("api.v1.health.ConnectorRegistry") as mock_registry:
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_aioredis.from_url.return_value = mock_redis
            mock_settings.redis_url = "redis://localhost"
            mock_settings.env = "test"
            mock_registry.all_names.return_value = []

            resp = await health_check()

        assert resp["status"] == "healthy"
        assert resp["checks"]["db"] == "healthy"
        assert resp["checks"]["redis"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_db_unhealthy(self):
        from api.v1.health import health_check

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch("api.v1.health.async_session_factory") as mock_sf, \
             patch("api.v1.health.aioredis") as mock_aioredis, \
             patch("api.v1.health.settings") as mock_settings, \
             patch("api.v1.health.ConnectorRegistry") as mock_registry:
            mock_sf.return_value.__aenter__ = AsyncMock(side_effect=ConnectionError("refused"))
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_aioredis.from_url.return_value = mock_redis
            mock_settings.redis_url = "redis://localhost"
            mock_settings.env = "test"
            mock_registry.all_names.return_value = []

            resp = await health_check()

        assert resp["status"] == "unhealthy"
        assert "unhealthy" in resp["checks"]["db"]

    @pytest.mark.asyncio
    async def test_health_check_redis_unhealthy(self):
        from api.v1.health import health_check

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("api.v1.health.async_session_factory") as mock_sf, \
             patch("api.v1.health.aioredis") as mock_aioredis, \
             patch("api.v1.health.settings") as mock_settings, \
             patch("api.v1.health.ConnectorRegistry") as mock_registry:
            mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_aioredis.from_url.return_value = mock_redis
            mock_settings.redis_url = "redis://localhost"
            mock_settings.env = "test"
            mock_registry.all_names.return_value = []

            resp = await health_check()

        assert resp["status"] == "unhealthy"
        assert "unhealthy" in resp["checks"]["redis"]
        assert resp["checks"]["db"] == "healthy"


# ============================================================================
# 2. evals.py — _load_scorecard(), get_evals(), get_agent_evals()
# ============================================================================

_SAMPLE_SCORECARD = {
    "agent_aggregates": {
        "finance": {"accuracy": 0.95},
        "hr": {"accuracy": 0.88},
    },
    "case_results": [
        {"agent_type": "finance", "case": "c1", "score": 0.9},
        {"agent_type": "finance", "case": "c2", "score": 1.0},
        {"agent_type": "hr", "case": "c3", "score": 0.85},
    ],
}


class TestEvalsEndpoints:

    @pytest.mark.asyncio
    async def test_get_evals_returns_full_scorecard(self, tmp_path):
        from api.v1.evals import get_evals

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps(_SAMPLE_SCORECARD))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            resp = await get_evals()

        assert resp == _SAMPLE_SCORECARD

    @pytest.mark.asyncio
    async def test_get_evals_file_not_found(self, tmp_path):
        from fastapi import HTTPException

        from api.v1.evals import get_evals

        missing = tmp_path / "no_such_file.json"
        with patch("api.v1.evals._SCORECARD_PATH", missing):
            with pytest.raises(HTTPException) as exc_info:
                await get_evals()
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_agent_evals_happy(self, tmp_path):
        from api.v1.evals import get_agent_evals

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps(_SAMPLE_SCORECARD))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            resp = await get_agent_evals("finance")

        assert resp["agent_type"] == "finance"
        assert resp["aggregate"] == {"accuracy": 0.95}
        assert len(resp["cases"]) == 2

    @pytest.mark.asyncio
    async def test_get_agent_evals_not_found(self, tmp_path):
        from fastapi import HTTPException

        from api.v1.evals import get_agent_evals

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps(_SAMPLE_SCORECARD))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            with pytest.raises(HTTPException) as exc_info:
                await get_agent_evals("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_agent_evals_filters_cases_correctly(self, tmp_path):
        from api.v1.evals import get_agent_evals

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps(_SAMPLE_SCORECARD))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            resp = await get_agent_evals("hr")

        assert len(resp["cases"]) == 1
        assert resp["cases"][0]["agent_type"] == "hr"


# ============================================================================
# 3. config.py — get_fleet_limits(), update_fleet_limits()
# ============================================================================

class TestConfigEndpoints:

    @pytest.mark.asyncio
    async def test_get_fleet_limits_with_stored_values(self, tenant_id, mock_session):
        from api.v1.config import get_fleet_limits

        mock_tenant = MagicMock()
        mock_tenant.settings = {
            "fleet_limits": {
                "max_active_agents": 50,
                "max_agents_per_domain": {},
                "max_shadow_agents": 10,
                "max_replicas_global_ceiling": 20,
            }
        }
        mock_session.execute.return_value = _make_result(scalar_one=mock_tenant)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            resp = await get_fleet_limits(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["max_active_agents"] == 50

    @pytest.mark.asyncio
    async def test_get_fleet_limits_defaults_when_none(self, tenant_id, mock_session):
        from api.v1.config import get_fleet_limits

        mock_tenant = MagicMock()
        mock_tenant.settings = {}
        mock_session.execute.return_value = _make_result(scalar_one=mock_tenant)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            resp = await get_fleet_limits(tenant_id=tenant_id)
        finally:
            ctx.stop()

        # Defaults from FleetLimits model
        assert resp["max_active_agents"] == 35
        assert resp["max_shadow_agents"] == 50

    @pytest.mark.asyncio
    async def test_get_fleet_limits_tenant_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.config import get_fleet_limits

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_fleet_limits(tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_get_fleet_limits_settings_is_none(self, tenant_id, mock_session):
        from api.v1.config import get_fleet_limits

        mock_tenant = MagicMock()
        mock_tenant.settings = None
        mock_session.execute.return_value = _make_result(scalar_one=mock_tenant)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            resp = await get_fleet_limits(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["max_active_agents"] == 35

    @pytest.mark.asyncio
    async def test_update_fleet_limits_happy(self, tenant_id, mock_session):
        from api.v1.config import update_fleet_limits
        from core.schemas.api import FleetLimits

        mock_tenant = MagicMock()
        mock_tenant.settings = {}
        mock_session.execute.return_value = _make_result(scalar_one=mock_tenant)

        body = FleetLimits(max_active_agents=100)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            resp = await update_fleet_limits(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["max_active_agents"] == 100

    @pytest.mark.asyncio
    async def test_update_fleet_limits_tenant_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.config import update_fleet_limits
        from core.schemas.api import FleetLimits

        mock_session.execute.return_value = _make_result(scalar_one=None)
        body = FleetLimits()

        ctx = _patch_tenant_session("config", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await update_fleet_limits(body=body, tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_update_fleet_limits_merges_into_existing_settings(self, tenant_id, mock_session):
        from api.v1.config import update_fleet_limits
        from core.schemas.api import FleetLimits

        mock_tenant = MagicMock()
        mock_tenant.settings = {"some_other_key": "value"}
        mock_session.execute.return_value = _make_result(scalar_one=mock_tenant)

        body = FleetLimits(max_active_agents=42)

        ctx = _patch_tenant_session("config", mock_session)
        try:
            resp = await update_fleet_limits(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        # The original key should still exist
        assert "some_other_key" in mock_tenant.settings
        assert mock_tenant.settings["fleet_limits"]["max_active_agents"] == 42
        assert resp["max_active_agents"] == 42


# ============================================================================
# 4. connectors.py — _connector_to_dict(), list_connectors(),
#                     register_connector(), connector_health()
# ============================================================================

def _make_connector(**overrides):
    conn = MagicMock()
    conn.id = overrides.get("id", uuid.uuid4())
    conn.name = overrides.get("name", "Slack")
    conn.category = overrides.get("category", "comms")
    conn.description = overrides.get("description", "Slack connector")
    conn.base_url = overrides.get("base_url", "https://slack.com/api")
    conn.auth_type = overrides.get("auth_type", "oauth2")
    conn.tool_functions = overrides.get("tool_functions", [])
    conn.data_schema_ref = overrides.get("data_schema_ref", None)
    conn.rate_limit_rpm = overrides.get("rate_limit_rpm", 60)
    conn.timeout_ms = overrides.get("timeout_ms", 5000)
    conn.status = overrides.get("status", "active")
    conn.health_check_at = overrides.get("health_check_at", datetime(2026, 1, 1, tzinfo=UTC))
    conn.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    conn.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return conn


class TestConnectorsEndpoints:

    def test_connector_to_dict_happy(self):
        from api.v1.connectors import _connector_to_dict
        conn = _make_connector()
        d = _connector_to_dict(conn)
        assert d["connector_id"] == str(conn.id)
        assert d["name"] == "Slack"
        assert d["status"] == "active"
        assert d["health_check_at"] is not None

    def test_connector_to_dict_no_timestamps(self):
        from api.v1.connectors import _connector_to_dict
        conn = _make_connector(health_check_at=None, created_at=None)
        d = _connector_to_dict(conn)
        assert d["health_check_at"] is None
        assert d["created_at"] is None

    def test_connector_to_dict_all_fields_present(self):
        from api.v1.connectors import _connector_to_dict
        conn = _make_connector()
        d = _connector_to_dict(conn)
        expected_keys = {
            "id", "connector_id", "name", "category", "description", "base_url",
            "auth_type", "tool_functions", "data_schema_ref", "rate_limit_rpm",
            "timeout_ms", "status", "health_check_at", "created_at",
        }
        assert set(d.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_list_connectors_happy(self, tenant_id, mock_session):
        from api.v1.connectors import list_connectors

        connectors = [_make_connector(name="Slack"), _make_connector(name="Jira")]
        # First execute returns count, second returns list
        mock_session.execute.side_effect = [
            _make_result(scalar_value=2),
            _make_result(scalars_list=connectors),
        ]

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await list_connectors(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["total"] == 2
        assert len(resp["items"]) == 2
        assert resp["page"] == 1

    @pytest.mark.asyncio
    async def test_list_connectors_empty(self, tenant_id, mock_session):
        from api.v1.connectors import list_connectors

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalars_list=[]),
        ]

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await list_connectors(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["total"] == 0
        assert resp["items"] == []

    @pytest.mark.asyncio
    async def test_list_connectors_with_category_filter(self, tenant_id, mock_session):
        from api.v1.connectors import list_connectors

        connectors = [_make_connector(category="crm")]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=connectors),
        ]

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await list_connectors(category="crm", tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["total"] == 1

    @pytest.mark.asyncio
    async def test_list_connectors_pagination(self, tenant_id, mock_session):
        from api.v1.connectors import list_connectors

        mock_session.execute.side_effect = [
            _make_result(scalar_value=100),
            _make_result(scalars_list=[_make_connector()]),
        ]

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await list_connectors(page=3, per_page=10, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["page"] == 3
        assert resp["per_page"] == 10
        assert resp["total"] == 100

    @pytest.mark.asyncio
    async def test_register_connector_happy(self, tenant_id, mock_session):
        from api.v1.connectors import register_connector
        from core.schemas.api import ConnectorCreate

        body = ConnectorCreate(
            name="Hubspot",
            category="crm",
            auth_type="api_key",
            base_url="https://api.hubspot.com",
        )

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await register_connector(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        assert resp["name"] == "Hubspot"

    @pytest.mark.asyncio
    async def test_register_connector_sets_status_active(self, tenant_id, mock_session):
        from api.v1.connectors import register_connector
        from core.schemas.api import ConnectorCreate

        body = ConnectorCreate(name="Test", category="test", auth_type="none")

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await register_connector(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["status"] == "active"

    @pytest.mark.asyncio
    async def test_connector_health_happy(self, tenant_id, mock_session):
        from api.v1.connectors import connector_health

        conn = _make_connector(status="active")
        mock_session.execute.return_value = _make_result(scalar_one=conn)

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await connector_health(conn_id=conn.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["healthy"] is True
        assert resp["status"] == "active"

    @pytest.mark.asyncio
    async def test_connector_health_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.connectors import connector_health

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await connector_health(conn_id=uuid.uuid4(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_connector_health_inactive(self, tenant_id, mock_session):
        from api.v1.connectors import connector_health

        conn = _make_connector(status="inactive")
        mock_session.execute.return_value = _make_result(scalar_one=conn)

        ctx = _patch_tenant_session("connectors", mock_session)
        try:
            resp = await connector_health(conn_id=conn.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["healthy"] is False


# ============================================================================
# 5. approvals.py — _hitl_to_dict(), list_approvals(), decide()
# ============================================================================

def _make_hitl(**overrides):
    item = MagicMock()
    item.id = overrides.get("id", uuid.uuid4())
    item.workflow_run_id = overrides.get("workflow_run_id", uuid.uuid4())
    item.agent_id = overrides.get("agent_id", uuid.uuid4())
    item.title = overrides.get("title", "Approve payment")
    item.trigger_type = overrides.get("trigger_type", "threshold")
    item.priority = overrides.get("priority", "high")
    item.status = overrides.get("status", "pending")
    item.assignee_role = overrides.get("assignee_role", "manager")
    item.decision_options = overrides.get("decision_options", {"approve": True, "reject": True})
    item.context = overrides.get("context", {"amount": 5000})
    item.decision = overrides.get("decision", None)
    item.decision_by = overrides.get("decision_by", None)
    item.decision_at = overrides.get("decision_at", None)
    item.decision_notes = overrides.get("decision_notes", None)
    item.expires_at = overrides.get("expires_at", datetime(2099, 1, 1, tzinfo=UTC))
    item.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    item.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return item


class TestApprovalsEndpoints:

    def test_hitl_to_dict_happy(self):
        from api.v1.approvals import _hitl_to_dict
        item = _make_hitl()
        d = _hitl_to_dict(item)
        assert d["id"] == str(item.id)
        assert d["title"] == "Approve payment"
        assert d["status"] == "pending"
        assert d["decision"] is None

    def test_hitl_to_dict_with_decision(self):
        from api.v1.approvals import _hitl_to_dict
        item = _make_hitl(
            decision="approve",
            decision_by=uuid.uuid4(),
            decision_at=datetime(2026, 3, 1, tzinfo=UTC),
            decision_notes="LGTM",
        )
        d = _hitl_to_dict(item)
        assert d["decision"] == "approve"
        assert d["decision_notes"] == "LGTM"
        assert d["decision_by"] is not None

    def test_hitl_to_dict_none_timestamps(self):
        from api.v1.approvals import _hitl_to_dict
        item = _make_hitl(decision_at=None, expires_at=None, created_at=None, decision_by=None)
        d = _hitl_to_dict(item)
        assert d["decision_at"] is None
        assert d["expires_at"] is None
        assert d["created_at"] is None
        assert d["decision_by"] is None

    @pytest.mark.asyncio
    async def test_list_approvals_happy(self, tenant_id, mock_session):
        from api.v1.approvals import list_approvals

        items = [_make_hitl(), _make_hitl()]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=2),
            _make_result(scalars_list=items),
        ]

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            resp = await list_approvals(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 2
        assert len(resp.items) == 2

    @pytest.mark.asyncio
    async def test_list_approvals_empty(self, tenant_id, mock_session):
        from api.v1.approvals import list_approvals

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalars_list=[]),
        ]

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            resp = await list_approvals(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 0
        assert resp.items == []

    @pytest.mark.asyncio
    async def test_list_approvals_with_priority_filter(self, tenant_id, mock_session):
        from api.v1.approvals import list_approvals

        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=[_make_hitl(priority="critical")]),
        ]

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            resp = await list_approvals(priority="critical", tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_list_approvals_with_status_filter(self, tenant_id, mock_session):
        from api.v1.approvals import list_approvals

        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=[_make_hitl(status="decided")]),
        ]

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            resp = await list_approvals(status="decided", tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_decide_happy(self, tenant_id, mock_session):
        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        item = _make_hitl(status="pending")
        mock_session.execute.return_value = _make_result(scalar_one=item)

        body = HITLDecision(decision="approve", notes="Looks good")

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            resp = await decide(hitl_id=item.id, body=body, background_tasks=BackgroundTasks(), tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["decision"] == "approve"
        assert resp["status"] == "decided"

    @pytest.mark.asyncio
    async def test_decide_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        mock_session.execute.return_value = _make_result(scalar_one=None)
        body = HITLDecision(decision="approve")

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await decide(hitl_id=uuid.uuid4(), body=body, background_tasks=BackgroundTasks(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_decide_already_resolved(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        item = _make_hitl(status="decided")
        mock_session.execute.return_value = _make_result(scalar_one=item)
        body = HITLDecision(decision="approve")

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await decide(hitl_id=item.id, body=body, background_tasks=BackgroundTasks(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 409
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_decide_expired(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.approvals import decide
        from core.schemas.api import HITLDecision

        item = _make_hitl(
            status="pending",
            expires_at=datetime(2020, 1, 1, tzinfo=UTC),  # past
        )
        mock_session.execute.return_value = _make_result(scalar_one=item)
        body = HITLDecision(decision="approve")

        ctx = _patch_tenant_session("approvals", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await decide(hitl_id=item.id, body=body, background_tasks=BackgroundTasks(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 410
        finally:
            ctx.stop()


# ============================================================================
# 6. compliance.py — dsar_access, dsar_erase, dsar_export, evidence_package
# ============================================================================

class TestComplianceEndpoints:

    @pytest.mark.asyncio
    async def test_dsar_access_happy(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_access
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_access(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["type"] == "access"
        assert resp["status"] == "processing"
        assert resp["subject_email"] == "user@example.com"
        assert "request_id" in resp

    @pytest.mark.asyncio
    async def test_dsar_access_creates_audit_entry(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_access
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            await dsar_access(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dsar_access_unique_request_ids(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_access
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp1 = await dsar_access(body=body, tenant_id=tenant_id)
            resp2 = await dsar_access(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp1["request_id"] != resp2["request_id"]

    @pytest.mark.asyncio
    async def test_dsar_erase_happy(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_erase
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_erase(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["type"] == "erase"
        assert resp["status"] == "processing"
        assert resp["deadline_days"] == 30

    @pytest.mark.asyncio
    async def test_dsar_erase_has_deadline(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_erase
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_erase(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert "deadline" in resp
        assert resp["deadline_days"] == 30

    @pytest.mark.asyncio
    async def test_dsar_erase_creates_audit_entry(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_erase
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            await dsar_erase(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_dsar_export_happy(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_export
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="user@example.com")
        # _create_dsar_audit_entry uses add+flush (not execute), then
        # dsar_export calls execute once for the count query
        mock_session.execute.return_value = _make_result(scalar_value=500)

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_export(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["type"] == "export"
        assert resp["format"] == "json"
        assert resp["estimated_records"] == 500
        assert resp["estimated_size_mb"] == 1.0  # 500 * 0.002

    @pytest.mark.asyncio
    async def test_dsar_export_zero_records(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_export
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="nobody@example.com")
        mock_session.execute.return_value = _make_result(scalar_value=0)

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_export(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["estimated_records"] == 0
        assert resp["estimated_size_mb"] == 0.0

    @pytest.mark.asyncio
    async def test_dsar_export_large_dataset(self, tenant_id, mock_session):
        from api.v1.compliance import dsar_export
        from core.schemas.api import DSARRequest

        body = DSARRequest(subject_email="power@example.com")
        mock_session.execute.return_value = _make_result(scalar_value=10000)

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await dsar_export(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["estimated_records"] == 10000
        assert resp["estimated_size_mb"] == 20.0

    @pytest.mark.asyncio
    async def test_evidence_package_happy(self, tenant_id, mock_session):
        from api.v1.compliance import evidence_package

        mock_session.execute.side_effect = [
            _make_result(scalar_value=10),   # access_controls
            _make_result(scalar_value=100),  # audit_total
            _make_result(scalar_value=5),    # deploy_count
            _make_result(scalar_value=2),    # incident_count
        ]

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await evidence_package(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert "package_id" in resp
        assert resp["tenant_id"] == tenant_id
        assert resp["sections"]["access_controls"]["event_count"] == 10
        assert resp["sections"]["audit_logs"]["total_entries"] == 100
        assert resp["sections"]["deployment_records"]["event_count"] == 5
        assert resp["sections"]["incident_history"]["event_count"] == 2

    @pytest.mark.asyncio
    async def test_evidence_package_all_zeros(self, tenant_id, mock_session):
        from api.v1.compliance import evidence_package

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
        ]

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await evidence_package(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["sections"]["access_controls"]["event_count"] == 0
        assert resp["sections"]["audit_logs"]["total_entries"] == 0

    @pytest.mark.asyncio
    async def test_evidence_package_has_generated_at(self, tenant_id, mock_session):
        from api.v1.compliance import evidence_package

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
            _make_result(scalar_value=0),
        ]

        ctx = _patch_tenant_session("compliance", mock_session)
        try:
            resp = await evidence_package(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert "generated_at" in resp


# ============================================================================
# 7. schemas.py — _schema_to_dict(), list_schemas(), create_schema(),
#                  upsert_schema()
# ============================================================================

def _make_schema_row(**overrides):
    s = MagicMock()
    s.id = overrides.get("id", uuid.uuid4())
    s.name = overrides.get("name", "invoice_v1")
    s.version = overrides.get("version", "1")
    s.description = overrides.get("description", "Invoice schema")
    s.json_schema = overrides.get("json_schema", {"type": "object"})
    s.is_default = overrides.get("is_default", False)
    s.created_by = overrides.get("created_by", uuid.uuid4())
    s.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    s.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return s


class TestSchemasEndpoints:

    def test_schema_to_dict_happy(self):
        from api.v1.schemas import _schema_to_dict
        s = _make_schema_row()
        d = _schema_to_dict(s)
        assert d["name"] == "invoice_v1"
        assert d["version"] == "1"
        assert d["json_schema"] == {"type": "object"}

    def test_schema_to_dict_no_created_by(self):
        from api.v1.schemas import _schema_to_dict
        s = _make_schema_row(created_by=None, created_at=None)
        d = _schema_to_dict(s)
        assert d["created_by"] is None
        assert d["created_at"] is None

    def test_schema_to_dict_all_fields(self):
        from api.v1.schemas import _schema_to_dict
        s = _make_schema_row()
        d = _schema_to_dict(s)
        expected_keys = {"id", "name", "version", "description", "json_schema",
                         "is_default", "created_by", "created_at"}
        assert set(d.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_list_schemas_happy(self, tenant_id, mock_session):
        from api.v1.schemas import list_schemas

        schemas = [_make_schema_row(), _make_schema_row(name="order_v1")]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=2),
            _make_result(scalars_list=schemas),
        ]

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await list_schemas(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp.total == 2
        assert len(resp.items) == 2

    @pytest.mark.asyncio
    async def test_list_schemas_empty(self, tenant_id, mock_session):
        from api.v1.schemas import list_schemas

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalars_list=[]),
        ]

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await list_schemas(tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp.total == 0
        assert resp.items == []
        assert resp.pages == 1  # minimum 1 page

    @pytest.mark.asyncio
    async def test_list_schemas_pagination(self, tenant_id, mock_session):
        from api.v1.schemas import list_schemas

        mock_session.execute.side_effect = [
            _make_result(scalar_value=50),
            _make_result(scalars_list=[_make_schema_row()]),
        ]

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await list_schemas(page=2, per_page=10, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp.page == 2
        assert resp.per_page == 10
        assert resp.pages == 5  # ceil(50/10)

    @pytest.mark.asyncio
    async def test_create_schema_happy(self, tenant_id, mock_session):
        from api.v1.schemas import create_schema
        from core.schemas.api import SchemaCreate

        body = SchemaCreate(
            name="payment_v1",
            json_schema={"type": "object", "properties": {"amount": {"type": "number"}}},
        )

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await create_schema(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        assert resp["name"] == "payment_v1"

    @pytest.mark.asyncio
    async def test_create_schema_with_defaults(self, tenant_id, mock_session):
        from api.v1.schemas import create_schema
        from core.schemas.api import SchemaCreate

        body = SchemaCreate(
            name="default_schema",
            json_schema={"type": "object"},
            is_default=True,
        )

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await create_schema(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["is_default"] is True

    @pytest.mark.asyncio
    async def test_upsert_schema_creates_new(self, tenant_id, mock_session):
        from api.v1.schemas import upsert_schema
        from core.schemas.api import SchemaCreate

        mock_session.execute.return_value = _make_result(scalar_one=None)
        body = SchemaCreate(
            name="new_schema",
            version="1",
            json_schema={"type": "object"},
        )

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await upsert_schema(name="new_schema", body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["created"] is True
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_schema_updates_existing(self, tenant_id, mock_session):
        from api.v1.schemas import upsert_schema
        from core.schemas.api import SchemaCreate

        existing = _make_schema_row(name="existing", version="1")
        mock_session.execute.return_value = _make_result(scalar_one=existing)
        body = SchemaCreate(
            name="existing",
            version="1",
            description="Updated",
            json_schema={"type": "array"},
        )

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            resp = await upsert_schema(name="existing", body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["created"] is False
        assert existing.description == "Updated"
        assert existing.json_schema == {"type": "array"}

    @pytest.mark.asyncio
    async def test_upsert_schema_updates_is_default(self, tenant_id, mock_session):
        from api.v1.schemas import upsert_schema
        from core.schemas.api import SchemaCreate

        existing = _make_schema_row(name="test", version="1", is_default=False)
        mock_session.execute.return_value = _make_result(scalar_one=existing)
        body = SchemaCreate(
            name="test",
            version="1",
            json_schema={"type": "object"},
            is_default=True,
        )

        ctx = _patch_tenant_session("schemas", mock_session)
        try:
            await upsert_schema(name="test", body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert existing.is_default is True


# ============================================================================
# 8. audit.py — _audit_to_dict(), query_audit()
# ============================================================================

def _make_audit_entry(**overrides):
    entry = MagicMock()
    entry.id = overrides.get("id", uuid.uuid4())
    entry.event_type = overrides.get("event_type", "agent.action")
    entry.actor_type = overrides.get("actor_type", "agent")
    entry.actor_id = overrides.get("actor_id", "finance-bot")
    entry.agent_id = overrides.get("agent_id", uuid.uuid4())
    entry.workflow_run_id = overrides.get("workflow_run_id", uuid.uuid4())
    entry.resource_type = overrides.get("resource_type", "invoice")
    entry.resource_id = overrides.get("resource_id", "INV-001")
    entry.action = overrides.get("action", "process_invoice")
    entry.outcome = overrides.get("outcome", "success")
    entry.details = overrides.get("details", {"key": "value"})
    entry.trace_id = overrides.get("trace_id", "trace-123")
    entry.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    entry.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return entry


class TestAuditEndpoints:

    def test_audit_to_dict_happy(self):
        from api.v1.audit import _audit_to_dict
        entry = _make_audit_entry()
        d = _audit_to_dict(entry)
        assert d["event_type"] == "agent.action"
        assert d["action"] == "process_invoice"
        assert d["outcome"] == "success"

    def test_audit_to_dict_nullable_fields(self):
        from api.v1.audit import _audit_to_dict
        entry = _make_audit_entry(agent_id=None, workflow_run_id=None, created_at=None)
        d = _audit_to_dict(entry)
        assert d["agent_id"] is None
        assert d["workflow_run_id"] is None
        assert d["created_at"] is None

    def test_audit_to_dict_all_fields(self):
        from api.v1.audit import _audit_to_dict
        entry = _make_audit_entry()
        d = _audit_to_dict(entry)
        expected_keys = {
            "id", "event_type", "actor_type", "actor_id", "agent_id",
            "workflow_run_id", "resource_type", "resource_id", "action",
            "outcome", "details", "trace_id", "created_at",
        }
        assert set(d.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_query_audit_happy(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        entries = [_make_audit_entry(), _make_audit_entry()]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=2),
            _make_result(scalars_list=entries),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                tenant_id=tenant_id, user_domains=None, user_role="admin",
            )
        finally:
            ctx.stop()

        assert resp.total == 2
        assert len(resp.items) == 2

    @pytest.mark.asyncio
    async def test_query_audit_empty(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalars_list=[]),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                tenant_id=tenant_id, user_domains=None, user_role="admin",
            )
        finally:
            ctx.stop()

        assert resp.total == 0
        assert resp.items == []
        assert resp.pages == 1

    @pytest.mark.asyncio
    async def test_query_audit_with_event_type_filter(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        entries = [_make_audit_entry(event_type="auth.login")]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=entries),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                event_type="auth.login",
                tenant_id=tenant_id,
                user_domains=None,
                user_role="admin",
            )
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_query_audit_with_agent_id_filter(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        agent_id = str(uuid.uuid4())
        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=[_make_audit_entry()]),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                agent_id=agent_id,
                tenant_id=tenant_id,
                user_domains=None,
                user_role="admin",
            )
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_query_audit_with_date_filters(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        mock_session.execute.side_effect = [
            _make_result(scalar_value=5),
            _make_result(scalars_list=[_make_audit_entry()] * 5),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                date_from="2026-01-01T00:00:00",
                date_to="2026-12-31T23:59:59",
                tenant_id=tenant_id,
                user_domains=None,
                user_role="admin",
            )
        finally:
            ctx.stop()

        assert resp.total == 5

    @pytest.mark.asyncio
    async def test_query_audit_domain_filtered_non_auditor(self, tenant_id, mock_session):
        from api.v1.audit import query_audit

        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=[_make_audit_entry()]),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                tenant_id=tenant_id,
                user_domains=["finance"],
                user_role="domain_head",
            )
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_query_audit_auditor_sees_all(self, tenant_id, mock_session):
        """Auditor role should bypass domain filtering."""
        from api.v1.audit import query_audit

        mock_session.execute.side_effect = [
            _make_result(scalar_value=10),
            _make_result(scalars_list=[_make_audit_entry()] * 10),
        ]

        ctx = _patch_tenant_session("audit", mock_session)
        try:
            resp = await query_audit(
                tenant_id=tenant_id,
                user_domains=["finance"],
                user_role="auditor",
            )
        finally:
            ctx.stop()

        assert resp.total == 10


# ============================================================================
# 9. workflows.py — serializers + endpoints
# ============================================================================

def _make_workflow_def(**overrides):
    wf = MagicMock()
    wf.id = overrides.get("id", uuid.uuid4())
    wf.name = overrides.get("name", "Invoice Processing")
    wf.version = overrides.get("version", "1.0")
    wf.description = overrides.get("description", "Process invoices")
    wf.domain = overrides.get("domain", "finance")
    wf.trigger_type = overrides.get("trigger_type", "manual")
    wf.trigger_config = overrides.get("trigger_config", {})
    wf.is_active = overrides.get("is_active", True)
    wf.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    wf.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    wf.definition = overrides.get("definition", {"steps": [{"id": "s1"}, {"id": "s2"}]})
    return wf


def _make_step_execution(**overrides):
    step = MagicMock()
    step.id = overrides.get("id", uuid.uuid4())
    step.step_id = overrides.get("step_id", "step-1")
    step.step_type = overrides.get("step_type", "agent")
    step.agent_id = overrides.get("agent_id", uuid.uuid4())
    step.status = overrides.get("status", "completed")
    step.input = overrides.get("input", {"data": "test"})
    step.output = overrides.get("output", {"result": "ok"})
    step.confidence = overrides.get("confidence", 0.95)
    step.error = overrides.get("error", None)
    step.retry_count = overrides.get("retry_count", 0)
    step.latency_ms = overrides.get("latency_ms", 150)
    step.started_at = overrides.get("started_at", datetime(2026, 1, 1, tzinfo=UTC))
    step.completed_at = overrides.get("completed_at", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC))
    return step


def _make_workflow_run(**overrides):
    run = MagicMock()
    run.id = overrides.get("id", uuid.uuid4())
    run.workflow_def_id = overrides.get("workflow_def_id", uuid.uuid4())
    run.status = overrides.get("status", "running")
    run.trigger_payload = overrides.get("trigger_payload", {})
    run.context = overrides.get("context", {})
    run.result = overrides.get("result", None)
    run.error = overrides.get("error", None)
    run.steps_total = overrides.get("steps_total", 3)
    run.steps_completed = overrides.get("steps_completed", 1)
    run.started_at = overrides.get("started_at", datetime(2026, 1, 1, tzinfo=UTC))
    run.completed_at = overrides.get("completed_at", None)
    run.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    run.steps = overrides.get("steps", [])
    run.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return run


class TestWorkflowsEndpoints:

    def test_wf_to_dict_happy(self):
        from api.v1.workflows import _wf_to_dict
        wf = _make_workflow_def()
        d = _wf_to_dict(wf)
        assert d["name"] == "Invoice Processing"
        assert d["is_active"] is True
        assert d["domain"] == "finance"

    def test_wf_to_dict_none_created_at(self):
        from api.v1.workflows import _wf_to_dict
        wf = _make_workflow_def(created_at=None)
        d = _wf_to_dict(wf)
        assert d["created_at"] is None

    def test_step_to_dict_happy(self):
        from api.v1.workflows import _step_to_dict
        step = _make_step_execution()
        d = _step_to_dict(step)
        assert d["step_id"] == "step-1"
        assert d["confidence"] == 0.95
        assert d["latency_ms"] == 150

    def test_step_to_dict_nullable_fields(self):
        from api.v1.workflows import _step_to_dict
        step = _make_step_execution(
            agent_id=None, confidence=None,
            started_at=None, completed_at=None,
        )
        d = _step_to_dict(step)
        assert d["agent_id"] is None
        assert d["confidence"] is None
        assert d["started_at"] is None
        assert d["completed_at"] is None

    def test_run_to_dict_without_steps(self):
        from api.v1.workflows import _run_to_dict
        run = _make_workflow_run()
        d = _run_to_dict(run, include_steps=False)
        assert "steps" not in d
        assert d["status"] == "running"

    def test_run_to_dict_with_steps(self):
        from api.v1.workflows import _run_to_dict
        steps = [_make_step_execution(), _make_step_execution(step_id="step-2")]
        run = _make_workflow_run(steps=steps)
        d = _run_to_dict(run, include_steps=True)
        assert "steps" in d
        assert len(d["steps"]) == 2

    def test_run_to_dict_empty_steps_list(self):
        from api.v1.workflows import _run_to_dict
        run = _make_workflow_run(steps=[])
        d = _run_to_dict(run, include_steps=True)
        # steps key is always present when include_steps=True (even if empty)
        assert "steps" in d
        assert d["steps"] == []

    def test_run_to_dict_none_timestamps(self):
        from api.v1.workflows import _run_to_dict
        run = _make_workflow_run(started_at=None, completed_at=None, created_at=None)
        d = _run_to_dict(run, include_steps=False)
        assert d["started_at"] is None
        assert d["completed_at"] is None
        assert d["created_at"] is None

    @pytest.mark.asyncio
    async def test_list_workflows_happy(self, tenant_id, mock_session):
        from api.v1.workflows import list_workflows

        wfs = [_make_workflow_def(), _make_workflow_def(name="HR Onboarding")]
        mock_session.execute.side_effect = [
            _make_result(scalar_value=2),
            _make_result(scalars_list=wfs),
        ]

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await list_workflows(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 2
        assert len(resp.items) == 2

    @pytest.mark.asyncio
    async def test_list_workflows_empty(self, tenant_id, mock_session):
        from api.v1.workflows import list_workflows

        mock_session.execute.side_effect = [
            _make_result(scalar_value=0),
            _make_result(scalars_list=[]),
        ]

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await list_workflows(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp.total == 0
        assert resp.items == []

    @pytest.mark.asyncio
    async def test_list_workflows_domain_filter(self, tenant_id, mock_session):
        from api.v1.workflows import list_workflows

        mock_session.execute.side_effect = [
            _make_result(scalar_value=1),
            _make_result(scalars_list=[_make_workflow_def(domain="finance")]),
        ]

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await list_workflows(tenant_id=tenant_id, user_domains=["finance"])
        finally:
            ctx.stop()

        assert resp.total == 1

    @pytest.mark.asyncio
    async def test_get_workflow_happy(self, tenant_id, mock_session):
        from api.v1.workflows import get_workflow

        wf = _make_workflow_def()
        mock_session.execute.return_value = _make_result(scalar_one=wf)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await get_workflow(wf_id=wf.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["name"] == "Invoice Processing"

    @pytest.mark.asyncio
    async def test_get_workflow_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.workflows import get_workflow

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_workflow(wf_id=uuid.uuid4(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_create_workflow_happy(self, tenant_id, mock_session):
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        body = WorkflowCreate(
            name="Test WF",
            definition={"steps": [{"id": "s1"}]},
        )

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await create_workflow(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        assert resp["name"] == "Test WF"

    @pytest.mark.asyncio
    async def test_create_workflow_with_optional_fields(self, tenant_id, mock_session):
        from api.v1.workflows import create_workflow
        from core.schemas.api import WorkflowCreate

        body = WorkflowCreate(
            name="Full WF",
            version="2.0",
            description="Full workflow",
            domain="hr",
            definition={"steps": [{"id": "s1", "type": "agent", "agent_type": "payroll_engine"}]},
            trigger_type="schedule",
            trigger_config={"cron": "0 * * * *"},
        )

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await create_workflow(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["name"] == "Full WF"
        assert resp["version"] == "2.0"

    @pytest.mark.asyncio
    async def test_run_workflow_happy(self, tenant_id, mock_session):
        from api.v1.workflows import run_workflow
        from core.schemas.api import WorkflowRunTrigger

        wf = _make_workflow_def(is_active=True)
        mock_session.execute.return_value = _make_result(scalar_one=wf)

        body = WorkflowRunTrigger(payload={"key": "value"})

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await run_workflow(wf_id=wf.id, background_tasks=BackgroundTasks(), body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["status"] == "running"
        assert resp["workflow_def_id"] == str(wf.id)

    @pytest.mark.asyncio
    async def test_run_workflow_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.workflows import run_workflow

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await run_workflow(wf_id=uuid.uuid4(), background_tasks=BackgroundTasks(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_run_workflow_inactive(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.workflows import run_workflow

        wf = _make_workflow_def(is_active=False)
        mock_session.execute.return_value = _make_result(scalar_one=wf)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await run_workflow(wf_id=wf.id, background_tasks=BackgroundTasks(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 409
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_run_workflow_no_body(self, tenant_id, mock_session):
        """When body is None, it should default to WorkflowRunTrigger()."""
        from api.v1.workflows import run_workflow

        wf = _make_workflow_def(is_active=True)
        mock_session.execute.return_value = _make_result(scalar_one=wf)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await run_workflow(wf_id=wf.id, background_tasks=BackgroundTasks(), body=None, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["status"] == "running"

    @pytest.mark.asyncio
    async def test_get_workflow_run_happy(self, tenant_id, mock_session):
        from api.v1.workflows import get_workflow_run

        steps = [_make_step_execution()]
        run = _make_workflow_run(steps=steps)
        mock_session.execute.return_value = _make_result(scalar_one=run)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            resp = await get_workflow_run(run_id=run.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["status"] == "running"
        assert "steps" in resp

    @pytest.mark.asyncio
    async def test_get_workflow_run_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.workflows import get_workflow_run

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("workflows", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_workflow_run(run_id=uuid.uuid4(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()


# ============================================================================
# 10. prompt_templates.py — all CRUD endpoints
# ============================================================================

def _make_prompt_template(**overrides):
    t = MagicMock()
    t.id = overrides.get("id", uuid.uuid4())
    t.name = overrides.get("name", "Invoice Classifier")
    t.agent_type = overrides.get("agent_type", "finance")
    t.domain = overrides.get("domain", "finance")
    t.template_text = overrides.get("template_text", "Classify this invoice: {text}")
    t.variables = overrides.get("variables", [{"name": "text", "type": "string"}])
    t.description = overrides.get("description", "Classifies invoices")
    t.is_builtin = overrides.get("is_builtin", False)
    t.is_active = overrides.get("is_active", True)
    t.created_by = overrides.get("created_by", uuid.uuid4())
    t.created_at = overrides.get("created_at", datetime(2026, 1, 1, tzinfo=UTC))
    t.updated_at = overrides.get("updated_at", datetime(2026, 1, 2, tzinfo=UTC))
    t.tenant_id = overrides.get("tenant_id", TENANT_UUID)
    return t


class TestPromptTemplatesEndpoints:

    def test_template_to_dict_happy(self):
        from api.v1.prompt_templates import _template_to_dict
        t = _make_prompt_template()
        d = _template_to_dict(t)
        assert d["name"] == "Invoice Classifier"
        assert d["agent_type"] == "finance"
        assert d["is_builtin"] is False

    def test_template_to_dict_nullable_fields(self):
        from api.v1.prompt_templates import _template_to_dict
        t = _make_prompt_template(created_by=None, created_at=None, updated_at=None)
        d = _template_to_dict(t)
        assert d["created_by"] is None
        assert d["created_at"] is None
        assert d["updated_at"] is None

    def test_template_to_dict_all_fields(self):
        from api.v1.prompt_templates import _template_to_dict
        t = _make_prompt_template()
        d = _template_to_dict(t)
        expected_keys = {
            "id", "name", "agent_type", "domain", "template_text",
            "variables", "description", "is_builtin", "is_active",
            "created_by", "created_at", "updated_at",
        }
        assert set(d.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_list_prompt_templates_happy(self, tenant_id, mock_session):
        from api.v1.prompt_templates import list_prompt_templates

        templates = [_make_prompt_template(), _make_prompt_template(name="HR Bot")]
        mock_session.execute.return_value = _make_result(scalars_list=templates)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await list_prompt_templates(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert len(resp) == 2

    @pytest.mark.asyncio
    async def test_list_prompt_templates_empty(self, tenant_id, mock_session):
        from api.v1.prompt_templates import list_prompt_templates

        mock_session.execute.return_value = _make_result(scalars_list=[])

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await list_prompt_templates(tenant_id=tenant_id, user_domains=None)
        finally:
            ctx.stop()

        assert resp == []

    @pytest.mark.asyncio
    async def test_list_prompt_templates_with_agent_type_filter(self, tenant_id, mock_session):
        from api.v1.prompt_templates import list_prompt_templates

        templates = [_make_prompt_template(agent_type="hr")]
        mock_session.execute.return_value = _make_result(scalars_list=templates)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await list_prompt_templates(
                agent_type="hr", tenant_id=tenant_id, user_domains=None,
            )
        finally:
            ctx.stop()

        assert len(resp) == 1

    @pytest.mark.asyncio
    async def test_list_prompt_templates_with_domain_filter(self, tenant_id, mock_session):
        from api.v1.prompt_templates import list_prompt_templates

        templates = [_make_prompt_template(domain="finance")]
        mock_session.execute.return_value = _make_result(scalars_list=templates)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await list_prompt_templates(
                domain="finance", tenant_id=tenant_id, user_domains=None,
            )
        finally:
            ctx.stop()

        assert len(resp) == 1

    @pytest.mark.asyncio
    async def test_list_prompt_templates_domain_rbac(self, tenant_id, mock_session):
        from api.v1.prompt_templates import list_prompt_templates

        templates = [_make_prompt_template(domain="finance")]
        mock_session.execute.return_value = _make_result(scalars_list=templates)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await list_prompt_templates(
                tenant_id=tenant_id, user_domains=["finance"],
            )
        finally:
            ctx.stop()

        assert len(resp) == 1

    @pytest.mark.asyncio
    async def test_get_prompt_template_happy(self, tenant_id, mock_session):
        from api.v1.prompt_templates import get_prompt_template

        template = _make_prompt_template()
        mock_session.execute.return_value = _make_result(scalar_one=template)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await get_prompt_template(template_id=template.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["name"] == "Invoice Classifier"

    @pytest.mark.asyncio
    async def test_get_prompt_template_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.prompt_templates import get_prompt_template

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_prompt_template(template_id=uuid.uuid4(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_create_prompt_template_happy(self, tenant_id, mock_session):
        from api.v1.prompt_templates import create_prompt_template
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="New Template",
            agent_type="sales",
            domain="sales",
            template_text="Respond to lead: {name}",
        )

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await create_prompt_template(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()
        assert resp["created"] is True

    @pytest.mark.asyncio
    async def test_create_prompt_template_with_variables(self, tenant_id, mock_session):
        from api.v1.prompt_templates import create_prompt_template
        from core.schemas.api import PromptTemplateCreate

        body = PromptTemplateCreate(
            name="Template with vars",
            agent_type="finance",
            domain="finance",
            template_text="Process {invoice_id} for {amount}",
            variables=[
                {"name": "invoice_id", "type": "string"},
                {"name": "amount", "type": "number"},
            ],
        )

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await create_prompt_template(body=body, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["created"] is True

    @pytest.mark.asyncio
    async def test_update_prompt_template_happy(self, tenant_id, mock_session):
        from api.v1.prompt_templates import update_prompt_template
        from core.schemas.api import PromptTemplateUpdate

        template = _make_prompt_template(is_builtin=False)
        mock_session.execute.return_value = _make_result(scalar_one=template)

        body = PromptTemplateUpdate(name="Updated Name", template_text="New text: {x}")

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await update_prompt_template(
                template_id=template.id, body=body, tenant_id=tenant_id,
            )
        finally:
            ctx.stop()

        assert resp["updated"] is True
        assert template.name == "Updated Name"
        assert template.template_text == "New text: {x}"

    @pytest.mark.asyncio
    async def test_update_prompt_template_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.prompt_templates import update_prompt_template
        from core.schemas.api import PromptTemplateUpdate

        mock_session.execute.return_value = _make_result(scalar_one=None)
        body = PromptTemplateUpdate(name="X")

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await update_prompt_template(
                    template_id=uuid.uuid4(), body=body, tenant_id=tenant_id,
                )
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_update_prompt_template_builtin_rejected(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.prompt_templates import update_prompt_template
        from core.schemas.api import PromptTemplateUpdate

        template = _make_prompt_template(is_builtin=True)
        mock_session.execute.return_value = _make_result(scalar_one=template)
        body = PromptTemplateUpdate(name="Nope")

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await update_prompt_template(
                    template_id=template.id, body=body, tenant_id=tenant_id,
                )
            assert exc_info.value.status_code == 409
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_update_prompt_template_partial_update(self, tenant_id, mock_session):
        """Only specified fields should be updated."""
        from api.v1.prompt_templates import update_prompt_template
        from core.schemas.api import PromptTemplateUpdate

        template = _make_prompt_template(is_builtin=False)
        original_text = template.template_text
        mock_session.execute.return_value = _make_result(scalar_one=template)

        body = PromptTemplateUpdate(description="New description only")

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await update_prompt_template(
                template_id=template.id, body=body, tenant_id=tenant_id,
            )
        finally:
            ctx.stop()

        assert resp["updated"] is True
        assert template.description == "New description only"
        # template_text should NOT have changed
        assert template.template_text == original_text

    @pytest.mark.asyncio
    async def test_delete_prompt_template_happy(self, tenant_id, mock_session):
        from api.v1.prompt_templates import delete_prompt_template

        template = _make_prompt_template(is_builtin=False)
        mock_session.execute.return_value = _make_result(scalar_one=template)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            resp = await delete_prompt_template(template_id=template.id, tenant_id=tenant_id)
        finally:
            ctx.stop()

        assert resp["deleted"] is True
        assert template.is_active is False  # soft delete

    @pytest.mark.asyncio
    async def test_delete_prompt_template_not_found(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.prompt_templates import delete_prompt_template

        mock_session.execute.return_value = _make_result(scalar_one=None)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await delete_prompt_template(template_id=uuid.uuid4(), tenant_id=tenant_id)
            assert exc_info.value.status_code == 404
        finally:
            ctx.stop()

    @pytest.mark.asyncio
    async def test_delete_prompt_template_builtin_rejected(self, tenant_id, mock_session):
        from fastapi import HTTPException

        from api.v1.prompt_templates import delete_prompt_template

        template = _make_prompt_template(is_builtin=True)
        mock_session.execute.return_value = _make_result(scalar_one=template)

        ctx = _patch_tenant_session("prompt_templates", mock_session)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await delete_prompt_template(template_id=template.id, tenant_id=tenant_id)
            assert exc_info.value.status_code == 409
        finally:
            ctx.stop()


# ============================================================================
# _create_dsar_audit_entry helper (compliance.py)
# ============================================================================

class TestCreateDsarAuditEntry:

    @pytest.mark.asyncio
    async def test_creates_audit_log_entry(self, mock_session):
        from api.v1.compliance import _create_dsar_audit_entry

        request_id = uuid.uuid4()
        await _create_dsar_audit_entry(
            session=mock_session,
            tenant_id=TENANT_UUID,
            request_type="access",
            subject_email="test@example.com",
            request_id=request_id,
        )

        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audit_entry_has_correct_event_type(self, mock_session):
        from api.v1.compliance import _create_dsar_audit_entry

        request_id = uuid.uuid4()
        await _create_dsar_audit_entry(
            session=mock_session,
            tenant_id=TENANT_UUID,
            request_type="erase",
            subject_email="test@example.com",
            request_id=request_id,
        )

        added_entry = mock_session.add.call_args[0][0]
        assert added_entry.event_type == "dsar.erase"
        assert added_entry.action == "dsar_erase_request"

    @pytest.mark.asyncio
    async def test_audit_entry_details_contain_request_info(self, mock_session):
        from api.v1.compliance import _create_dsar_audit_entry

        request_id = uuid.uuid4()
        await _create_dsar_audit_entry(
            session=mock_session,
            tenant_id=TENANT_UUID,
            request_type="export",
            subject_email="user@co.com",
            request_id=request_id,
        )

        added_entry = mock_session.add.call_args[0][0]
        assert added_entry.details["subject_email"] == "user@co.com"
        assert added_entry.details["request_id"] == str(request_id)
        assert added_entry.details["request_type"] == "export"


# ============================================================================
# evals._load_scorecard helper
# ============================================================================

class TestLoadScorecard:

    def test_load_scorecard_happy(self, tmp_path):
        from api.v1.evals import _load_scorecard

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps({"test": True}))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            data = _load_scorecard()

        assert data == {"test": True}

    def test_load_scorecard_missing_file(self, tmp_path):
        from fastapi import HTTPException

        from api.v1.evals import _load_scorecard

        missing = tmp_path / "nope.json"
        with patch("api.v1.evals._SCORECARD_PATH", missing):
            with pytest.raises(HTTPException) as exc_info:
                _load_scorecard()
            assert exc_info.value.status_code == 404

    def test_load_scorecard_reads_latest(self, tmp_path):
        from api.v1.evals import _load_scorecard

        scorecard_file = tmp_path / "scorecard.json"
        scorecard_file.write_text(json.dumps({"version": 1}))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            data1 = _load_scorecard()

        scorecard_file.write_text(json.dumps({"version": 2}))

        with patch("api.v1.evals._SCORECARD_PATH", scorecard_file):
            data2 = _load_scorecard()

        assert data1["version"] == 1
        assert data2["version"] == 2
