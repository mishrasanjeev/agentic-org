# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""On-demand OACP artifact freshness (core/commerce/artifact_freshness.py).

Single flight per merchant scope, a minimum interval and backoff between
refreshes, a bounded wait for the read, nothing without the flag or without the
shared lock store, bounded cache growth, and a sweep that touches only recently
read scopes.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.commerce import artifact_freshness as fresh
from core.config import settings
from tests.oacp_fresh_fakes import FakeRedis
from tests.ucp_fixtures import MERCHANT_ID, SELLER_AGENT_ID, TENANT_ID, cache_record

NOW = datetime.now(UTC).replace(microsecond=0)
TENANT = TENANT_ID
SCOPE = {"tenant_id": TENANT, "merchant_id": MERCHANT_ID, "seller_agent_id": SELLER_AGENT_ID}


def records(**lifetimes: int) -> list[Any]:
    """Verified shared cache records (the UCP test fixture), one per type, expiring ``seconds`` from now."""
    return [
        cache_record(artifact_type, now=NOW, expires_in=timedelta(seconds=seconds))
        for artifact_type, seconds in lifetimes.items()
    ]


@pytest.fixture()
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()

    async def _redis() -> FakeRedis:
        return client

    monkeypatch.setattr(fresh, "_redis", _redis)
    monkeypatch.setattr(settings, "oacp_on_demand_refresh_enabled", True)
    monkeypatch.setattr(settings, "oacp_refresh_sweep_enabled", False)
    monkeypatch.setattr(settings, "oacp_refresh_wait_ms", 2000)
    monkeypatch.setattr(fresh, "LOCK_POLL_SECONDS", 0.01)

    async def _no_prune(_scope: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(fresh, "prune_scope", _no_prune)
    return client


class Refresher:
    def __init__(self, outcome: str = fresh.REFRESHED, delay: float = 0.0, error: Exception | None = None) -> None:
        self.calls: list[fresh.Scope] = []
        self.outcome, self.delay, self.error = outcome, delay, error

    async def __call__(self, scope: fresh.Scope) -> str:
        self.calls.append(scope)
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.outcome


def _install(monkeypatch: pytest.MonkeyPatch, refresher: Refresher) -> Refresher:
    monkeypatch.setattr(fresh, "refresher", refresher)
    return refresher


EXPIRED = {"catalog_snapshot": 3600, "price": 200, "inventory": -5}


async def _ensure(recs: list[Any], required: tuple[str, ...] = fresh.CATALOG_TYPES) -> bool:
    return await fresh.ensure_fresh(**SCOPE, records=recs, required=required, now=NOW)


def _all_types(seconds: int) -> list[Any]:
    return records(**dict.fromkeys(fresh.HOST_TYPES, seconds))


def test_missing_types_honours_the_margin() -> None:
    recs = records(catalog_snapshot=3600, price=200, inventory=5)
    assert fresh.missing_types(recs, ("catalog_snapshot", "price", "inventory"), now=NOW) == ("inventory",)
    assert fresh.missing_types(recs, ("inventory",), now=NOW, margin_seconds=0) == ()
    assert fresh.missing_types([], ("price",), now=NOW) == ("price",)


async def test_flag_off_never_touches_the_lock_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oacp_on_demand_refresh_enabled", False)

    async def _boom() -> None:
        raise AssertionError("no Redis with the flag off")

    monkeypatch.setattr(fresh, "_redis", _boom)
    refresher = _install(monkeypatch, Refresher())
    assert await _ensure(records(inventory=-60)) is False
    assert refresher.calls == []


async def test_fresh_scope_is_not_refreshed(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    refresher = _install(monkeypatch, Refresher())
    assert await _ensure(_all_types(3600)) is False
    assert refresher.calls == [] and redis.values == {}


async def test_concurrent_reads_trigger_one_refresh(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    refresher = _install(monkeypatch, Refresher(delay=0.05))
    results = await asyncio.gather(*(_ensure(records(**EXPIRED)) for _ in range(12)))
    assert len(refresher.calls) == 1
    assert refresher.calls[0] == fresh.Scope(TENANT, MERCHANT_ID, SELLER_AGENT_ID)
    assert all(results)  # the winner saw it finish; the others waited for the lock and re-read
    assert redis.keys("oacp:fresh:lock:*") == []


async def test_minimum_interval_bounds_issuance(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    refresher = _install(monkeypatch, Refresher())
    assert await _ensure(records(**EXPIRED)) is True
    # Still missing right after (for example, Grantex issued a shorter TTL): no second issuance in the interval.
    assert await _ensure(records(**EXPIRED)) is False
    assert len(refresher.calls) == 1
    assert redis.keys("oacp:fresh:recent:*")


@pytest.mark.parametrize(
    ("outcome", "error", "backoff"),
    [
        (fresh.FAILED, None, fresh.RETRY_BACKOFF_SECONDS),
        (fresh.REFUSED, None, fresh.REFUSAL_BACKOFF_SECONDS),
        (fresh.NOT_REFRESHABLE, None, fresh.NOT_REFRESHABLE_BACKOFF_SECONDS),
        (fresh.REFRESHED, RuntimeError("grantex unreachable"), fresh.RETRY_BACKOFF_SECONDS),
    ],
)
async def test_failures_back_off_and_never_fail_the_read(
    monkeypatch: pytest.MonkeyPatch, redis: FakeRedis, outcome: str, error: Exception | None, backoff: int
) -> None:
    refresher = _install(monkeypatch, Refresher(outcome=outcome, error=error))
    assert await _ensure(records(**EXPIRED)) is False
    [key] = redis.keys("oacp:fresh:backoff:*")
    remaining = redis.values[key][1] - time.time()
    assert backoff - 2 <= remaining <= backoff
    assert await _ensure(records(**EXPIRED)) is False
    assert len(refresher.calls) == 1
    assert redis.keys("oacp:fresh:lock:*") == []


async def test_the_read_waits_at_most_its_budget(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    monkeypatch.setattr(settings, "oacp_refresh_wait_ms", 50)
    refresher = _install(monkeypatch, Refresher(delay=0.4))
    started = time.monotonic()
    assert await _ensure(records(**EXPIRED)) is False  # the read goes on with the cached (stale) state
    assert time.monotonic() - started < 0.3
    assert redis.keys("oacp:fresh:lock:*")  # still refreshing in the background, holding the lock
    await asyncio.gather(*fresh._INFLIGHT)
    assert redis.keys("oacp:fresh:lock:*") == [] and redis.keys("oacp:fresh:recent:*")
    assert len(refresher.calls) == 1


async def test_a_slow_refresh_is_cut_off_before_its_lock_lapses(
    monkeypatch: pytest.MonkeyPatch, redis: FakeRedis
) -> None:
    monkeypatch.setattr(fresh, "LOCK_TTL_SECONDS", 6)  # wait_for gets LOCK_TTL - 5 = 1 s
    monkeypatch.setattr(settings, "oacp_refresh_wait_ms", 3000)
    _install(monkeypatch, Refresher(delay=5))
    assert await _ensure(records(**EXPIRED)) is False
    assert redis.keys("oacp:fresh:backoff:*") and redis.keys("oacp:fresh:lock:*") == []


async def test_without_the_lock_store_nothing_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oacp_on_demand_refresh_enabled", True)

    async def _none() -> None:
        return None

    monkeypatch.setattr(fresh, "_redis", _none)
    refresher = _install(monkeypatch, Refresher())
    assert await _ensure(records(**EXPIRED)) is False
    assert refresher.calls == []


async def test_scope_without_a_seller_agent_is_left_alone(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    refresher = _install(monkeypatch, Refresher())
    assert (
        await fresh.ensure_fresh(
            tenant_id=TENANT, merchant_id="m", seller_agent_id=None, records=[], required=("price",), now=NOW
        )
        is False
    )
    assert refresher.calls == []


def test_prune_keeps_the_newest_records_per_type_and_drops_long_expired_ones() -> None:
    rows = []
    for index in range(40):  # 40 issuances, the newest last
        issued = NOW - timedelta(seconds=45 * (40 - index))
        for artifact_type, ttl in (("inventory", 60), ("price", 300), ("protocol_adapter", 86400)):
            rows.append((f"{artifact_type}-{index}", artifact_type, issued, issued + timedelta(seconds=ttl)))
    doomed = set(fresh.prune_ids(rows, now=NOW))
    kept = [row for row in rows if row[0] not in doomed]
    by_type: dict[str, list[str]] = {}
    for record_id, artifact_type, _issued, _expires in kept:
        by_type.setdefault(artifact_type, []).append(record_id)
    assert all(len(ids) <= fresh.KEEP_PER_TYPE for ids in by_type.values())
    # The newest issuance always survives; nothing kept is expired beyond the grace.
    assert {"inventory-39", "price-39", "protocol_adapter-39"} <= {row[0] for row in kept}
    cutoff = NOW - timedelta(seconds=fresh.PRUNE_GRACE_SECONDS)
    assert all(row[3] >= cutoff for row in kept)


async def test_sweep_refreshes_only_recently_read_scopes_that_need_it(
    monkeypatch: pytest.MonkeyPatch, redis: FakeRedis
) -> None:
    monkeypatch.setattr(settings, "oacp_refresh_sweep_enabled", True)
    refresher = _install(monkeypatch, Refresher())
    stale = fresh.Scope(TENANT, MERCHANT_ID, SELLER_AGENT_ID)
    healthy = fresh.Scope(TENANT, MERCHANT_ID, "seller-healthy")
    idle = fresh.Scope(TENANT, MERCHANT_ID, "seller-idle")
    now = time.time()
    await redis.zadd(fresh.DEMAND_KEY, {stale.member: now - 60, healthy.member: now - 60, idle.member: now - 3600})

    async def _records(*, tenant_id: str, merchant_id: str, seller_agent_id: str) -> list[Any]:
        if seller_agent_id == "seller-healthy":
            return _all_types(3600)
        return records(**{**dict.fromkeys(fresh.HOST_TYPES, 3600), "price": 120})  # price lapses before the next sweep

    from core.commerce.ucp import store

    monkeypatch.setattr(store, "load_shared_cache_records", _records)
    result = await fresh.sweep()
    assert result == {"enabled": 1, "scopes": 2, "refreshed": 1}
    assert refresher.calls == [stale]
    assert idle.member not in redis.zsets[fresh.DEMAND_KEY]  # read an hour ago: forgotten, never refreshed


async def test_sweep_is_a_no_op_unless_both_flags_are_on(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    assert await fresh.sweep() == {"enabled": 0, "scopes": 0, "refreshed": 0}
    monkeypatch.setattr(settings, "oacp_refresh_sweep_enabled", True)
    monkeypatch.setattr(settings, "oacp_on_demand_refresh_enabled", False)
    assert await fresh.sweep() == {"enabled": 0, "scopes": 0, "refreshed": 0}


async def test_reads_mark_demand_only_for_the_sweep(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> None:
    _install(monkeypatch, Refresher())
    await _ensure(_all_types(3600))
    assert fresh.DEMAND_KEY not in redis.zsets
    monkeypatch.setattr(settings, "oacp_refresh_sweep_enabled", True)
    await _ensure(_all_types(3600))
    assert list(redis.zsets[fresh.DEMAND_KEY]) == [f"{TENANT}|{MERCHANT_ID}|{SELLER_AGENT_ID}"]


def test_the_sweep_is_scheduled_on_the_existing_beat() -> None:
    from core.tasks.celery_app import app

    entry = app.conf.beat_schedule["sweep-oacp-artifact-freshness"]
    assert entry["task"] == "core.tasks.oacp_freshness_tasks.sweep_artifact_freshness"
    assert entry["schedule"] == 300.0 and entry["options"] == {"queue": "maintenance"}
    assert "core.tasks.oacp_freshness_tasks" in app.conf.include


def test_the_celery_task_runs_the_sweep_and_logs_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.tasks import oacp_freshness_tasks

    outcomes = iter([{"enabled": 0, "scopes": 0, "refreshed": 0}, {"enabled": 1, "scopes": 3, "refreshed": 1}])

    async def _sweep() -> dict[str, int]:
        return next(outcomes)

    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(fresh, "sweep", _sweep)
    monkeypatch.setattr(oacp_freshness_tasks.logger, "info", lambda event, **kw: logged.append({"event": event, **kw}))
    monkeypatch.setattr(oacp_freshness_tasks, "run_async", asyncio.run)
    assert oacp_freshness_tasks.sweep_artifact_freshness() == {"enabled": 0, "scopes": 0, "refreshed": 0}
    assert logged == []
    assert oacp_freshness_tasks.sweep_artifact_freshness() == {"enabled": 1, "scopes": 3, "refreshed": 1}
    assert logged == [{"event": "oacp_freshness_sweep", "scopes": 3, "refreshed": 1}]
