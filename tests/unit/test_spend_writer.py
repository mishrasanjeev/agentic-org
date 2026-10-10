# SPDX-License-Identifier: Apache-2.0
"""The spend usage writer: the call-path queue, the flush, retries and spills, gaps, the pause and the drain."""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from core.config import settings
from core.spend import meter, writer
from core.spend.meter import WriteResult
from tests.unit.spend_usage_fakes import OTHER_TENANT, TENANT, install
from tests.unit.test_spend_usage import event

TID = str(TENANT)


@pytest.fixture(autouse=True)
def clean_writer():
    writer._GAPS.clear()
    writer._WRITER.update({"writer": None, "pid": None})
    yield
    writer._GAPS.clear()
    writer._WRITER.update({"writer": None, "pid": None})


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


@pytest.fixture
def spilled(monkeypatch):
    sent: list[list] = []

    class Task:
        @staticmethod
        def apply_async(args, queue, retry, ignore_result):
            assert queue == "maintenance" and retry is False and ignore_result is True
            sent.append(args[0])

    import core.tasks.spend_tasks as tasks

    monkeypatch.setattr(tasks, "persist_usage", Task)
    return sent


def idle_writer() -> writer._Writer:
    """A writer whose thread is never started (driven synchronously by the tests)."""
    return writer._Writer()


class TestCallPath:
    def test_submit_never_does_io_and_never_raises(self, monkeypatch):
        started: list[int] = []
        monkeypatch.setattr(writer._Writer, "start", lambda self: started.append(1))
        writer.submit([event()])
        writer.submit([])
        assert started == [1] and writer.started() and writer.pending() == 1
        writer.submit([event()])
        assert started == [1] and writer.pending() == 2  # one writer per process

        def broken(create):
            raise RuntimeError("no writer")

        monkeypatch.setattr(writer, "_writer", broken)
        writer.submit([event()])  # counted, never raised

    def test_queue_full_is_counted_and_gapped_per_tenant(self, monkeypatch):
        monkeypatch.setattr(writer._Writer, "start", lambda self: None)
        monkeypatch.setattr(writer, "MAX_PENDING", 2)
        writer.submit([event(), event()])
        writer.submit([event()])
        assert writer.pending() == 2
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "queue_full", ""): 1}

    def test_gap_aggregator_is_bounded(self, monkeypatch):
        monkeypatch.setattr(writer, "MAX_GAP_KEYS", 1)
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "paused")
        writer.add_gap(TID, date(2026, 10, 2), "llm_tokens", "paused")
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "paused", count=2)
        assert writer.pop_gaps(TID) == {(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 3}

    def test_drain_returns_zero_when_never_started(self):
        assert writer.drain_blocking(0.1) == 0
        from core import spend

        assert spend.drain_blocking(0.1) == 0
        assert asyncio.run(spend.drain(0.1)) == 0

    def test_drain_spills_leftovers_and_resets_state(self, monkeypatch, spilled):
        monkeypatch.setattr(writer._Writer, "start", lambda self: None)
        writer.submit([event(), event()])
        current = writer._WRITER["writer"]
        current._retries.append(writer._Retry(0.0, TID, [event()], 1, None, 0))
        assert writer.drain_blocking(0.1) == 3
        assert len(spilled) == 1 and len(spilled[0]) == 3 and writer._WRITER["writer"] is None
        assert not writer.started() and writer.pending() == 0

    def test_drain_counts_shutdown_lost_when_the_spill_fails(self, monkeypatch):
        monkeypatch.setattr(writer._Writer, "start", lambda self: None)

        class Broken:
            @staticmethod
            def apply_async(**kwargs):
                raise ConnectionError("broker down")

        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(tasks, "persist_usage", Broken)
        writer.submit([event()])
        assert writer.drain_blocking(0.1) == 1
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "shutdown_lost", ""): 1}

    def test_the_thread_runs_and_stops(self, monkeypatch):
        ran = threading.Event()

        async def fake_main(self):
            ran.set()

        monkeypatch.setattr(writer._Writer, "_main", fake_main)
        live = writer._Writer()
        live.start()
        live._thread.join(timeout=5)
        assert ran.is_set() and not live.alive()

        async def broken_main(self):
            raise RuntimeError("boom")

        monkeypatch.setattr(writer._Writer, "_main", broken_main)
        failing = writer._Writer()
        failing.start()
        failing._thread.join(timeout=5)  # logged, never raised into the process

    def test_take_waits_and_batches(self):
        live = idle_writer()
        assert live._take(max_events=2, wait=0.01) == []
        live.put([event(), event(), event()])
        assert len(live._take(max_events=2, wait=0.01)) == 2 and live.pending() == 1
        live._stop.set()
        assert not live._finished()
        live._take(max_events=5, wait=0.01)
        assert live._finished()


