# SPDX-License-Identifier: Apache-2.0
"""The usage writer: one background thread per process, off every call path.

``submit`` is what the model-call hook and the metering handlers call. It
starts the writer on first use (a forked process gets its own), puts the
events on a bounded in-process queue with no I/O and no await, and returns.
The only drop on the call path is a full queue (``MAX_PENDING`` events): it
is counted globally and per tenant.

The thread runs its own event loop and its own two-connection engine, so it
never competes with request handlers for the shared pool. It takes batches
of up to ``MAX_BATCH`` events every ``FLUSH_INTERVAL_S``, groups them by
tenant and writes each tenant's events in one transaction
(``core/spend/meter.py:write_events``):

* a paused tenant (the feature flag ``spend.metering_paused``, read here and
  never on the call path) has its events dropped and counted;
* a rollup day held by a rebuild answers ``busy``: the tenant's events wait
  for the next backoff step while other tenants are written; after
  ``BUSY_SPILL_AFTER_S`` they are spilled;
* a transient database error (a dropped connection, a pool timeout, a
  deadlock) is retried once, then the events are spilled;
* any other failure is logged, counted and spilled.

**Spill** hands the events to the Celery task ``persist_usage`` on the
``maintenance`` queue, which writes them idempotently with the same keys.
If the publish fails, the loss is counted (``spill_failed``) per tenant.

**Gaps** (usage that could not be metered) are aggregated in memory per
tenant, day, usage type, reason and detail, and flushed by the writer with
additive upserts into ``spend_meter_gaps``. A gap seen on a call path
(``note_gap``) starts the writer too, so a process that only ever sees
failed or cancelled calls still flushes its gaps.

**Drain** at shutdown (the API lifespan, a Celery worker's exit) stops the
thread, waits up to its timeout and spills whatever is still queued or
waiting for a retry, counting what cannot be spilled as ``shutdown_lost``.
Gap counts nothing flushed are counted as ``shutdown_lost`` too. Once a
drain has begun, the process starts no new writer: a late submit or gap,
and a batch the stopping thread tries to requeue after the drain collected
the queue, are counted as ``shutdown_lost`` rather than left to die
uncounted.
"""

from __future__ import annotations

import asyncio
import collections
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from core.spend.meter import UsageEvent

logger = structlog.get_logger()

MAX_PENDING = 5000  # events
FLUSH_INTERVAL_S = 0.2
MAX_BATCH = 500
RETRY_BACKOFF_S = (0.25, 0.5, 1.0, 2.0, 5.0)
BUSY_SPILL_AFTER_S = 120.0
WRITER_POOL_SIZE = 2
MAX_GAP_KEYS = 10_000
PAUSE_FLAG = "spend.metering_paused"
PAUSE_CACHE_TTL_S = 30.0  # the feature flag module's TTL
PAUSE_CACHE_MAX = 1024  # tenants; cleared when full
SHUTDOWN_LOST = "shutdown_lost"

# enterprise-gate: process-local-ok reason=one-writer-thread-per-process-bounded-queue-drained-at-shutdown-and-counted
_WRITER: dict[str, Any] = {"writer": None, "pid": None, "closed_pid": None}
# enterprise-gate: process-local-ok reason=gap-counts-aggregated-per-tenant-flushed-by-the-writer-bounded-10k-keys
_GAPS: dict[tuple[str, date, str, str, str], int] = {}
# enterprise-gate: process-local-ok reason=guards-the-writer-holder-and-gap-aggregator-keeps-no-tenant-data
_LOCK = threading.Lock()


def _metrics() -> Any:
    from observability import metrics

    return metrics


def _count(usage_type: str, reason: str, count: int = 1) -> None:
    _metrics().spend_usage_write_failures_total.labels(usage_type=usage_type, reason=reason).inc(count)


def _transient(exc: BaseException) -> bool:
    from core.spend.errors import retryable

    return retryable(exc)


# ---------------------------------------------------------------- gaps


def _add_gap_locked(key: tuple[str, date, str, str, str], count: int) -> None:
    if key not in _GAPS and len(_GAPS) >= MAX_GAP_KEYS:
        return
    _GAPS[key] = _GAPS.get(key, 0) + int(count)


def _gap_key(tenant_id: str, day: date, usage_type: str, reason: str, detail: str) -> tuple[str, date, str, str, str]:
    return (str(tenant_id), day, usage_type, reason, str(detail or "")[:160])


