# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights rollups without a database: latency buckets, percentiles, tenant days, dims hashing and the sweeps."""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.insights import buckets, rollup
from core.tasks import insights_tasks

IST = ZoneInfo("Asia/Kolkata")


# ── buckets ─────────────────────────────────────────────────────────


def test_bounds_are_the_twelve_documented_upper_bounds() -> None:
    assert buckets.LATENCY_BOUNDS_MS == (
        500,
        1000,
        2000,
        5000,
        10000,
        20000,
        30000,
        60000,
        120000,
        300000,
        600000,
        math.inf,
    )
    assert buckets.BUCKET_COUNT == 12
    assert buckets.FINITE_BOUNDS_MS[-1] == buckets.OPEN_BUCKET_FLOOR_MS == 600_000


def test_bucket_count_matches_the_insights_daily_check_constraint() -> None:
    from core.models.insights import LATENCY_BUCKET_COUNT

    assert buckets.BUCKET_COUNT == LATENCY_BUCKET_COUNT


@pytest.mark.parametrize(
    ("ms", "index"),
    [
        (0, 0),
        (-25, 0),
        (500, 0),
        (500.5, 1),
        (501, 1),
        (1000, 1),
        (1001, 2),
        (4999, 3),
        (30_000, 6),
        (600_000, 10),
        (600_001, 11),
        (10**12, 11),
    ],
)
def test_bucket_index_uses_inclusive_upper_bounds(ms: float, index: int) -> None:
    assert buckets.bucket_index(ms) == index


@pytest.mark.parametrize("bad", [None, float("nan")])
def test_bucket_index_refuses_a_missing_duration(bad: object) -> None:
    with pytest.raises(ValueError):
        buckets.bucket_index(bad)  # type: ignore[arg-type]


def test_bucket_counts_skip_unmeasured_runs() -> None:
    counts = buckets.bucket_counts([100, 500, 900, None, 700_000, None])
    assert counts == [2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert sum(counts) == 4


def test_sum_buckets_adds_elementwise_and_checks_length() -> None:
    a = [1] * 12
    b = list(range(12))
    assert buckets.sum_buckets([a, None, b]) == [1 + i for i in range(12)]
    with pytest.raises(ValueError):
        buckets.sum_buckets([[1, 2, 3]])


def test_percentile_of_nothing_is_none() -> None:
    assert buckets.percentile_from_buckets(buckets.empty_buckets(), 0.5) is None


def test_percentile_is_the_upper_bound_of_the_bucket_holding_the_quantile() -> None:
    # 90 runs under 500 ms, 9 between 2 s and 5 s, 1 at 25 s.
    counts = buckets.empty_buckets()
    counts[0], counts[3], counts[6] = 90, 9, 1
    assert buckets.percentile_from_buckets(counts, 0.5) == 500.0
    assert buckets.percentile_from_buckets(counts, 0.9) == 500.0
    assert buckets.percentile_from_buckets(counts, 0.91) == 5000.0
    assert buckets.percentile_from_buckets(counts, 0.99) == 5000.0
    assert buckets.percentile_from_buckets(counts, 1.0) == 30000.0
    # q=0 is the first counted bucket, not bucket 0 when that is empty.
    counts[0] = 0
    assert buckets.percentile_from_buckets(counts, 0) == 5000.0


def test_percentile_in_the_open_bucket_is_a_finite_floor() -> None:
    counts = buckets.empty_buckets()
    counts[11] = 3
    found = buckets.percentile_from_buckets(counts, 0.95)
    assert found == 600_000.0
    assert math.isfinite(found)


def test_percentile_matches_bucket_counts_from_raw_durations() -> None:
    durations = [120, 480, 950, 1500, 1800, 2500, 7000, 15000, 45000, 90000]
    counts = buckets.bucket_counts(durations)
    assert buckets.percentile_from_buckets(counts, 0.5) == 2000.0  # 5th of 10 is 1800 ms
    assert buckets.percentile_from_buckets(counts, 0.95) == 120000.0


@pytest.mark.parametrize("q", [-0.01, 1.01, float("nan")])
def test_percentile_refuses_a_quantile_outside_zero_to_one(q: float) -> None:
    with pytest.raises(ValueError):
        buckets.percentile_from_buckets(buckets.empty_buckets(), q)


def test_percentile_refuses_a_wrong_length_array() -> None:
    with pytest.raises(ValueError):
        buckets.percentile_from_buckets([1, 2], 0.5)


# ── tenant zones and day bounds ─────────────────────────────────────


def test_tenant_zone_defaults_by_region() -> None:
    assert rollup.tenant_zone({}, "IN") == ZoneInfo("Asia/Kolkata")
    assert rollup.tenant_zone(None, "in") == ZoneInfo("Asia/Kolkata")
    assert rollup.tenant_zone({}, "US") == ZoneInfo("UTC")
    assert rollup.tenant_zone({}, None) == ZoneInfo("UTC")


def test_tenant_zone_reads_the_reporting_timezone() -> None:
    settings = {"reporting": {"timezone": "America/New_York"}, "other": 1}
    assert rollup.tenant_zone(settings, "IN") == ZoneInfo("America/New_York")


def test_tenant_zone_reads_a_reporting_settings_object() -> None:
    found = SimpleNamespace(timezone="Europe/London", fiscal_year_start_month=1, currency="USD")
    assert rollup.tenant_zone(found) == ZoneInfo("Europe/London")


@pytest.mark.parametrize("bad", ["Not/AZone", "", "   ", 42, None])
def test_tenant_zone_falls_back_on_an_unknown_zone(bad: object) -> None:
    assert rollup.tenant_zone({"reporting": {"timezone": bad}}, "IN") == ZoneInfo("Asia/Kolkata")
    assert rollup.tenant_zone({"reporting": {"timezone": bad}}, "EU") == ZoneInfo("UTC")


def test_tenant_zone_ignores_a_malformed_reporting_block() -> None:
    assert rollup.tenant_zone({"reporting": "Asia/Tokyo"}, "US") == ZoneInfo("UTC")


def test_ist_day_bounds_start_at_1830_utc_the_previous_day() -> None:
    start, end = rollup.day_bounds(date(2026, 11, 1), IST)
    assert start == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 11, 1, 18, 30, tzinfo=UTC)
    assert start.tzinfo == UTC and end.tzinfo == UTC


