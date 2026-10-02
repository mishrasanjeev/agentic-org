# SPDX-License-Identifier: Apache-2.0
"""Run timelines: the spans of one agent run, kept for the console's waterfall.

Tracing exports spans to a collector, and no environment of this platform
has one yet; the console needs the waterfall of one run without it. With
``AGENTICORG_TRACING_TIMELINE_ENABLED`` (off by default, and nothing while
tracing itself is off) a span processor keeps the finished spans that
describe a run (the run, each model call, tool call and knowledge search) in
memory per trace, and the runner stores that trace's spans in ``run_spans``
when the run ends: on the run's own event loop, through the run's own tenant
session, so no thread and no second engine is involved. A trace nothing
stores (a search outside a run) ages out of memory.

A stored span carries identifiers, timings, outcomes and the governance
events; never prompts, answers, tool arguments or retrieved text. An
exception event keeps only the exception's type.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.trace import StatusCode

from core.config import settings
from observability import tracing

logger = structlog.get_logger()

STORED_SPANS: frozenset[str] = frozenset(
    {
        "agenticorg.agent.run",
        "agenticorg.agent.resume",
        "agenticorg.agent.reason",
        "agenticorg.tool.call",
        "agenticorg.knowledge.search",
    }
)
ROOT_SPANS: frozenset[str] = frozenset({"agenticorg.agent.run", "agenticorg.agent.resume"})
MAX_BUFFERED_SPANS = 20_000
BUFFER_TTL_SECONDS = 1800.0
_EXCEPTION_KEYS = frozenset({"exception.type"})

_processor: TimelineProcessor | None = None


class TimelineProcessor(SpanProcessor):
    """Keeps each trace's finished catalogue spans until the run that owns them stores or abandons them."""

    def __init__(self, *, max_spans: int = MAX_BUFFERED_SPANS, ttl_seconds: float = BUFFER_TTL_SECONDS) -> None:
        self._buffers: OrderedDict[str, list[ReadableSpan]] = OrderedDict()
        self._touched: dict[str, float] = {}
        self._count = 0
        self._lock = threading.Lock()
        self.max_spans = max_spans
        self.ttl_seconds = ttl_seconds

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        if span.name not in STORED_SPANS:
            return
        context = span.get_span_context()
        if context is None or not context.is_valid:
            return
        key = format(context.trace_id, "032x")
        now = time.monotonic()
        with self._lock:
            self._buffers.setdefault(key, []).append(span)
            self._buffers.move_to_end(key)
            self._touched[key] = now
            self._count += 1
            self._evict(now)

    def take(self, trace_id: str) -> list[ReadableSpan]:
        """Remove and return the spans buffered for ``trace_id``."""
        with self._lock:
            spans = self._buffers.pop(trace_id, [])
            self._touched.pop(trace_id, None)
            self._count -= len(spans)
            return spans

    def buffered(self) -> int:
        with self._lock:
            return self._count

    def _evict(self, now: float) -> None:
        # Least recently touched first: traces past the TTL go, then the oldest until under the cap.
        while self._buffers:
            oldest = next(iter(self._buffers))
            stale = now - self._touched.get(oldest, now) > self.ttl_seconds
            if not stale and self._count <= self.max_spans:
                break
            dropped = self._buffers.pop(oldest)
            self._touched.pop(oldest, None)
            self._count -= len(dropped)

    def shutdown(self) -> None:
        with self._lock:
            self._buffers.clear()
            self._touched.clear()
            self._count = 0

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def install() -> TimelineProcessor | None:
    """Attach the processor to the installed tracer while the timeline is on; None otherwise."""
    global _processor
    if not settings.tracing_timeline_enabled or not tracing.enabled():
        return None
    if _processor is None:
        processor = TimelineProcessor()
        if not tracing.add_span_processor(processor):
            return None
        _processor = processor
    return _processor


def uninstall() -> None:
    """Forget the processor (the tracer that held it is shut down separately)."""
    global _processor
    processor, _processor = _processor, None
    if processor is not None:
        processor.shutdown()


def enabled() -> bool:
    """Whether run spans are being kept in this process."""
    return _processor is not None


def processor() -> TimelineProcessor | None:
    return _processor


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [_scalar(item) for item in value]
    return str(value)


def _json_safe(values: Mapping[str, Any] | None) -> dict[str, Any]:
    return {str(key): _scalar(value) for key, value in (values or {}).items()}


def _status(span: ReadableSpan) -> str:
    code = span.status.status_code if span.status is not None else StatusCode.UNSET
    if code is StatusCode.OK:
        return "ok"
    if code is StatusCode.ERROR:
        return "error"
    return "unset"


