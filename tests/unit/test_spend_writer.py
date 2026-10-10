# SPDX-License-Identifier: Apache-2.0
"""The spend usage writer: the call-path queue, the flush, retries and spills, gaps, the pause and the drain."""

from __future__ import annotations

import asyncio
import os
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
    writer._WRITER.update({"writer": None, "pid": None, "closed_pid": None})
    yield
    writer._GAPS.clear()
    writer._WRITER.update({"writer": None, "pid": None, "closed_pid": None})


def failures(reason: str) -> float:
    from observability import metrics

    return metrics.spend_usage_write_failures_total.labels(usage_type="llm_tokens", reason=reason)._value.get()


def gaps_lost(reason: str) -> float:
    from observability import metrics

    return metrics.spend_meter_gaps_lost_total.labels(usage_type="llm_tokens", reason=reason)._value.get()


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

    def test_a_gap_on_the_call_path_starts_the_writer(self, monkeypatch):
        started: list[int] = []
        monkeypatch.setattr(writer._Writer, "start", lambda self: started.append(1))
        writer.note_gap(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai")
        writer.note_gap(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai")
        assert started == [1] and writer.started()  # no event was ever submitted
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai"): 2}

    def test_drain_counts_unflushed_gaps_as_lost_gaps_not_write_failures(self, monkeypatch):
        writer.add_gap(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai", count=2)
        writer.add_gap(TID, date(2026, 10, 2), "llm_tokens", "queue_full")
        before = failures("shutdown_lost"), gaps_lost("failed_no_usage"), gaps_lost("queue_full")
        assert writer.drain_blocking(0.1) == 0  # no writer ever started
        # The usage behind each gap was counted when it went unmetered: not counted again as a failure.
        assert failures("shutdown_lost") == before[0] and writer.pop_gaps() == {}
        assert gaps_lost("failed_no_usage") == before[1] + 2 and gaps_lost("queue_full") == before[2] + 1

    def test_after_a_drain_late_usage_and_gaps_are_counted_and_no_writer_starts(self, monkeypatch, spilled):
        started: list[int] = []
        monkeypatch.setattr(writer._Writer, "start", lambda self: started.append(1))
        writer.submit([event()])
        assert writer.drain_blocking(0.1) == 1 and started == [1]
        before = failures("shutdown_lost"), gaps_lost("failed_no_usage")
        writer.submit([event(), event()])  # late: would die in a new thread nobody drains
        writer.note_gap(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "openai")
        writer.start_for_gaps()
        assert started == [1] and not writer.started() and writer.pending() == 0
        assert failures("shutdown_lost") == before[0] + 2 and writer.pop_gaps() == {}
        assert gaps_lost("failed_no_usage") == before[1] + 1
        writer.reopen()  # a new lifespan in the same process
        writer.submit([event()])
        assert started == [1, 1] and writer.pending() == 1

    def test_the_batch_in_flight_when_the_join_times_out_is_spilled_once(self, monkeypatch, spilled):
        class Stalled:
            def join(self, timeout=None):
                return None

            def is_alive(self):
                return True

        live = idle_writer()
        live._thread = Stalled()
        flying = [event(), event(), event()]
        live._inflight = list(flying)
        live.put([event()])
        left = live.stop_and_collect(0.0)
        assert len(left) == 4 and live._inflight == [] and live._handed_over
        before = failures("shutdown_lost")
        live._requeue(writer._Retry(0.0, TID, flying, 1, None, 0))  # the stalled write gives up later
        assert failures("shutdown_lost") == before and live.pending() == 0
        # A thread that finished before the join hands nothing over.
        done = idle_writer()
        done._inflight = [event()]
        assert done.stop_and_collect(0.0) == [] and not done._handed_over
        # Through the drain: the in-flight batch is spilled with the queue.
        live_two = idle_writer()
        with writer._LOCK:
            writer._WRITER.update(writer=live_two, pid=os.getpid())
        live_two._thread = Stalled()
        live_two._inflight = [event(), event()]
        assert writer.drain_blocking(0.0) == 2 and sum(len(batch) for batch in spilled) == 2

    def test_a_requeue_or_gap_restore_after_the_drain_collected_is_counted(self):
        live = idle_writer()
        live.put([event()])
        assert len(live.stop_and_collect(0.0)) == 1
        before = failures("shutdown_lost"), gaps_lost("paused"), failures("paused")
        live._requeue(writer._Retry(0.0, TID, [event(), event()], 1, None, 0))  # a thread that outlived the join
        live._restore_gaps({(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 4})
        assert live.pending() == 0 and writer.pop_gaps() == {}
        assert failures("shutdown_lost") == before[0] + 2 and gaps_lost("paused") == before[1] + 4
        assert failures("paused") == before[2]  # a paused tenant's gaps never reach the write-failure alert
        assert live.put([event()]) == "shutdown_lost" and live.pending() == 0
        open_writer = idle_writer()
        open_writer._restore_gaps({(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 1})
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 1}  # before: kept

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

        async def flag_row(tenant_id, key):
            assert key == "spend.metering_paused" and tenant_id == TENANT
            return {"enabled": True, "rollout_percentage": 100}

        async def shared_cache_path(*args, **kwargs):
            raise AssertionError("the writer thread must not use the flag module's shared cache")

        monkeypatch.setattr(feature_flags, "_query_flag", flag_row)
        monkeypatch.setattr(feature_flags, "is_enabled", shared_cache_path)
        live = idle_writer()
        await live._flush([event()])
        assert store.of("spend_usage_records") == []
        assert writer.pop_gaps(TID) == {(TID, date(2026, 10, 1), "llm_tokens", "paused", ""): 1}

        async def unreadable(*args, **kwargs):
            raise RuntimeError("flag store down")

        monkeypatch.setattr(feature_flags, "_query_flag", unreadable)
        assert await idle_writer()._paused(TID) is False  # a failed read keeps metering on

    @pytest.mark.asyncio
    async def test_pause_flag_reads_use_the_writers_own_bounded_ttl_cache(self, monkeypatch):
        from core import feature_flags

        queries: list[uuid.UUID] = []
        rows = {TENANT: {"enabled": True, "rollout_percentage": 100}, OTHER_TENANT: None}

        async def flag_row(tenant_id, key):
            queries.append(tenant_id)
            return rows.get(tenant_id, {"enabled": True, "rollout_percentage": 0})  # 0% is off for everyone

        monkeypatch.setattr(feature_flags, "_query_flag", flag_row)
        shared_before = dict(feature_flags._cache)
        live = idle_writer()
        assert await live._paused(TID) is True
        assert await live._paused(TID) is True  # cached: no second query
        assert await live._paused(str(OTHER_TENANT)) is False
        assert queries == [TENANT, OTHER_TENANT]
        assert feature_flags._cache == shared_before  # the shared cache is never touched by the writer
        live._pause_cache[TID] = (True, 0.0)  # expired
        rows[TENANT] = None  # the pause row was removed
        assert await live._paused(TID) is False and queries[-1] == TENANT
        monkeypatch.setattr(writer, "PAUSE_CACHE_MAX", 2)
        third = str(uuid.uuid4())
        assert await live._paused(third) is False  # a 0% row: not paused
        assert set(live._pause_cache) == {third}  # full: cleared, never grows past the bound

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


# ---------------------------------------------------------------- review fixes: the spill task and the gauge


def _new_loop_run(awaitable):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


class _LockedError(Exception):
    sqlstate = "55P03"


class _RetriedError(Exception):
    pass


class TestSpillTask:
    def test_a_lock_timeout_is_retried_with_only_the_tenants_not_yet_written(self, monkeypatch, store):
        from sqlalchemy.exc import DBAPIError

        import core.tasks.spend_tasks as tasks

        real = meter.write_events

        async def other_tenant_waits_too_long(session, tenant_id, events, **kwargs):
            if tenant_id == OTHER_TENANT:
                raise DBAPIError("INSERT", {}, _LockedError("lock timeout"))
            return await real(session, tenant_id, events, **kwargs)

        retried: list = []

        def retry(args=None, exc=None, countdown=None, **kwargs):
            retried.append((args, type(exc).__name__, countdown))
            raise _RetriedError

        monkeypatch.setattr(tasks, "run_async", _new_loop_run)
        monkeypatch.setattr(meter, "write_events", other_tenant_waits_too_long)
        monkeypatch.setattr(tasks.persist_usage, "retry", retry)
        mine = event().to_wire()
        theirs = event(tenant_id=str(OTHER_TENANT)).to_wire()
        with pytest.raises(_RetriedError):
            tasks.persist_usage.run([mine, theirs])
        assert retried == [([[theirs]], "DBAPIError", 1)]
        assert len(store.of("spend_usage_records")) == 1  # the first tenant's events are written once

    def test_events_that_cannot_be_written_are_counted_and_gapped(self, monkeypatch, store):
        import core.tasks.spend_tasks as tasks
        from observability import metrics

        async def broken(*args, **kwargs):
            raise RuntimeError("bad row")

        def failures() -> float:
            return metrics.spend_usage_write_failures_total.labels(
                usage_type="llm_tokens", reason="spill_failed"
            )._value.get()

        monkeypatch.setattr(tasks, "run_async", _new_loop_run)
        monkeypatch.setattr(meter, "write_events", broken)
        before = failures()
        with pytest.raises(RuntimeError):
            tasks.persist_usage.run([event().to_wire(), event(unit="output_token", calls=0).to_wire()])
        assert failures() == before + 2
        gaps = store.of("spend_meter_gaps")
        assert [(g.reason, g.count, g.day) for g in gaps] == [("spill_failed", 2, date(2026, 10, 1))]

    def test_retries_spent_record_the_loss_and_a_failed_gap_write_waits_for_the_writer(self, monkeypatch, store):
        from sqlalchemy.exc import OperationalError

        import core.tasks.spend_tasks as tasks

        async def down(*args, **kwargs):
            raise OperationalError("INSERT", {}, Exception("connection refused"))

        started: list[int] = []
        monkeypatch.setattr(tasks, "run_async", _new_loop_run)
        monkeypatch.setattr(meter, "write_events", down)
        monkeypatch.setattr(meter, "upsert_gaps", down)
        monkeypatch.setattr(tasks.persist_usage, "max_retries", 0)
        monkeypatch.setattr(writer._Writer, "start", lambda self: started.append(1))
        with pytest.raises(OperationalError):
            tasks.persist_usage.run([event().to_wire()])
        assert writer.pop_gaps() == {(TID, date(2026, 10, 1), "llm_tokens", "spill_failed", ""): 1}
        assert started == [1]  # the writer flushes the kept gaps

    def test_an_unreadable_spilled_event_is_counted(self, monkeypatch, store):
        import core.tasks.spend_tasks as tasks

        monkeypatch.setattr(tasks, "run_async", _new_loop_run)
        with pytest.raises(Exception):  # noqa: B017 - whatever the malformed payload raises, it is counted first
            tasks.persist_usage.run([{"tenant_id": TID, "broken": True}])
        assert store.of("spend_meter_gaps") == []

    def test_start_for_gaps_starts_one_writer_and_never_raises(self, monkeypatch):
        started: list[int] = []
        monkeypatch.setattr(writer._Writer, "start", lambda self: started.append(1))
        writer.start_for_gaps()
        writer.start_for_gaps()
        assert started == [1] and writer.started()

        def broken(create):
            raise RuntimeError("no thread")

        monkeypatch.setattr(writer, "_writer", broken)
        writer.start_for_gaps()

    def test_the_writer_retries_a_deadlock_once(self):
        from sqlalchemy.exc import DBAPIError

        class DeadlockError(Exception):
            sqlstate = "40P01"

        class ConstraintError(Exception):
            sqlstate = "23514"

        assert writer._transient(DBAPIError("INSERT", {}, DeadlockError()))
        assert not writer._transient(DBAPIError("INSERT", {}, ConstraintError()))

    def test_the_backlog_gauge_forgets_dead_processes(self):
        from observability import metrics

        assert metrics.spend_usage_pending._multiprocess_mode == "livemax"