class TestFlush:
    @pytest.mark.asyncio
    async def test_writer_uses_its_own_engine_not_the_shared_pool(self, monkeypatch, store):
        import sqlalchemy.ext.asyncio as sa_async

        import core.database

        created: list[dict] = []

        class Engine:
            url = SimpleNamespace(render_as_string=lambda hide_password: "postgresql+asyncpg://u:p@h/db")
            disposed = False

            async def dispose(self):
                Engine.disposed = True

        def fake_engine(url, **kwargs):
            created.append({"url": url, **kwargs})
            return Engine()

        seen_factories: list = []

        async def fake_write(session, tid, events, **kwargs):
            seen_factories.append(core.database._session_factory_override.get())
            return WriteResult(written=len(events), duplicates=0, skipped=0, refused=0, busy=False)

        monkeypatch.setattr(core.database, "engine", Engine())
        monkeypatch.setattr(sa_async, "create_async_engine", fake_engine)
        monkeypatch.setattr(meter, "write_events", fake_write)
        live = idle_writer()

        async def not_paused(tenant_id):
            return False

        monkeypatch.setattr(live, "_paused", not_paused)
        live.put([event()])
        live._stop.set()
        await live._main()
        assert created and created[0]["pool_size"] == writer.WRITER_POOL_SIZE and created[0]["max_overflow"] == 0
        assert seen_factories and seen_factories[0] is not None and Engine.disposed
        assert core.database._session_factory_override.get() is None

    @pytest.mark.asyncio
    async def test_busy_rollup_day_requeues_without_blocking_other_tenants(self, monkeypatch, store):
        written: list[str] = []

        async def fake_write(session, tid, events, **kwargs):
            if tid == TENANT:
                return WriteResult(written=0, duplicates=0, skipped=0, refused=0, busy=True)
            written.append(str(tid))
            return WriteResult(written=len(events), duplicates=0, skipped=0, refused=0, busy=False)

        monkeypatch.setattr(meter, "write_events", fake_write)
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        await live._flush([event(), event(tenant_id=str(OTHER_TENANT))])
        assert written == [str(OTHER_TENANT)] and len(live._retries) == 1 and live._retries[0].attempt == 1
        retry = live._retries[0]
        assert live._due_retries(now=retry.due - 1) == [] and live._due_retries(now=retry.due + 1) == [retry]

    @pytest.mark.asyncio
    async def test_busy_too_long_is_spilled(self, monkeypatch, store, spilled):
        async def busy(session, tid, events, **kwargs):
            return WriteResult(written=0, duplicates=0, skipped=0, refused=0, busy=True)

        monkeypatch.setattr(meter, "write_events", busy)
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        await live._flush([], [writer._Retry(0.0, TID, [event()], 3, -1_000_000.0, 0)])
        assert len(spilled) == 1 and live._retries == []

    @pytest.mark.asyncio
    async def test_transient_error_retries_once_then_spills(self, monkeypatch, store, spilled):
        async def flaky(session, tid, events, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))

        monkeypatch.setattr(meter, "write_events", flaky)
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        await live._flush([event()])
        assert len(live._retries) == 1 and live._retries[0].transient_tries == 1 and spilled == []
        await live._flush([], live._due_retries(now=10**9))
        assert len(spilled) == 1 and live._retries == []

    @pytest.mark.asyncio
    async def test_other_errors_are_counted_and_spilled(self, monkeypatch, store, spilled):
        async def broken(session, tid, events, **kwargs):
            raise ValueError("bad row")

        monkeypatch.setattr(meter, "write_events", broken)
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "queue_full")
        await live._flush([event()])
        assert len(spilled) == 1
        assert writer.pop_gaps(TID)  # the tenant's gaps were put back for the next pass

    @pytest.mark.asyncio
    async def test_paused_tenant_events_are_dropped_and_counted(self, monkeypatch, store):
        from core import feature_flags

        async def flag(key, *, tenant_id=None, user_id=None, default=False):
            assert key == "spend.metering_paused" and tenant_id == TENANT
            return True

        monkeypatch.setattr(feature_flags, "is_enabled", flag)
        live = idle_writer()
        await live._flush([event()])
        assert store.of("spend_usage_records") == []
        assert writer.pop_gaps(TID) == {(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 1}

        async def unreadable(*args, **kwargs):
            raise RuntimeError("flag store down")

        monkeypatch.setattr(feature_flags, "is_enabled", unreadable)
        assert await live._paused(TID) is False

    @pytest.mark.asyncio
    async def test_flush_writes_through_the_tenant_session(self, monkeypatch, store):
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai")
        await live._flush([event()])
        assert len(store.of("spend_usage_records")) == 1
        assert store.of("spend_meter_gaps")[0].reason == "failed_no_usage"

    @pytest.mark.asyncio
    async def test_bad_tenant_id_is_counted(self, monkeypatch, store):
        live = idle_writer()
        monkeypatch.setattr(live, "_paused", _never_paused)
        await live._flush([event(tenant_id="not-a-uuid")])
        assert store.of("spend_usage_records") == []

    @pytest.mark.asyncio
    async def test_gap_flush_writes_per_tenant_and_keeps_failures(self, monkeypatch, store):
        live = idle_writer()
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "paused", count=2)
        writer.add_gap("not-a-uuid", date(2026, 10, 1), "llm_tokens", "paused")
        await live._flush_gaps()
        assert store.of("spend_meter_gaps")[0].count == 2

        async def broken(session, tid, gaps):
            raise RuntimeError("db down")

        monkeypatch.setattr(meter, "upsert_gaps", broken)
        writer.add_gap(TID, date(2026, 10, 2), "llm_tokens", "paused")
        await live._flush_gaps()
        assert writer.pop_gaps(TID) == {(TID, date(2026, 10, 2), "llm_tokens", "paused", ""): 1}
        await live._flush_gaps()  # nothing left: a no-op


