# SPDX-License-Identifier: Apache-2.0
"""Tracing wiring and correlation ids (observability/tracing.py).

Off by default, every helper is a no-op that touches no tracer. On, the
call sites open spans, the log context carries the trace id and the signed
audit rows record it; an incoming W3C trace context is continued across the
API and the task queue.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, StatusCode, TraceFlags

from observability import tracing

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


@pytest.fixture(autouse=True)
def _clean():
    tracing.shutdown_tracing()
    structlog.contextvars.clear_contextvars()
    yield
    tracing.shutdown_tracing()
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    memory = InMemorySpanExporter()
    tracing.init_tracing("test-service", memory)
    return memory


def _finished(exporter: InMemorySpanExporter) -> list:
    tracing.flush()
    return list(exporter.get_finished_spans())


def _one(exporter: InMemorySpanExporter, name: str):
    spans = [s for s in _finished(exporter) if s.name == name]
    assert len(spans) == 1, [s.name for s in _finished(exporter)]
    return spans[0]


class TestOffByDefault:
    def test_nothing_is_installed_and_the_helpers_are_no_ops(self):
        assert tracing.enabled() is False
        with tracing.span("agenticorg.anything", **{"tenant.id": "t"}) as current:
            assert current.is_recording() is False
            assert tracing.current_trace_id() == ""
            tracing.add_event("x", a=1)
            tracing.set_attributes(a=1)
        handle = tracing.start("agenticorg.manual")
        handle.set(a=1)
        handle.error(RuntimeError("x"))
        handle.end()
        handle.end()
        carrier: dict[str, str] = {}
        tracing.inject_headers(carrier)
        assert carrier == {}

    def test_ambient_opentelemetry_span_is_ignored_when_agenticorg_tracing_is_off(self):
        ambient = NonRecordingSpan(
            SpanContext(
                trace_id=int(TRACE, 16),
                span_id=int("00f067aa0ba902b7", 16),
                is_remote=True,
                trace_flags=TraceFlags(0x01),
            )
        )
        token = trace.context_api.attach(trace.set_span_in_context(ambient))
        try:
            assert tracing.enabled() is False
            assert tracing.current_trace_id() == ""
        finally:
            trace.context_api.detach(token)

    def test_the_settings_switch_is_off_and_init_from_settings_installs_nothing(self):
        assert tracing.settings.tracing_enabled is False
        assert tracing.init_tracing_from_settings() is False
        assert tracing.enabled() is False

    def test_audit_rows_fall_back_to_the_fallback_then_the_request_id(self):
        assert tracing.audit_trace_id() == ""
        structlog.contextvars.bind_contextvars(request_id="req-" + "x" * 100)
        assert tracing.audit_trace_id() == ("req-" + "x" * 100)[:64]
        assert tracing.audit_trace_id("corr-1") == "corr-1"


class TestSpans:
    def test_a_span_records_its_cleaned_attributes(self, exporter):
        tid = uuid.uuid4()
        with tracing.span(
            "agenticorg.test", kind=SpanKind.CLIENT, **{"tenant.id": tid, "empty": "", "none": None, "ok": True, "n": 3}
        ):
            pass
        span = _one(exporter, "agenticorg.test")
        assert span.kind is SpanKind.CLIENT
        assert dict(span.attributes) == {"tenant.id": str(tid), "ok": True, "n": 3}

    def test_nested_spans_share_the_trace_and_the_log_context_carries_it(self, exporter):
        assert tracing.current_trace_id() == ""
        with tracing.span("outer"):
            outer = tracing.current_trace_id()
            assert len(outer) == 32
            assert structlog.contextvars.get_contextvars()["trace_id"] == outer
            with tracing.span("inner"):
                assert tracing.current_trace_id() == outer
                assert tracing.audit_trace_id("ignored-fallback") == outer
            assert structlog.contextvars.get_contextvars()["trace_id"] == outer
        assert tracing.current_trace_id() == ""
        assert "trace_id" not in structlog.contextvars.get_contextvars()
        spans = {s.name: s for s in _finished(exporter)}
        assert format(spans["inner"].context.trace_id, "032x") == outer
        assert spans["inner"].parent.span_id == spans["outer"].context.span_id

    def test_events_and_late_attributes_land_on_the_span_in_progress(self, exporter):
        with tracing.span("with-events"):
            tracing.add_event("guardrail.outcome", stage="input", applied=True, rule_id=None)
            tracing.set_attributes(**{"llm.tokens": 12})
        span = _one(exporter, "with-events")
        assert span.attributes["llm.tokens"] == 12
        assert [e.name for e in span.events] == ["guardrail.outcome"]
        assert dict(span.events[0].attributes) == {"stage": "input", "applied": True}

    def test_an_exception_marks_the_span_and_still_propagates(self, exporter):
        with pytest.raises(ValueError), tracing.span("failing"):
            raise ValueError("boom")
        span = _one(exporter, "failing")
        assert span.status.status_code is StatusCode.ERROR
        assert [e.name for e in span.events] == ["exception"]

    def test_a_handle_manages_a_span_across_callbacks(self, exporter):
        handle = tracing.start("manual", **{"task.id": "t1"})
        assert tracing.current_trace_id() != ""
        handle.set(**{"task.state": "SUCCESS"})
        handle.error(RuntimeError("late"))
        handle.end()
        handle.end()
        assert tracing.current_trace_id() == ""
        span = _one(exporter, "manual")
        assert span.attributes["task.state"] == "SUCCESS" and span.status.status_code is StatusCode.ERROR

    def test_a_parent_context_continues_the_trace_and_headers_are_injected(self, exporter):
        with tracing.span("continued", parent={"traceparent": PARENT}):
            assert tracing.current_trace_id() == TRACE
            carrier: dict[str, str] = {}
            tracing.inject_headers(carrier)
            assert carrier["traceparent"].split("-")[1] == TRACE
        span = _one(exporter, "continued")
        assert format(span.context.trace_id, "032x") == TRACE

    def test_trace_headers_are_picked_from_any_case_text_or_bytes(self):
        headers = {b"TraceParent": PARENT.encode(), "x-request-id": "r", "tracestate": b"a=b", "other": "x"}
        assert tracing.trace_headers(headers) == {"traceparent": PARENT, "tracestate": "a=b"}
        assert tracing.trace_headers(None) == {}

    def test_init_is_idempotent_and_shutdown_resets(self, exporter):
        first = tracing.get_tracer()
        assert tracing.init_tracing("again") is first
        tracing.shutdown_tracing()
        assert tracing.enabled() is False
        with tracing.span("after-shutdown") as current:
            assert current.is_recording() is False


class TestSettings:
    @pytest.fixture
    def on(self, monkeypatch):
        monkeypatch.setattr(tracing.settings, "tracing_enabled", True)
        monkeypatch.setattr(tracing.settings, "tracing_protocol", "http/protobuf")
        monkeypatch.setattr(tracing.settings, "tracing_sample_ratio", 1.0)
        monkeypatch.setattr(tracing.settings, "env", "test")
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "")
        monkeypatch.setattr(tracing.external_keys, "otel_service_name", "agenticorg-core")

    def test_an_unknown_protocol_is_refused(self, on, monkeypatch):
        monkeypatch.setattr(tracing.settings, "tracing_protocol", "thrift")
        with pytest.raises(tracing.TracingError, match="AGENTICORG_TRACING_PROTOCOL"):
            tracing.init_tracing_from_settings()
        assert tracing.enabled() is False

    @pytest.mark.parametrize("ratio", [-0.1, 1.5, float("nan"), True, "1"])
    def test_a_sample_ratio_outside_zero_to_one_is_refused(self, on, monkeypatch, ratio):
        monkeypatch.setattr(tracing.settings, "tracing_sample_ratio", ratio)
        with pytest.raises(tracing.TracingError, match="SAMPLE_RATIO"):
            tracing.init_tracing_from_settings()

    def test_a_strict_runtime_refuses_to_start_without_an_endpoint(self, on, monkeypatch):
        monkeypatch.setattr(tracing.settings, "env", "production")
        with pytest.raises(tracing.TracingError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
            tracing.init_tracing_from_settings()
        assert tracing.enabled() is False

    def test_a_relaxed_runtime_records_without_an_exporter(self, on):
        assert tracing.init_tracing_from_settings() is True
        assert tracing.enabled() is True
        assert tracing.init_tracing_from_settings() is True
        with tracing.span("recorded") as current:
            assert current.is_recording() is True

    def test_an_endpoint_builds_the_exporter_for_the_protocol(self, on, monkeypatch):
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "https://collector.example")
        monkeypatch.setattr(tracing.settings, "tracing_sample_ratio", 0.25)
        built: list[tuple[str, str]] = []

        def _exporter(protocol, endpoint):
            built.append((protocol, endpoint))
            return InMemorySpanExporter()

        monkeypatch.setattr(tracing, "otlp_exporter", _exporter)
        assert tracing.init_tracing_from_settings() is True
        assert built == [("http/protobuf", "https://collector.example")]

    def test_validate_settings_returns_what_init_needs_and_refuses_the_rest(self, on, monkeypatch):
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", " https://collector.example ")
        assert tracing.validate_settings() == ("http/protobuf", 1.0, "https://collector.example")
        monkeypatch.setattr(tracing.settings, "env", "production")
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "")
        with pytest.raises(tracing.TracingError):
            tracing.validate_settings()

    def test_deployment_wide_residency_keeps_the_exporter_out(self, on, monkeypatch):
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "https://collector.example")
        monkeypatch.setattr(tracing.settings, "residency_enforce", True)
        built: list[tuple[str, str]] = []
        monkeypatch.setattr(tracing, "otlp_exporter", lambda protocol, endpoint: built.append((protocol, endpoint)))
        assert tracing.init_tracing_from_settings() is True
        assert built == [] and tracing.enabled() is True

    def test_tenant_scoped_residency_wraps_the_exporter(self, on, monkeypatch):
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "https://collector.example")
        wrapped: list = []
        monkeypatch.setattr(tracing, "otlp_exporter", lambda protocol, endpoint: InMemorySpanExporter())
        original = tracing.ResidencyExporter

        def _record(inner):
            exporter = original(inner)
            wrapped.append(exporter)
            return exporter

        monkeypatch.setattr(tracing, "ResidencyExporter", _record)
        assert tracing.init_tracing_from_settings() is True
        assert len(wrapped) == 1 and isinstance(wrapped[0].inner, InMemorySpanExporter)

    def test_the_http_exporter_gets_the_traces_path_once(self):
        assert tracing.traces_endpoint("https://collector.example/") == "https://collector.example/v1/traces"
        assert tracing.traces_endpoint("https://collector.example/v1/traces") == "https://collector.example/v1/traces"
        http = tracing.otlp_exporter("http/protobuf", "https://collector.example")
        assert http._endpoint == "https://collector.example/v1/traces"


class TestResidency:
    def test_a_tenant_reference_is_keyed_bounded_and_stable(self):
        tenant = str(uuid.uuid4())
        ref = tracing.tenant_ref(tenant)
        assert len(ref) == 16 and ref == tracing.tenant_ref(tenant) and ref != tracing.tenant_ref(uuid.uuid4())
        assert tenant[:8] not in ref and tracing.tenant_ref(uuid.UUID(tenant)) == ref

    def test_a_span_records_the_reference_and_the_withholding_mark(self, exporter, monkeypatch):
        known: dict[str, bool | None] = {"home": True, "free": False}
        monkeypatch.setattr("core.governance.residency.enforcement_known", lambda tenant: known.get(str(tenant)))
        with (
            tracing.span("home", tenant="home"),
            tracing.span("free", tenant="free"),
            tracing.span("unread", tenant="x"),
        ):
            pass
        with tracing.span("nobody"):
            pass
        spans = {s.name: dict(s.attributes) for s in _finished(exporter)}
        assert spans["home"] == {"tenant.ref": tracing.tenant_ref("home"), "export.withheld": "residency"}
        assert spans["free"] == {"tenant.ref": tracing.tenant_ref("free")}
        assert spans["unread"]["export.withheld"] == "residency"
        assert spans["nobody"] == {}

    def test_the_exporter_withholds_what_residency_keeps_home(self, exporter, monkeypatch):
        known: dict[str, bool | None] = {"home": True, "free": False}
        monkeypatch.setattr("core.governance.residency.enforcement_known", lambda tenant: known.get(str(tenant)))
        anyone = {"value": False}
        monkeypatch.setattr("core.governance.residency.any_tenant_enforcing", lambda: anyone["value"])
        with tracing.span("home", tenant="home"), tracing.span("free", tenant="free"), tracing.span("nobody"):
            pass
        finished = _finished(exporter)

        class _Inner:
            def __init__(self):
                self.received: list[str] = []
                self.flushed = False
                self.stopped = False

            def export(self, spans):
                self.received.extend(s.name for s in spans)
                return tracing.SpanExportResult.SUCCESS

            def force_flush(self, timeout_millis=30000):
                self.flushed = True
                return True

            def shutdown(self):
                self.stopped = True

        inner = _Inner()
        wrapper = tracing.ResidencyExporter(inner)
        assert wrapper.export(finished) is tracing.SpanExportResult.SUCCESS
        assert sorted(inner.received) == ["free", "nobody"]
        anyone["value"] = True
        inner.received.clear()
        wrapper.export(finished)
        assert inner.received == ["free"]
        assert wrapper.export([s for s in finished if s.name == "home"]) is tracing.SpanExportResult.SUCCESS
        assert inner.received == ["free"]
        assert wrapper.force_flush() is True and inner.flushed
        wrapper.shutdown()
        assert inner.stopped


class TestWorkerStartup:
    def test_a_worker_refuses_to_start_with_tracing_on_and_misconfigured(self, monkeypatch):
        from core.tasks.celery_app import _refuse_worker_with_invalid_tracing, _start_tracing

        assert _refuse_worker_with_invalid_tracing() is None
        monkeypatch.setattr(tracing.settings, "tracing_enabled", True)
        monkeypatch.setattr(tracing.settings, "tracing_protocol", "thrift")
        with pytest.raises(SystemExit, match="Refusing to start the worker"):
            _refuse_worker_with_invalid_tracing()
        monkeypatch.setattr(
            tracing, "init_tracing_from_settings", lambda: (_ for _ in ()).throw(tracing.TracingError("bad"))
        )
        with pytest.raises(SystemExit, match="Refusing to start the worker process"):
            _start_tracing()

    def test_a_worker_process_installs_the_tracer_when_the_settings_are_good(self, monkeypatch):
        from core.tasks.celery_app import _start_tracing

        monkeypatch.setattr(tracing.settings, "tracing_enabled", True)
        monkeypatch.setattr(tracing.settings, "env", "test")
        monkeypatch.setattr(tracing.external_keys, "otel_exporter_otlp_endpoint", "")
        _start_tracing()
        assert tracing.enabled() is True


class TestRequestMiddleware:
    @staticmethod
    def _app() -> FastAPI:
        from api.middleware.request_id import RequestIDMiddleware

        app = FastAPI()

        @app.get("/echo")
        async def echo():
            return {"trace_id": tracing.current_trace_id(), "context": structlog.contextvars.get_contextvars()}

        app.add_middleware(RequestIDMiddleware)
        return app

    def test_the_request_span_continues_the_callers_trace_and_records_the_status(self, exporter):
        client = TestClient(self._app())
        resp = client.get("/echo", headers={"X-Request-ID": "req-1", "traceparent": PARENT})
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == TRACE and body["context"]["request_id"] == "req-1"
        assert body["context"]["trace_id"] == TRACE
        assert structlog.contextvars.get_contextvars() == {}
        span = _one(exporter, "agenticorg.http.request")
        assert span.kind is SpanKind.SERVER
        assert format(span.context.trace_id, "032x") == TRACE
        assert span.attributes["http.request.method"] == "GET"
        assert span.attributes["url.path"] == "/echo"
        assert span.attributes["request.id"] == "req-1"
        assert span.attributes["http.response.status_code"] == 200

    def test_without_tracing_the_request_sees_no_trace_id(self):
        resp = TestClient(self._app()).get("/echo", headers={"traceparent": PARENT})
        assert resp.status_code == 200
        assert resp.json()["trace_id"] == ""
        assert "trace_id" not in resp.json()["context"]


class TestTaskQueue:
    def test_the_publisher_puts_the_trace_context_in_the_task_headers(self, exporter):
        from core.tasks.celery_app import propagate_request_id_to_task

        headers: dict = {}
        structlog.contextvars.bind_contextvars(request_id="req-7")
        with tracing.span("publisher"):
            propagate_request_id_to_task(headers=headers)
            assert headers["request_id"] == "req-7"
            assert headers["traceparent"].split("-")[1] == tracing.current_trace_id()

    def test_the_worker_continues_the_trace_for_the_task_and_ends_it_after(self, exporter):
        from core.tasks.celery_app import bind_task_log_context, clear_task_log_context

        task = SimpleNamespace(
            name="core.tasks.example", request=SimpleNamespace(headers={"traceparent": PARENT, "request_id": "req-9"})
        )
        bind_task_log_context(task_id="task-1", task=task)
        try:
            assert tracing.current_trace_id() == TRACE
            assert structlog.contextvars.get_contextvars()["trace_id"] == TRACE
            assert structlog.contextvars.get_contextvars()["request_id"] == "req-9"
        finally:
            clear_task_log_context(task_id="task-1", state="SUCCESS")
        assert tracing.current_trace_id() == ""
        assert structlog.contextvars.get_contextvars() == {}
        span = _one(exporter, "agenticorg.task.run")
        assert span.kind is SpanKind.CONSUMER
        assert format(span.context.trace_id, "032x") == TRACE
        assert span.attributes["task.name"] == "core.tasks.example"
        assert span.attributes["task.id"] == "task-1"
        assert span.attributes["request.id"] == "req-9"
        assert span.attributes["task.state"] == "SUCCESS"

    def test_without_tracing_the_task_hooks_still_bind_and_clear_the_log_context(self):
        from core.tasks.celery_app import bind_task_log_context, clear_task_log_context

        bind_task_log_context(task_id="task-2", task=SimpleNamespace(name="x"))
        assert structlog.contextvars.get_contextvars()["request_id"] == "task-2"
        clear_task_log_context(task_id="task-2")
        assert structlog.contextvars.get_contextvars() == {}


class TestToolCalls:
    @pytest.mark.parametrize(
        ("result", "outcome"),
        [
            ({"ok": True}, "ok"),
            ({"error": "guardrail_blocked", "message": "x"}, "guardrail_blocked"),
            ({"error": "operator_override"}, "operator_override"),
            ({"error": "action_contained"}, "action_contained"),
            ({"error": "Connector 'x' not found in registry"}, "error"),
        ],
    )
    def test_the_dispatch_runs_inside_a_tool_span_with_its_outcome(self, exporter, result, outcome):
        from core.langgraph import tool_adapter

        with patch.object(tool_adapter, "_dispatch_connector_tool", AsyncMock(return_value=result)) as dispatch:
            got = asyncio.run(
                tool_adapter._execute_connector_tool("tally", "list_ledgers", {"a": 1}, tenant_id="t-1", agent_id="a-1")
            )
        assert got == result
        dispatch.assert_awaited_once()
        span = _one(exporter, "agenticorg.tool.call")
        assert span.kind is SpanKind.CLIENT
        assert span.attributes["tool.name"] == "list_ledgers"
        assert span.attributes["connector.id"] == "tally"
        assert span.attributes["agent.id"] == "a-1"
        assert span.attributes["tenant.ref"] == tracing.tenant_ref("t-1") and "tenant.id" not in span.attributes
        assert span.attributes["tool.outcome"] == outcome


class TestAgentRuns:
    def test_a_resumed_run_has_a_span_with_its_routing_decision_and_outcome(self, exporter):
        from auth.grant_enforcement import EnforcementMode
        from auth.run_grants import RunGrant
        from core.governance.guardrails.schema import GuardrailBlocked
        from core.governance.model_gateway import RouteDecision
        from core.langgraph import runner

        refusal = GuardrailBlocked("blocked", stage="output", correlation_id="c9", rule_id="r1", rule_name="cards")

        class _Compiled:
            async def ainvoke(self, _command, config=None):
                assert tracing.current_trace_id() != ""
                raise refusal

        graph = MagicMock()
        graph.compile.return_value = _Compiled()
        decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="corr-run", reason="p", applied=True)
        tenant = str(uuid.uuid4())
        with (
            patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
            patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
            patch.object(runner, "route_for_agent", AsyncMock(return_value=decision)),
        ):
            result = asyncio.run(
                runner.resume_agent(
                    agent_id="a1",
                    thread_id=runner._run_thread_id(uuid.UUID(tenant), "t-1", "a1"),
                    decision={"action": "approve"},
                    system_prompt="x",
                    authorized_tools=[],
                    tenant_id=tenant,
                    run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
                )
            )
        assert result["status"] == "guardrail_blocked"
        assert tracing.current_trace_id() == ""
        span = _one(exporter, "agenticorg.agent.resume")
        assert span.attributes["tenant.ref"] == tracing.tenant_ref(tenant) and "tenant.id" not in span.attributes
        assert span.attributes["agent.id"] == "a1"
        assert span.attributes["gateway.correlation_id"] == "corr-run"
        assert span.attributes["llm.provider"] == "openai" and span.attributes["llm.model"] == "gpt-4o"
        assert span.attributes["agent.run.status"] == "guardrail_blocked"
        assert span.attributes["agent.run.error_code"] == "E1016"

    def test_the_run_path_and_the_reasoning_node_are_wired_the_same_way(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        runner = (root / "core" / "langgraph" / "runner.py").read_text(encoding="utf-8")
        run = runner[runner.index("async def run_agent(") : runner.index("async def resume_agent(")]
        assert '"agenticorg.agent.run"' in run and "tracing.start(" in run
        assert run.count("_traced_result(run_span") >= 3 and "run_span.end()" in run
        graph = (root / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        assert '"agenticorg.agent.reason"' in graph and '"llm.output_tokens": output_tokens' in graph
        gateway = (root / "core" / "governance" / "model_gateway.py").read_text(encoding="utf-8")
        assert '"model_gateway.decision"' in gateway
        engine = (root / "core" / "governance" / "guardrails" / "engine.py").read_text(encoding="utf-8")
        assert '"guardrail.outcome"' in engine
        knowledge = (root / "api" / "v1" / "knowledge.py").read_text(encoding="utf-8")
        assert '"agenticorg.knowledge.search"' in knowledge and "tracing.span(" in knowledge


class TestAuditRows:
    def test_governance_audit_rows_record_the_trace_in_progress_else_the_request_id(self, exporter):
        from core.governance import model_gateway, operator_override, residency
        from core.governance.guardrails import engine

        tid = uuid.uuid4()
        structlog.contextvars.bind_contextvars(request_id="req-audit")
        with tracing.span("request"):
            trace_id = tracing.current_trace_id()
            row = model_gateway._audit_entry(tid, actor_id="user:1", action="create", policy_id="p1", details={})
            assert row.trace_id == trace_id
            assert (
                engine._audit_entry(tid, actor_id="user:1", event="create", resource_id="r1", details={}).trace_id
                == trace_id
            )
        assert (
            model_gateway._audit_entry(tid, actor_id="user:1", action="create", policy_id="p1", details={}).trace_id
            == "req-audit"
        )
        assert (
            engine._audit_entry(tid, actor_id="user:1", event="create", resource_id="r1", details={}).trace_id
            == "req-audit"
        )
        for module in (operator_override, residency):
            source = __import__("inspect").getsource(module)
            assert '"trace_id": tracing.audit_trace_id()' in source, module.__name__
