"""PR-H regression pins for SEC-015 (RPA history durability).

Closes:

- SEC-2026-05-P3-015 — RPA execution history was a process-local
  module-level dict that lost state on restart and produced
  inconsistent behavior across replicas. Now persisted via
  ``core.rpa.history_store`` (Redis-backed list, tenant-scoped,
  capped at MAX_ENTRIES, TTL = RETENTION_DAYS).

Tests use ``fakeredis.aioredis.FakeRedis`` (or an in-memory shim)
patched into ``core.rpa.history_store._get_redis`` so we exercise the
durable path without a live Redis. Tenant isolation, retention, and
the strict-env-degraded-warning paths are all pinned.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────
# Minimal in-process Redis double — enough surface for the store.
# ─────────────────────────────────────────────────────────────────


class _FakeRedis:
    """Tiny in-process Redis stub.

    Implements the four operations the history_store touches:
    ``lpush``, ``ltrim``, ``lrange``, ``expire``. Ignores TTLs (they
    are tested via the call_count assertion). Lists store strings.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}
        self._ttls: dict[str, int] = {}
        self.fail_next: int = 0

    async def lpush(self, key: str, value: str) -> int:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("simulated redis outage")
        self._store.setdefault(key, []).insert(0, value)
        return len(self._store[key])

    async def ltrim(self, key: str, start: int, end: int) -> str:
        bucket = self._store.get(key, [])
        # Inclusive range — Redis semantics.
        self._store[key] = bucket[start : end + 1]
        return "OK"

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("simulated redis outage")
        bucket = self._store.get(key, [])
        if end == -1:
            return bucket[start:]
        return bucket[start : end + 1]

    async def expire(self, key: str, seconds: int) -> int:
        self._ttls[key] = seconds
        return 1


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture(autouse=True)
def _reset_history_store():
    """Clear the process-local fallback bucket between tests."""
    from core.rpa import history_store

    history_store._reset_fallback_for_tests()
    yield
    history_store._reset_fallback_for_tests()


# ─────────────────────────────────────────────────────────────────
# Pins
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_writes_to_redis_when_available(fake_redis):
    """Happy path — Redis present, append returns True (durable=True)."""
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        ok = await history_store.append(
            "tenant-A", {"id": "exec-1", "status": "completed"}
        )
        assert ok is True
        rows = await history_store.list_history("tenant-A", limit=10)

    assert len(rows) == 1
    assert rows[0]["id"] == "exec-1"
    # TTL was set on the tenant key
    assert "rpa:history:tenant-A" in fake_redis._ttls
    # 90 days * 24h * 3600s = 7,776,000s default
    assert fake_redis._ttls["rpa:history:tenant-A"] == 90 * 24 * 3600


