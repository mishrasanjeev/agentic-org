# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Runtime counters: the tool-call recorder at the gateway, the guardrail and security counters, and their metrics."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from core.config import settings
from core.insights import runtime_counters as rc

TENANT = uuid.uuid4()
# An invented business reference used as an idempotency key (built at run time).
INVOICE_REF = "-".join(("invoice", "INV", "42"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "insights_enabled", True)
    rc._COUNTS.clear()
    rc._PENDING.clear()
    monkeypatch.setattr(rc, "_FLUSHER", None)
    yield
    rc._COUNTS.clear()


@pytest.mark.parametrize(
    ("result", "replayed", "expected"),
    [
        ({"ok": True}, False, ("success", None)),
        ({"error": {"code": "E1007", "message": "scope_denied"}}, False, ("refused", "E1007")),
        ({"error": {"code": "E1008"}}, False, ("refused", "E1008")),
        ({"error": {"code": "E1001", "message": "boom"}}, False, ("failed", "E1001")),
        ({"error": {"code": "E1005"}}, False, ("failed", "E1005")),
        ({"error": "plain"}, False, ("failed", "plain")),
        ({"ok": True}, True, ("replayed", None)),
        ("not a dict", False, ("success", None)),
    ],
)
def test_tool_call_status(result, replayed, expected):
    assert rc.tool_call_status(result, replayed=replayed) == expected


def test_a_tool_call_row_keeps_ids_codes_and_timings_only():
    row = rc.tool_call_row(
        tenant_id=str(TENANT), agent_id="customer-agent", tool_name="t" * 150, connector_name="ledger",
        status="success", error_code=None, latency_ms=12.7, idempotency_key=INVOICE_REF, attempt=0,
    )
    assert row is not None
    assert row["tenant_id"] == TENANT and row["agent_id"] is None
    assert len(row["tool_name"]) == 100 and row["connector_name"] == "ledger"
    assert row["latency_ms"] == 12 and row["attempt"] == 1 and row["replayed"] is False
    # The idempotency key is kept as a digest; it may carry a business reference.
    assert row["idempotency_key"] != INVOICE_REF and len(row["idempotency_key"]) == 64
    assert rc.tool_call_row(tenant_id="nope", agent_id=None, tool_name="t", connector_name=None,
                            status="success", error_code=None, latency_ms=1) is None
    odd = rc.tool_call_row(tenant_id=TENANT, agent_id=uuid.uuid4(), tool_name="", connector_name="",
                           status="weird", error_code="E" * 20, latency_ms="x")
    assert odd is not None
    assert (odd["tool_name"], odd["connector_name"], odd["status"], odd["error_code"], odd["latency_ms"]) == (
        "unknown", None, "failed", "E" * 10, None
    )


@pytest.mark.asyncio
async def test_record_tool_call_is_off_with_insights_and_never_raises(monkeypatch):
    insert = AsyncMock()
    monkeypatch.setattr(rc, "_insert_tool_call", insert)
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(return_value=False))
    fields = {"tenant_id": TENANT, "agent_id": None, "tool_name": "t", "connector_name": "c", "status": "success",
              "error_code": None, "latency_ms": 3}
    assert await rc.record_tool_call(**fields) is False
    insert.assert_not_awaited()
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(return_value=True))
    assert await rc.record_tool_call(**fields) is True
    insert.side_effect = RuntimeError("database down")
    assert await rc.record_tool_call(**fields) is False


@pytest.mark.asyncio
async def test_schedule_tool_call_records_in_the_background(monkeypatch):
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(rc, "record_tool_call", record)
    rc.schedule_tool_call(tenant_id=TENANT, agent_id=None, tool_name="t", connector_name="c",
                          result={"error": {"code": "E1009"}}, started=0.0, idempotency_key="k")
    await asyncio.gather(*list(rc._PENDING))
    kwargs = record.await_args.kwargs
    assert kwargs["status"] == "refused" and kwargs["error_code"] == "E1009" and kwargs["latency_ms"] > 0
    monkeypatch.setattr(settings, "insights_enabled", False)
    rc.schedule_tool_call(tenant_id=TENANT, agent_id=None, tool_name="t", connector_name="c", result={},
                          started=0.0)
    assert record.await_count == 1