def test_day_bounds_cover_25_hours_on_a_dst_fall_back_day() -> None:
    start, end = rollup.day_bounds(date(2026, 11, 1), ZoneInfo("America/New_York"))
    assert end - start == timedelta(hours=25)
    assert start == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)


def test_day_bounds_cover_23_hours_on_a_dst_spring_forward_day() -> None:
    start, end = rollup.day_bounds(date(2026, 3, 8), ZoneInfo("America/New_York"))
    assert end - start == timedelta(hours=23)


def test_local_day_of_an_instant_across_the_ist_midnight() -> None:
    assert rollup.local_day(datetime(2026, 10, 31, 18, 29, tzinfo=UTC), IST) == date(2026, 10, 31)
    assert rollup.local_day(datetime(2026, 10, 31, 18, 30, tzinfo=UTC), IST) == date(2026, 11, 1)
    # A naive instant is read as UTC.
    assert rollup.local_day(datetime(2026, 10, 31, 18, 30, tzinfo=UTC).replace(tzinfo=None), IST) == date(2026, 11, 1)


# ── dimension hashing ───────────────────────────────────────────────


def _dims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "company_id": None,
        "agent_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "agent_ref": None,
        "agent_type": "ap_processor",
        "domain": "finance",
        "entrypoint": "agents",
        "task_type": None,
        "status": "completed",
        "environment": "production",
        "data_quality": "measured",
    }
    base.update(overrides)
    return base


def test_dims_hash_is_a_stable_sha256_hex() -> None:
    first = rollup.dims_hash(_dims())
    assert first == rollup.dims_hash(_dims())
    assert len(first) == 64 and all(c in "0123456789abcdef" for c in first)


def test_dims_hash_of_a_mapping_and_its_ordered_tuple_agree() -> None:
    dims = _dims()
    assert rollup.dims_hash(dims) == rollup.dims_hash(tuple(dims[name] for name in rollup.DIMENSIONS))


def test_dims_hash_reads_a_uuid_and_its_text_alike() -> None:
    agent = uuid.uuid4()
    assert rollup.dims_hash(_dims(agent_id=agent)) == rollup.dims_hash(_dims(agent_id=str(agent)))