def _event(event: Any, start_ns: int) -> dict[str, Any]:
    attributes = dict(event.attributes or {})
    if event.name == "exception":
        attributes = {key: value for key, value in attributes.items() if key in _EXCEPTION_KEYS}
    return {
        "name": event.name,
        "offset_ms": max(0, int((int(event.timestamp or start_ns) - start_ns) // 1_000_000)),
        "attributes": _json_safe(attributes),
    }


def row_for(span: ReadableSpan, tenant_id: uuid.UUID) -> Any:
    """The ``run_spans`` row for a finished span."""
    from core.models.run_span import RunSpan

    context = span.get_span_context()
    parent = span.parent
    start_ns = int(span.start_time or 0)
    end_ns = int(span.end_time or start_ns)
    attributes = _json_safe(span.attributes)
    agent_id = str(attributes.get("agent.id") or "")[:64] or None
    correlation_id = str(attributes.get("gateway.correlation_id") or "")[:128] or None
    return RunSpan(
        tenant_id=tenant_id,
        trace_id=format(context.trace_id, "032x"),
        span_id=format(context.span_id, "016x"),
        parent_span_id=format(parent.span_id, "016x") if parent is not None else None,
        name=span.name[:64],
        kind=str(getattr(span.kind, "name", "internal")).lower()[:16],
        status=_status(span),
        agent_id=agent_id,
        correlation_id=correlation_id,
        started_at=datetime.fromtimestamp(start_ns / 1_000_000_000, UTC),
        duration_ms=max(0, int((end_ns - start_ns) // 1_000_000)),
        attributes=attributes,
        events=[_event(event, start_ns) for event in (span.events or [])],
    )


async def persist(span: trace.Span, tenant_id: str | uuid.UUID | None) -> int:
    """Store the buffered spans of ``span``'s trace for ``tenant_id``; the number stored.

    Nothing while the timeline is off or the span records nothing. A storage
    failure is logged and the run's result stands: the timeline is evidence
    for the console, not a gate on the run.
    """
    processor = _processor
    if processor is None or tenant_id is None:
        return 0
    context = span.get_span_context()
    if not context.is_valid:
        return 0
    trace_id = format(context.trace_id, "032x")
    spans = processor.take(trace_id)
    if not spans:
        return 0
    try:
        tid = uuid.UUID(str(tenant_id))
    except ValueError:
        logger.warning("run_timeline_tenant_invalid", trace_id=trace_id)
        return 0
    rows = [row_for(item, tid) for item in spans]
    try:
        from core.database import get_tenant_session

        async with get_tenant_session(tid) as session:
            session.add_all(rows)
    # enterprise-gate: broad-except-ok reason=timeline-storage-failure-is-logged-and-the-run-result-stands
    except Exception as exc:
        logger.warning("run_timeline_store_failed", trace_id=trace_id, error_type=type(exc).__name__, spans=len(rows))
        return 0
    logger.info("run_timeline_stored", trace_id=trace_id, spans=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def root_summary(row: Any) -> dict[str, Any]:
    """What the console lists for one run: the root span and the attributes that name it."""
    attributes = dict(row.attributes or {})
    tokens = attributes.get("llm.tokens")
    return {
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "name": row.name,
        "agent_id": row.agent_id,
        "status": row.status,
        "run_status": attributes.get("agent.run.status"),
        "started_at": _iso(row.started_at),
        "duration_ms": int(row.duration_ms or 0),
        "provider": attributes.get("llm.provider"),
        "model": attributes.get("llm.model"),
        "tokens": int(tokens) if isinstance(tokens, int | float) else None,
        "correlation_id": row.correlation_id,
    }


async def recent_traces(tenant_id: uuid.UUID, *, agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """The newest stored runs for the tenant, one entry per root span."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.run_span import RunSpan

    async with get_tenant_session(tenant_id) as session:
        query = select(RunSpan).where(RunSpan.tenant_id == tenant_id, RunSpan.name.in_(sorted(ROOT_SPANS)))
        if agent_id:
            query = query.where(RunSpan.agent_id == agent_id)
        rows = (await session.execute(query.order_by(RunSpan.started_at.desc()).limit(limit))).scalars().all()
    return [root_summary(row) for row in rows]


async def trace_detail(tenant_id: uuid.UUID, trace_id: str) -> dict[str, Any] | None:
    """Every stored span of one trace, with offsets from the trace's start; None when nothing is stored."""
    from sqlalchemy import select

    from core.database import get_tenant_session
    from core.models.run_span import RunSpan

    async with get_tenant_session(tenant_id) as session:
        query = (
            select(RunSpan)
            .where(RunSpan.tenant_id == tenant_id, RunSpan.trace_id == trace_id)
            .order_by(RunSpan.started_at.asc(), RunSpan.duration_ms.desc())
        )
        rows = (await session.execute(query)).scalars().all()
    if not rows:
        return None
    start = min(row.started_at for row in rows)
    end = max(row.started_at.timestamp() * 1000 + int(row.duration_ms or 0) for row in rows)
    spans = [
        {
            "span_id": row.span_id,
            "parent_span_id": row.parent_span_id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "agent_id": row.agent_id,
            "offset_ms": max(0, int((row.started_at - start).total_seconds() * 1000)),
            "duration_ms": int(row.duration_ms or 0),
            "attributes": dict(row.attributes or {}),
            "events": list(row.events or []),
        }
        for row in rows
    ]
    return {
        "trace_id": trace_id,
        "started_at": _iso(start),
        "duration_ms": max(0, int(end - start.timestamp() * 1000)),
        "spans": spans,
    }
