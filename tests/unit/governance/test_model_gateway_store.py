# SPDX-License-Identifier: Apache-2.0
"""Model gateway store paths: loading, caching, policy changes with audit rows, and the agent's sensitivity.

Database sessions and Redis are faked; what is checked is what each path reads,
writes, audits and invalidates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.governance import model_gateway as gw

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "name": "finance",
        "priority": 10,
        "enabled": True,
        "use_case": None,
        "sensitivity": None,
        "agent_id": None,
        "business_unit": "finance",
        "language": None,
        "provider": "openai",
        "model": "gpt-4o",
        "tier": None,
        "allowed_providers": ["openai"],
        "in_region_only": False,
        "reason": "finance stays with one provider",
        "created_by": "user:1",
        "created_at": datetime.now(UTC),
        "updated_by": None,
        "updated_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Session:
    """A session that answers every query with the same rows and records adds and deletes."""

    def __init__(self, rows):
        self.rows = rows
        self.added: list = []
        self.deleted: list = []
        self.flushed = 0

    async def execute(self, _query, _params=None):
        rows = self.rows
        result = MagicMock()
        result.scalars.return_value = iter(rows)
        result.scalar_one_or_none.return_value = rows[0] if rows else None
        result.fetchone.return_value = rows[0] if rows else None
        return result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "priority"):
                obj.id = uuid.uuid4()


@pytest.fixture
def session(monkeypatch):
    sess = _Session([])

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield sess

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    monkeypatch.setattr(gw.settings, "secret_key", "ci-test-secret-key-minimum-16")
    with patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)):
        yield sess


def _audits(session):
    return [obj for obj in session.added if type(obj).__name__ == "AuditLog"]


class TestLoading:
    def test_rows_map_to_policies(self, session):
        session.rows = [_row(allowed_providers=None, in_region_only=True)]
        loaded = asyncio.run(gw._load_policies(TENANT))
        assert len(loaded) == 1
        policy = loaded[0]
        assert policy.name == "finance" and policy.provider == "openai" and policy.allowed_providers is None
        assert policy.in_region_only is True and policy.business_unit == "finance"
        assert gw.Policy.from_dict(policy.to_dict()) == policy

    def test_active_policies_fill_the_shared_cache_and_invalidate_drops_it(self, session):
        session.rows = [_row()]
        store: dict[str, str] = {}
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=lambda key: store.get(key))
        redis.set = AsyncMock(side_effect=lambda key, value, ex=None: store.__setitem__(key, value))
        redis.delete = AsyncMock(side_effect=lambda key: store.pop(key, None))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            loaded = asyncio.run(gw.active_policies(TENANT))
            assert loaded[0].allowed_providers == ("openai",)
            assert json.loads(next(iter(store.values())))[0]["name"] == "finance"
            assert redis.set.await_args.kwargs["ex"] == gw.CACHE_TTL_SECONDS
            session.rows = []
            assert len(asyncio.run(gw.active_policies(TENANT))) == 1  # served from the cache
            asyncio.run(gw.invalidate(TENANT))
            assert store == {}
            assert asyncio.run(gw.active_policies(TENANT)) == []

    def test_cache_failures_fall_through_to_the_database(self, session):
        session.rows = [_row()]
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            assert len(asyncio.run(gw.active_policies(TENANT))) == 1
            asyncio.run(gw.invalidate(TENANT))  # best effort, never raises


class TestChanges:
    def test_set_policy_writes_the_row_and_a_signed_audit_entry(self, session):
        policy = asyncio.run(
            gw.set_policy(
                TENANT,
                actor_id="user:1",
                name="finance",
                business_unit="Finance",
                provider="openai",
                model="gpt-4o",
                allowed_providers=["openai"],
                reason="finance stays with one provider",
            )
        )
        assert policy.business_unit == "finance" and policy.allowed_providers == ("openai",)
        rows = [obj for obj in session.added if type(obj).__name__ == "ModelRoutingPolicy"]
        assert len(rows) == 1 and str(rows[0].id) == policy.id and rows[0].created_by == "user:1"
        audit = _audits(session)
        assert len(audit) == 1 and audit[0].event_type == "model_gateway_policy.set" and audit[0].signature
        assert audit[0].resource_id == policy.id and audit[0].details["provider"] == "openai"

    def test_set_policy_refuses_an_unusable_policy_before_any_write(self, session):
        with pytest.raises(ValueError, match="must route"):
            asyncio.run(gw.set_policy(TENANT, actor_id="user:1", name="empty"))
        assert session.added == []

    def test_update_merges_revalidates_and_audits(self, session):
        row = _row()
        session.rows = [row]
        updated = asyncio.run(
            gw.update_policy(TENANT, row.id, actor_id="user:2", changes={"priority": 5, "model": "gpt-4o-mini"})
        )
        assert updated is not None and updated.priority == 5 and updated.model == "gpt-4o-mini"
        assert row.updated_by == "user:2" and row.priority == 5
        audit = _audits(session)
        assert audit[0].event_type == "model_gateway_policy.update"
        assert audit[0].details["changes"] == {"priority": 5, "model": "gpt-4o-mini"}

    def test_update_refuses_a_change_that_leaves_the_policy_unusable(self, session):
        row = _row()
        session.rows = [row]
        with pytest.raises(ValueError, match="must be among"):
            asyncio.run(gw.update_policy(TENANT, row.id, actor_id="user:2", changes={"allowed_providers": ["gemini"]}))
        assert _audits(session) == []

    def test_update_and_delete_report_a_missing_policy(self, session):
        session.rows = []
        assert asyncio.run(gw.update_policy(TENANT, uuid.uuid4(), actor_id="u", changes={"priority": 1})) is None
        assert asyncio.run(gw.delete_policy(TENANT, uuid.uuid4(), actor_id="u")) is False
        assert session.added == []

    def test_delete_removes_the_row_and_audits(self, session):
        row = _row()
        session.rows = [row]
        assert asyncio.run(gw.delete_policy(TENANT, row.id, actor_id="user:3")) is True
        assert session.deleted == [row]
        audit = _audits(session)
        assert audit[0].event_type == "model_gateway_policy.delete" and audit[0].actor_id == "user:3"


class TestAgentSensitivity:
    def test_reads_the_agents_recorded_sensitivity(self, session):
        agent_id = uuid.uuid4()
        session.rows = [({"sensitivity": "Restricted"},)]
        assert asyncio.run(gw.agent_sensitivity(TENANT, agent_id)) == "restricted"
        session.rows = [(json.dumps({"sensitivity": "internal"}),)]
        assert asyncio.run(gw.agent_sensitivity(TENANT, str(agent_id))) == "internal"
        session.rows = [({"provider": "openai"},)]
        assert asyncio.run(gw.agent_sensitivity(TENANT, agent_id)) is None
        session.rows = []
        assert asyncio.run(gw.agent_sensitivity(TENANT, agent_id)) is None
        assert asyncio.run(gw.agent_sensitivity(TENANT, "not-a-uuid")) is None

    def test_route_for_agent_reads_sensitivity_only_when_the_gateway_is_on(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", False)
        reads = AsyncMock(return_value="restricted")
        with (
            patch.object(gw, "enabled", AsyncMock(return_value=False)),
            patch.object(gw, "agent_sensitivity", reads),
        ):
            decision = asyncio.run(
                gw.route_for_agent(
                    TENANT,
                    use_case="agent_run",
                    agent_id="a1",
                    business_unit="finance",
                    requested_provider="gemini",
                    requested_model="gemini-2.5-flash",
                )
            )
        assert decision.applied is False and decision.reason == "gateway off"
        reads.assert_not_called()

    def test_route_for_agent_carries_the_sensitivity_into_the_decision(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", True)
        monkeypatch.setattr(gw.settings, "env", "test")
        restricted_policy = gw.Policy(id="p1", name="restricted", priority=1, sensitivity="restricted", model="gpt-4o")
        seen: list[gw.RouteRequest] = []

        async def fake_decide(request, policies, correlation_id):
            seen.append(request)
            return gw._passthrough(request, correlation_id, "probe")

        with (
            patch.object(gw, "active_policies", AsyncMock(return_value=[restricted_policy])),
            patch.object(gw, "agent_sensitivity", AsyncMock(return_value="restricted")),
            patch.object(gw, "_decide", fake_decide),
        ):
            asyncio.run(
                gw.route_for_agent(
                    TENANT,
                    use_case="agent_run",
                    agent_id="a1",
                    business_unit="finance",
                    requested_provider="gemini",
                    requested_model="gemini-2.5-flash",
                )
            )
        assert seen[0].sensitivity == "restricted" and seen[0].agent_id == "a1" and seen[0].business_unit == "finance"

    def test_an_unreadable_sensitivity_refuses_in_a_strict_runtime_and_is_unknown_in_a_relaxed_one(self, monkeypatch):
        monkeypatch.setattr(gw.settings, "model_gateway_enabled", True)
        monkeypatch.setattr(gw.settings, "env", "production")
        with (
            patch.object(gw, "active_policies", AsyncMock(return_value=[])),
            patch.object(gw, "agent_sensitivity", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            with pytest.raises(gw.ModelGatewayRefused, match="sensitivity could not be read"):
                asyncio.run(
                    gw.route_for_agent(
                        TENANT, use_case="agent_run", agent_id="a1", business_unit=None,
                        requested_provider="gemini", requested_model="gemini-2.5-flash",
                    )
                )
            monkeypatch.setattr(gw.settings, "env", "test")
            decision = asyncio.run(
                gw.route_for_agent(
                    TENANT, use_case="agent_run", agent_id="a1", business_unit=None,
                    requested_provider="gemini", requested_model="gemini-2.5-flash",
                )
            )
        assert decision.applied is False and decision.restricted is False