def test_dims_hash_tells_every_dimension_apart() -> None:
    seen = {rollup.dims_hash(_dims())}
    for name in rollup.DIMENSIONS:
        seen.add(rollup.dims_hash(_dims(**{name: f"other-{name}"})))
    assert len(seen) == len(rollup.DIMENSIONS) + 1


def test_dims_hash_tells_null_from_the_text_none_and_empty() -> None:
    hashes = {rollup.dims_hash(_dims(domain=value)) for value in (None, "None", "", "null")}
    assert len(hashes) == 4


def test_dims_hash_does_not_collide_when_values_shift_between_dimensions() -> None:
    assert rollup.dims_hash(_dims(agent_type="a", domain="b")) != rollup.dims_hash(_dims(agent_type="a,b", domain=""))


def test_dims_hash_refuses_a_wrong_length_tuple() -> None:
    with pytest.raises(ValueError):
        rollup.dims_hash(("only", "two"))


def test_dimensions_are_insights_daily_columns() -> None:
    from core.models.insights import InsightsDaily, RunFact

    for name in rollup.DIMENSIONS:
        assert name in InsightsDaily.__table__.c
        assert name in RunFact.__table__.c


# ── reconciliation arithmetic ───────────────────────────────────────


def _totals(**overrides: object) -> rollup.Totals:
    values: dict[str, object] = {
        "source_runs": 10,
        "source_tokens": 1000,
        "source_cost_usd": Decimal("100.000000"),
        "rolled_runs": 10,
        "rolled_tokens": 1000,
        "rolled_cost_usd": Decimal("100.000000"),
    }
    values.update(overrides)
    return rollup.Totals(**values)  # type: ignore[arg-type]


def test_matching_totals_reconcile() -> None:
    assert _totals().differences() == []
    assert _totals(source_cost_usd=Decimal("0"), rolled_cost_usd=Decimal("0")).differences() == []


def test_cost_within_one_hundredth_of_a_percent_reconciles() -> None:
    assert _totals(rolled_cost_usd=Decimal("100.010000")).differences() == []
    assert _totals(rolled_cost_usd=Decimal("100.010200")).differences() == ["cost_usd 100.000000!=100.010200"]
    assert rollup.cost_within_tolerance(Decimal("0"), Decimal("0.000001")) is False


def test_counts_must_match_exactly() -> None:
    assert _totals(rolled_runs=9).differences() == ["runs 10!=9"]
    assert _totals(rolled_tokens=1001).differences() == ["tokens 1000!=1001"]


# ── closed-day decisions ────────────────────────────────────────────


def _local(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 5, hour, minute, tzinfo=IST)


TODAY = date(2026, 11, 5)
YESTERDAY = TODAY - timedelta(days=1)


def test_before_two_am_only_unrolled_older_days_are_caught_up() -> None:
    assert insights_tasks.closed_days_to_roll(_local(1, 59), {}, None) == [date(2026, 11, 2), date(2026, 11, 3)]
    full = {date(2026, 11, 2): _local(0), date(2026, 11, 3): _local(0)}
    assert insights_tasks.closed_days_to_roll(_local(1, 59), full, None) == []


def test_from_two_am_yesterday_without_state_is_rolled() -> None:
    states = {date(2026, 11, 2): _local(0), date(2026, 11, 3): _local(0)}
    assert insights_tasks.closed_days_to_roll(_local(2), states, None) == [YESTERDAY]


def test_yesterday_is_rolled_again_when_a_fact_arrived_after_its_rollup() -> None:
    rolled = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
    states = {date(2026, 11, 2): rolled, date(2026, 11, 3): rolled, YESTERDAY: rolled}
    assert insights_tasks.closed_days_to_roll(_local(3), states, rolled + timedelta(seconds=1)) == [YESTERDAY]
    assert insights_tasks.closed_days_to_roll(_local(3), states, rolled - timedelta(seconds=1)) == []
    assert insights_tasks.closed_days_to_roll(_local(3), states, None) == []


def test_a_state_never_rolled_counts_as_needing_a_roll() -> None:
    states = {date(2026, 11, 2): None, date(2026, 11, 3): None, YESTERDAY: None}
    # Older days with any state are left alone; yesterday without a roll time is rolled.
    assert insights_tasks.closed_days_to_roll(_local(4), states, None) == [YESTERDAY]