def _closed_locked() -> bool:
    """Whether a drain has begun in this process (callers hold ``_LOCK``)."""
    return _WRITER.get("closed_pid") == os.getpid()


def add_gap(tenant_id: str, day: date, usage_type: str, reason: str, detail: str = "", count: int = 1) -> None:
    """Count usage that could not be metered; flushed by the writer. Never raises; bounded at 10 000 keys."""
    key = _gap_key(tenant_id, day, usage_type, reason, detail)
    with _LOCK:
        _add_gap_locked(key, count)


def _gaps_lost(gaps: dict[tuple[str, date, str, str, str], int]) -> None:
    """Count gap counts nothing will write now, under the gap's own reason, and log them.

    Not a write failure: the usage behind each gap was counted when it went
    unmetered (a write failure, or an unmetered call), so counting it again
    there would double the loss and could hold the write-failure alert open.
    """
    if not gaps:
        return
    counter = _metrics().spend_meter_gaps_lost_total
    for (_tenant, _day, usage_type, reason, _detail), count in gaps.items():
        counter.labels(usage_type=usage_type, reason=reason).inc(count)
    logger.warning("spend_gaps_lost_at_shutdown", keys=len(gaps), count=sum(gaps.values()))


def note_gap(tenant_id: str, day: date, usage_type: str, reason: str, detail: str = "", count: int = 1) -> None:
    """A gap seen on a call path: aggregated, and this process's writer started so it gets flushed.

    Once a drain has begun nothing would flush it, so it is counted as a lost
    gap instead. No I/O.
    """
    key = _gap_key(tenant_id, day, usage_type, reason, detail)
    with _LOCK:
        closed = _closed_locked()
        if not closed:
            _add_gap_locked(key, count)
    if closed:
        _gaps_lost({key: int(count)})
        return
    start_for_gaps()


def pop_gaps(tenant_id: str | None = None) -> dict[tuple[str, date, str, str, str], int]:
    """Take the aggregated gaps (of one tenant, or all) out of the aggregator."""
    with _LOCK:
        keys = [k for k in _GAPS if tenant_id is None or k[0] == str(tenant_id)]
        return {k: _GAPS.pop(k) for k in keys}


def restore_gaps(gaps: dict[tuple[str, date, str, str, str], int]) -> None:
    """Put gaps back after a failed flush, so the next pass writes them."""
    for (tenant_id, day, usage_type, reason, detail), count in gaps.items():
        add_gap(tenant_id, day, usage_type, reason, detail, count)


def _tenant_gaps(gaps: dict[tuple[str, date, str, str, str], int]) -> dict[tuple[date, str, str, str], int]:
    return {(day, usage_type, reason, detail): count for (_t, day, usage_type, reason, detail), count in gaps.items()}


# ---------------------------------------------------------------- spill


def spill(events: Sequence[UsageEvent], *, reason: str, failed_reason: str = "spill_failed") -> bool:
    """Hand events to the ``persist_usage`` Celery task; count and gap them when the publish fails."""
    if not events:
        return True
    try:
        from core.tasks.spend_tasks import persist_usage

        persist_usage.apply_async(
            args=[[event.to_wire() for event in events]], queue="maintenance", retry=False, ignore_result=True
        )
    # enterprise-gate: broad-except-ok reason=spill-publish-failure-is-logged-counted-and-gapped-per-tenant
    except Exception as exc:
        logger.warning("spend_usage_spill_failed", error_type=type(exc).__name__, events=len(events))
        from core.spend import clock

        for event in events:
            _count(event.usage_type, failed_reason)
            add_gap(event.tenant_id, clock.event_date_of(event.event_time), event.usage_type, failed_reason)
        return False
    for event in events:
        _count(event.usage_type, reason)
    return True


# ---------------------------------------------------------------- the writer


@dataclass
class _Retry:
    due: float
    tenant_id: str
    events: list[UsageEvent]
    attempt: int
    busy_since: float | None
    transient_tries: int