def test_schedule_tool_call_outside_a_loop_is_a_no_op():
    rc.schedule_tool_call(tenant_id=TENANT, agent_id=None, tool_name="t", connector_name="c", result={}, started=0.0)
    assert not rc._PENDING


@pytest.mark.asyncio
async def test_the_gateway_records_each_outcome_without_changing_it(monkeypatch):
    from core.tool_gateway import gateway as gw

    seen: list[dict] = []
    monkeypatch.setattr(rc, "schedule_tool_call", lambda **kw: seen.append(kw))

    class Fake:
        @gw._records_tool_call
        async def execute(self, tenant_id, agent_id, agent_scopes, connector_name, tool_name, params,
                          idempotency_key=None, *, run_grant):
            if params.get("replay"):
                gw._replayed.set(True)
                return {"cached": True}
            if params.get("raise"):
                raise RuntimeError("boom")
            return {"ok": True}

    fake = Fake()
    assert await fake.execute("t1", "a1", [], "ledger", "post", {}, "key-1", run_grant=None) == {"ok": True}
    assert await fake.execute("t1", "a1", [], "ledger", "post", {"replay": True}, run_grant=None) == {"cached": True}
    with pytest.raises(RuntimeError):
        await fake.execute("t1", "a1", [], "ledger", "post", {"raise": True}, run_grant=None)
    assert [(s["connector_name"], s["tool_name"], s["replayed"]) for s in seen] == [
        ("ledger", "post", False), ("ledger", "post", True), ("ledger", "post", False),
    ]
    assert seen[0]["idempotency_key"] == "key-1" and seen[0]["result"] == {"ok": True}
    assert seen[2]["result"] == {"error": {"code": "E1001"}}
    # The replay flag does not leak into the next call.
    assert gw._replayed.get() is False


@pytest.mark.asyncio
async def test_a_recorder_failure_never_reaches_the_caller(monkeypatch):
    from core.tool_gateway import gateway as gw

    def broken(**_kw):
        raise RuntimeError("recorder down")

    monkeypatch.setattr(rc, "schedule_tool_call", broken)

    @gw._records_tool_call
    async def execute(tenant_id, agent_id, connector_name, tool_name):
        return {"ok": 1}

    assert await execute("t", "a", "c", "x") == {"ok": 1}


@pytest.mark.asyncio
async def test_counters_add_up_and_flush_one_upsert_per_tenant(monkeypatch):
    other = uuid.uuid4()
    upserts: list[tuple] = []

    async def upsert(tid, rows):
        upserts.append((tid, rows))

    monkeypatch.setattr(rc, "_upsert", upsert)
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(side_effect=lambda t: t == TENANT))
    monkeypatch.setattr(rc, "FLUSH_SECONDS", 3600)
    for _ in range(3):
        rc.count_security_event(TENANT, "rate_limited")
    rc.count_security_event(str(TENANT), "login_failed")
    rc.count_security_event(other, "login_failed")
    rc.count_security_event(TENANT, "not_a_kind")
    rc.count_security_event(None, "login_failed")
    rc.count_guardrail_event(TENANT, "input", "pii", "mask", "enforced")
    rc.count_guardrail_event(TENANT, "input", "pii", "mask", "enforced")
    assert rc._FLUSHER is not None
    written = await rc.flush()
    rc._FLUSHER.cancel()
    assert written == 3
    assert [tid for tid, _ in upserts] == [TENANT]
    rows = upserts[0][1]
    assert sorted((r["event_kind"], r["count"]) for r in rows["security"]) == [("login_failed", 1),
                                                                              ("rate_limited", 3)]
    assert [(r["detector"], r["count"]) for r in rows["guardrail"]] == [("pii", 2)]
    assert await rc.flush() == 0