def test_reconcile_waits_for_two_thirty_and_stops_once_reconciled() -> None:
    assert insights_tasks.should_reconcile(_local(2, 29), None) is False
    assert insights_tasks.should_reconcile(_local(2, 30), None) is True
    assert insights_tasks.should_reconcile(_local(2, 30), "rolled") is True
    assert insights_tasks.should_reconcile(_local(9), "mismatch") is True
    assert insights_tasks.should_reconcile(_local(9), "reconciled") is False


# ── reconcile_day flow (no database) ────────────────────────────────


async def test_reconcile_day_that_agrees_does_not_reroll(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def fake_compare(tenant_id, day, zone, *, final):
        calls.append(("compare", final))
        return _totals(), zone or IST

    async def fake_rollup(*args, **kwargs):
        calls.append(("rollup", False))
        return {}

    monkeypatch.setattr(rollup, "_compare", fake_compare)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    found = await rollup.reconcile_day(uuid.uuid4(), YESTERDAY, zone=IST)
    assert found == {"day": "2026-11-04", "status": "reconciled", "rerolled": False}
    assert calls == [("compare", False)]


async def test_reconcile_day_rerolls_once_and_reconciles(monkeypatch) -> None:
    results = iter([_totals(rolled_runs=7), _totals()])
    calls: list[str] = []

    async def fake_compare(tenant_id, day, zone, *, final):
        calls.append(f"compare:{final}")
        return next(results), IST

    async def fake_rollup(tenant_id, day, *, zone=None):
        calls.append("rollup")
        return {}

    monkeypatch.setattr(rollup, "_compare", fake_compare)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    found = await rollup.reconcile_day(uuid.uuid4(), YESTERDAY)
    assert found["status"] == "reconciled" and found["rerolled"] is True
    assert calls == ["compare:False", "rollup", "compare:True"]


async def test_reconcile_day_still_off_after_a_reroll_is_a_mismatch(monkeypatch) -> None:
    async def fake_compare(tenant_id, day, zone, *, final):
        return _totals(rolled_runs=7), IST

    async def fake_rollup(tenant_id, day, *, zone=None):
        return {}

    logged: list[tuple[str, dict]] = []

    class _Log:
        def warning(self, event, **kw):
            logged.append((event, kw))

    tenant = uuid.uuid4()
    monkeypatch.setattr(rollup, "_compare", fake_compare)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    monkeypatch.setattr(rollup, "logger", _Log())
    before = rollup.reconcile_mismatch_total._value.get()
    found = await rollup.reconcile_day(tenant, YESTERDAY)
    assert found == {"day": "2026-11-04", "status": "mismatch", "rerolled": True, "detail": "runs 10!=7"}
    assert rollup.reconcile_mismatch_total._value.get() == before + 1
    assert logged == [("insights_reconcile_mismatch", {"day": "2026-11-04", "detail": "runs 10!=7"})]
    # Neither the log nor the metric carries the tenant id.
    assert str(tenant) not in repr(logged)
    assert rollup.reconcile_mismatch_total._labelnames == ()


# ── the sweeps ──────────────────────────────────────────────────────


def _run_sync(monkeypatch) -> None:
    monkeypatch.setattr(insights_tasks, "run_async", lambda awaitable: asyncio.run(awaitable))


@pytest.mark.parametrize("task", ["rollup_today", "rollup_closed_days", "reconcile_yesterday"])
def test_every_task_skips_while_insights_is_off(monkeypatch, task: str) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", False)

    async def boom():
        raise AssertionError("must not enumerate tenants while off")

    monkeypatch.setattr(insights_tasks, "_tenants", boom)
    assert getattr(insights_tasks, task)() == {"skipped": "insights_disabled"}


def _tenant_clocks(n: int) -> list[insights_tasks.TenantClock]:
    return [insights_tasks.TenantClock(uuid.uuid4(), IST) for _ in range(n)]


def test_rollup_today_rolls_each_enabled_tenants_local_today(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", True)
    _run_sync(monkeypatch)
    tenants = _tenant_clocks(3)
    disabled = tenants[1].tenant_id
    rolled: list[tuple[uuid.UUID, date, ZoneInfo]] = []

    async def fake_tenants():
        return tenants

    async def fake_enabled(tenant_id):
        return tenant_id != disabled

    async def fake_rollup(tenant_id, day, *, zone=None):
        rolled.append((tenant_id, day, zone))
        return {}

    monkeypatch.setattr(insights_tasks, "_tenants", fake_tenants)
    monkeypatch.setattr(insights_tasks, "_enabled_for", fake_enabled)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    # 20:00 UTC is already the next day in IST.
    monkeypatch.setattr(insights_tasks, "_now", lambda: datetime(2026, 11, 4, 20, 0, tzinfo=UTC))
    found = insights_tasks.rollup_today()
    assert found == {"tenants": 2, "days": 2, "skipped": 1, "failed": 0}
    assert [r[0] for r in rolled] == [tenants[0].tenant_id, tenants[2].tenant_id]
    assert {r[1] for r in rolled} == {date(2026, 11, 5)}
    assert {r[2] for r in rolled} == {IST}


def test_one_tenants_failure_does_not_stop_the_sweep(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", True)
    _run_sync(monkeypatch)
    tenants = _tenant_clocks(3)
    rolled: list[uuid.UUID] = []
    logged: list[tuple[str, dict]] = []

    class _Log:
        def warning(self, event, **kw):
            logged.append((event, kw))

    async def fake_tenants():
        return tenants

    async def fake_enabled(tenant_id):
        return True

    async def fake_rollup(tenant_id, day, *, zone=None):
        if tenant_id == tenants[0].tenant_id:
            raise RuntimeError("database went away")
        rolled.append(tenant_id)
        return {}

    monkeypatch.setattr(insights_tasks, "_tenants", fake_tenants)
    monkeypatch.setattr(insights_tasks, "_enabled_for", fake_enabled)
    monkeypatch.setattr(insights_tasks, "logger", _Log())
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    found = insights_tasks.rollup_today()
    assert found == {"tenants": 2, "days": 2, "skipped": 0, "failed": 1}
    assert rolled == [tenants[1].tenant_id, tenants[2].tenant_id]
    assert logged == [("insights_sweep_tenant_failed", {"task": "rollup_today", "error_type": "RuntimeError"})]
    assert str(tenants[0].tenant_id) not in repr(logged)


def test_a_failed_flag_lookup_counts_as_that_tenants_failure(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", True)
    _run_sync(monkeypatch)
    tenants = _tenant_clocks(2)

    async def fake_tenants():
        return tenants

    async def fake_enabled(tenant_id):
        if tenant_id == tenants[0].tenant_id:
            raise RuntimeError("flag store down")
        return True

    async def fake_rollup(tenant_id, day, *, zone=None):
        return {}

    monkeypatch.setattr(insights_tasks, "_tenants", fake_tenants)
    monkeypatch.setattr(insights_tasks, "_enabled_for", fake_enabled)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    assert insights_tasks.rollup_today() == {"tenants": 1, "days": 1, "skipped": 0, "failed": 1}


def test_rollup_closed_days_rolls_what_the_decision_names(monkeypatch) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", True)
    _run_sync(monkeypatch)
    tenants = _tenant_clocks(1)
    rolled: list[date] = []

    async def fake_tenants():
        return tenants

    async def fake_enabled(tenant_id):
        return True

    async def fake_inputs(tenant, today):
        assert today == TODAY
        return {date(2026, 11, 2): datetime(2026, 11, 2, tzinfo=UTC)}, None

    async def fake_rollup(tenant_id, day, *, zone=None):
        rolled.append(day)
        return {}

    monkeypatch.setattr(insights_tasks, "_tenants", fake_tenants)
    monkeypatch.setattr(insights_tasks, "_enabled_for", fake_enabled)
    monkeypatch.setattr(insights_tasks, "_closed_day_inputs", fake_inputs)
    monkeypatch.setattr(rollup, "rollup_day", fake_rollup)
    # 03:05 IST on 5 November.
    monkeypatch.setattr(insights_tasks, "_now", lambda: datetime(2026, 11, 4, 21, 35, tzinfo=UTC))
    found = insights_tasks.rollup_closed_days()
    assert rolled == [date(2026, 11, 3), YESTERDAY]
    assert found == {"tenants": 1, "days": 2, "skipped": 0, "failed": 0}


@pytest.mark.parametrize(
    ("utc_now", "status", "expected"),
    [
        (datetime(2026, 11, 4, 20, 59, tzinfo=UTC), None, []),  # 02:29 IST: too early
        (datetime(2026, 11, 4, 21, 0, tzinfo=UTC), None, [YESTERDAY]),  # 02:30 IST
        (datetime(2026, 11, 4, 22, 0, tzinfo=UTC), "rolled", [YESTERDAY]),
        (datetime(2026, 11, 4, 22, 0, tzinfo=UTC), "reconciled", []),
    ],
)
def test_reconcile_yesterday_once_per_tenant_day_after_two_thirty(monkeypatch, utc_now, status, expected) -> None:
    from core.config import settings

    monkeypatch.setattr(settings, "insights_enabled", True)
    _run_sync(monkeypatch)
    tenants = _tenant_clocks(1)
    reconciled: list[date] = []

    async def fake_tenants():
        return tenants

    async def fake_enabled(tenant_id):
        return True

    async def fake_status(tenant, day):
        assert day == YESTERDAY
        return status

    async def fake_reconcile(tenant_id, day, *, zone=None):
        reconciled.append(day)
        return {"status": "reconciled"}

    monkeypatch.setattr(insights_tasks, "_tenants", fake_tenants)
    monkeypatch.setattr(insights_tasks, "_enabled_for", fake_enabled)
    monkeypatch.setattr(insights_tasks, "_state_status", fake_status)
    monkeypatch.setattr(rollup, "reconcile_day", fake_reconcile)
    monkeypatch.setattr(insights_tasks, "_now", lambda: utc_now)
    insights_tasks.reconcile_yesterday()
    assert reconciled == expected


async def test_enabled_for_accepts_a_sync_or_async_flag_helper(monkeypatch) -> None:
    import sys
    import types

    module = types.ModuleType("core.insights.flags")

    async def async_flag(tenant_id):
        return False

    module.insights_enabled_for = async_flag  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "core.insights.flags", module)
    assert await insights_tasks._enabled_for(uuid.uuid4()) is False
    module.insights_enabled_for = lambda tenant_id: True  # type: ignore[attr-defined]
    assert await insights_tasks._enabled_for(uuid.uuid4()) is True


# ── Celery registration ─────────────────────────────────────────────


def test_tasks_are_registered_and_scheduled_on_the_maintenance_queue() -> None:
    from celery.schedules import crontab

    from core.tasks.celery_app import app

    assert "core.tasks.insights_tasks" in app.conf.include
    schedule = app.conf.beat_schedule
    today = schedule["insights-rollup-today"]
    closed = schedule["insights-rollup-closed-days"]
    reconcile = schedule["insights-reconcile-yesterday"]
    assert today["task"] == "core.tasks.insights_tasks.rollup_today"
    assert today["schedule"] == 900.0
    assert closed["task"] == "core.tasks.insights_tasks.rollup_closed_days"
    assert closed["schedule"] == crontab(minute=5)
    assert reconcile["task"] == "core.tasks.insights_tasks.reconcile_yesterday"
    assert reconcile["schedule"] == crontab(minute=35)
    for entry in (today, closed, reconcile):
        assert entry["options"]["queue"] == "maintenance"
        assert entry["task"] in app.tasks


@pytest.mark.parametrize(
    ("settings", "region"),
    [
        ({}, "IN"),
        ({}, "US"),
        ({"reporting": {"timezone": "Asia/Tokyo"}}, "IN"),
        ({"reporting": {"timezone": " Europe/Paris "}}, "US"),
        ({"reporting": {"timezone": "Not/AZone"}}, "IN"),
        ({"reporting": {"timezone": 42}}, "US"),
        ({"reporting": "UTC"}, "IN"),
    ],
)
def test_tenant_zone_agrees_with_the_reporting_settings(settings: dict, region: str) -> None:
    """The sweeps read the zone from the tenants row; rollup_day from load_reporting_settings: same answer."""
    from core.insights.reporting_settings import settings_from_tenant

    resolved = settings_from_tenant(settings, region)
    assert rollup.tenant_zone(settings, region) == ZoneInfo(resolved.timezone) == rollup.tenant_zone(resolved)