class _Writer:
    """The queue and the thread that drains it."""

    def __init__(self) -> None:
        self._queue: collections.deque[UsageEvent] = collections.deque()
        self._cond = threading.Condition()
        self._retries: list[_Retry] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False  # set when the drain collects what is left; guarded by _cond
        # The events of the flush in progress, so a drain whose join times out can still spill them;
        # once handed over, the outliving thread's requeue of them is not a loss. Guarded by _cond.
        self._inflight: list[UsageEvent] = []
        self._handed_over = False
        # The pause flag per tenant: (paused, expires_at). Read and written on the writer thread only.
        self._pause_cache: dict[str, tuple[bool, float]] = {}

    # -- the call path

    def pending(self) -> int:
        with self._cond:
            return len(self._queue) + sum(len(r.events) for r in self._retries)

    def put(self, events: Sequence[UsageEvent]) -> str | None:
        """Queue ``events``; never blocks. Returns why they were refused (``queue_full``, ``shutdown_lost``) or None."""
        with self._cond:
            if self._closed:
                return SHUTDOWN_LOST
            waiting = len(self._queue) + sum(len(r.events) for r in self._retries)
            if waiting + len(events) > MAX_PENDING:
                return "queue_full"
            self._queue.extend(events)
            self._cond.notify()
        return None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="spend-usage-writer", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the thread

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        # enterprise-gate: broad-except-ok reason=writer-thread-failure-is-logged-and-leftovers-are-spilled-at-drain
        except Exception as exc:
            logger.error("spend_usage_writer_stopped", error_type=type(exc).__name__)

    def _finished(self) -> bool:
        with self._cond:
            return self._stop.is_set() and not self._queue

    def _take(self, *, max_events: int, wait: float) -> list[UsageEvent]:
        with self._cond:
            if not self._queue and not self._stop.is_set():
                self._cond.wait(timeout=wait)
            batch: list[UsageEvent] = []
            while self._queue and len(batch) < max_events:
                batch.append(self._queue.popleft())
            return batch

    def _due_retries(self, now: float | None = None) -> list[_Retry]:
        moment = time.monotonic() if now is None else now
        with self._cond:
            due = [r for r in self._retries if r.due <= moment]
            self._retries = [r for r in self._retries if r.due > moment]
        return due

    def _requeue(self, retry: _Retry) -> None:
        """Keep a batch for a later attempt; after the drain collected what was left, count it lost.

        A batch that was in flight when the drain's join timed out was already
        handed to the drain's spill, so its requeue is dropped without a count.
        """
        with self._cond:
            closed = self._closed
            handed_over = self._handed_over
            if not closed:
                self._retries.append(retry)
        if closed and not handed_over:
            for event in retry.events:
                _count(event.usage_type, SHUTDOWN_LOST)

    def _restore_gaps(self, gaps: dict[tuple[str, date, str, str, str], int]) -> None:
        """Put back gaps a failed write took; after the drain counted the leftovers, count them lost."""
        with self._cond:
            if not self._closed:
                restore_gaps(gaps)
                return
        _gaps_lost(gaps)

    async def _main(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        import core.database

        engine = create_async_engine(
            core.database.engine.url.render_as_string(hide_password=False),
            pool_size=WRITER_POOL_SIZE,
            max_overflow=0,
            pool_timeout=2.0,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            with core.database.session_factory_scope(lambda: factory):
                while not self._finished():
                    batch = self._take(max_events=MAX_BATCH, wait=FLUSH_INTERVAL_S)
                    retries = self._due_retries()
                    if batch or retries:
                        with self._cond:
                            self._inflight = list(batch) + [e for r in retries for e in r.events]
                        try:
                            await self._flush(batch, retries)
                        finally:
                            with self._cond:
                                self._inflight = []
                    await self._flush_gaps()
                    _metrics().spend_usage_pending.set(self.pending())
                await self._flush_gaps()
        finally:
            await engine.dispose()

    async def _paused(self, tenant_id: str) -> bool:
        """Whether ``spend.metering_paused`` holds for the tenant (its row, else the global row).

        The flag module's cache is a plain dict that request handlers share,
        so this thread never touches it: it runs the module's own lookup and
        evaluation and keeps the answer in its own cache for 30 seconds. A
        failed read keeps metering on, logged, and is cached the same way.
        """
        import uuid

        from core import feature_flags

        now = time.monotonic()
        cached = self._pause_cache.get(tenant_id)
        if cached is not None and cached[1] > now:
            return cached[0]
        try:
            tenant_uuid = uuid.UUID(tenant_id)
            row = await feature_flags._query_flag(tenant_uuid, PAUSE_FLAG)
            paused = bool(feature_flags._evaluate(row, PAUSE_FLAG, tenant_uuid, None, False))
        # enterprise-gate: broad-except-ok reason=pause-flag-read-failure-keeps-metering-on-and-logs
        except Exception as exc:
            logger.warning("spend_pause_flag_unreadable", error_type=type(exc).__name__)
            paused = False
        if tenant_id not in self._pause_cache and len(self._pause_cache) >= PAUSE_CACHE_MAX:
            self._pause_cache.clear()
        self._pause_cache[tenant_id] = (paused, now + PAUSE_CACHE_TTL_S)
        return paused

    async def _flush(self, batch: Sequence[UsageEvent], retries: Sequence[_Retry] = ()) -> None:
        """Write a batch tenant by tenant; requeue, spill or count what cannot be written."""
        started = time.perf_counter()
        groups: dict[str, list[UsageEvent]] = {}
        for event in batch:
            groups.setdefault(event.tenant_id, []).append(event)
        work = [_Retry(0.0, tid, events, 0, None, 0) for tid, events in groups.items()]
        work.extend(retries)
        for item in work:
            await self._flush_tenant(item)
        _metrics().spend_writer_flush_seconds.observe(time.perf_counter() - started)
        _metrics().spend_usage_pending.set(self.pending())

    async def _flush_tenant(self, item: _Retry) -> None:
        import uuid

        from core.database import get_tenant_session
        from core.spend import clock, meter

        if await self._paused(item.tenant_id):
            for event in item.events:
                _count(event.usage_type, "paused")
                add_gap(item.tenant_id, clock.event_date_of(event.event_time), event.usage_type, "paused")
            return
        try:
            tenant_uuid = uuid.UUID(item.tenant_id)
        except ValueError:
            for event in item.events:
                _count(event.usage_type, "no_tenant")
            return
        gaps = pop_gaps(item.tenant_id)
        try:
            async with get_tenant_session(tenant_uuid) as session:
                result = await meter.write_events(
                    session, tenant_uuid, item.events, lock="try", gaps=_tenant_gaps(gaps)
                )
        # enterprise-gate: broad-except-ok reason=usage-write-failure-is-logged-counted-and-spilled-off-the-call-path
        except Exception as exc:
            self._restore_gaps(gaps)
            if _transient(exc) and item.transient_tries == 0:
                self._requeue(_Retry(time.monotonic() + RETRY_BACKOFF_S[0], item.tenant_id, item.events, 0, None, 1))
                return
            logger.warning(
                "spend_usage_write_failed",
                usage_type=item.events[0].usage_type if item.events else "",
                error_type=type(exc).__name__,
                idempotency_key=item.events[0].idempotency_key if item.events else "",
            )
            for event in item.events:
                _count(event.usage_type, "db_error")
            spill(item.events, reason="spilled")
            return
        if result.busy:
            self._restore_gaps(gaps)
            now = time.monotonic()
            since = item.busy_since if item.busy_since is not None else now
            if now - since >= BUSY_SPILL_AFTER_S:
                spill(item.events, reason="spilled")
                return
            step = RETRY_BACKOFF_S[min(item.attempt, len(RETRY_BACKOFF_S) - 1)]
            self._requeue(_Retry(now + step, item.tenant_id, item.events, item.attempt + 1, since, 0))

    async def _flush_gaps(self) -> None:
        import uuid

        from core.database import get_tenant_session
        from core.spend import meter

        taken = pop_gaps()
        if not taken:
            return
        by_tenant: dict[str, dict[tuple[str, date, str, str, str], int]] = {}
        for key, count in taken.items():
            by_tenant.setdefault(key[0], {})[key] = count
        for tenant_id, gaps in by_tenant.items():
            try:
                tenant_uuid = uuid.UUID(tenant_id)
            except ValueError:
                continue
            try:
                async with get_tenant_session(tenant_uuid) as session:
                    await meter.upsert_gaps(session, tenant_uuid, _tenant_gaps(gaps))
            # enterprise-gate: broad-except-ok reason=gap-flush-failure-keeps-the-counts-for-the-next-pass-and-logs
            except Exception as exc:
                logger.warning("spend_gap_flush_failed", error_type=type(exc).__name__)
                self._restore_gaps(gaps)

    # -- shutdown

    def stop_and_collect(self, timeout: float) -> list[UsageEvent]:
        """Stop the thread, wait up to ``timeout``, and return every event still queued or awaiting a retry.

        When the thread outlives the join mid-flush, the batch it is writing is
        returned too, so the drain spills it: records are keyed and inserted
        with ``ON CONFLICT DO NOTHING``, so a batch that the thread still
        finishes writing is not counted twice.
        """
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))
        outlived = self._thread is not None and self._thread.is_alive()
        with self._cond:
            # From here a thread that outlived the join can no longer requeue into a queue nobody reads.
            self._closed = True
            left = list(self._queue) + [e for r in self._retries for e in r.events]
            if outlived and self._inflight:
                left.extend(self._inflight)
                self._handed_over = True
            self._inflight = []
            self._queue.clear()
            self._retries = []
        return left


