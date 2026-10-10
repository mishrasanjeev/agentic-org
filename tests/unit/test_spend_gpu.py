# SPDX-License-Identifier: Apache-2.0
"""GPU node hours: pool hours, cross-tenant allocation, the operator command, settings, tasks, routes and migration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from api.deps import ActiveHumanAdmin
from api.v1 import spend as api
from core.config import Settings, parse_spend_gpu_pools, settings
from core.models.spend_gpu import SpendGpuAllocation, SpendGpuPoolHour
from core.spend import gpu, gpu_cli, meter, pricing, vocab
from core.spend.errors import SpendError
from tests.unit.spend_metering_fakes import install_metering, tenant_ids, tenants_since
from tests.unit.spend_usage_fakes import ACTOR, NOW, OTHER_TENANT, TENANT
from tests.unit.test_spend_usage import card, event

TID = str(TENANT)
HOUR = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
RUN = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)
MIGRATION = Path("migrations/versions/v6_z81_spend_gpu.py")
MODELS = (SpendGpuPoolHour, SpendGpuAllocation)
ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(ACTOR), tenant_id=TENANT, email="admin@example.com", role="admin")
MODEL = "inhouse-70b"


@pytest.fixture
def store(monkeypatch):
    from core.spend import tenants

    found = install_metering(monkeypatch)
    monkeypatch.setattr(tenants, "active_tenant_ids", tenant_ids(TENANT, OTHER_TENANT))
    monkeypatch.setattr(tenants, "tenants_since", tenants_since(TENANT, OTHER_TENANT))
    return found


def pool_hour(store, **over) -> SpendGpuPoolHour:
    base = {
        "id": uuid.uuid4(),
        "provider": "vllm",
        "node_pool": "pool-a",
        "models": [MODEL],
        "hour_start": HOUR,
        "node_hours": Decimal("2"),
        "source": "config",
        "status": "pending",
    }
    base.update(over)
    row = SpendGpuPoolHour(**base)
    store.add(row)
    return row


def calls(store, tenant, tokens, *, model=MODEL, provider="vllm", minute=10, created=None, prefix=None):
    """In-house calls of ``tenant`` in the hour: one input record per entry of ``tokens``."""
    prefix = prefix or str(tenant)[:4]
    events = [
        event(
            tenant_id=str(tenant),
            provider=provider,
            model=model,
            quantity=Decimal(count),
            event_time=HOUR + timedelta(minutes=minute),
            idempotency_key=f"llm:{prefix}{i}:input_token",
            source_ref=f"{prefix}{i:03d}",
            billing_account="in_house",
        )
        for i, count in enumerate(tokens)
    ]
    return events, created or HOUR + timedelta(minutes=20)


async def write_calls(store, tenant, tokens, **kw):
    events, created = calls(store, tenant, tokens, **kw)
    await meter.write_events(store, tenant, events, now=HOUR + timedelta(minutes=30))
    for record in store.of("spend_usage_records"):
        if record.created_at is None:
            record.created_at = created
    return events


def gpu_card(tenant=TENANT, **over):
    base = {
        "tenant_id": tenant,
        "provider": "vllm",
        "usage_type": "gpu_hours",
        "model_sku": "pool-a",
        "unit": "gpu_node_hour",
        "unit_price": Decimal("200"),
        "currency": "INR",
    }
    base.update(over)
    return card(**base)


def _printed(text: str) -> dict:
    """The JSON object the command printed last (log lines may precede it)."""
    lines = text.splitlines()
    start = max(i for i, line in enumerate(lines) if line == "{")
    return json.loads("\n".join(lines[start:]))


def gpu_records(store, tenant=None):
    return sorted(
        (
            r
            for r in store.of("spend_usage_records")
            if r.usage_type == "gpu_hours" and (tenant is None or r.tenant_id == tenant)
        ),
        key=lambda r: r.source_ref,
    )


# ---------------------------------------------------------------- pure helpers


class TestLargestRemainder:
    def test_largest_remainder_sums_exactly_and_never_goes_negative(self):
        weights = [(f"call{i:03d}", Decimal(1)) for i in range(150)]
        shares = gpu.largest_remainder(Decimal("0.0001"), weights, vocab.QTY_QUANT)
        assert sum(s for _k, s in shares) == Decimal("0.0001") and all(s >= 0 for _k, s in shares)
        assert sum(1 for _k, s in shares if s > 0) == 100
        assert [k for k, s in shares if s > 0] == [f"call{i:03d}" for i in range(100)]  # ties go by key
        two = gpu.largest_remainder(Decimal("1"), [("a", Decimal(1)), ("b", Decimal(2))], vocab.QTY_QUANT)
        assert two == [("a", Decimal("0.333333")), ("b", Decimal("0.666667"))]
        assert gpu.largest_remainder(Decimal("5"), [], vocab.QTY_QUANT) == []
        assert gpu.largest_remainder(Decimal("5"), [("a", Decimal(0))], vocab.QTY_QUANT) == [("a", Decimal("0.000000"))]
        mixed = gpu.largest_remainder(
            Decimal("1"), [("a", Decimal(0)), ("b", Decimal(-1)), ("c", Decimal(3))], vocab.QTY_QUANT
        )
        assert mixed == [("a", Decimal("0.000000")), ("b", Decimal("0.000000")), ("c", Decimal("1.000000"))]
        money = gpu.largest_remainder(Decimal("100"), [("x", Decimal(1))] * 3, vocab.AMOUNT_QUANT)
        assert sum(s for _k, s in money) == Decimal("100")

    def test_whole_hours_and_config_hours(self):
        assert gpu.whole_hour(HOUR.replace(tzinfo=None), field="h") == HOUR  # naive is read as UTC
        with pytest.raises(SpendError) as info:
            gpu.whole_hour(datetime(2026, 10, 1, 9, 30, tzinfo=UTC), field="h")
        assert info.value.code == "invalid_period"
        pools = parse_spend_gpu_pools(
            json.dumps(
                [
                    {
                        "provider": "vllm",
                        "node_pool": "pool-a",
                        "models": [MODEL],
                        "nodes": 2,
                        "from": "2026-09-01T00:00:00Z",
                    },
                    {
                        "provider": "ollama",
                        "node_pool": "pool-b",
                        "models": ["m"],
                        "nodes": "1.5",
                        "from": "2026-10-01T05:00:00+00:00",
                        "to": "2026-10-01T07:00:00Z",
                    },
                ]
            )
        )
        hours = gpu.config_hours(pools, now=datetime(2026, 10, 1, 9, 30, tzinfo=UTC))
        a = [h for p, h in hours if p.node_pool == "pool-a"]
        b = [h for p, h in hours if p.node_pool == "pool-b"]
        assert a[0] == datetime(2026, 9, 24, 10, 0, tzinfo=UTC) and a[-1] == datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
        assert b == [datetime(2026, 10, 1, 5, 0, tzinfo=UTC), datetime(2026, 10, 1, 6, 0, tzinfo=UTC)]


# ---------------------------------------------------------------- settings


class TestGpuPoolSettings:
    def test_gpu_pools_setting_is_validated_only_while_on(self):
        good = json.dumps(
            [
                {
                    "provider": "VLLM",
                    "node_pool": "Pool-A",
                    "models": ["InHouse-70B", "inhouse-70b"],
                    "nodes": 4,
                    "from": "2026-10-01T00:00:00Z",
                }
            ]
        )
        (pool,) = parse_spend_gpu_pools(good)
        assert (pool.provider, pool.node_pool, pool.models, pool.nodes) == ("vllm", "pool-a", (MODEL,), Decimal(4))
        assert pool.start == datetime(2026, 10, 1, tzinfo=UTC) and pool.end is None
        assert parse_spend_gpu_pools("") == () and parse_spend_gpu_pools(None) == ()
        entry = {"provider": "vllm", "node_pool": "p", "models": ["m"], "nodes": 1, "from": "2026-10-01T00:00:00Z"}
        bad = [
            "{not json",
            '{"provider": "vllm"}',
            json.dumps(["not a pool"]),
            json.dumps([{**entry, "extra": 1}]),
            json.dumps([{**entry, "provider": "openai"}]),
            json.dumps([{**entry, "models": []}]),
            json.dumps([{**entry, "models": ["m"] * 51}]),
            json.dumps([{**entry, "models": ["=bad"]}]),
            json.dumps([{**entry, "node_pool": 5}]),
            json.dumps([{**entry, "node_pool": "p" * 65}]),  # the tables hold 64 characters
            json.dumps([{**entry, "from": "2026-10-01T00:30:00Z"}]),
            json.dumps([{**entry, "from": "yesterday"}]),
            json.dumps([{**entry, "to": "2026-10-01T00:00:00Z"}]),
            json.dumps([{**entry, "nodes": 0}]),
            json.dumps([{**entry, "nodes": 10001}]),
            json.dumps([{**entry, "nodes": "1.00001"}]),
            json.dumps([{**entry, "nodes": True}]),
            json.dumps([{**entry, "nodes": "many"}]),
            json.dumps([entry, {**entry, "from": "2026-10-02T00:00:00Z"}]),  # the first one never ends
        ]
        for raw in bad:
            with pytest.raises(ValueError, match="spend_gpu_pools_json"):
                parse_spend_gpu_pools(raw)
        ok_twice = json.dumps(
            [{**entry, "to": "2026-10-02T00:00:00Z"}, {**entry, "from": "2026-10-02T00:00:00Z", "nodes": 2}]
        )
        assert len(parse_spend_gpu_pools(ok_twice)) == 2
        assert Settings(spend_intelligence_enabled=False, spend_gpu_pools_json="{not json").spend_gpu_pools_json
        assert Settings(spend_intelligence_enabled=True, spend_gpu_pools_json=good).spend_gpu_pools_json == good
        with pytest.raises(ValidationError) as info:
            Settings(spend_intelligence_enabled=True, spend_gpu_pools_json="{not json")
        assert "spend_gpu_pools_json" in str(info.value)
        assert Settings.model_fields["spend_gpu_pools_json"].default == ""

    def test_deeply_nested_json_is_refused_naming_the_setting(self):
        deep = "[" * 100_000 + "]" * 100_000  # beyond the parser's nesting limit: RecursionError, not ValueError
        with pytest.raises(ValueError, match="spend_gpu_pools_json: not valid JSON"):
            parse_spend_gpu_pools(deep)
        for field in ("spend_gpu_pools_json", "spend_provider_billing_timezones_json"):
            with pytest.raises(ValidationError) as info:
                Settings(spend_intelligence_enabled=True, **{field: deep})
            assert f"{field}: not valid JSON" in str(info.value) and "[[[" not in str(info.value)

    def test_the_settings_sku_pattern_is_the_spend_one(self):
        from core.config import SPEND_SKU_PATTERN

        assert SPEND_SKU_PATTERN == vocab.SKU_PATTERN


# ---------------------------------------------------------------- pool hours


class TestPoolHours:
    @pytest.mark.asyncio
    async def test_config_pools_materialise_pending_hours_without_overwriting(self, monkeypatch, store):
        pools = [
            {"provider": "vllm", "node_pool": "pool-a", "models": [MODEL], "nodes": 2, "from": "2026-10-01T05:00:00Z"}
        ]
        monkeypatch.setattr(settings, "spend_gpu_pools_json", json.dumps(pools))
        metered = pool_hour(
            store, hour_start=datetime(2026, 10, 1, 6, 0, tzinfo=UTC), node_hours=Decimal(3), source="metrics"
        )
        done = pool_hour(store, hour_start=datetime(2026, 10, 1, 7, 0, tzinfo=UTC), status="allocated")
        now = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
        assert await gpu.materialise_config_hours(now=now) == 2  # 05:00 and 08:00; 09:00 has not closed
        rows = {r.hour_start.hour: r for r in store.of("spend_gpu_pool_hours")}
        assert sorted(rows) == [5, 6, 7, 8]
        assert rows[6] is metered and rows[6].node_hours == 3 and rows[6].source == "metrics"
        assert rows[7] is done and rows[7].status == "allocated"
        assert rows[5].source == "config" and rows[5].status == "pending" and rows[5].models == [MODEL]
        assert await gpu.materialise_config_hours(now=now) == 0
        monkeypatch.setattr(settings, "spend_gpu_pools_json", "")
        assert await gpu.materialise_config_hours(now=now) == 0

    @pytest.mark.asyncio
    async def test_gpu_cli_records_whole_hours_and_refuses_allocated_rows(self, monkeypatch, store, capsys):
        pool_hour(store, node_pool="pool-b", hour_start=datetime(2026, 10, 1, 7, 0, tzinfo=UTC), status="allocated")
        pending = pool_hour(store, node_pool="pool-b", hour_start=datetime(2026, 10, 1, 6, 0, tzinfo=UTC))
        argv = [
            "record", "--provider", "vllm", "--pool", "Pool-B", "--models", f"{MODEL},other",
            "--hour-start", "2026-10-01T05:00:00Z", "--hour-end", "2026-10-01T08:00:00Z",
            "--node-hours", "1.5", "--source", "metrics", "--actor", "ops-oncall",
        ]  # fmt: skip
        assert await asyncio.to_thread(gpu_cli.main, argv) == 0
        out = _printed(capsys.readouterr().out)
        assert out == {"created": 1, "updated": 1, "already_allocated": ["2026-10-01T07:00:00+00:00"]}
        assert (
            pending.node_hours == Decimal("1.5") and pending.source == "metrics" and pending.recorded_by == "ops-oncall"
        )
        assert pending.models == [MODEL, "other"] and store.commits >= 1
        created = next(r for r in store.of("spend_gpu_pool_hours") if r.hour_start.hour == 5)
        assert (created.node_pool, created.status, created.source) == ("pool-b", "pending", "metrics")
        listing = ["list", "--start", "2026-10-01T00:00:00Z", "--end", "2026-10-02T00:00:00Z"]
        assert await asyncio.to_thread(gpu_cli.main, listing) == 0
        items = _printed(capsys.readouterr().out)["items"]
        assert all(i["skipped_node_hours"] == "0" for i in items)
        assert [i["hour_start"][11:13] for i in items] == ["05", "06", "07"]
        half = argv[:8] + ["2026-10-01T05:30:00Z"] + argv[9:]
        assert await asyncio.to_thread(gpu_cli.main, half) == 2
        assert "invalid_period" in capsys.readouterr().err
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert await asyncio.to_thread(gpu_cli.main, argv) == 2
        assert "spend intelligence is off" in capsys.readouterr().err
        with pytest.raises(SystemExit):
            gpu_cli.build_parser().parse_args(["record", "--provider", "vllm", "--hour-start", "soon"])

    def test_record_input_is_checked(self):
        base = {
            "provider": "vllm",
            "node_pool": "pool",
            "models": [MODEL],
            "hour_start": HOUR,
            "hour_end": None,
            "node_hours": "1",
            "source": "manual",
            "actor": "ops",
        }
        assert len(gpu.check_record(**base)["hours"]) == 1
        for over, code in (
            ({"provider": "openai"}, "invalid_reference"),
            ({"source": "config"}, "invalid_value"),
            ({"node_pool": "p" * 65}, "invalid_sku"),
            ({"models": []}, "invalid_sku"),
            ({"hour_end": HOUR}, "invalid_period"),
            ({"hour_end": HOUR + timedelta(hours=745)}, "range_too_long"),
            ({"node_hours": "0"}, "invalid_number"),
            ({"node_hours": "1.00001"}, "invalid_number"),
            ({"actor": "=cmd"}, "invalid_text"),
            ({"actor": ""}, "invalid_text"),
        ):
            with pytest.raises(SpendError) as info:
                gpu.check_record(**{**base, **over})
            assert info.value.code == code, over

    @pytest.mark.asyncio
    async def test_record_logs_who_recorded_what_and_the_values_it_overwrote(self, store):
        from structlog.testing import capture_logs

        replaced = pool_hour(
            store,
            node_pool="pool-b",
            hour_start=datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
            node_hours=Decimal("2.5"),
            source="manual",
            recorded_by="ops-day",
        )
        with capture_logs() as logs:
            out = await gpu.record_hours(
                now=RUN,
                provider="vllm",
                node_pool="Pool-B",
                models=[MODEL, "other"],
                hour_start=datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
                hour_end=datetime(2026, 10, 1, 7, 0, tzinfo=UTC),
                node_hours="1.5",
                source="metrics",
                actor="ops-night",
            )
        assert out == {"created": 1, "updated": 1, "already_allocated": []}
        assert (replaced.node_hours, replaced.source, replaced.recorded_by) == (Decimal("1.5"), "metrics", "ops-night")
        (recorded,) = [e for e in logs if e["event"] == "spend_gpu_hours_recorded"]
        assert {k: v for k, v in recorded.items() if k not in ("event", "log_level")} == {
            "actor": "ops-night",
            "provider": "vllm",
            "node_pool": "pool-b",
            "models": [MODEL, "other"],
            "first_hour": "2026-10-01T05:00:00+00:00",
            "last_hour": "2026-10-01T06:00:00+00:00",
            "node_hours": "1.5",
            "source": "metrics",
            "created": 1,
            "updated": 1,
            "already_allocated": 0,
        }
        (overwritten,) = [e for e in logs if e["event"] == "spend_gpu_hour_overwritten"]
        assert overwritten["hour_start"] == "2026-10-01T06:00:00+00:00" and overwritten["actor"] == "ops-night"
        assert (
            overwritten["previous_node_hours"],
            overwritten["previous_source"],
            overwritten["previous_recorded_by"],
        ) == ("2.5", "manual", "ops-day")

    @pytest.mark.asyncio
    async def test_record_upserts_atomically_and_never_overwrites_a_claimed_hour(self, monkeypatch, store):
        from sqlalchemy.sql.dml import Insert

        claimed = pool_hour(store, hour_start=datetime(2026, 10, 1, 5, 0, tzinfo=UTC))
        real = store.execute
        upserts: list = []

        async def racing(statement, params=None):
            if isinstance(statement, Insert) and statement.table.name == "spend_gpu_pool_hours":
                if not upserts:
                    # Between the command's read and its first upsert: the allocator claims 05:00, and another
                    # command inserts 06:00 (pending) and 07:00 (already being allocated).
                    claimed.status = "allocating"
                    pool_hour(store, hour_start=datetime(2026, 10, 1, 6, 0, tzinfo=UTC), recorded_by="other")
                    pool_hour(store, hour_start=datetime(2026, 10, 1, 7, 0, tzinfo=UTC), status="allocating")
                upserts.append(statement)
            return await real(statement, params)

        monkeypatch.setattr(store, "execute", racing)
        out = await gpu.record_hours(
            now=RUN,
            provider="vllm",
            node_pool="pool-a",
            models=[MODEL],
            hour_start=datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
            hour_end=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            node_hours="1.5",
            source="metrics",
            actor="ops",
        )
        assert out == {
            "created": 1,
            "updated": 1,
            "already_allocated": ["2026-10-01T05:00:00+00:00", "2026-10-01T07:00:00+00:00"],
        }
        assert (claimed.node_hours, claimed.source, claimed.status) == (Decimal(2), "config", "allocating")
        rows = {r.hour_start.hour: r for r in store.of("spend_gpu_pool_hours")}
        assert (rows[6].node_hours, rows[6].recorded_by) == (Decimal("1.5"), "ops")  # still pending: updated
        assert rows[7].node_hours == Decimal(2) and (rows[8].status, rows[8].source) == ("pending", "metrics")
        sql = " ".join(str(upserts[0].compile(dialect=postgresql.dialect())).split())
        assert "ON CONFLICT (provider, node_pool, hour_start) DO UPDATE SET" in sql
        assert "WHERE spend_gpu_pool_hours.status = " in sql and sql.endswith("RETURNING spend_gpu_pool_hours.id")

    def test_record_refuses_hours_not_yet_over_or_beyond_the_lookback(self, store, capsys):
        now = datetime(2026, 10, 8, 9, 40, tzinfo=UTC)
        assert gpu.RECORD_LOOKBACK == gpu.CONFIG_LOOKBACK == timedelta(days=7)
        whole = [datetime(2026, 10, 1, 10, 0, tzinfo=UTC) + gpu.HOUR * n for n in range(167)]
        gpu.check_record_window(whole, now=now)  # from 7 days back (to the hour) to the last hour that is over
        for hours, field in (
            ([datetime(2026, 10, 8, 9, 0, tzinfo=UTC)], "hour_end"),  # the current hour is not over
            ([datetime(2026, 10, 8, 8, 0, tzinfo=UTC), datetime(2026, 10, 8, 10, 0, tzinfo=UTC)], "hour_end"),
            ([datetime(2026, 10, 1, 9, 0, tzinfo=UTC)], "hour_start"),  # more than 7 days back
        ):
            with pytest.raises(SpendError) as info:
                gpu.check_record_window(hours, now=now)
            assert info.value.code == "invalid_period" and info.value.message.startswith(field), hours
        record = [
            "record", "--provider", "vllm", "--pool", "pool-a", "--models", MODEL,
            "--node-hours", "1", "--source", "manual", "--actor", "ops", "--hour-start",
        ]  # fmt: skip
        for start, why in (("2026-10-01T09:00:00Z", "hour_end"), ("2026-09-24T08:00:00Z", "hour_start")):
            assert gpu_cli.main([*record, start]) == 2  # the clock reads 09:00 on 1 October
            assert f"invalid_period: {why}" in capsys.readouterr().err
        assert store.of("spend_gpu_pool_hours") == []
        assert gpu_cli.main([*record, "2026-09-24T09:00:00Z"]) == 0
        assert _printed(capsys.readouterr().out)["created"] == 1


# ---------------------------------------------------------------- allocation


class TestAllocation:
    @pytest.mark.asyncio
    async def test_allocation_spreads_one_hour_across_tenants_by_tokens(self, store):
        hour = pool_hour(store)
        store.add(gpu_card(TENANT))
        store.add(gpu_card(OTHER_TENANT))
        await write_calls(store, TENANT, [1000, 1000, 1000])
        await write_calls(store, OTHER_TENANT, [1000])
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["claimed"] and out["tenants"] == 2 and out["records"] == 4 and out["idle"] is False
        assert out["total_tokens"] == "4000.000000" and out["failed"] == 0
        mine, theirs = gpu_records(store, TENANT), gpu_records(store, OTHER_TENANT)
        assert [r.quantity for r in mine] == [Decimal("0.5")] * 3 and [r.quantity for r in theirs] == [Decimal("0.5")]
        assert [r.amount for r in mine + theirs] == [Decimal("100")] * 4
        allocations = {a.tenant_id: a for a in store.of("spend_gpu_allocations")}
        assert allocations[TENANT].node_hours == Decimal("1.5") and allocations[TENANT].status == "written"
        assert allocations[TENANT].amount == Decimal("300") and allocations[TENANT].currency == "INR"
        assert allocations[TENANT].records == 3 and allocations[TENANT].tokens == Decimal(3000)
        assert allocations[OTHER_TENANT].node_hours == Decimal("0.5")
        assert hour.status == "allocated" and hour.total_tokens == Decimal(4000) and hour.tenant_count == 2
        assert hour.allocated_at == RUN and hour.idle is False and hour.frozen_at == NOW

    @pytest.mark.asyncio
    async def test_allocation_counts_only_the_pool_models(self, store):
        hour = pool_hour(store)
        await write_calls(store, TENANT, [1000])
        await write_calls(store, TENANT, [3000], model="another-model", prefix="other")
        await write_calls(store, TENANT, [3000], provider="ollama", prefix="ollama")
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["total_tokens"] == "1000.000000"
        (record,) = gpu_records(store)
        assert record.quantity == Decimal(2) and record.allocated_from == f"{TID[:4]}000"

    @pytest.mark.asyncio
    async def test_allocation_skips_card_priced_in_house_calls(self, store):
        hour = pool_hour(store)
        store.add(card(tenant_id=OTHER_TENANT, provider="vllm", model_sku=MODEL, unit_price=Decimal("1")))
        await write_calls(store, TENANT, [500])
        await write_calls(store, OTHER_TENANT, [5000, 5000])
        assert {r.price_source for r in store.of("spend_usage_records") if r.tenant_id == OTHER_TENANT} == {"contract"}
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["priced_calls_skipped"] == 2 and out["tenants"] == 1 and out["total_tokens"] == "10500.000000"
        # The card-priced calls carry their own cost: no GPU record, and their share stays with the platform.
        assert gpu_records(store, OTHER_TENANT) == []
        assert [r.quantity for r in gpu_records(store)] == [Decimal("0.095238")]  # 2 hours x 500 / 10500
        assert hour.priced_calls_skipped == 2 and hour.skipped_node_hours == Decimal("1.904762")
        (allocation,) = store.of("spend_gpu_allocations")
        assert allocation.tenant_id == TENANT and allocation.node_hours + hour.skipped_node_hours == hour.node_hours

    @pytest.mark.asyncio
    async def test_another_tenants_card_never_raises_my_share(self, store):
        hour = pool_hour(store, node_hours=Decimal("1"))
        store.add(card(tenant_id=OTHER_TENANT, provider="vllm", model_sku=MODEL, unit_price=Decimal("0.5")))
        await write_calls(store, OTHER_TENANT, [900])
        await write_calls(store, TENANT, [100])
        await gpu.allocate_hour(hour.id, now=RUN)
        assert [r.quantity for r in gpu_records(store, TENANT)] == [Decimal("0.1")]  # 10% of the tokens, 10%
        assert hour.skipped_node_hours == Decimal("0.9") and gpu_records(store, OTHER_TENANT) == []

    @pytest.mark.asyncio
    async def test_calls_a_zero_priced_card_prices_share_the_hour(self, store):
        hour = pool_hour(store)
        store.add(card(tenant_id=OTHER_TENANT, provider="vllm", model_sku=MODEL, unit_price=Decimal("0")))
        await write_calls(store, OTHER_TENANT, [3000])
        await write_calls(store, TENANT, [1000])
        mine = {r.price_source for r in store.of("spend_usage_records") if r.tenant_id == OTHER_TENANT}
        assert mine == {"contract"}
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["priced_calls_skipped"] == 0 and out["tenants"] == 2
        assert [r.quantity for r in gpu_records(store, OTHER_TENANT)] == [Decimal("1.5")]
        assert [r.quantity for r in gpu_records(store, TENANT)] == [Decimal("0.5")]
        assert hour.skipped_node_hours == 0

    @pytest.mark.asyncio
    async def test_a_deleted_tenants_share_stays_with_the_platform(self, monkeypatch, store):
        from core.spend import tenants

        hour = pool_hour(store)
        monkeypatch.setattr(tenants, "tenants_since", tenants_since(TENANT, OTHER_TENANT, deleted=(OTHER_TENANT,)))
        await write_calls(store, TENANT, [1000])
        await write_calls(store, OTHER_TENANT, [3000])
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["tenants"] == 1 and out["total_tokens"] == "4000.000000"
        assert [r.quantity for r in gpu_records(store, TENANT)] == [Decimal("0.5")]
        assert gpu_records(store, OTHER_TENANT) == [] and hour.skipped_node_hours == Decimal("1.5")
        assert [a.tenant_id for a in store.of("spend_gpu_allocations")] == [TENANT]

    @pytest.mark.asyncio
    async def test_a_deleted_tenants_frozen_row_is_settled_on_a_resumed_run(self, monkeypatch, store):
        from core.spend import tenants

        hour = pool_hour(store, status="allocating", claimed_at=NOW - timedelta(hours=2), frozen_at=NOW)
        await write_calls(store, OTHER_TENANT, [3000])
        store.add(
            SpendGpuAllocation(
                id=uuid.uuid4(),
                tenant_id=OTHER_TENANT,
                pool_hour_id=hour.id,
                provider="vllm",
                node_pool="pool-a",
                hour_start=HOUR,
                tokens=Decimal(3000),
                status="frozen",
                records=0,
            )
        )
        monkeypatch.setattr(tenants, "tenants_since", tenants_since(OTHER_TENANT, deleted=(OTHER_TENANT,)))
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["claimed"] and out["tenants"] == 0 and gpu_records(store) == []
        (allocation,) = store.of("spend_gpu_allocations")
        assert (allocation.status, allocation.node_hours, allocation.records) == ("written", 0, 0)
        assert hour.status == "allocated" and hour.skipped_node_hours == hour.node_hours

    @pytest.mark.asyncio
    async def test_a_share_that_rounds_to_nothing_settles_its_row(self, store):
        hour = pool_hour(store, node_hours=Decimal("0.0001"))
        await write_calls(store, TENANT, [1])
        await write_calls(store, OTHER_TENANT, [1_000_000_000])
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["tenants"] == 2 and out["failed"] == 0 and gpu_records(store, TENANT) == []
        allocations = {a.tenant_id: a for a in store.of("spend_gpu_allocations")}
        assert (allocations[TENANT].status, allocations[TENANT].node_hours, allocations[TENANT].records) == (
            "written",
            0,
            0,
        )
        assert allocations[OTHER_TENANT].node_hours == Decimal("0.0001") and hour.status == "allocated"
        assert all(a.status == "written" for a in allocations.values())  # nothing left frozen after the close

    @pytest.mark.asyncio
    async def test_a_frozen_row_left_without_charged_tokens_is_settled(self, store):
        # A resumed hour whose tenant's calls a card now prices above zero (a restatement since the first run).
        hour = pool_hour(store, status="allocating", claimed_at=NOW - timedelta(hours=2), frozen_at=NOW)
        store.add(card(tenant_id=TENANT, provider="vllm", model_sku=MODEL, unit_price=Decimal("1")))
        await write_calls(store, TENANT, [500])
        store.add(
            SpendGpuAllocation(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                pool_hour_id=hour.id,
                provider="vllm",
                node_pool="pool-a",
                hour_start=HOUR,
                tokens=Decimal(500),
                status="frozen",
                records=0,
            )
        )
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["tenants"] == 0 and out["priced_calls_skipped"] == 1 and gpu_records(store) == []
        (allocation,) = store.of("spend_gpu_allocations")
        assert (allocation.status, allocation.tokens, allocation.node_hours) == ("written", 0, 0)
        assert hour.status == "allocated" and hour.skipped_node_hours == hour.node_hours

    @pytest.mark.asyncio
    async def test_allocation_prices_each_tenant_share_once_and_conserves_money(self, monkeypatch, store):
        from core.models.spend import SpendFxRate

        hour = pool_hour(store, node_hours=Decimal("1"))
        store.add(gpu_card(TENANT, unit_price=Decimal("1"), currency="USD"))
        store.add(
            SpendFxRate(
                id=uuid.uuid4(),
                tenant_id=TENANT,
                rate_date=HOUR.date(),
                currency="USD",
                rate_to_inr=Decimal("83.12345678"),
            )
        )
        await write_calls(store, TENANT, [1, 1, 1])
        priced: list = []
        real = pricing.price

        async def counted(session, tenant_id, usage):
            priced.append(usage)
            return await real(session, tenant_id, usage)

        monkeypatch.setattr(pricing, "price", counted)
        await gpu.allocate_hour(hour.id, now=RUN)
        assert len(priced) == 1 and priced[0].quantity == Decimal("1") and priced[0].unit == "gpu_node_hour"
        records = gpu_records(store)
        assert [r.quantity for r in records] == [Decimal("0.333334"), Decimal("0.333333"), Decimal("0.333333")]
        assert sum(r.amount for r in records) == Decimal("1")
        assert sum(r.amount_inr for r in records) == Decimal("83.1234567800")
        assert {r.currency for r in records} == {"USD"} and {r.unit_price for r in records} == {Decimal("1")}
        assert all(r.fx_estimated is False and r.unconverted is False for r in records)

    @pytest.mark.asyncio
    async def test_allocation_copies_attribution_and_flags_allocated(self, store):
        hour = pool_hour(store)
        await write_calls(store, TENANT, [100])
        node, unit, agent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        for record in store.of("spend_usage_records"):
            record.org_node_id, record.business_unit_node_id = node, unit
            record.attribution_path, record.unattributed_reason = "agent_mapping", None
            record.product_line, record.use_case, record.agent_id = "retail", "kyc", agent
            record.run_id, record.application, record.risk_tier = "run_1", "agents", "high"
        await gpu.allocate_hour(hour.id, now=RUN)
        (record,) = gpu_records(store)
        assert (record.org_node_id, record.business_unit_node_id, record.attribution_path) == (
            node,
            unit,
            "agent_mapping",
        )
        assert (record.product_line, record.use_case, record.agent_id) == ("retail", "kyc", agent)
        assert (record.run_id, record.application, record.risk_tier) == ("run_1", "agents", "high")
        assert record.allocated is True and record.allocated_from == f"{TID[:4]}000" and record.calls == 0
        assert record.idempotency_key == f"gpu:{hour.id}:{TID[:4]}000" and record.event_time == HOUR
        assert (record.provider, record.model, record.billing_account) == ("vllm", "pool-a", "in_house")
        assert record.unit == "gpu_node_hour" and record.usage_type == "gpu_hours"
        rollup = [r for r in store.of("spend_usage_rollups") if r.usage_type == "gpu_hours"]
        assert len(rollup) == 1 and rollup[0].allocated_count == 1

    @pytest.mark.asyncio
    async def test_idle_hour_stays_with_the_platform(self, store):
        hour = pool_hour(store)
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["idle"] is True and out["records"] == 0
        assert hour.status == "allocated" and hour.idle is True and hour.total_tokens == 0
        assert gpu_records(store) == [] and store.of("spend_gpu_allocations") == []

    @pytest.mark.asyncio
    async def test_claim_is_exclusive_and_a_stale_claim_is_resumed(self, store):
        # Claims run on the database clock (the fake's now() is NOW), never on the run's own start time.
        busy = pool_hour(store, status="allocating", claimed_at=NOW - timedelta(minutes=10))
        assert await gpu.allocate_hour(busy.id, now=RUN) == {"id": str(busy.id), "claimed": False}
        assert await gpu.allocate_hour(busy.id, now=RUN + timedelta(hours=3)) == {"id": str(busy.id), "claimed": False}
        done = pool_hour(store, status="allocated", hour_start=HOUR - timedelta(hours=1))
        assert (await gpu.allocate_hour(done.id, now=RUN))["claimed"] is False
        stale = pool_hour(
            store,
            status="allocating",
            claimed_at=NOW - timedelta(hours=2),
            frozen_at=HOUR + timedelta(minutes=30),
            hour_start=HOUR + timedelta(hours=1),
        )
        out = await gpu.allocate_hour(stale.id, now=RUN)
        assert out["claimed"] and stale.status == "allocated" and stale.claimed_at == NOW
        assert stale.frozen_at == HOUR + timedelta(minutes=30)  # a resumed claim keeps its frozen instant

    @pytest.mark.asyncio
    async def test_rerun_after_a_crash_reuses_frozen_tokens(self, monkeypatch, store):
        hour = pool_hour(store)
        await write_calls(store, TENANT, [1000])
        await write_calls(store, OTHER_TENANT, [3000])
        real = gpu._write_tenant

        async def crash_for_other(tenant_id, *args, **kwargs):
            if tenant_id == OTHER_TENANT:
                raise RuntimeError("worker lost")
            return await real(tenant_id, *args, **kwargs)

        monkeypatch.setattr(gpu, "_write_tenant", crash_for_other)
        out = await gpu.allocate_hour(hour.id, now=RUN)
        assert out["failed"] == 1 and hour.status == "allocating"
        statuses = {a.tenant_id: a.status for a in store.of("spend_gpu_allocations")}
        assert statuses == {TENANT: "written", OTHER_TENANT: "frozen"}
        await write_calls(store, OTHER_TENANT, [9000], prefix="late", created=NOW + timedelta(minutes=5))
        monkeypatch.setattr(gpu, "_write_tenant", real)
        assert (await gpu.allocate_hour(hour.id, now=RUN))["claimed"] is False  # the claim is still fresh
        assert hour.claimed_at == NOW  # the database clock at the claim
        hour.claimed_at = NOW - timedelta(hours=2)  # two hours pass on the database clock
        later = RUN + timedelta(hours=2)
        out = await gpu.allocate_hour(hour.id, now=later)
        assert out["claimed"] and out["failed"] == 0 and out["total_tokens"] == "4000.000000"
        assert [r.quantity for r in gpu_records(store, TENANT)] == [Decimal("0.5")]  # not written twice
        assert [r.quantity for r in gpu_records(store, OTHER_TENANT)] == [Decimal("1.5")]
        assert hour.status == "allocated" and hour.allocated_at == later

    @pytest.mark.asyncio
    async def test_a_paused_tenant_keeps_its_share_frozen_and_the_hour_resumes(self, monkeypatch, store):
        from core import feature_flags

        hour = pool_hour(store)
        await write_calls(store, TENANT, [1000])
        await write_calls(store, OTHER_TENANT, [3000])
        paused = {OTHER_TENANT}

        async def flag_row(tenant_id, key):
            assert key == "spend.metering_paused"
            return {"enabled": True, "rollout_percentage": 100} if tenant_id in paused else None

        async def shared_cache_path(*args, **kwargs):
            raise AssertionError("the allocation must not use the flag module's shared cache")

        monkeypatch.setattr(feature_flags, "_query_flag", flag_row)
        monkeypatch.setattr(feature_flags, "is_enabled", shared_cache_path)
        out = await gpu.allocate_pending(now=RUN)
        assert out == {"materialised": 0, "hours": 1, "idle": 0, "records": 1, "failed": 0, "paused": 1}
        assert hour.status == "allocating" and hour.allocated_at is None  # not closed
        statuses = {a.tenant_id: a.status for a in store.of("spend_gpu_allocations")}
        assert statuses == {TENANT: "written", OTHER_TENANT: "frozen"}
        assert gpu_records(store, OTHER_TENANT) == []
        paused.clear()  # metering resumes; the claim goes stale on the database clock
        hour.claimed_at = NOW - timedelta(hours=2)
        out = await gpu.allocate_hour(hour.id, now=RUN + timedelta(hours=2))
        assert out["paused"] == 0 and out["failed"] == 0 and hour.status == "allocated"
        assert [r.quantity for r in gpu_records(store, TENANT)] == [Decimal("0.5")]  # not written twice
        assert [r.quantity for r in gpu_records(store, OTHER_TENANT)] == [Decimal("1.5")]

    @pytest.mark.asyncio
    async def test_allocation_waits_seventy_five_minutes(self, store):
        hour = pool_hour(store)
        await write_calls(store, TENANT, [100])
        early = await gpu.allocate_pending(now=HOUR + timedelta(minutes=74))
        assert early == {"materialised": 0, "hours": 0, "idle": 0, "records": 0, "failed": 0}
        assert hour.status == "pending"
        out = await gpu.allocate_pending(now=HOUR + timedelta(minutes=75))
        assert out == {"materialised": 0, "hours": 1, "idle": 0, "records": 1, "failed": 0}
        assert hour.status == "allocated"

    @pytest.mark.asyncio
    async def test_allocate_pending_isolates_a_failed_hour(self, monkeypatch, store):
        first = pool_hour(store)
        pool_hour(store, hour_start=HOUR + timedelta(hours=1))

        real = gpu.allocate_hour

        async def flaky(pool_hour_id, *, now):
            if pool_hour_id == first.id:
                raise RuntimeError("hour broken")
            return await real(pool_hour_id, now=now)

        monkeypatch.setattr(gpu, "allocate_hour", flaky)
        out = await gpu.allocate_pending(now=RUN + timedelta(hours=1))
        assert out["failed"] == 1 and out["hours"] == 1 and out["idle"] == 1

    @pytest.mark.asyncio
    async def test_fresh_hours_go_before_resumed_ones(self, monkeypatch, store):
        stale = pool_hour(store, status="allocating", claimed_at=NOW - timedelta(hours=3))
        fresh = pool_hour(store, hour_start=HOUR + timedelta(hours=1))
        monkeypatch.setattr(gpu, "MAX_HOURS_PER_RUN", 1)
        out = await gpu.allocate_pending(now=RUN + timedelta(hours=1))
        assert out["hours"] == 1 and fresh.status == "allocated" and stale.status == "allocating"
        await gpu.allocate_pending(now=RUN + timedelta(hours=1))
        assert stale.status == "allocated"  # the older resumed hour takes the next free slot

    @pytest.mark.asyncio
    async def test_a_failing_config_never_stops_recorded_hours(self, monkeypatch, store):
        recorded = pool_hour(store, source="manual")

        async def broken(*, now):
            raise RuntimeError("value too long for type character varying(64)")

        monkeypatch.setattr(gpu, "materialise_config_hours", broken)
        out = await gpu.allocate_pending(now=RUN)
        assert out == {"materialised": 0, "hours": 1, "idle": 1, "records": 0, "failed": 1}
        assert recorded.status == "allocated"

    @pytest.mark.asyncio
    async def test_an_hour_another_run_took_is_not_counted(self, monkeypatch, store):
        pool_hour(store)

        async def taken(pool_hour_id, *, now):
            return {"id": str(pool_hour_id), "claimed": False}

        monkeypatch.setattr(gpu, "allocate_hour", taken)
        assert await gpu.allocate_pending(now=RUN) == {
            "materialised": 0,
            "hours": 0,
            "idle": 0,
            "records": 0,
            "failed": 0,
        }

    @pytest.mark.asyncio
    async def test_tenants_since_keeps_tenants_deleted_after_the_hour(self, monkeypatch):
        import core.database
        from core.models.tenant import Tenant
        from core.spend import tenants
        from tests.unit.spend_usage_fakes import UsageSession

        session = UsageSession()
        live, late, early = (
            uuid.UUID("10000000-0000-4000-8000-000000000001"),
            uuid.UUID("10000000-0000-4000-8000-000000000002"),
            uuid.UUID("10000000-0000-4000-8000-000000000003"),
        )
        session.add(Tenant(id=live, name="a", slug="a", deleted_at=None))
        session.add(Tenant(id=late, name="b", slug="b", deleted_at=HOUR + timedelta(minutes=30)))
        session.add(Tenant(id=early, name="c", slug="c", deleted_at=HOUR - timedelta(days=1)))
        monkeypatch.setattr(core.database, "async_session_factory", lambda: session)
        assert await tenants.tenants_since(HOUR) == [(live, True), (late, False)]

    @pytest.mark.asyncio
    async def test_gpu_without_card_is_unpriced(self, store):
        hour = pool_hour(store)
        await write_calls(store, TENANT, [100, 300])
        await gpu.allocate_hour(hour.id, now=RUN)
        records = gpu_records(store)
        assert [r.quantity for r in records] == [Decimal("0.5"), Decimal("1.5")]
        assert all(r.unpriced and r.amount is None and r.price_source == "none" for r in records)
        (allocation,) = store.of("spend_gpu_allocations")
        assert allocation.amount is None and allocation.currency is None and allocation.node_hours == 2


# ---------------------------------------------------------------- tasks, routes, migration


def _run(awaitable):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


class TestTasksAndRoutes:
    def test_spend_tasks_registered_and_scheduled(self):
        from celery.schedules import crontab

        from core.tasks.celery_app import app

        app.loader.import_default_modules()
        for name in ("sample_storage", "allocate_gpu_hours"):
            assert f"core.tasks.spend_tasks.{name}" in app.tasks, name
        assert "core.tasks.spend_tasks" in app.conf.include
        beat = app.conf.beat_schedule
        assert beat["spend-sample-storage"]["task"] == "core.tasks.spend_tasks.sample_storage"
        assert beat["spend-sample-storage"]["schedule"] == crontab(hour=23, minute=30)
        assert beat["spend-allocate-gpu-hours"]["task"] == "core.tasks.spend_tasks.allocate_gpu_hours"
        assert beat["spend-allocate-gpu-hours"]["schedule"] == crontab(minute=20)
        assert {beat[k]["options"]["queue"] for k in ("spend-sample-storage", "spend-allocate-gpu-hours")} == {
            "maintenance"
        }

    def test_tasks_skip_while_off(self, monkeypatch):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        for task in (tasks.sample_storage, tasks.allocate_gpu_hours):
            assert task.run() == {"skipped": "spend_intelligence_disabled"}
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", False)
        for task in (tasks.sample_storage, tasks.allocate_gpu_hours):
            assert task.run() == {"skipped": "spend_sweeps_disabled"}

    def test_storage_task_loads_no_spend_module_while_skipped(self, monkeypatch):
        import sys

        import core.spend
        import core.tasks.spend_tasks as tasks

        monkeypatch.delitem(sys.modules, "core.spend.storage", raising=False)
        monkeypatch.delattr(core.spend, "storage", raising=False)
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert tasks.sample_storage.run() == {"skipped": "spend_intelligence_disabled"}
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", False)
        assert tasks.sample_storage.run() == {"skipped": "spend_sweeps_disabled"}
        assert "core.spend.storage" not in sys.modules  # the import comes after the guard

    def test_task_bodies_sample_and_allocate(self, monkeypatch, store):
        import core.tasks.spend_tasks as tasks
        from core.spend import clock

        monkeypatch.setattr(tasks, "run_async", _run)
        monkeypatch.setattr(settings, "spend_sweeps_enabled", True)
        monkeypatch.setattr(clock, "now_utc", lambda: RUN)
        assert tasks.sample_storage.run()["day"] == "2026-10-01"
        pool_hour(store)
        assert tasks.allocate_gpu_hours.run()["hours"] == 1

    @pytest.mark.asyncio
    async def test_metering_routes_not_found_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        for call in (
            api.list_gpu_allocations(HOUR, RUN, tenant_id=TID),
            api.preview_storage_sample(ADMIN, tenant_id=TID),
        ):
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "spend_disabled"
        assert (await api.spend_status(tenant_id=TID))["enabled"] is False

    @pytest.mark.asyncio
    async def test_gpu_allocations_route_returns_only_the_callers_tenant(self, store):
        hour = pool_hour(store)
        store.add(gpu_card(TENANT))
        await write_calls(store, TENANT, [1000])
        await write_calls(store, OTHER_TENANT, [1000])
        await gpu.allocate_hour(hour.id, now=RUN)
        out = await api.list_gpu_allocations(HOUR.replace(tzinfo=None), RUN, tenant_id=TID)
        (item,) = out["items"]  # the other tenant's share is not listed
        assert (item["hour_start"], item["provider"], item["node_pool"]) == (HOUR.isoformat(), "vllm", "pool-a")
        assert (item["currency"], item["records"], item["status"]) == ("INR", 1, "written")
        assert Decimal(item["tokens"]) == 1000 and Decimal(item["node_hours"]) == 1
        assert Decimal(item["amount"]) == Decimal("200")
        with pytest.raises(HTTPException) as info:
            await api.list_gpu_allocations(HOUR, HOUR + timedelta(days=32), tenant_id=TID)
        assert info.value.status_code == 422 and info.value.detail["error"] == "range_too_long"
        with pytest.raises(HTTPException) as info:
            await api.list_gpu_allocations(RUN, HOUR, tenant_id=TID)
        assert info.value.detail["error"] == "invalid_period"
        status = await api.spend_status(tenant_id=TID)
        assert status["limits"]["gpu_allocation_days"] == 31

    def test_routes_are_registered_with_their_scopes(self):
        from api.deps import require_tenant_admin
        from api.main import app
        from api.route_enforcement import SCOPE_FAMILIES
        from api.route_metadata import ROUTE_METADATA_ATTR

        paths = set(app.openapi()["paths"])
        assert {"/api/v1/spend/gpu-allocations", "/api/v1/spend/storage/sample"} <= paths
        meta = {r.path: getattr(r.endpoint, ROUTE_METADATA_ATTR) for r in api.router.routes}
        assert meta["/spend/gpu-allocations"]["scope"] == "spend.gpu.read"
        assert meta["/spend/storage/sample"]["scope"] == "spend.storage.sensitive.write"
        assert meta["/spend/storage/sample"]["audit_event"] == "spend.storage.preview"
        assert SCOPE_FAMILIES["spend"] == ("audit:read", "approvals:write")
        sample = next(r for r in api.router.routes if r.path == "/spend/storage/sample")
        assert require_tenant_admin in sample.dependencies
        assert api.spend_admin in [d.call for d in sample.dependant.dependencies]
        allocations = next(r for r in api.router.routes if r.path == "/spend/gpu-allocations")
        assert require_tenant_admin not in allocations.dependencies


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z81_spend_gpu", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TestMigration:
    def test_migration_v6z81_platform_table_has_no_tenant_and_allocations_have_rls(self, monkeypatch):
        migration = _migration()
        assert migration.revision == "v6z81_spend_gpu" and len(migration.revision) <= 32
        assert migration.down_revision == "v6z80_spend_usage"
        assert migration.TABLES == tuple(m.__tablename__ for m in MODELS)
        op = _Op()
        monkeypatch.setattr(migration, "op", op)
        migration.upgrade()
        assert "tenant_id" not in SpendGpuPoolHour.__table__.c and "tenant_id" in SpendGpuAllocation.__table__.c
        assert not any("spend_gpu_pool_hours" in s and "ROW LEVEL" in s for s in op.sql)
        assert "ALTER TABLE spend_gpu_allocations FORCE ROW LEVEL SECURITY;" in op.sql
        assert any(s.startswith("CREATE POLICY spend_gpu_allocations_tenant_isolation") for s in op.sql)
        assert not any(
            s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in op.sql
        )  # no existing table altered
        fk = next(iter(SpendGpuAllocation.__table__.foreign_key_constraints))
        assert [c.name for c in fk.columns] == ["pool_hour_id"] and fk.ondelete == "RESTRICT"
        leading = [[c.name for c in index.columns][0] for index in SpendGpuAllocation.__table__.indexes]
        assert "pool_hour_id" in leading
        down = _Op()
        monkeypatch.setattr(migration, "op", down)
        migration.downgrade()
        assert down.sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(migration.TABLES)]
        from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

        assert "spend_gpu_allocations" in _rls_tables_declared_in_migrations()

    def test_migration_v6z81_whole_hour_check(self):
        from core.models.spend_gpu import WHOLE_HOUR_CHECK

        sql = MIGRATION.read_text(encoding="utf-8")
        assert _strip_ws(WHOLE_HOUR_CHECK) in _strip_ws(sql)
        assert "AT TIME ZONE 'UTC'" in WHOLE_HOUR_CHECK  # an IST session cannot truncate to a half hour
        checks = {
            c.name: str(c.sqltext)
            for c in SpendGpuPoolHour.__table__.constraints
            if c.name and c.name.startswith("ck_")
        }
        assert checks["ck_spend_gpu_pool_hours_whole_hour"] == WHOLE_HOUR_CHECK

    def test_models_compile_to_the_migration_ddl(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        ddl = ""
        for model in MODELS:
            ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            for index in model.__table__.indexes:
                ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        flat_ddl = _strip_ws(ddl)
        names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
        assert len(names) == 13
        for name in names:
            assert name in ddl, name
        for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
            depth, start, index = 1, match.end(), match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(sql[index], 0)
                index += 1
            body = _strip_ws(sql[start : index - 1])
            assert f"CHECK({body})" in flat_ddl, body
        for model in MODELS:
            table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
            table_sql = table_sql[: table_sql.index(");")]
            for column in model.__table__.columns:
                assert re.search(rf"\b{column.name} ", table_sql), (model.__tablename__, column.name)
            for name, _default in re.findall(r"(\w+) [A-Z(),0-9 ]+? NOT NULL DEFAULT ([^,\n]+)", table_sql):
                assert model.__table__.c[name].server_default is not None, (model.__tablename__, name)
        assert str(SpendGpuAllocation.__table__.c.currency.type) == "CHAR(3)"