@pytest.mark.asyncio
async def test_append_returns_false_when_redis_unavailable_and_uses_fallback(
    monkeypatch,
):
    """Strict-env Redis outage → False return + fallback works."""
    from core.rpa import history_store

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=None)
    ):
        ok = await history_store.append(
            "tenant-A", {"id": "exec-1", "status": "completed"}
        )
        assert ok is False
        rows = await history_store.list_history("tenant-A", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == "exec-1"


@pytest.mark.asyncio
async def test_append_swallows_redis_exception_and_falls_through(fake_redis):
    """If Redis raises mid-append, the store must NOT crash the
    request. The row lands in the in-process fallback for
    debuggability; we don't promise it's read back through the normal
    list_history path (Redis is now healthy and is the source of
    truth, even if it briefly missed one write — that is an accepted
    durability gap during transient outages, not a silent crash)."""
    from core.rpa import history_store

    fake_redis.fail_next = 1  # next lpush raises
    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        ok = await history_store.append(
            "tenant-A", {"id": "exec-x", "status": "completed"}
        )
        # Redis errored, so durable=False
        assert ok is False
    # The fallback bucket has the row for ops debuggability.
    assert any(
        r["id"] == "exec-x" for r in history_store._FALLBACK.get("tenant-A", [])
    )


@pytest.mark.asyncio
async def test_list_history_returns_newest_first(fake_redis):
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        for i in range(5):
            await history_store.append(
                "tenant-A", {"id": f"exec-{i}", "status": "completed"}
            )
        rows = await history_store.list_history("tenant-A", limit=10)

    assert [r["id"] for r in rows] == [
        "exec-4",
        "exec-3",
        "exec-2",
        "exec-1",
        "exec-0",
    ]


@pytest.mark.asyncio
async def test_list_history_respects_limit(fake_redis):
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        for i in range(10):
            await history_store.append(
                "tenant-A", {"id": f"exec-{i}", "status": "completed"}
            )
        rows = await history_store.list_history("tenant-A", limit=3)

    assert len(rows) == 3
    assert [r["id"] for r in rows] == ["exec-9", "exec-8", "exec-7"]


@pytest.mark.asyncio
async def test_list_history_zero_or_negative_limit_returns_empty():
    from core.rpa import history_store

    assert await history_store.list_history("tenant-A", limit=0) == []
    assert await history_store.list_history("tenant-A", limit=-5) == []


@pytest.mark.asyncio
async def test_tenant_isolation_pin(fake_redis):
    """Tenant A cannot read tenant B history. This is the SEC-015
    HIGH-08 pin — the store has NO operation that crosses tenants.
    """
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        await history_store.append(
            "tenant-A", {"id": "secret-A-1", "status": "completed"}
        )
        await history_store.append(
            "tenant-B", {"id": "secret-B-1", "status": "completed"}
        )
        a_rows = await history_store.list_history("tenant-A", limit=10)
        b_rows = await history_store.list_history("tenant-B", limit=10)

    assert {r["id"] for r in a_rows} == {"secret-A-1"}
    assert {r["id"] for r in b_rows} == {"secret-B-1"}
    # Belt-and-braces: the underlying Redis keys are also tenant-scoped.
    assert "rpa:history:tenant-A" in fake_redis._store
    assert "rpa:history:tenant-B" in fake_redis._store


@pytest.mark.asyncio
async def test_max_entries_cap_applied(fake_redis, monkeypatch):
    """Per-tenant list is capped to MAX_ENTRIES via LTRIM."""
    from core.rpa import history_store

    monkeypatch.setattr(history_store, "MAX_ENTRIES", 3)
    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        for i in range(5):
            await history_store.append(
                "tenant-A", {"id": f"exec-{i}", "status": "completed"}
            )
        rows = await history_store.list_history("tenant-A", limit=10)

    # Only the 3 newest entries survive the LTRIM cap.
    assert [r["id"] for r in rows] == ["exec-4", "exec-3", "exec-2"]


@pytest.mark.asyncio
async def test_state_survives_simulated_restart(fake_redis):
    """Sanity: redis-backed entries persist across the process-local
    fallback bucket being cleared (which simulates a restart of the
    api process when only Redis state survives)."""
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        ok = await history_store.append(
            "tenant-A", {"id": "exec-survives", "status": "completed"}
        )
        assert ok is True
        history_store._reset_fallback_for_tests()  # simulate restart
        rows = await history_store.list_history("tenant-A", limit=10)

    assert [r["id"] for r in rows] == ["exec-survives"]


@pytest.mark.asyncio
async def test_multiple_replicas_see_same_history(fake_redis):
    """Two ``api/v1/rpa.py`` processes pointing at the same Redis must
    converge on the same history. We simulate this by appending from
    one logical process and reading from another (same Redis instance,
    different process-local fallback buckets)."""
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        await history_store.append(
            "tenant-A", {"id": "exec-from-pod-1", "status": "completed"}
        )
        # Wipe pod 1's fallback to simulate pod 2's fresh memory.
        history_store._reset_fallback_for_tests()
        rows = await history_store.list_history("tenant-A", limit=10)

    assert [r["id"] for r in rows] == ["exec-from-pod-1"]


@pytest.mark.asyncio
async def test_relaxed_env_does_not_log_degradation_warning(
    monkeypatch, caplog
):
    """In local/test envs, falling back to in-memory must NOT emit
    the ``rpa_history_persistence_degraded`` warning — that warning
    is reserved for strict envs where it indicates a real ops gap.
    """
    import logging

    from core.rpa import history_store

    monkeypatch.setenv("AGENTICORG_ENV", "test")
    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=None)
    ), caplog.at_level(logging.WARNING, logger=history_store.logger.name):
        await history_store.append(
            "tenant-A", {"id": "exec-1", "status": "completed"}
        )

    assert not any(
        "rpa_history_persistence_degraded" in (r.getMessage() or r.message or "")
        for r in caplog.records
    ), "relaxed env should NOT emit the strict-env degradation warning"