@pytest.mark.asyncio
async def test_a_failed_flush_drops_that_tenant_and_carries_on(monkeypatch):
    other = uuid.uuid4()
    calls: list = []

    async def upsert(tid, rows):
        calls.append(tid)
        if tid == TENANT:
            raise RuntimeError("database down")

    monkeypatch.setattr(rc, "_upsert", upsert)
    monkeypatch.setattr(rc, "insights_enabled_for", AsyncMock(return_value=True))
    monkeypatch.setattr(rc, "FLUSH_SECONDS", 3600)
    rc.count_security_event(TENANT, "scope_denied")
    rc.count_security_event(other, "scope_denied")
    assert await rc.flush() == 1
    assert set(calls) == {TENANT, other}
    rc._FLUSHER.cancel()


@pytest.mark.asyncio
async def test_the_flusher_writes_after_its_delay(monkeypatch):
    flushed = AsyncMock(return_value=1)
    monkeypatch.setattr(rc, "flush", flushed)
    monkeypatch.setattr(rc, "FLUSH_SECONDS", 0)
    rc.count_security_event(TENANT, "api_key_used")
    first = rc._FLUSHER
    rc.count_security_event(TENANT, "api_key_used")
    assert rc._FLUSHER is first
    await first
    flushed.assert_awaited_once()


def test_counters_are_off_with_insights_and_outside_a_loop_keep_counting(monkeypatch):
    rc.count_security_event(TENANT, "login_failed")
    # No running loop: counted, flushed by the next loop that counts.
    assert sum(rc._COUNTS.values()) == 1 and rc._FLUSHER is None
    monkeypatch.setattr(settings, "insights_enabled", False)
    rc.count_security_event(TENANT, "login_failed")
    rc.count_guardrail_event(TENANT, "input", "pii", "mask", "enforced")
    assert sum(rc._COUNTS.values()) == 1


def test_a_full_buffer_drops_new_keys_but_still_counts_known_ones(monkeypatch):
    monkeypatch.setattr(rc, "MAX_BUFFERED_KEYS", 1)
    rc.count_security_event(TENANT, "login_failed")
    rc.count_security_event(TENANT, "rate_limited")
    rc.count_security_event(TENANT, "login_failed")
    assert list(rc._COUNTS.values()) == [2]


@pytest.mark.asyncio
async def test_route_denials_are_counted_for_the_callers_tenant(monkeypatch):
    from api import route_enforcement

    counted: list[tuple] = []
    monkeypatch.setattr(rc, "count_security_event", lambda tid, kind: counted.append((tid, kind)))
    monkeypatch.setattr(route_enforcement, "_enforcement_mode", lambda: "enforce")
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=str(TENANT)), url=SimpleNamespace(path="/x"))
    for status in (429, 403):
        with pytest.raises(HTTPException):
            route_enforcement._deny(request, status, "denied")
    assert counted == [(str(TENANT), "rate_limited"), (str(TENANT), "scope_denied")]
    monkeypatch.setattr(route_enforcement, "_enforcement_mode", lambda: "log")
    route_enforcement._deny(request, 429, "denied")
    assert len(counted) == 2


@pytest.mark.parametrize(
    ("day", "baseline", "expected"),
    [
        (40, [2] * 30, True),
        (4, [0] * 30, False),
        (6, [0] * 30, True),
        (12, [5, 15] * 15, False),
        (30, [5, 15] * 15, True),
        (50, [], False),
    ],
)
def test_spike(day, baseline, expected):
    from core.insights.metrics_runtime import spike

    assert spike(day, baseline) is expected


def test_the_kinds_match_the_migration():
    import importlib

    from core.models.insights import SECURITY_EVENT_KINDS

    migration = importlib.import_module("migrations.versions.v6_z115_insights_runtime_counters")
    assert migration.SECURITY_KINDS == SECURITY_EVENT_KINDS