def _writer(create: bool) -> _Writer | None:
    with _LOCK:
        current = _WRITER.get("writer")
        if current is not None and _WRITER.get("pid") == os.getpid():
            return current
        if not create or _closed_locked():
            return None
        writer = _Writer()
        _WRITER["writer"] = writer
        _WRITER["pid"] = os.getpid()
    writer.start()
    return writer


def submit(events: Sequence[UsageEvent]) -> None:
    """Queue events for the writer (starting it on first use). Synchronous; no I/O; never raises."""
    if not events:
        return
    try:
        writer = _writer(create=True)
        # No writer means a drain has begun in this process: nothing would write the events.
        refused = writer.put(events) if writer is not None else SHUTDOWN_LOST
        if refused is not None:
            from core.spend import clock

            for event in events:
                _count(event.usage_type, refused)
                if refused == "queue_full":
                    add_gap(event.tenant_id, clock.event_date_of(event.event_time), event.usage_type, refused)
            return
        metrics = _metrics()
        for event in events:
            metrics.spend_usage_submitted_total.labels(usage_type=event.usage_type).inc()
        metrics.spend_usage_pending.set(writer.pending())
    # enterprise-gate: broad-except-ok reason=queue-put-failure-is-logged-and-counted-the-call-proceeds
    except Exception as exc:
        logger.warning("spend_usage_submit_failed", error_type=type(exc).__name__)
        for event in events:
            _count(event.usage_type, "hook_error")


