# SPDX-License-Identifier: Apache-2.0
"""Operator override store paths: loading, caching, placing and releasing overrides.

Database sessions and Redis are faked; what is checked is what each path reads,
writes, audits and invalidates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.governance import operator_override as oo

TENANT = uuid.uuid4()


def _row(**over):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "target_kind": "agent",
        "target_id": "agent-1",
        "mode": "halt",
        "limit_per_minute": None,
        "reason": "drill",
        "created_by": "user:1",
        "created_at": datetime.now(UTC),
        "expires_at": None,
        "released_at": None,
        "released_by": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _Session:
    """A session that answers every query with the same rows and records adds."""

    def __init__(self, rows):
        self.rows = rows
        self.added: list = []
        self.flushed = 0

    async def execute(self, _query):
        rows = self.rows
        result = MagicMock()
        result.scalars.return_value = iter(rows)
        result.scalar_one_or_none.return_value = rows[0] if rows else None
        return result

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "target_kind"):
                obj.id = uuid.uuid4()


@pytest.fixture
def session(monkeypatch):
    sess = _Session([])

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield sess

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    monkeypatch.setattr(oo.settings, "secret_key", "ci-test-secret-key-minimum-16")
    return sess


@pytest.fixture
def no_redis():
    with patch("core.async_redis.get_async_redis", AsyncMock(return_value=None)):
        yield


class TestLoading:
    def test_load_from_db_maps_rows(self, session):
        session.rows = [_row(mode="throttle", limit_per_minute=5, target_id="")]
        loaded = asyncio.run(oo._load_from_db(TENANT))
        assert len(loaded) == 1
        assert loaded[0].mode == "throttle" and loaded[0].limit_per_minute == 5 and loaded[0].target_id == ""

    def test_active_overrides_fills_the_cache_and_invalidate_drops_it(self, session):
        session.rows = [_row()]
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        store: dict[str, str] = {}

        async def _set(key, value, ex=None):
            store[key] = value

        async def _delete(key):
            store.pop(key, None)

        redis.set = AsyncMock(side_effect=_set)
        redis.delete = AsyncMock(side_effect=_delete)
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            loaded = asyncio.run(oo.active_overrides(TENANT))
            assert len(loaded) == 1
            assert json.loads(next(iter(store.values())))[0]["target_kind"] == "agent"
            asyncio.run(oo.invalidate(TENANT))
        assert store == {}

    def test_cache_failures_fall_through_to_the_database(self, session):
        session.rows = [_row()]
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=redis)):
            assert len(asyncio.run(oo.active_overrides(TENANT))) == 1
        broken = AsyncMock()
        broken.get = AsyncMock(return_value=None)
        broken.set = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch("core.async_redis.get_async_redis", AsyncMock(return_value=broken)):
            assert len(asyncio.run(oo.active_overrides(TENANT))) == 1
        with patch("core.async_redis.get_async_redis", AsyncMock(side_effect=RuntimeError("no pool"))):
            asyncio.run(oo.invalidate(TENANT))  # never raises

    def test_enabled_without_a_tenant_is_the_settings_switch(self, monkeypatch):
        monkeypatch.setattr(oo.settings, "operator_override_enabled", False)
        assert asyncio.run(oo.enabled(None)) is False
        monkeypatch.setattr(oo.settings, "operator_override_enabled", True)
        assert asyncio.run(oo.enabled(None)) is True


class TestThrottleCounter:
    def test_zero_limit_blocks_without_a_counter(self):
        o = oo.Override(id="o", target_kind="agent", target_id="a", mode="throttle", limit_per_minute=0, reason="r")
        with patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)) as counter:
            assert asyncio.run(oo._throttled(TENANT, o)) is True
        counter.assert_not_called()

    def test_counter_outage_blocks(self):
        o = oo.Override(id="o", target_kind="agent", target_id="a", mode="throttle", limit_per_minute=3, reason="r")
        with patch("core.auth_state.check_window_rate", AsyncMock(side_effect=RuntimeError("down"))):
            assert asyncio.run(oo._throttled(TENANT, o)) is True


class TestChanges:
    def test_set_override_validates_before_writing(self, session, no_redis):
        bad = [
            {"target_kind": "spaceship", "target_id": "x", "mode": "halt", "limit_per_minute": None},
            {"target_kind": "agent", "target_id": "x", "mode": "rewind", "limit_per_minute": None},
            {"target_kind": "agent", "target_id": "x", "mode": "throttle", "limit_per_minute": None},
            {"target_kind": "agent", "target_id": "x", "mode": "throttle", "limit_per_minute": -1},
            {"target_kind": "agent", "target_id": " ", "mode": "halt", "limit_per_minute": None},
        ]
        for kwargs in bad:
            with pytest.raises(ValueError):
                asyncio.run(oo.set_override(TENANT, reason="r", actor_id="user:1", expires_at=None, **kwargs))
        assert session.added == []

    def test_set_override_writes_the_row_and_a_signed_audit_entry(self, session, no_redis):
        expires = datetime.now(UTC) + timedelta(hours=1)
        placed = asyncio.run(
            oo.set_override(
                TENANT,
                target_kind="tool_pipeline",
                target_id="ignored",
                mode="halt",
                limit_per_minute=7,
                reason="  incident 42 ",
                actor_id="user:1",
                expires_at=expires,
            )
        )
        assert placed.target_kind == "tool_pipeline" and placed.target_id == "" and placed.mode == "halt"
        assert placed.limit_per_minute is None and placed.reason == "incident 42"
        row, audit = session.added
        assert row.target_kind == "tool_pipeline" and row.expires_at == expires and row.created_by == "user:1"
        assert audit.event_type == "operator_override.set" and audit.actor_id == "user:1"
        assert audit.details["reason"] == "incident 42" and audit.details["expires_at"] == expires.isoformat()
        assert audit.signature

    def test_release_override_marks_the_row_and_audits(self, session, no_redis):
        row = _row()
        session.rows = [row]
        released = asyncio.run(oo.release_override(TENANT, row.id, actor_id="api_key:k"))
        assert released is not None and released.id == str(row.id)
        assert row.released_at is not None and row.released_by == "api_key:k"
        (audit,) = session.added
        assert audit.event_type == "operator_override.released" and audit.resource_id == str(row.id)

    def test_release_of_an_unknown_override_is_none(self, session, no_redis):
        assert asyncio.run(oo.release_override(TENANT, uuid.uuid4(), actor_id="user:1")) is None
        assert session.added == []
