# SPDX-License-Identifier: Apache-2.0
"""Streaming latency: the first-token time of a model call and a task's queue wait."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.config import settings
from observability import streaming

ROOT = Path(__file__).resolve().parents[3]
MESSAGES = [HumanMessage(content="What is the rate?")]


class _Model:
    """Answers whole through ``ainvoke``; through ``astream``, in chunks after a delay before the first."""

    def __init__(self, chunks: list, *, delay: float = 0.0, whole: AIMessage | None = None) -> None:
        self.chunks = chunks
        self.delay = delay
        self.whole = whole or AIMessage(content="whole answer")
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        self.calls.append("ainvoke")
        return self.whole

    async def astream(self, messages):
        self.calls.append("astream")
        if self.delay:
            await asyncio.sleep(self.delay)
        for chunk in self.chunks:
            yield chunk


class TestInvokeTimed:
    def test_off_by_default_the_call_is_made_as_before(self):
        assert settings.model_stream_timing_enabled is False
        model = _Model([AIMessageChunk(content="never read")])
        answer, first_token_ms = asyncio.run(streaming.invoke_timed(model, MESSAGES))
        assert answer is model.whole and first_token_ms is None and model.calls == ["ainvoke"]

    def test_on_the_answer_is_streamed_reassembled_and_timed(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)
        model = _Model([AIMessageChunk(content="The rate "), AIMessageChunk(content="is 3.5%.")], delay=0.05)
        answer, first_token_ms = asyncio.run(streaming.invoke_timed(model, MESSAGES))
        assert model.calls == ["astream"]
        assert type(answer) is AIMessage and answer.content == "The rate is 3.5%."
        assert first_token_ms is not None and 40 <= first_token_ms < 2000

    def test_the_first_token_is_the_first_chunk_that_carries_output(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)

        class _Slow(_Model):
            async def astream(self, messages):
                yield AIMessageChunk(content="")
                await asyncio.sleep(0.05)
                yield AIMessageChunk(content="late")

        answer, first_token_ms = asyncio.run(streaming.invoke_timed(_Slow([]), MESSAGES))
        assert answer.content == "late" and first_token_ms >= 40

    def test_a_streamed_tool_call_is_reassembled_and_counts_as_output(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)
        chunks = [
            AIMessageChunk(
                content="", tool_call_chunks=[{"name": "search_policy", "args": '{"q": ', "id": "c1", "index": 0}]
            ),
            AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": '"rate"}', "id": None, "index": 0}]),
        ]
        answer, first_token_ms = asyncio.run(streaming.invoke_timed(_Model(chunks), MESSAGES))
        assert first_token_ms is not None
        assert [(call["name"], call["args"], call["id"]) for call in answer.tool_calls] == [
            ("search_policy", {"q": "rate"}, "c1")
        ]

    def test_token_usage_survives_reassembly(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)
        usage = {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
        chunks = [AIMessageChunk(content="ok"), AIMessageChunk(content="", usage_metadata=usage)]
        answer, _ = asyncio.run(streaming.invoke_timed(_Model(chunks), MESSAGES))
        assert answer.usage_metadata == usage

    def test_a_model_that_does_not_stream_yields_its_whole_answer(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)
        whole = AIMessage(content="whole answer")
        answer, first_token_ms = asyncio.run(streaming.invoke_timed(_Model([whole]), MESSAGES))
        assert answer is whole and first_token_ms is not None

    def test_an_empty_stream_falls_back_to_the_plain_call(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)
        model = _Model([])
        answer, first_token_ms = asyncio.run(streaming.invoke_timed(model, MESSAGES))
        assert answer is model.whole and first_token_ms is None and model.calls == ["astream", "ainvoke"]

    def test_a_failing_stream_raises_as_a_failing_call_does(self, monkeypatch):
        monkeypatch.setattr(settings, "model_stream_timing_enabled", True)

        class _Broken(_Model):
            async def astream(self, messages):
                raise RuntimeError("provider unavailable")
                yield  # pragma: no cover

        with pytest.raises(RuntimeError, match="provider unavailable"):
            asyncio.run(streaming.invoke_timed(_Broken([]), MESSAGES))


class TestFirstTokenMetric:
    def test_a_measured_time_is_observed_by_provider_and_model(self):
        from observability.metrics import model_first_token_seconds

        assert tuple(model_first_token_seconds._labelnames) == ("provider", "model")
        series = model_first_token_seconds.labels(provider="openai", model="m-test")
        before = series._sum.get()
        streaming.observe_first_token("openai", "m-test", 250)
        assert round(series._sum.get() - before, 3) == 0.25
        streaming.observe_first_token("openai", "m-test", None)
        assert round(series._sum.get() - before, 3) == 0.25

    def test_the_reasoning_node_times_the_call_and_reports_it(self):
        src = (ROOT / "core" / "langgraph" / "agent_graph.py").read_text(encoding="utf-8")
        reason = src[src.index("async def reason(") : src.index("async def evaluate(")]
        assert "response, first_token_ms = await invoke_timed(llm, messages)" in reason
        assert '"llm.first_token_ms": first_token_ms,' in reason
        assert "observe_first_token(called_provider, called_model, first_token_ms)" in reason
        assert "llm.ainvoke(" not in reason


def _task(headers: dict | None = None, *, eta=None, routing_key: str = "maintenance") -> SimpleNamespace:
    request = SimpleNamespace(headers=headers or {}, eta=eta, delivery_info={"routing_key": routing_key})
    request.get = lambda key, default=None: None
    return SimpleNamespace(request=request, name="core.tasks.example")


class TestQueueWait:
    def test_a_published_task_is_stamped_once_unless_it_is_scheduled(self):
        headers: dict = {}
        streaming.stamp_enqueued(headers, now=1000.5)
        assert headers == {streaming.ENQUEUED_AT_HEADER: "1000.5"}
        streaming.stamp_enqueued(headers, now=2000.0)
        assert headers[streaming.ENQUEUED_AT_HEADER] == "1000.5"
        scheduled = {"eta": "2026-10-04T12:00:00+00:00"}
        streaming.stamp_enqueued(scheduled, now=1000.5)
        assert streaming.ENQUEUED_AT_HEADER not in scheduled

    def test_the_wait_is_the_time_between_publish_and_start(self):
        task = _task({streaming.ENQUEUED_AT_HEADER: "1000.0"})
        assert streaming.queue_wait_seconds(task, now=1002.5) == 2.5

    @pytest.mark.parametrize(
        ("headers", "eta", "now"),
        [
            ({}, None, 1002.0),
            ({streaming.ENQUEUED_AT_HEADER: "not a number"}, None, 1002.0),
            ({streaming.ENQUEUED_AT_HEADER: "1000.0"}, "2026-10-04T12:00:00+00:00", 1002.0),
            ({streaming.ENQUEUED_AT_HEADER: "1000.0"}, None, 999.0),
            ({streaming.ENQUEUED_AT_HEADER: "1000.0"}, None, 1000.0 + streaming.MAX_QUEUE_WAIT_SECONDS + 1),
        ],
    )
    def test_no_wait_is_reported_without_a_usable_stamp(self, headers, eta, now):
        assert streaming.queue_wait_seconds(_task(headers, eta=eta), now=now) is None
        assert streaming.queue_wait_seconds(SimpleNamespace(), now=now) is None

    def test_the_wait_is_observed_by_queue_with_no_task_or_tenant_label(self):
        from observability.metrics import task_queue_wait_seconds

        assert tuple(task_queue_wait_seconds._labelnames) == ("queue",)
        series = task_queue_wait_seconds.labels(queue="maintenance")
        before = series._sum.get()
        waited = streaming.observe_queue_wait(_task({streaming.ENQUEUED_AT_HEADER: "1000.0"}), now=1004.0)
        assert waited == 4.0 and round(series._sum.get() - before, 3) == 4.0
        assert streaming.observe_queue_wait(_task({}), now=1004.0) is None
        assert round(series._sum.get() - before, 3) == 4.0

    def test_the_celery_signals_stamp_and_meter(self):
        from core.tasks import celery_app

        headers: dict = {}
        celery_app.stamp_task_publish_time(headers=headers)
        celery_app.stamp_task_publish_time(headers=None)
        assert float(headers[streaming.ENQUEUED_AT_HEADER]) > 0
        src = (ROOT / "core" / "tasks" / "celery_app.py").read_text(encoding="utf-8")
        prerun = src[src.index("def bind_task_log_context(") : src.index("def clear_task_log_context(")]
        assert "waited = observe_queue_wait(task)" in prerun and '"task.queue_wait_ms"' in prerun
