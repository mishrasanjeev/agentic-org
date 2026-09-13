"""core.kpi_cache — set()/is_stale() with a fake Redis and fake PG session.

Regressions covered:
  - ``datetime.now(datetime.UTC)`` raised AttributeError on every set() and
    on the PG staleness path, so KPI writes never happened.
  - Cache keys and PG rows were tenant-scoped only; company-scoped metrics
    aliased tenant-wide ones and each other.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core import kpi_cache as kc


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int]] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = (value, ex)

    async def get(self, key):
        return self.store.get(key, (None, None))[0]

    async def ttl(self, key):
        return self.store[key][1] if key in self.store else -2

    async def aclose(self):
        return None


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Records executed statements; returns a canned row for SELECTs."""

    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return _FakeResult(self.row)

    async def commit(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    redis = _FakeRedis()

    async def _get():
        return redis

    monkeypatch.setattr(kc, "_get_redis", _get)
    return redis


def _session_factory(monkeypatch, session):
    monkeypatch.setattr(kc, "async_session_factory", lambda: session)


@pytest.mark.asyncio
async def test_set_writes_company_scoped_key_and_pg_row(fake_redis, monkeypatch):
    session = _FakeSession()
    _session_factory(monkeypatch, session)

    await kc.KPICache().set("t1", "cfo", "revenue", {"v": 1}, ttl=60, company_id="c1")

    assert "kpi:t1:c1:cfo:revenue" in fake_redis.store
    assert "kpi:t1:-:cfo:revenue" not in fake_redis.store
    stmt, params = session.calls[0]
    assert "company_id" in stmt
    assert params["cid"] == "c1"
    assert params["tid"] == "t1"


@pytest.mark.asyncio
async def test_set_tenant_wide_uses_null_company(fake_redis, monkeypatch):
    session = _FakeSession()
    _session_factory(monkeypatch, session)

    await kc.KPICache().set("t1", "cfo", "revenue", {"v": 1})

    assert "kpi:t1:-:cfo:revenue" in fake_redis.store
    assert session.calls[0][1]["cid"] is None


@pytest.mark.asyncio
async def test_is_stale_company_scope_does_not_alias(fake_redis, monkeypatch):
    _session_factory(monkeypatch, _FakeSession(row=None))
    cache = kc.KPICache()
    await cache.set("t1", "cfo", "revenue", {"v": 1}, ttl=60, company_id="c1")

    assert await cache.is_stale("t1", "cfo", "revenue", company_id="c1") is False
    # Same tenant, other company / tenant-wide: key absent in Redis, PG has no row.
    assert await cache.is_stale("t1", "cfo", "revenue", company_id="c2") is True
    assert await cache.is_stale("t1", "cfo", "revenue") is True


@pytest.mark.asyncio
async def test_pg_is_stale_ttl_math(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(kc, "_get_redis", _no_redis)
    fresh = SimpleNamespace(stale=False, computed_at=datetime.now(UTC), ttl_seconds=3600)
    _session_factory(monkeypatch, _FakeSession(row=fresh))
    assert await kc.KPICache().is_stale("t1", "cfo", "revenue", company_id="c1") is False

    expired = SimpleNamespace(
        stale=False, computed_at=datetime.now(UTC) - timedelta(hours=2), ttl_seconds=3600
    )
    session = _FakeSession(row=expired)
    _session_factory(monkeypatch, session)
    assert await kc.KPICache().is_stale("t1", "cfo", "revenue", company_id="c1") is True
    assert session.calls[0][1]["cid"] == "c1"
    assert "IS NOT DISTINCT FROM" in session.calls[0][0]