def start_for_gaps() -> None:
    """Start this process's writer if it has none, so gaps left in the aggregator get flushed. Never raises."""
    try:
        _writer(create=True)
    # enterprise-gate: broad-except-ok reason=writer-start-failure-is-logged-the-gaps-keep-in-the-aggregator
    except Exception as exc:
        logger.warning("spend_usage_writer_start_failed", error_type=type(exc).__name__)


def reopen() -> None:
    """Let this process start a writer again after a drain (a new API lifespan in the same process)."""
    with _LOCK:
        if _closed_locked():
            _WRITER["closed_pid"] = None


def started() -> bool:
    """Whether this process has a writer."""
    return _writer(create=False) is not None


def pending() -> int:
    """Events queued or awaiting a retry in this process."""
    writer = _writer(create=False)
    return writer.pending() if writer is not None else 0


def _count_lost_gaps() -> None:
    """Count the gap counts nothing will flush now (as lost gaps, not write failures) and drop them."""
    _gaps_lost(pop_gaps())


def drain_blocking(timeout: float) -> int:
    """Stop the writer and spill what is left; 0 when the writer never started.

    Returns the number of events that were still queued, awaiting a retry, or
    in flight when the join timed out. Events whose spill fails are counted as
    ``shutdown_lost``; gap counts the writer did not flush are counted on
    ``spend_meter_gaps_lost_total``. From here this process starts no new writer.
    """
    with _LOCK:
        writer = _WRITER.get("writer") if _WRITER.get("pid") == os.getpid() else None
        _WRITER["writer"] = None
        _WRITER["pid"] = None
        _WRITER["closed_pid"] = os.getpid()
    left = writer.stop_and_collect(timeout) if writer is not None else []
    # Before the spill below, whose failures are gapped (and counted) on their own.
    _count_lost_gaps()
    if writer is None:
        return 0
    if left:
        spill(left, reason="spilled", failed_reason=SHUTDOWN_LOST)
    _metrics().spend_usage_pending.set(0)
    return len(left)