class TestSpill:
    def test_spill_payload_carries_no_text_and_round_trips(self, spilled):
        original = event()
        assert writer.spill([original], reason="spilled") is True
        payload = spilled[0]
        assert all(isinstance(v, (str, int, bool, type(None), dict)) for v in payload[0].values())
        assert meter.UsageEvent.from_wire(payload[0]) == original
        assert writer.spill([], reason="spilled") is True

    def test_spill_failure_counts_and_gaps(self, monkeypatch):
        class Broken:
            @staticmethod
            def apply_async(**kwargs):
                raise ConnectionError("broker down")

        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(tasks, "persist_usage", Broken)
        assert writer.spill([event()], reason="spilled") is False
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "spill_failed", ""): 1}

    def test_persist_usage_task_writes_and_is_idempotent(self, monkeypatch, store):
        import core.tasks.spend_tasks as tasks

        captured: list = []

        def run_async(awaitable):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(awaitable)
            finally:
                loop.close()
            captured.append(result)
            return result

        monkeypatch.setattr(tasks, "run_async", run_async)
        payload = [event().to_wire(), event(unit="output_token", calls=0).to_wire()]
        first = tasks.persist_usage.run(payload)
        second = tasks.persist_usage.run(payload)
        assert first["written"] == 2 and second == {"written": 0, "duplicates": 2, "refused": 0, "skipped": 0}
        assert len(store.of("spend_usage_records")) == 2
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert tasks.persist_usage.run(payload) == {"skipped": "spend_intelligence_disabled"}


async def _never_paused(tenant_id):
    return False


def test_uuid_tenants_only():
    assert uuid.UUID(TID)
