# SPDX-License-Identifier: Apache-2.0
"""The model-call hook and the entry points: isolated from the call, synchronous, and a no-op while off."""

from __future__ import annotations

import asyncio
import decimal
import inspect
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import settings
from core.governance import model_gateway_records as records
from core.llm import router as llm_router
from core.spend import context, meter, metering, tokens, writer
from tests.unit.spend_usage_fakes import T0, TENANT

TID = str(TENANT)
ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.fixture
def on(monkeypatch):
    from core.spend import clock

    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    monkeypatch.setattr(settings, "spend_reporting_timezone", "Asia/Kolkata")
    monkeypatch.setattr(clock, "now_utc", lambda: T0)
    got: dict[str, list] = {"events": [], "gaps": []}
    monkeypatch.setattr(writer, "submit", lambda events: got["events"].extend(events))
    monkeypatch.setattr(writer, "add_gap", lambda *args, **kw: got["gaps"].append(args))
    return got


def decision(**over):
    base = {"tenant_id": TID, "correlation_id": "corr-1", "use_case": "completion", "gated": False, "provider": None}
    base.update(over)
    return SimpleNamespace(**base)


class TestHook:
    @pytest.mark.asyncio
    async def test_hook_off_calls_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)

        def explode(*args, **kwargs):
            raise AssertionError("the meter ran while spend is off")

        monkeypatch.setattr(meter, "meter_model_call", explode)
        record = await records.record_model_call(
            decision(), provider="gpt", model="gpt-4o", outcome="completed", latency_ms=5, tokens=10
        )
        assert record.tokens == 10

    @pytest.mark.asyncio
    async def test_hook_runs_after_the_gated_write(self, monkeypatch, on):
        order: list[str] = []

        async def write(record):
            order.append("write")
            return True

        monkeypatch.setattr(records, "_write", write)
        monkeypatch.setattr(meter, "meter_model_call", lambda record, **kw: order.append("meter"))
        await records.record_model_call(
            decision(gated=True), provider="gpt", model="gpt-4o", outcome="completed", latency_ms=5, tokens=10
        )
        assert order == ["write", "meter"]

    @pytest.mark.asyncio
    async def test_hook_is_synchronous_and_queues_only(self, on):
        assert not inspect.iscoroutinefunction(meter.meter_model_call)
        assert not inspect.iscoroutinefunction(records._meter_spend)
        record = await records.record_model_call(
            decision(), provider="gpt", model="gpt-4o", outcome="completed", latency_ms=5, tokens=15,
            input_tokens=10, output_tokens=5,
        )  # fmt: skip
        assert [e.unit for e in on["events"]] == ["input_token", "output_token"]
        assert on["events"][0].event_time == record.created_at

    @pytest.mark.asyncio
    async def test_hook_failure_never_changes_the_call_or_the_gated_write(self, monkeypatch, on):
        written: list = []

        async def write(record):
            written.append(record)
            return True

        def explode(*args, **kwargs):
            raise RuntimeError("meter down")

        monkeypatch.setattr(records, "_write", write)
        monkeypatch.setattr(meter, "meter_model_call", explode)
        record = await records.record_model_call(
            decision(gated=True), provider="gpt", model="gpt-4o", outcome="completed", latency_ms=5, tokens=10
        )
        assert record.outcome == "completed" and written == [record]

    @pytest.mark.asyncio
    async def test_invalid_operation_in_detail_extraction_never_reaches_the_router(self, monkeypatch, on):
        recorded: list[str] = []
        real = records.record_model_call

        async def record_call(*args, **kwargs):
            recorded.append(kwargs["outcome"])
            return await real(*args, **kwargs)

        def bad_details(usage):
            raise decimal.InvalidOperation("usage is malformed")

        async def call_model(model, messages, temperature, max_tokens, **scope):
            return llm_router.LLMResponse(content="ok", model=model, tokens_used=3, input_tokens=2, output_tokens=1)

        monkeypatch.setattr(llm_router, "record_model_call", record_call)
        monkeypatch.setattr(tokens, "details_of", bad_details)
        router = llm_router.LLMRouter()
        monkeypatch.setattr(router, "_call_model", call_model)
        response = await router.complete([{"role": "user", "content": "hi"}], model_override="gpt-4o")
        assert response.content == "ok" and recorded == ["completed"]  # no failed record, no fallback
        assert on["events"] == []

    @pytest.mark.asyncio
    async def test_graph_call_usage_extraction_failure_never_fails_the_run(self, on):
        class Hostile:
            @property
            def usage_metadata(self):
                raise decimal.InvalidOperation("bad metadata")

        usage = context.call_usage("message", response=Hostile(), llm=object(), tenant_id=TID)
        record = await records.record_model_call(
            provider="openai", model="gpt-4o", outcome="completed", latency_ms=5, tokens=3, input_tokens=2,
            output_tokens=1, spend_usage=usage,
        )  # fmt: skip
        assert record.outcome == "completed"

    @pytest.mark.asyncio
    async def test_unrouted_call_with_details_tenant_is_metered(self, on):
        usage = context.call_usage("message", response=SimpleNamespace(), tenant_id=TID)
        record = await records.record_model_call(
            provider="openai", model="gpt-4o", outcome="completed", latency_ms=5, tokens=3, input_tokens=2,
            output_tokens=1, spend_usage=usage,
        )  # fmt: skip
        assert record.tenant_id is None and {e.tenant_id for e in on["events"]} == {TID}

    @pytest.mark.asyncio
    async def test_router_cancellation_is_counted(self, monkeypatch, on):
        async def decide(request):
            return SimpleNamespace(applied=False, provider=None, model=request.requested_model, tenant_id=TID,
                                   correlation_id="c", gated=False, use_case="completion")  # fmt: skip

        async def admit(decision_):
            return None

        async def release(lease):
            return None

        async def cancelled(model, messages, temperature, max_tokens, **scope):
            raise asyncio.CancelledError()

        monkeypatch.setattr(llm_router, "gateway_decide", decide)
        monkeypatch.setattr(llm_router, "gateway_admit", admit)
        monkeypatch.setattr(llm_router, "gateway_release", release)
        router = llm_router.LLMRouter()
        monkeypatch.setattr(router, "_call_model", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await router.complete([{"role": "user", "content": "hi"}], model_override="gpt-4o", tenant_id=TID)
        assert on["gaps"] and on["gaps"][0][3:] == ("failed_no_usage", "cancelled:openai")

    def test_flag_off_router_raw_and_cassette_payload_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        response = SimpleNamespace(candidates=["c"])
        usage = SimpleNamespace(cached_content_token_count=5, thoughts_token_count=7)
        assert llm_router._gemini_raw(response, usage) == {"candidates": "['c']"}
        assert set(asdict(llm_router.LLMResponse(content="x", model="m"))) == {
            "content", "model", "tokens_used", "cost_usd", "latency_ms", "raw", "input_tokens", "output_tokens",
        }  # fmt: skip
        assert llm_router._spend_usage(None, None, [], TID) is None
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        raw = llm_router._gemini_raw(response, usage)
        assert raw["usage"] == {"cached_content_token_count": 5, "thoughts_token_count": 7}
        assert llm_router._count(SimpleNamespace(x="nan"), "x") is None and llm_router._count(None, "x") is None
        assert llm_router._spend_usage(None, None, [], TID).source == "router"

    def test_router_notes_the_credential_after_resolving_it(self):
        text = source("core/llm/router.py")
        resolver_block = text[text.index("resolved = await get_provider_credential(") :][:400]
        assert "spend_context.note_credential(provider, resolved.source)" in resolver_block
        assert "spend_usage=_spend_usage(response, exc, messages, tenant_id)" in text
        assert 'spend.note("cancelled", tenant_id, provider=_model_provider(model))' in text

    def test_graph_hands_its_usage_and_billing_account_to_the_record(self):
        text = source("core/langgraph/agent_graph.py")
        assert "spend_billing = spend_context.billing_account_of(prefetched_credentials, tenant_id" in text
        completed = text[text.index('outcome="completed",') :][:900]
        assert "spend_usage=spend_context.call_usage(" in completed and "billing_account=spend_billing" in completed


class TestEntryPoints:
    def test_agents_run_binds_run_id_version_and_user(self):
        text = source("api/v1/agents.py")
        run = text[text.index("async def run_agent(") :]
        run = run[: run.index("\n@router.")]
        bind = run[run.index("spend_context.bind_scope(") :][:500]
        for part in ('application="agents"', "agent_id=str(agent_id)", "run_id=correlation_id", "caller.user_id"):
            assert part in bind, part
        assert run.index("spend_context.bind_scope(") < run.index("lg_result = await langgraph_run(")
        assert "spend_context.reset_scope(spend_token)" in run
        assert 'inputs.get("business_unit")' not in bind and "use_case" not in bind

    @pytest.mark.parametrize(
        ("path", "anchor"),
        [
            ("api/v1/chat.py", 'spend_context.scope(application="chat")'),
            ("api/v1/a2a.py", 'spend_context.scope(application="a2a")'),
            ("api/v1/mcp.py", 'spend_context.scope(application="mcp")'),
            ("api/v1/agent_debug.py", 'application="agents", agent_id=str(agent_id)'),
            ("core/voice/livekit_agent.py", 'spend_context.scope(application="voice"'),
            ("core/approvals/agent_run_resume.py", 'application="agents", agent_id=str(claim.agent_id)'),
            ("workflows/step_types.py", 'application="workflows",'),
            ("workflows/step_types.py", 'spend_context.scope(agent_id=stored_config.get("id"))'),
            ("workflows/engine.py", 'application="workflows",'),
            ("core/content/services.py", 'spend_context.scope(application="content", default_use_case="content")'),
            ("core/speech/summary.py", 'application="speech", default_use_case="speech.summary"'),
            ("core/txn/narrative.py", 'application="txn", default_use_case="txn.narrative"'),
            ("core/prompts/compare.py", 'application="console", default_use_case="prompts.compare"'),
            ("core/evals/scoring.py", 'application="console", default_use_case="evals.judge"'),
            ("core/workflow_generator.py", 'application="console", default_use_case="workflow.generate"'),
            ("core/agent_generator.py", 'application="console", default_use_case="agent.generate"'),
        ],
    )
    def test_entry_points_bind_their_application(self, path, anchor):
        assert anchor in source(path)

    def test_workflow_step_naming_a_retired_agent_is_not_attributed_to_it(self, monkeypatch):
        """Only the id of an agent the loader found runnable is bound; the step's raw id never is."""
        text = source("workflows/step_types.py")
        execute = text[text.index("async def _execute_agent(") :]
        execute = execute[: execute.index("result = await agent_instance.execute(task)")]
        assert 'spend_context.scope(agent_id=stored_config.get("id"))' in execute
        assert 'spend_context.scope(agent_id=config["id"])' not in text
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        stored_config: dict = {}  # what the loader returns for a missing, retired or deleted agent
        with context.scope(application="workflows", workflow_id="wf"):
            with context.scope(agent_id=stored_config.get("id")):
                assert context.current_scope().agent_id is None

    @pytest.mark.asyncio
    async def test_execute_step_binds_the_workflow_scope(self, monkeypatch):
        from workflows import step_types

        seen: list = []

        async def dispatch(step, state):
            seen.append(context.current_scope())
            return {"status": "completed"}

        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        monkeypatch.setattr(step_types, "_dispatch_step", dispatch)
        state = {"tenant_id": TID, "workflow_id": "wf-1", "workflow_run_id": str(uuid.uuid4())}
        assert await step_types.execute_step({"id": "s"}, state) == {"status": "completed"}
        assert seen[0].application == "workflows" and seen[0].workflow_id == "wf-1" and seen[0].tenant_id == TID
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        await step_types.execute_step({"id": "s"}, state)
        assert seen[1] is None

    def test_direct_callers_and_replanner_are_metered_only_when_on(self):
        for path, use_case in (
            ("core/explainer.py", "run.explanation"),
            ("core/feedback/analyzer.py", "feedback.analysis"),
            ("core/langgraph/sop_parser.py", "sop.parse"),
        ):
            text = source(path)
            assert (
                "if spend.enabled():" in text
                and f'spend.note("message", tenant_id, message=response, llm=llm, default_use_case="{use_case}")'
                in text
            )
        replanner = source("workflows/replanner.py")
        assert replanner.count('"direct_response",') == 2 and replanner.count('billing_account="platform_key"') == 2

    def test_lifespan_drains_before_stopping_metrics(self):
        text = source("api/main.py")
        after = text[text.index("    yield\n") :]
        assert after.index("await spend.drain(timeout=5.0)") < after.index("stop_metrics_server()")
        celery = source("core/tasks/celery_app.py")
        assert "def _drain_spend_writer(" in celery and "spend.drain_blocking(5.0)" in celery


class TestMeteringHandlers:
    def test_note_returns_at_once_while_off(self, monkeypatch):
        from core import spend

        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)

        def explode(*args):
            raise AssertionError("handled while off")

        monkeypatch.setattr(metering, "handle", explode)
        spend.note("message", TID, message=None)

    def test_note_failure_is_counted_never_raised(self, monkeypatch, on):
        from core import spend

        def explode(*args):
            raise decimal.InvalidOperation("bad")

        monkeypatch.setattr(metering, "handle", explode)
        spend.note("message", TID, message=None)

    def test_message_handler_meters_a_direct_langchain_call(self, on):
        class ChatAnthropic:
            model = "claude-sonnet"

        message = SimpleNamespace(usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
        metering.handle(
            "message", TID, {"message": message, "llm": ChatAnthropic(), "default_use_case": "run.explanation"}
        )
        assert [(e.provider, e.unit, e.quantity) for e in on["events"]] == [
            ("anthropic", "input_token", 120),
            ("anthropic", "output_token", 30),
        ]
        assert on["events"][0].hints.default_use_case == "run.explanation"
        assert on["events"][0].idempotency_key.startswith("llm:")

    def test_azure_class_stays_azure(self, on):
        class AzureChatOpenAI:
            model_name = "gpt-4o"

        message = SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        metering.handle("message", TID, {"message": message, "llm": AzureChatOpenAI()})
        assert on["events"][0].provider == "azure_openai"

    def test_direct_response_handler_reads_google_and_openai_usage(self, on):
        google = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=40, candidates_token_count=10))
        with context.scope(tenant_id=TID, application="workflows"):
            metering.handle(
                "direct_response",
                None,
                {
                    "provider": "gemini",
                    "model": "gemini-1.5-flash",
                    "response": google,
                    "billing_account": "platform_key",
                },
            )
            openai = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3))
            metering.handle("direct_response", None, {"provider": "openai", "model": "gpt-4o-mini", "response": openai})
        units = [(e.provider, e.unit, e.quantity, e.billing_account) for e in on["events"]]
        assert units[:2] == [
            ("gemini", "input_token", 40, "platform_key"),
            ("gemini", "output_token", 10, "platform_key"),
        ]
        assert units[2][:3] == ("openai", "input_token", 7)
        metering.handle("direct_response", TID, {"provider": "x", "response": object()})
        assert on["gaps"][-1][3] == "failed_no_usage"

    def test_cancelled_handler_counts_and_unknown_kinds_are_ignored(self, on):
        metering.handle("cancelled", TID, {"provider": "claude"})
        assert on["gaps"] == [(TID, date(2026, 10, 1), "llm_tokens", "failed_no_usage", "cancelled:anthropic")]
        metering.handle("cancelled", None, {"provider": "gpt"})  # no tenant: metric only
        metering.handle("embeddings", TID, {})  # a later part's kind: ignored here
        assert metering._int_or_none("x") is None and metering._int_or_none(True) is None