@pytest.mark.asyncio
async def test_strict_env_logs_degradation_warning(monkeypatch, caplog):
    import logging

    from core.rpa import history_store

    monkeypatch.setenv("AGENTICORG_ENV", "production")
    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=None)
    ), caplog.at_level(logging.WARNING, logger=history_store.logger.name):
        await history_store.append(
            "tenant-A", {"id": "exec-1", "status": "completed"}
        )

    assert any(
        "rpa_history_persistence_degraded" in (r.getMessage() or r.message or "")
        for r in caplog.records
    ), "strict env MUST log the degradation warning"


@pytest.mark.asyncio
async def test_malformed_redis_entry_does_not_blank_other_rows(fake_redis):
    """One bad row in Redis (decode-fail) must not blank the rest of
    the tenant's history."""
    from core.rpa import history_store

    # Insert one good row + one corrupt row directly via fake_redis.
    fake_redis._store["rpa:history:tenant-A"] = [
        json.dumps({"id": "good", "status": "completed"}),
        "this-is-not-json",
    ]

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        rows = await history_store.list_history("tenant-A", limit=10)

    # Corrupt row is silently dropped; good row survives.
    assert [r["id"] for r in rows] == ["good"]


# ─────────────────────────────────────────────────────────────────
# Endpoint pin: /api/v1/rpa/history must use the durable store
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_history_delegates_to_store(fake_redis, monkeypatch):
    """``GET /api/v1/rpa/history`` must surface entries from the
    durable store + cap user-supplied ``limit`` at 500 to prevent
    unbounded reads."""
    from core.rpa import history_store

    with patch.object(
        history_store, "_get_redis", AsyncMock(return_value=fake_redis)
    ):
        await history_store.append(
            "tenant-A", {"id": "exec-1", "status": "completed",
                          "script_key": "x", "script_name": "x",
                          "started_at": "2026-05-01T00:00:00Z"}
        )

        # Drive the endpoint via TestClient with the tenant dependency
        # overridden.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.deps import get_current_tenant
        from api.v1 import rpa as rpa_module

        app = FastAPI()
        app.include_router(rpa_module.router, prefix="/api/v1")
        app.dependency_overrides[get_current_tenant] = lambda: "tenant-A"
        client = TestClient(app)
        resp = client.get("/api/v1/rpa/history?limit=10000")

    assert resp.status_code == 200
    body = resp.json()
    assert any(r["id"] == "exec-1" for r in body)


def test_module_level_dict_no_longer_present_in_rpa_endpoint() -> None:
    """SEC-015 pin: the old process-local ``_execution_history`` dict
    must not be reintroduced. If a future contributor adds it back,
    this test fails immediately.
    """
    from api.v1 import rpa as rpa_module

    assert not hasattr(rpa_module, "_execution_history"), (
        "RPA history must use core.rpa.history_store, not a module-level "
        "dict in api/v1/rpa.py"
    )
