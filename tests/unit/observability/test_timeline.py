# SPDX-License-Identifier: Apache-2.0
"""Run timelines (observability/timeline.py): kept per trace, stored when the run ends, read for the console."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import structlog
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from observability import timeline, tracing

TENANT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _clean():
    timeline.uninstall()
    tracing.shutdown_tracing()
    structlog.contextvars.clear_contextvars()
    yield
    timeline.uninstall()
    tracing.shutdown_tracing()


@pytest.fixture
def on(monkeypatch) -> timeline.TimelineProcessor:
    monkeypatch.setattr(timeline.settings, "tracing_timeline_enabled", True)
    tracing.init_tracing("test-service", InMemorySpanExporter())
    processor = timeline.install()
    assert processor is not None
    return processor


@pytest.fixture
def stored(monkeypatch):
    rows: list = []

    class _Session:
        def add_all(self, items):
            rows.extend(items)

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield _Session()

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    return rows


class TestInstall:
    def test_off_by_default_nothing_is_installed(self):
        assert timeline.settings.tracing_timeline_enabled is False
        tracing.init_tracing("test-service", InMemorySpanExporter())
        assert timeline.install() is None and timeline.enabled() is False

    def test_nothing_without_tracing(self, monkeypatch):
        monkeypatch.setattr(timeline.settings, "tracing_timeline_enabled", True)
        assert timeline.install() is None and timeline.enabled() is False

    def test_installed_once_with_tracing_on(self, on):
        assert timeline.enabled() is True
        assert timeline.install() is on

    def test_settings_init_installs_the_timeline(self, monkeypatch):
        monkeypatch.setattr(tracing.settings, "tracing_enabled", True)
        monkeypatch.setattr(tracing.settings, "tracing_timeline_enabled", True)
        monkeypatch.setattr(tracing.settings, "env", "test")
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "")
        assert tracing.init_tracing_from_settings() is True
        assert timeline.enabled() is True


class TestProcessor:
    def test_keeps_only_the_catalogue_spans_per_trace(self, on):
        with tracing.span("agenticorg.http.request", kind=SpanKind.SERVER):
            with tracing.span("agenticorg.agent.run", **{"agent.id": "a1"}) as root:
                with tracing.span("agenticorg.agent.reason"):
                    pass
                with tracing.span("agenticorg.tool.call"):
                    pass
                with tracing.span("not.in.the.catalogue"):
                    pass
            trace_id = format(root.get_span_context().trace_id, "032x")
        assert on.buffered() == 3
        taken = on.take(trace_id)
        assert sorted(s.name for s in taken) == [
            "agenticorg.agent.reason",
            "agenticorg.agent.run",
            "agenticorg.tool.call",
        ]
        assert on.buffered() == 0 and on.take(trace_id) == []

    def test_the_cap_drops_the_least_recently_touched_trace(self):
        processor = timeline.TimelineProcessor(max_spans=3)
        tracing.init_tracing("test-service", InMemorySpanExporter())
        tracing.add_span_processor(processor)
        ids = []
        for _ in range(4):
            with tracing.span("agenticorg.agent.reason") as span:
                ids.append(format(span.get_span_context().trace_id, "032x"))
        assert processor.buffered() == 3
        assert processor.take(ids[0]) == [] and len(processor.take(ids[3])) == 1

    def test_a_stale_trace_ages_out(self, monkeypatch):
        processor = timeline.TimelineProcessor(ttl_seconds=10.0)
        tracing.init_tracing("test-service", InMemorySpanExporter())
        tracing.add_span_processor(processor)
        clock = [1000.0]
        monkeypatch.setattr(timeline.time, "monotonic", lambda: clock[0])
        with tracing.span("agenticorg.agent.reason") as first:
            stale_id = format(first.get_span_context().trace_id, "032x")
        clock[0] += 11.0
        with tracing.span("agenticorg.agent.reason") as second:
            fresh_id = format(second.get_span_context().trace_id, "032x")
        assert processor.take(stale_id) == [] and len(processor.take(fresh_id)) == 1


class TestPersist:
    def test_stores_the_runs_spans_with_timings_parents_and_events(self, on, stored):
        with tracing.span("agenticorg.agent.run", **{"agent.id": "a1", "gateway.correlation_id": "corr-1"}) as root:
            tracing.add_event("model_gateway.decision", provider="openai", model="gpt-4o", reason="policy")
            with (
                pytest.raises(RuntimeError),
                tracing.span("agenticorg.tool.call", kind=SpanKind.CLIENT, **{"tool.name": "x"}),
            ):
                raise RuntimeError("provider down")
            with tracing.span("agenticorg.agent.reason", kind=SpanKind.CLIENT, **{"llm.model": "gpt-4o"}):
                tracing.add_event("guardrail.outcome", stage="input", applied=True)
        stored_count = asyncio.run(timeline.persist(root, str(TENANT)))
        assert stored_count == 3 and len(stored) == 3
        by_name = {row.name: row for row in stored}
        run = by_name["agenticorg.agent.run"]
        trace_id = format(root.get_span_context().trace_id, "032x")
        assert run.tenant_id == TENANT and run.trace_id == trace_id and run.parent_span_id is None
        assert run.agent_id == "a1" and run.correlation_id == "corr-1" and run.kind == "internal"
        assert run.events == [
            {
                "name": "model_gateway.decision",
                "offset_ms": 0,
                "attributes": {"provider": "openai", "model": "gpt-4o", "reason": "policy"},
            }
        ]
        assert run.started_at.tzinfo is not None and run.duration_ms >= 0
        tool = by_name["agenticorg.tool.call"]
        assert tool.parent_span_id == run.span_id and tool.status == "error" and tool.kind == "client"
        assert tool.events[0]["name"] == "exception" and tool.events[0]["attributes"] == {
            "exception.type": "RuntimeError"
        }
        reason = by_name["agenticorg.agent.reason"]
        assert reason.attributes == {"llm.model": "gpt-4o"} and reason.events[0]["attributes"] == {
            "stage": "input",
            "applied": True,
        }
        assert on.buffered() == 0

    def test_nothing_while_off_or_without_a_tenant(self, stored):
        tracing.init_tracing("test-service", InMemorySpanExporter())
        with tracing.span("agenticorg.agent.run") as root:
            pass
        assert asyncio.run(timeline.persist(root, str(TENANT))) == 0 and stored == []

    def test_an_invalid_tenant_or_a_storage_failure_is_logged_not_raised(self, on, monkeypatch):
        with tracing.span("agenticorg.agent.run") as root:
            pass
        assert asyncio.run(timeline.persist(root, None)) == 0
        assert asyncio.run(timeline.persist(root, "not-a-uuid")) == 0
        with tracing.span("agenticorg.agent.run") as again:
            pass

        @contextlib.asynccontextmanager
        async def _broken(_tid):
            raise RuntimeError("database down")
            yield  # pragma: no cover

        monkeypatch.setattr("core.database.get_tenant_session", _broken)
        assert asyncio.run(timeline.persist(again, str(TENANT))) == 0
        assert on.buffered() == 0

    def test_the_runner_stores_the_run_on_both_paths(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[3] / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        assert src.count("await timeline.persist(run_span.span, tenant_id)") == 2


def _row(**over):
    base = {
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": None,
        "name": "agenticorg.agent.run",
        "kind": "internal",
        "status": "unset",
        "agent_id": "a1",
        "correlation_id": "corr-1",
        "started_at": datetime(2026, 10, 2, 10, 0, tzinfo=UTC),
        "duration_ms": 2400,
        "attributes": {
            "agent.run.status": "completed",
            "llm.provider": "openai",
            "llm.model": "gpt-4o",
            "llm.tokens": 321,
        },
        "events": [],
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def session_rows(monkeypatch):
    rows: list = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return list(rows)

    class _Session:
        async def execute(self, _query):
            return _Result()

    @contextlib.asynccontextmanager
    async def _ctx(_tid):
        yield _Session()

    monkeypatch.setattr("core.database.get_tenant_session", _ctx)
    return rows


class TestReads:
    def test_recent_traces_summarise_the_root_spans(self, session_rows):
        session_rows.append(_row())
        listed = asyncio.run(timeline.recent_traces(TENANT, agent_id="a1", limit=10))
        assert listed == [
            {
                "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
                "span_id": "00f067aa0ba902b7",
                "name": "agenticorg.agent.run",
                "agent_id": "a1",
                "status": "unset",
                "run_status": "completed",
                "started_at": "2026-10-02T10:00:00+00:00",
                "duration_ms": 2400,
                "provider": "openai",
                "model": "gpt-4o",
                "tokens": 321,
                "correlation_id": "corr-1",
            }
        ]

    def test_trace_detail_offsets_every_span_from_the_runs_start(self, session_rows):
        start = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
        session_rows.append(_row())
        session_rows.append(
            _row(
                span_id="1111111111111111",
                parent_span_id="00f067aa0ba902b7",
                name="agenticorg.agent.reason",
                started_at=start + timedelta(milliseconds=100),
                duration_ms=1200,
                attributes={},
            )
        )
        session_rows.append(
            _row(
                span_id="2222222222222222",
                parent_span_id="00f067aa0ba902b7",
                name="agenticorg.tool.call",
                started_at=start + timedelta(milliseconds=1400),
                duration_ms=1500,
                status="error",
                attributes={},
            )
        )
        detail = asyncio.run(timeline.trace_detail(TENANT, "4bf92f3577b34da6a3ce929d0e0e4736"))
        assert detail is not None
        assert detail["started_at"] == "2026-10-02T10:00:00+00:00" and detail["duration_ms"] == 2900
        assert [(s["name"], s["offset_ms"], s["duration_ms"], s["parent_span_id"]) for s in detail["spans"]] == [
            ("agenticorg.agent.run", 0, 2400, None),
            ("agenticorg.agent.reason", 100, 1200, "00f067aa0ba902b7"),
            ("agenticorg.tool.call", 1400, 1500, "00f067aa0ba902b7"),
        ]

    def test_trace_detail_is_none_when_nothing_is_stored(self, session_rows):
        assert asyncio.run(timeline.trace_detail(TENANT, "4bf92f3577b34da6a3ce929d0e0e4736")) is None


class TestPrune:
    def test_prunes_each_tenant_past_the_cutoff_and_isolates_a_failing_tenant(self, monkeypatch):
        from core.tasks import timeline_tasks

        monkeypatch.setattr("core.config.settings.tracing_timeline_retention_days", 30)
        t1, t2 = uuid.uuid4(), uuid.uuid4()

        class _Catalogue:
            async def execute(self, _statement, *_a, **_k):
                return None

            async def scalars(self, _query):
                return SimpleNamespace(all=lambda: [t1, t2])

        @contextlib.asynccontextmanager
        async def factory():
            yield _Catalogue()

        deletes: list[tuple[uuid.UUID, str]] = []

        class _TenantSession:
            def __init__(self, tenant_id):
                self.tenant_id = tenant_id

            async def execute(self, statement):
                if self.tenant_id == t2:
                    raise RuntimeError("tenant database unavailable")
                deletes.append((self.tenant_id, str(statement)))
                return SimpleNamespace(rowcount=4)

        @contextlib.asynccontextmanager
        async def tenant_session(tenant_id):
            yield _TenantSession(tenant_id)

        with (
            patch("core.database.async_session_factory", factory),
            patch("core.database.get_tenant_session", tenant_session),
        ):
            result = asyncio.run(timeline_tasks._prune_run_spans_async())
        assert result["tenants"] == 2 and result["deleted"] == 4 and result["errors"] == 1
        assert len(deletes) == 1 and deletes[0][0] == t1 and "run_spans" in deletes[0][1]
        cutoff = datetime.fromisoformat(result["cutoff"])
        assert datetime.now(UTC) - cutoff > timedelta(days=29)
