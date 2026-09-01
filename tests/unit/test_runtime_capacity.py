"""Runtime capacity and local performance regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.runtime_capacity import AsyncCapacityGate, CapacityLimitError


@pytest.mark.asyncio
async def test_capacity_gate_never_exceeds_limit() -> None:
    gate = AsyncCapacityGate("test", limit=2, queue_timeout_seconds=1)
    active = 0
    maximum = 0

    async def work() -> None:
        nonlocal active, maximum
        async with gate.slot():
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(work() for _ in range(12)))

    assert maximum == 2
    assert gate.snapshot.active == 0
    assert gate.snapshot.waiting == 0


@pytest.mark.asyncio
async def test_capacity_gate_rejects_when_queue_deadline_expires() -> None:
    gate = AsyncCapacityGate("test workload", limit=1, queue_timeout_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_slot() -> None:
        async with gate.slot():
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold_slot())
    await entered.wait()
    with pytest.raises(CapacityLimitError, match="test workload capacity is busy"):
        async with gate.slot():
            pytest.fail("timed-out work must not start")
    release.set()
    await holder


@pytest.mark.asyncio
async def test_rpa_wrapper_returns_retryable_capacity_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.rpa import executor

    gate = AsyncCapacityGate("RPA browser execution", limit=1, queue_timeout_seconds=0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_execute(**_kwargs):  # noqa: ANN003, ANN202
        entered.set()
        await release.wait()
        return {"success": True}

    monkeypatch.setattr(executor, "_RPA_CAPACITY", gate)
    monkeypatch.setattr(executor, "_execute_rpa_script", fake_execute)
    first = asyncio.create_task(executor.execute_rpa_script("test", {}))
    await entered.wait()
    refused = await executor.execute_rpa_script("test", {})
    release.set()
    assert await first == {"success": True}
    assert refused["success"] is False
    assert refused["error_class"] == "rpa_capacity_exhausted"
    assert refused["retryable"] is True


@pytest.mark.asyncio
async def test_health_checks_run_dependencies_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    async def delayed() -> str:
        await asyncio.sleep(0.04)
        return "healthy"

    monkeypatch.setattr(health, "_db_health_status", delayed)
    monkeypatch.setattr(health, "_redis_health_status", delayed)
    monkeypatch.setattr(health, "_health_dependency_cache", None)
    monkeypatch.setattr(health, "_health_dependency_lock", None)
    started = asyncio.get_running_loop().time()
    result = await health._critical_dependency_checks()
    elapsed = asyncio.get_running_loop().time() - started

    assert result == {"db": "healthy", "redis": "healthy"}
    assert elapsed < 0.07


@pytest.mark.asyncio
async def test_health_checks_coalesce_concurrent_probe_bursts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.v1 import health

    async def healthy_probe() -> str:
        await asyncio.sleep(0)
        return "healthy"

    db_probe = AsyncMock(side_effect=healthy_probe)
    redis_probe = AsyncMock(side_effect=healthy_probe)
    monkeypatch.setattr(health, "_db_health_status", db_probe)
    monkeypatch.setattr(health, "_redis_health_status", redis_probe)
    monkeypatch.setattr(health, "_health_dependency_cache", None)
    monkeypatch.setattr(health, "_health_dependency_lock", None)

    results = await asyncio.gather(*(health._critical_dependency_checks() for _ in range(50)))

    assert results == [{"db": "healthy", "redis": "healthy"}] * 50
    assert db_probe.await_count == 1
    assert redis_probe.await_count == 1


@pytest.mark.asyncio
async def test_health_redis_client_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1 import health

    client = AsyncMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(health, "_health_redis_client", None)
    monkeypatch.setattr(health.aioredis, "from_url", factory)

    assert health._get_health_redis_client() is client
    assert health._get_health_redis_client() is client
    assert factory.call_count == 1
    await health.close_health_resources()
    client.aclose.assert_awaited_once()


def test_docker_image_allows_web_concurrency_tuning() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    command = dockerfile.rsplit("CMD", 1)[-1]
    assert '"--workers", "1"' not in command
