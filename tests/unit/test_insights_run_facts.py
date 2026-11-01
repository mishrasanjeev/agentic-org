# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The run-fact writer and its hooks: normalised rows, never a failed run, nothing written while off."""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.dialects import postgresql

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.config import settings
from core.insights import context as insights_context
from core.insights import flags as insights_flags
from core.insights import run_facts

TENANT = str(uuid.uuid4())
AGENT = str(uuid.uuid4())


class _Recorded:
    """Stands in for ``record_run_fact``: keeps every call's fields."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **fields: Any) -> bool:
        self.calls.append(fields)
        return True


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> _Recorded:
    sink = _Recorded()
    monkeypatch.setattr(run_facts, "record_run_fact", sink)
    return sink


@pytest.fixture
def insights_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "insights_enabled", True)
    monkeypatch.setattr("core.feature_flags.is_enabled", AsyncMock(return_value=True))


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_a_valid_fact_keeps_its_values(self):
        started = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
        row = run_facts.normalise_fact(
            {
                "tenant_id": TENANT,
                "run_key": "chat:corr-1",
                "entrypoint": "chat",
                "agent_id": AGENT,
                "status": "completed",
                "confidence": 0.91,
                "tokens_in": 100,
                "tokens_out": 20,
                "tokens_cached": 40,
                "cost_usd": 0.0123456789,
                "duration_ms": 850,
                "started_at": started,
            }
        )
        assert row is not None
        assert row["tenant_id"] == uuid.UUID(TENANT) and row["agent_id"] == uuid.UUID(AGENT)
        assert row["agent_ref"] is None and row["entrypoint"] == "chat" and row["status"] == "completed"
        assert row["tokens_total"] == 120 and row["tokens_cached"] == 40
        assert row["cost_usd"] == Decimal("0.012346") and row["duration_ms"] == 850
        assert row["started_at"] == started and row["environment"] == "production"
        assert row["data_quality"] == "measured" and row["hitl_required"] is False and row["error_class"] is None

    def test_unknown_status_is_failed_with_the_error_class_kept(self):
        row = run_facts.normalise_fact({"tenant_id": TENANT, "status": "exploded", "error_class": "ValueError"})
        assert row["status"] == "failed" and row["error_class"] == "ValueError"
        row = run_facts.normalise_fact({"tenant_id": TENANT, "status": "exploded"})
        assert row["status"] == "failed" and row["error_class"] == "exploded"
        assert run_facts.normalise_fact({"tenant_id": TENANT})["status"] == "failed"

    @pytest.mark.parametrize("status", ["operator_override", "model_gateway_refused", "guardrail_blocked"])
    def test_refusal_statuses_are_refused_and_named(self, status):
        row = run_facts.normalise_fact({"tenant_id": TENANT, "status": status})
        assert row["status"] == "refused" and row["error_class"] == status
        coded = run_facts.normalise_fact({"tenant_id": TENANT, "status": status, "error_class": "E1016"})
        assert coded["error_class"] == "E1016"

    def test_hitl_status_marks_hitl_required(self):
        row = run_facts.normalise_fact({"tenant_id": TENANT, "status": "HITL_TRIGGERED"})
        assert row["status"] == "hitl_triggered" and row["hitl_required"] is True

    def test_an_approval_resume_is_always_hitl_required(self):
        # The resume of a paused run exists because a person approved it: never straight through.
        row = run_facts.normalise_fact({"tenant_id": TENANT, "status": "completed", "entrypoint": "hitl_resume"})
        assert row["entrypoint"] == "hitl_resume" and row["status"] == "completed" and row["hitl_required"] is True
        plain = run_facts.normalise_fact({"tenant_id": TENANT, "status": "completed", "entrypoint": "agents"})
        assert plain["hitl_required"] is False

    def test_unknown_codes_fall_back_to_their_defaults(self):
        row = run_facts.normalise_fact(
            {
                "tenant_id": TENANT,
                "status": "completed",
                "entrypoint": "telepathy",
                "environment": "staging",
                "data_quality": "guessed",
            }
        )
        assert (row["entrypoint"], row["environment"], row["data_quality"]) == ("agents", "production", "measured")
        assert run_facts.normalise_fact({"tenant_id": TENANT, "data_quality": "seeded"})["data_quality"] == "seeded"

    def test_a_non_uuid_agent_id_is_kept_as_agent_ref(self):
        row = run_facts.normalise_fact({"tenant_id": TENANT, "agent_id": "mcp_0123456789ab"})
        assert row["agent_id"] is None and row["agent_ref"] == "mcp_0123456789ab"
        row = run_facts.normalise_fact({"tenant_id": TENANT, "agent_id": None, "agent_ref": "web_scrape"})
        assert row["agent_id"] is None and row["agent_ref"] == "web_scrape"
        row = run_facts.normalise_fact({"tenant_id": TENANT, "company_id": "not-a-uuid", "workflow_run_id": "x"})
        assert row["company_id"] is None and row["workflow_run_id"] is None

    def test_strings_are_cut_to_their_columns(self):
        row = run_facts.normalise_fact(
            {
                "tenant_id": TENANT,
                "run_key": "k" * 500,
                "agent_id": "a" * 500,
                "agent_type": "t" * 500,
                "domain": "d" * 500,
                "model": "m" * 500,
                "provider": "p" * 500,
                "error_class": "E" * 500,
                "task_type": "x" * 500,
                "trace_id": "f" * 500,
            }
        )
        sizes = {name: len(row[name]) for name in ("run_key", "agent_ref", "agent_type", "domain", "model")}
        assert sizes == {"run_key": 160, "agent_ref": 128, "agent_type": 100, "domain": 50, "model": 128}
        assert len(row["provider"]) == 32 and len(row["error_class"]) == 100
        assert len(row["task_type"]) == 100 and len(row["trace_id"]) == 64

    def test_numbers_are_clamped(self):
        row = run_facts.normalise_fact(
            {
                "tenant_id": TENANT,
                "confidence": 1.7,
                "confidence_floor": -0.2,
                "tokens_in": -5,
                "tokens_out": "12",
                "tokens_total": 10**12,
                "cost_usd": -1,
                "duration_ms": -40,
            }
        )
        assert row["confidence"] == 1.0 and row["confidence_floor"] == 0.0
        assert row["tokens_in"] == 0 and row["tokens_out"] == 12 and row["tokens_total"] == 2_147_483_647
        assert row["cost_usd"] == Decimal("0") and row["duration_ms"] == 0
        odd = run_facts.normalise_fact(
            {"tenant_id": TENANT, "confidence": math.nan, "cost_usd": "abc", "tokens_in": True, "duration_ms": None}
        )
        assert odd["confidence"] is None and odd["cost_usd"] == Decimal("0") and odd["tokens_in"] == 0
        assert odd["duration_ms"] is None

    def test_started_at_is_utc_and_a_missing_one_is_now(self):
        naive = datetime(2026, 11, 1, 8, 0, tzinfo=UTC).replace(tzinfo=None)
        assert run_facts.normalise_fact({"tenant_id": TENANT, "started_at": naive})["started_at"].tzinfo is UTC
        parsed = run_facts.normalise_fact({"tenant_id": TENANT, "started_at": "2026-11-01T08:00:00+05:30"})
        assert parsed["started_at"] == datetime(2026, 11, 1, 2, 30, tzinfo=UTC)
        before = datetime.now(UTC)
        assert run_facts.normalise_fact({"tenant_id": TENANT})["started_at"] >= before

    def test_a_fact_without_a_valid_tenant_is_dropped_and_a_missing_run_key_is_generated(self):
        assert run_facts.normalise_fact({"tenant_id": ""}) is None
        assert run_facts.normalise_fact({"tenant_id": "tenant-a"}) is None
        assert run_facts.normalise_fact({}) is None
        row = run_facts.normalise_fact({"tenant_id": TENANT, "entrypoint": "voice"})
        assert row["run_key"].startswith("voice:") and len(row["run_key"]) > len("voice:")


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


class TestWriter:
    async def test_while_off_nothing_is_written(self, monkeypatch):
        insert = AsyncMock()
        monkeypatch.setattr(run_facts, "_insert", insert)
        flag = AsyncMock(return_value=True)
        monkeypatch.setattr("core.feature_flags.is_enabled", flag)
        assert settings.insights_enabled is False
        assert await run_facts.record_run_fact(tenant_id=TENANT, status="completed") is False
        assert await run_facts.record_run_facts(TENANT, [{"status": "completed"}]) == 0
        run_facts.schedule_run_fact(tenant_id=TENANT, status="completed")
        assert not run_facts._PENDING
        insert.assert_not_awaited()
        flag.assert_not_awaited()

    async def test_a_tenant_that_opted_out_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "insights_enabled", True)
        flag = AsyncMock(return_value=False)
        monkeypatch.setattr("core.feature_flags.is_enabled", flag)
        insert = AsyncMock()
        monkeypatch.setattr(run_facts, "_insert", insert)
        assert await run_facts.record_run_fact(tenant_id=TENANT, status="completed") is False
        insert.assert_not_awaited()
        flag.assert_awaited_once_with("insights_dashboard", tenant_id=uuid.UUID(TENANT), default=True)

    async def test_the_flag_helper_reads_the_switch_then_the_tenant_flag(self, monkeypatch):
        flag = AsyncMock(return_value=True)
        monkeypatch.setattr("core.feature_flags.is_enabled", flag)
        assert await insights_flags.insights_enabled_for(TENANT) is False
        monkeypatch.setattr(settings, "insights_enabled", True)
        assert await insights_flags.insights_enabled_for(TENANT) is True
        assert await insights_flags.insights_enabled_for("not-a-tenant") is False
        assert await insights_flags.insights_enabled_for(None) is False
        monkeypatch.setattr("core.feature_flags.is_enabled", AsyncMock(side_effect=RuntimeError("down")))
        assert await insights_flags.insights_enabled_for(TENANT) is False

    async def test_a_write_inserts_the_normalised_row(self, monkeypatch, insights_on):
        insert = AsyncMock()
        monkeypatch.setattr(run_facts, "_insert", insert)
        assert await run_facts.record_run_fact(tenant_id=TENANT, run_key="a2a:t1", agent_id="t1", status="ok") is True
        (tenant, rows), _ = insert.await_args
        assert tenant == uuid.UUID(TENANT) and len(rows) == 1
        assert rows[0]["agent_ref"] == "t1" and rows[0]["status"] == "failed" and rows[0]["error_class"] == "ok"

    async def test_it_never_raises(self, monkeypatch, insights_on):
        monkeypatch.setattr(run_facts, "_insert", AsyncMock(side_effect=ConnectionError("database down")))
        warnings = MagicMock()
        monkeypatch.setattr(run_facts.logger, "warning", warnings)
        assert await run_facts.record_run_fact(tenant_id=TENANT, status="completed") is False
        assert await run_facts.record_run_facts(TENANT, [{"status": "completed"}]) == 0
        warnings.assert_called_with("run_fact_write_failed", error_type="ConnectionError")
        # A malformed field never reaches the database layer as an exception either.
        monkeypatch.setattr(run_facts, "normalise_fact", MagicMock(side_effect=TypeError("bad")))
        assert await run_facts.record_run_fact(tenant_id=TENANT) is False

    def test_the_write_waits_at_most_two_seconds(self):
        # The write is awaited in the run's finally (and by BaseAgent.execute): it may hold a response 2 s at most.
        assert run_facts.WRITE_TIMEOUT_SECONDS == 2.0

    async def test_a_slow_database_cannot_hold_the_run(self, monkeypatch, insights_on):
        async def hang(*_args: Any) -> None:
            await asyncio.sleep(30)

        monkeypatch.setattr(run_facts, "_insert", hang)
        monkeypatch.setattr(run_facts, "WRITE_TIMEOUT_SECONDS", 0.05)
        assert await run_facts.record_run_fact(tenant_id=TENANT, status="completed") is False

    async def test_a_batch_is_one_statement_of_one_tenant(self, monkeypatch, insights_on):
        insert = AsyncMock()
        monkeypatch.setattr(run_facts, "_insert", insert)
        other = str(uuid.uuid4())
        written = await run_facts.record_run_facts(
            TENANT, [{"status": "completed", "tenant_id": other}, {"status": "failed", "data_quality": "seeded"}]
        )
        assert written == 2
        insert.assert_awaited_once()
        (tenant, rows), _ = insert.await_args
        assert tenant == uuid.UUID(TENANT) and {row["tenant_id"] for row in rows} == {uuid.UUID(TENANT)}
        assert await run_facts.record_run_facts("bad", [{"status": "completed"}]) == 0
        assert await run_facts.record_run_facts(TENANT, []) == 0

    def test_the_insert_is_idempotent_on_the_run_key_index(self):
        from sqlalchemy.dialects.postgresql import insert

        from core.models.insights import RunFact

        row = run_facts.normalise_fact({"tenant_id": TENANT, "run_key": "agents:corr-1", "status": "completed"})
        statement = (
            insert(RunFact)
            .values([row])
            .on_conflict_do_nothing(index_elements=[RunFact.tenant_id, RunFact.run_key, RunFact.started_at])
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (tenant_id, run_key, started_at) DO NOTHING" in sql
        index = next(i for i in RunFact.__table__.indexes if i.name == "ux_run_facts_tenant_run_key")
        assert index.unique and [c.name for c in index.columns] == ["tenant_id", "run_key", "started_at"]

    async def test_the_real_insert_writes_through_the_tenant_session_with_on_conflict(self, monkeypatch, insights_on):
        executed: list[Any] = []
        bound: list[Any] = []

        class _Session:
            async def execute(self, statement: Any) -> None:
                executed.append(statement)

        class _Context:
            def __init__(self, tenant_id: Any) -> None:
                bound.append(tenant_id)

            async def __aenter__(self) -> _Session:
                return _Session()

            async def __aexit__(self, *_args: Any) -> bool:
                return False

        monkeypatch.setattr("core.database.get_tenant_session", _Context)
        assert await run_facts.record_run_fact(tenant_id=TENANT, run_key="agents:x", status="completed")
        assert bound == [uuid.UUID(TENANT)] and len(executed) == 1
        sql = str(executed[0].compile(dialect=postgresql.dialect()))
        assert sql.startswith("INSERT INTO run_facts")
        assert "ON CONFLICT (tenant_id, run_key, started_at) DO NOTHING" in sql

    async def test_scheduled_writes_run_in_the_background(self, monkeypatch, recorded):
        monkeypatch.setattr(settings, "insights_enabled", True)
        run_facts.schedule_run_fact(tenant_id=TENANT, status="completed")
        await asyncio.gather(*list(run_facts._PENDING))
        assert recorded.calls == [{"tenant_id": TENANT, "status": "completed"}] and not run_facts._PENDING
        monkeypatch.setattr(run_facts, "MAX_PENDING_WRITES", 0)
        run_facts.schedule_run_fact(tenant_id=TENANT, status="completed")
        assert not run_facts._PENDING and len(recorded.calls) == 1

    def test_scheduling_outside_a_loop_is_a_no_op(self, monkeypatch, recorded):
        monkeypatch.setattr(settings, "insights_enabled", True)
        run_facts.schedule_run_fact(tenant_id=TENANT, status="completed")
        assert recorded.calls == []


class TestUsageSplit:
    def test_input_output_and_cached_tokens_come_from_usage_metadata(self):
        messages = [
            HumanMessage(content="hello"),
            AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "total_tokens": 130,
                    "input_token_details": {"cache_read": 60},
                },
            ),
            AIMessage(
                content="",
                response_metadata={"usage_metadata": {"prompt_token_count": 7, "candidates_token_count": 3}},
            ),
            AIMessage(content="no usage"),
        ]
        assert run_facts.usage_split(messages) == (107, 33, 60)
        assert run_facts.usage_split(None) == (0, 0, 0)


class TestContext:
    def test_the_entrypoint_is_bound_for_its_block_only(self):
        assert insights_context.get_entrypoint() == "agents"
        assert insights_context.get_entrypoint(default="hitl_resume") == "hitl_resume"
        with insights_context.entrypoint("chat"):
            assert insights_context.get_entrypoint() == "chat"
            with insights_context.entrypoint("sales"):
                assert insights_context.get_entrypoint(default="x") == "sales"
            assert insights_context.get_entrypoint() == "chat"
        assert insights_context.current_entrypoint.get() is None


# ---------------------------------------------------------------------------
# The runner hook
# ---------------------------------------------------------------------------


class _Graph:
    """A compiled graph that returns ``outcome`` (or raises it)."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    def compile(self, **_kwargs: Any) -> _Graph:
        return self

    async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return dict(self.outcome)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Any:
    from core.governance.model_gateway import RouteDecision
    from core.langgraph import runner
    from core.pii import pseudonymiser

    def no_database(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("no database in unit tests")

    decision = RouteDecision(provider="openai", model="gpt-4o", correlation_id="corr-run-1", reason="p", applied=True)
    monkeypatch.setattr("core.billing.metering.gate_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr("core.billing.metering.meter_agent_run", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "prefetch_llm_credential", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "generate_explanation", AsyncMock(return_value={}))
    monkeypatch.setattr(runner, "get_checkpointer", AsyncMock(return_value=None))
    monkeypatch.setattr(runner, "route_for_agent", AsyncMock(return_value=decision))
    monkeypatch.setattr(pseudonymiser, "pseudonymisation_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr("core.database.get_tenant_session", no_database)
    monkeypatch.setenv("AGENTICORG_PII_REDACTION_MODE", "before_llm")
    return runner


async def _run(runner: Any, monkeypatch: pytest.MonkeyPatch, outcome: Any, **overrides: Any) -> dict[str, Any]:
    monkeypatch.setattr(runner, "build_agent_graph", lambda **_kwargs: _Graph(outcome))
    arguments: dict[str, Any] = {
        "agent_id": AGENT,
        "agent_type": "analyst",
        "domain": "finance",
        "tenant_id": TENANT,
        "system_prompt": "You answer ledger questions.",
        "authorized_tools": [],
        "task_input": {"action": "summarise", "inputs": {}, "context": {}},
        "confidence_floor": 0.8,
        "run_grant": NO_RUN_GRANT_FOR_TESTS,
    }
    arguments.update(overrides)
    return await runner.run_agent(**arguments)


def _answer(**usage: Any) -> AIMessage:
    return AIMessage(content='{"answer": "ok"}', usage_metadata=usage or None)


class TestRunnerHook:
    async def test_a_completed_run_writes_one_fact_with_its_usage(self, offline, monkeypatch, recorded):
        usage = {
            "input_tokens": 400,
            "output_tokens": 100,
            "total_tokens": 500,
            "input_token_details": {"cache_read": 50},
        }
        result = await _run(
            offline,
            monkeypatch,
            {"messages": [_answer(**usage)], "status": "completed", "output": {"answer": "ok"}, "confidence": 0.93},
        )
        assert result["status"] == "completed"
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["tenant_id"] == TENANT and fact["agent_id"] == AGENT and fact["entrypoint"] == "agents"
        assert fact["run_key"] == "agents:corr-run-1" and fact["correlation_id"] == "corr-run-1"
        assert fact["status"] == "completed" and fact["confidence"] == 0.93 and fact["confidence_floor"] == 0.8
        assert fact["tokens_total"] == 500 and (fact["tokens_in"], fact["tokens_out"], fact["tokens_cached"]) == (
            400,
            100,
            50,
        )
        assert fact["cost_usd"] == result["performance"]["llm_cost_usd"] > 0
        assert fact["provider"] == "openai" and fact["model"] == "gpt-4o" and fact["error_class"] is None
        assert fact["task_type"] == "summarise" and fact["use_case"] == "agent_run" and fact["domain"] == "finance"
        assert fact["started_at"].tzinfo is UTC and fact["duration_ms"] >= 0
        # The writer accepts it as a valid row.
        assert run_facts.normalise_fact(fact)["status"] == "completed"

    async def test_a_failed_run_writes_one_failed_fact_with_the_exception_class(self, offline, monkeypatch, recorded):
        result = await _run(offline, monkeypatch, KeyError("missing"))
        assert result["status"] == "failed"
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["status"] == "failed" and fact["error_class"] == "KeyError"
        assert "missing" not in str(fact)

    async def test_a_paused_run_writes_one_hitl_fact(self, offline, monkeypatch, recorded):
        state = {
            "messages": [_answer(input_tokens=10, output_tokens=5, total_tokens=15)],
            "status": "running",
            "confidence": 0.4,
            "__interrupt__": [{"hitl_trigger": "confidence below floor"}],
        }
        result = await _run(offline, monkeypatch, state)
        assert result["status"] == "hitl_triggered"
        assert [fact["status"] for fact in recorded.calls] == ["hitl_triggered"]
        assert recorded.calls[0]["tokens_total"] == 15
        assert run_facts.normalise_fact(recorded.calls[0])["hitl_required"] is True

    async def test_a_graph_interrupt_writes_one_hitl_fact(self, offline, monkeypatch, recorded):
        from langgraph.errors import GraphInterrupt

        graph = _Graph(GraphInterrupt([{"hitl_trigger": "amount above limit"}]))
        graph.get_state = lambda _config: SimpleNamespace(values={"confidence": 0.5, "messages": []})  # type: ignore[attr-defined]
        monkeypatch.setattr(offline, "build_agent_graph", lambda **_kwargs: graph)
        result = await offline.run_agent(
            agent_id=AGENT,
            agent_type="analyst",
            domain="finance",
            tenant_id=TENANT,
            system_prompt="x",
            authorized_tools=[],
            task_input={"action": "pay"},
            run_grant=NO_RUN_GRANT_FOR_TESTS,
        )
        assert result["status"] == "hitl_triggered"
        assert [fact["status"] for fact in recorded.calls] == ["hitl_triggered"]

    async def test_a_timed_out_run_writes_one_failed_fact(self, offline, monkeypatch, recorded):
        result = await _run(offline, monkeypatch, TimeoutError())
        assert result["status"] == "failed" and result["error"].startswith("timeout")
        assert [(f["status"], f["error_class"]) for f in recorded.calls] == [("failed", "TimeoutError")]

    async def test_a_gateway_refusal_writes_one_refused_fact(self, offline, monkeypatch, recorded):
        from core.governance.model_gateway import ModelGatewayRefused

        monkeypatch.setattr(
            offline, "route_for_agent", AsyncMock(side_effect=ModelGatewayRefused("fenced", correlation_id="c-2"))
        )
        result = await _run(offline, monkeypatch, {})
        assert result["status"] == "model_gateway_refused"
        assert len(recorded.calls) == 1
        row = run_facts.normalise_fact(recorded.calls[0])
        assert row["status"] == "refused" and row["error_class"] == result["error_code"]

    async def test_a_billing_block_writes_one_limit_fact(self, offline, monkeypatch, recorded):
        from core.billing.metering import limit_exceeded_result

        monkeypatch.setattr(
            "core.billing.metering.gate_agent_run", AsyncMock(return_value=limit_exceeded_result(TENANT, 10, 10))
        )
        result = await _run(offline, monkeypatch, {})
        assert result["status"] == "limit_exceeded"
        assert [fact["status"] for fact in recorded.calls] == ["limit_exceeded"]

    async def test_the_bound_entrypoint_and_shadow_samples_are_recorded(self, offline, monkeypatch, recorded):
        with insights_context.entrypoint("chat"):
            await _run(offline, monkeypatch, {"messages": [], "status": "completed"})
        shadow = {"action": "shadow_sample"}
        await _run(offline, monkeypatch, {"messages": [], "status": "completed"}, task_input=shadow)
        assert [fact["entrypoint"] for fact in recorded.calls] == ["chat", "agents"]
        assert recorded.calls[0]["run_key"] == "chat:corr-run-1"
        assert run_facts.normalise_fact(recorded.calls[1])["environment"] == "shadow"

    async def test_a_cancelled_run_is_recorded_as_failed_and_still_cancels(self, offline, monkeypatch, recorded):
        with pytest.raises(asyncio.CancelledError):
            await _run(offline, monkeypatch, asyncio.CancelledError())
        assert [(f["status"], f["error_class"]) for f in recorded.calls] == [("failed", "CancelledError")]

    async def test_a_failing_writer_never_changes_the_result(self, offline, monkeypatch):
        expected = await _run(offline, monkeypatch, {"messages": [], "status": "completed", "confidence": 0.9})

        async def broken(**_fields: Any) -> bool:
            raise RuntimeError("writer bug")

        monkeypatch.setattr(run_facts, "record_run_fact", broken)
        result = await _run(offline, monkeypatch, {"messages": [], "status": "completed", "confidence": 0.9})
        assert result["status"] == expected["status"] == "completed"
        assert {k: v for k, v in result.items() if k != "performance"} == {
            k: v for k, v in expected.items() if k != "performance"
        }

    async def test_end_to_end_with_insights_on_the_row_reaches_the_insert(self, offline, monkeypatch, insights_on):
        insert = AsyncMock()
        monkeypatch.setattr(run_facts, "_insert", insert)
        await _run(offline, monkeypatch, {"messages": [], "status": "completed", "confidence": 0.9})
        insert.assert_awaited_once()
        (_tenant, rows), _ = insert.await_args
        assert rows[0]["status"] == "completed" and rows[0]["run_key"] == "agents:corr-run-1"
        assert rows[0]["agent_id"] == uuid.UUID(AGENT)

    @pytest.mark.parametrize("stage", ["prefetch_llm_credential", "build_agent_graph", "get_checkpointer"])
    async def test_a_setup_failure_records_one_failed_fact_and_raises_unchanged(
        self, offline, monkeypatch, recorded, stage
    ):
        boom = ConnectionError("credential store unreachable: secret-detail")
        arguments: dict[str, Any] = {
            "agent_id": AGENT,
            "agent_type": "analyst",
            "domain": "finance",
            "tenant_id": TENANT,
            "system_prompt": "x",
            "authorized_tools": [],
            "task_input": {"action": "summarise"},
            "run_grant": NO_RUN_GRANT_FOR_TESTS,
        }
        if stage == "build_agent_graph":
            monkeypatch.setattr(offline, "build_agent_graph", MagicMock(side_effect=boom))
        else:
            monkeypatch.setattr(offline, "build_agent_graph", lambda **_kwargs: _Graph({"status": "completed"}))
            monkeypatch.setattr(offline, stage, AsyncMock(side_effect=boom))
        with pytest.raises(ConnectionError) as raised:
            await offline.run_agent(**arguments)
        assert raised.value is boom  # the same exception, unchanged
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["status"] == "failed" and fact["error_class"] == "ConnectionError"
        assert fact["entrypoint"] == "agents" and "secret-detail" not in str(fact)

    async def test_a_resume_setup_failure_records_one_failed_fact_and_raises_unchanged(
        self, offline, monkeypatch, recorded
    ):
        boom = RuntimeError("no checkpoint store")
        monkeypatch.setattr(offline, "get_checkpointer", AsyncMock(side_effect=boom))
        with pytest.raises(RuntimeError) as raised:
            await offline.resume_agent(
                agent_id=AGENT,
                thread_id=offline._run_thread_id(TENANT, "t-1", AGENT),
                decision={"action": "approve"},
                system_prompt="x",
                authorized_tools=[],
                tenant_id=TENANT,
            )
        assert raised.value is boom
        assert [(f["entrypoint"], f["status"], f["error_class"]) for f in recorded.calls] == [
            ("hitl_resume", "failed", "RuntimeError")
        ]

    async def test_a_resume_records_hitl_resume_with_no_usage(self, offline, monkeypatch, recorded):
        from auth.grant_enforcement import EnforcementMode
        from auth.run_grants import RunGrant

        class _Compiled:
            async def aget_state(self, _config: Any) -> Any:
                return SimpleNamespace(values={"messages": []}, next=("hitl_gate",))

            async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
                spent = _answer(input_tokens=700, output_tokens=200, total_tokens=900)
                return {"status": "completed", "confidence": 0.9, "messages": [spent]}

        graph = MagicMock()
        graph.compile.return_value = _Compiled()
        monkeypatch.setattr(offline, "build_agent_graph", MagicMock(return_value=graph))
        monkeypatch.setattr(offline, "_checkpoint_pseudonym_state", AsyncMock(return_value=(None, [])))
        result = await offline.resume_agent(
            agent_id=AGENT,
            thread_id=offline._run_thread_id(TENANT, "t-1", AGENT),
            decision={"action": "approve"},
            system_prompt="x",
            authorized_tools=[],
            tenant_id=TENANT,
            run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
        )
        assert result["status"] == "completed" and result["performance"]["llm_tokens_used"] == 900
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["entrypoint"] == "hitl_resume" and fact["status"] == "completed"
        assert "tokens_total" not in fact and "cost_usd" not in fact
        row = run_facts.normalise_fact(fact)
        assert row["tokens_total"] == 0 and row["hitl_required"] is True

    async def test_a_refused_resume_records_its_reason(self, offline, monkeypatch, recorded):
        other = str(uuid.uuid4())
        with insights_context.entrypoint("debug"):
            result = await offline.resume_agent(
                agent_id=AGENT,
                thread_id=offline._run_thread_id(other, "t-1", AGENT),
                decision={},
                system_prompt="x",
                authorized_tools=[],
                tenant_id=TENANT,
            )
        assert result["reason"] == "checkpoint_thread_tenant_mismatch"
        assert [(f["entrypoint"], f["status"], f["error_class"]) for f in recorded.calls] == [
            ("debug", "failed", "checkpoint_thread_tenant_mismatch")
        ]


# ---------------------------------------------------------------------------
# The BaseAgent hook
# ---------------------------------------------------------------------------


def _assignment(workflow_run_id: str = "") -> Any:
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    return TaskAssignment(
        message_id="msg_1",
        correlation_id="corr-wf-1",
        workflow_run_id=workflow_run_id,
        workflow_definition_id="wf",
        step_id="step-1",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id=AGENT, agent_type="ap_processor", agent_token="runtime"),
        task=TaskInput(action="process_invoice", inputs={}, context={}),
    )


def _agent() -> Any:
    from core.agents.base import BaseAgent

    agent = BaseAgent(agent_id=AGENT, tenant_id=TENANT, domain="finance")
    agent.agent_type = "ap_processor"
    return agent


def _result(agent: Any, assignment: Any, status: str = "completed", **kwargs: Any) -> Any:
    return agent._make_result(assignment, "msg_9", status, {"a": 1}, 0.9, ["t"], [], **kwargs)


class TestBaseAgentHook:
    async def test_make_result_alone_writes_nothing(self, monkeypatch, recorded):
        monkeypatch.setattr(settings, "insights_enabled", True)
        result = _result(_agent(), _assignment(str(uuid.uuid4())))
        assert result.status == "completed" and recorded.calls == [] and not run_facts._PENDING

    async def test_execute_awaits_one_workflow_fact_and_returns_the_same_result(self, monkeypatch, recorded):
        import time

        monkeypatch.setattr(settings, "insights_enabled", True)
        run_id = str(uuid.uuid4())
        agent = _agent()
        assignment = _assignment(run_id)
        produced = _result(agent, assignment, start=time.monotonic() - 1.5)
        snapshot = produced.model_dump()
        monkeypatch.setattr(agent, "_execute", AsyncMock(return_value=produced))
        returned = await agent.execute(assignment)
        # The very object built by _make_result, unchanged; the fact is already written (awaited, not scheduled).
        assert returned is produced and returned.model_dump() == snapshot
        assert not run_facts._PENDING
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["entrypoint"] == "workflow" and fact["workflow_run_id"] == run_id
        assert fact["run_key"] == "workflow:msg_9" and fact["step_id"] == "step-1"
        assert fact["status"] == "completed" and fact["confidence"] == 0.9 and fact["task_type"] == "process_invoice"
        assert fact["agent_type"] == "ap_processor" and fact["domain"] == "finance"
        assert fact["duration_ms"] >= 1500 and "tokens_total" not in fact
        assert fact["started_at"] <= datetime.now(UTC) - timedelta(milliseconds=1500)

    async def test_sales_and_plain_runs_have_their_entrypoints(self, monkeypatch, recorded):
        monkeypatch.setattr(settings, "insights_enabled", True)
        agent = _agent()
        sales_task = _assignment("sales_pipeline_ab12cd34")
        plain_task = _assignment()
        sales_result = agent._make_result(sales_task, "msg_s", "hitl_triggered", {}, 0.5, [], [])
        plain_result = agent._make_result(
            plain_task, "msg_a", "failed", {}, 0.0, [], [], error={"code": "E5001", "message": "boom: secret"}
        )
        monkeypatch.setattr(agent, "_execute", AsyncMock(side_effect=[sales_result, plain_result]))
        with insights_context.entrypoint("sales"):
            await agent.execute(sales_task)
        await agent.execute(plain_task)
        sales, plain = recorded.calls
        assert sales["entrypoint"] == "sales" and sales["hitl_required"] is True
        assert run_facts.normalise_fact(sales)["workflow_run_id"] is None
        assert plain["entrypoint"] == "agents" and plain["error_class"] == "E5001"
        assert "secret" not in str(plain)

    @pytest.mark.parametrize("breakage", ["fields", "write", "insert"])
    async def test_a_failing_write_cannot_change_or_fail_the_result(self, monkeypatch, breakage):
        monkeypatch.setattr(settings, "insights_enabled", True)
        monkeypatch.setattr("core.feature_flags.is_enabled", AsyncMock(return_value=True))
        if breakage == "fields":
            monkeypatch.setattr(run_facts, "task_result_fields", MagicMock(side_effect=RuntimeError("bug")))
        elif breakage == "write":
            monkeypatch.setattr(run_facts, "record_run_fact", AsyncMock(side_effect=RuntimeError("bug")))
        else:
            monkeypatch.setattr(run_facts, "_insert", AsyncMock(side_effect=ConnectionError("db down")))
        agent = _agent()
        assignment = _assignment(str(uuid.uuid4()))
        produced = _result(agent, assignment)
        snapshot = produced.model_dump()
        monkeypatch.setattr(agent, "_execute", AsyncMock(return_value=produced))
        returned = await agent.execute(assignment)
        assert returned is produced and returned.model_dump() == snapshot

    async def test_a_subclass_execute_is_recorded_once(self, monkeypatch, recorded):
        from core.agents.base import BaseAgent

        monkeypatch.setattr(settings, "insights_enabled", True)

        class OwnPipeline(BaseAgent):
            agent_type = "own_pipeline"

            async def execute(self, task):
                return self._make_result(task, "msg_own", "completed", {}, 0.95, [], [])

        class Delegating(BaseAgent):
            agent_type = "delegating"

            async def execute(self, task):
                return await super().execute(task)

        own = OwnPipeline(agent_id=AGENT, tenant_id=TENANT, domain="finance")
        result = await own.execute(_assignment())
        assert result.message_id == "msg_own"
        delegating = Delegating(agent_id=AGENT, tenant_id=TENANT, domain="finance")
        produced = _result(delegating, _assignment())
        monkeypatch.setattr(delegating, "_execute", AsyncMock(return_value=produced))
        assert await delegating.execute(_assignment()) is produced
        assert [(f["agent_type"], f["run_key"]) for f in recorded.calls] == [
            ("own_pipeline", "agents:msg_own"),
            ("delegating", "agents:msg_9"),
        ]

    async def test_an_execute_that_raises_records_failed_and_raises_unchanged(self, monkeypatch, recorded):
        from core.agents.base import BaseAgent

        monkeypatch.setattr(settings, "insights_enabled", True)
        boom = LookupError("vendor missing")

        class Raising(BaseAgent):
            async def execute(self, task):
                raise boom

        with pytest.raises(LookupError) as raised:
            await Raising(agent_id=AGENT, tenant_id=TENANT, domain="finance").execute(_assignment())
        assert raised.value is boom
        assert [(f["status"], f["error_class"]) for f in recorded.calls] == [("failed", "LookupError")]

    async def test_while_off_execute_writes_nothing(self, monkeypatch, recorded):
        monkeypatch.setattr(settings, "insights_enabled", False)
        agent = _agent()
        produced = _result(agent, _assignment())
        monkeypatch.setattr(agent, "_execute", AsyncMock(return_value=produced))
        assert await agent.execute(_assignment()) is produced
        assert recorded.calls == []


# ---------------------------------------------------------------------------
# Customer turns, RPA and the seeder
# ---------------------------------------------------------------------------


class TestCustomerTurnHook:
    @staticmethod
    def _row() -> Any:
        return SimpleNamespace(tenant_id=uuid.UUID(TENANT), customer_ref="cust-1", id=uuid.uuid4())

    @pytest.mark.parametrize(
        ("turn_kwargs", "status", "code"),
        [
            ({"payload": {"confidence": 0.8, "outcome": {"kind": "answer"}}}, "completed", None),
            ({"payload": {"outcome": {"kind": "confirm"}}, "approval_required": True}, "paused", "approval_required"),
            ({"payload": {}, "refusal": "below level 2"}, "refused", "autonomy_refused"),
            ({"payload": {"outcome": {"kind": "handed_over"}}}, "hitl_triggered", None),
        ],
    )
    async def test_one_fact_per_turn_outcome(self, monkeypatch, recorded, turn_kwargs, status, code):
        from core.customer_agent import turns

        turn = turns.Turn(**turn_kwargs)
        monkeypatch.setattr(turns, "_advance_turn", AsyncMock(return_value=turn))
        assert await turns.run_customer_turn(self._row(), "hello") is turn
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["entrypoint"] == "customer_agent" and fact["status"] == status and fact["error_class"] == code
        assert "cust-1" not in str(fact) and "hello" not in str(fact)

    async def test_a_turn_that_raises_is_recorded_failed_and_still_raises(self, monkeypatch, recorded):
        from core.customer_agent import turns

        monkeypatch.setattr(turns, "_advance_turn", AsyncMock(side_effect=LookupError("no session")))
        with pytest.raises(LookupError):
            await turns.run_customer_turn(self._row(), "hello")
        assert [(f["status"], f["error_class"]) for f in recorded.calls] == [("failed", "LookupError")]


class TestRpaHooks:
    async def test_the_run_route_records_the_execution(self, monkeypatch, recorded):
        from api.v1 import rpa

        monkeypatch.setattr("rpa.scripts._registry.discover_scripts", lambda: {"web_scrape": {"name": "Web scrape"}})
        monkeypatch.setattr(
            "core.rpa.executor.execute_rpa_script",
            AsyncMock(
                return_value={"success": False, "elapsed_ms": 75, "error": "Page said: x", "error_class": "timeout"}
            ),
        )
        monkeypatch.setattr("core.rpa.history_store.append", AsyncMock(return_value=True))
        request = SimpleNamespace(state=SimpleNamespace(scopes=[], claims={}))
        out = await rpa.run_script("web_scrape", rpa.RPARunRequest(params={}), request, tenant_id=TENANT)
        assert out.status == "failed"
        assert len(recorded.calls) == 1
        fact = recorded.calls[0]
        assert fact["entrypoint"] == "rpa" and fact["agent_ref"] == "web_scrape" and "agent_id" not in fact
        assert fact["run_key"] == f"rpa:{out.id}" and fact["status"] == "failed" and fact["error_class"] == "timeout"
        assert fact["duration_ms"] == 75 and "Page said" not in str(fact)

    async def test_the_run_route_records_a_raised_failure_by_class(self, monkeypatch, recorded):
        from api.v1 import rpa

        monkeypatch.setattr("rpa.scripts._registry.discover_scripts", lambda: {"web_scrape": {"name": "Web scrape"}})
        monkeypatch.setattr("core.rpa.executor.execute_rpa_script", AsyncMock(side_effect=OSError("browser gone")))
        monkeypatch.setattr("core.rpa.history_store.append", AsyncMock(return_value=True))
        request = SimpleNamespace(state=SimpleNamespace(scopes=[], claims={}))
        await rpa.run_script("web_scrape", rpa.RPARunRequest(params={}), request, tenant_id=TENANT)
        assert recorded.calls[0]["error_class"] == "OSError"

    async def test_scheduled_runs_record_success_and_failure(self, monkeypatch, recorded):
        from core.tasks import rpa_tasks

        sid = uuid.uuid4()
        row = SimpleNamespace(
            id=sid,
            enabled=False,
            script_key="gst_rates",
            last_run_at=None,
            last_run_status=None,
            last_run_chunks_published=0,
            last_run_chunks_rejected=0,
            last_quality_avg=None,
        )

        class _Session:
            async def execute(self, *_args: Any) -> Any:
                return SimpleNamespace(scalar_one_or_none=lambda: row)

        class _Context:
            def __init__(self, *_args: Any) -> None:
                pass

            async def __aenter__(self) -> _Session:
                return _Session()

            async def __aexit__(self, *_args: Any) -> bool:
                return False

        monkeypatch.setattr("core.database.get_tenant_session", _Context)
        await rpa_tasks._record_success(uuid.UUID(TENANT), sid, 3, 1, 4.9)
        await rpa_tasks._record_failure(uuid.UUID(TENANT), sid, "script_error: ValueError", 0, 0, None)
        await rpa_tasks._record_failure(uuid.UUID(TENANT), sid, "Portal said: account 12345 locked", 0, 0, None)
        statuses = [(f["status"], f["error_class"], f["agent_ref"], f["entrypoint"]) for f in recorded.calls]
        assert statuses == [
            ("completed", None, "gst_rates", "rpa"),
            ("failed", "ValueError", "gst_rates", "rpa"),
            ("failed", "script_failed", "gst_rates", "rpa"),
        ]
        assert all(f["run_key"] == f"rpa:schedule:{sid}" for f in recorded.calls)
        assert rpa_tasks._failure_code("script_not_registered") == "script_not_registered"


class TestSeeder:
    async def test_seeded_task_results_get_matching_seeded_facts(self, monkeypatch):
        from core import seed_demo_data

        inserted: list[dict[str, Any]] = []

        class _Session:
            async def execute(self, _statement: Any, params: dict[str, Any]) -> None:
                inserted.append(params)

        class _Context:
            def __init__(self, *_args: Any) -> None:
                pass

            async def __aenter__(self) -> _Session:
                return _Session()

            async def __aexit__(self, *_args: Any) -> bool:
                return False

        written: list[tuple[Any, list[dict[str, Any]]]] = []

        async def capture(tenant_id: Any, facts: list[dict[str, Any]]) -> int:
            written.append((tenant_id, list(facts)))
            return len(facts)

        monkeypatch.setattr("core.database.get_tenant_session", _Context)
        monkeypatch.setattr(run_facts, "record_run_facts", capture)
        tid = uuid.UUID(TENANT)
        assert await seed_demo_data.seed_task_results(tid, count=7) == 7
        (tenant, facts), = written
        assert tenant == tid and len(facts) == 7 == len(inserted)
        for row, fact in zip(inserted, facts, strict=True):
            assert fact["run_key"] == f"seed:{row['id']}" and fact["agent_id"] == row["agent_id"]
            assert fact["data_quality"] == "seeded" and fact["status"] == row["status"]
            assert fact["tokens_total"] == row["tokens_used"] and fact["model"] == row["llm_model"]
            assert fact["started_at"] == row["created_at"]

    async def test_a_failed_fact_write_never_fails_the_seeder(self, monkeypatch, insights_on):
        from core import seed_demo_data

        class _Session:
            async def execute(self, *_args: Any) -> None:
                return None

        class _Context:
            def __init__(self, *_args: Any) -> None:
                pass

            async def __aenter__(self) -> _Session:
                return _Session()

            async def __aexit__(self, *_args: Any) -> bool:
                return False

        monkeypatch.setattr("core.database.get_tenant_session", _Context)
        monkeypatch.setattr(run_facts, "_insert", AsyncMock(side_effect=ConnectionError("down")))
        assert await seed_demo_data.seed_task_results(uuid.UUID(TENANT), count=3) == 3


class TestDsarNote:
    def test_the_dsar_module_says_insights_holds_no_subject_identifiers(self):
        from audit import dsar
        from core.models.insights import RunFact

        assert "run_facts" in (dsar.__doc__ or "") and "no subject identifiers" in (dsar.__doc__ or "")
        columns = {column.name for column in RunFact.__table__.columns}
        assert not columns & {"user_id", "email", "customer_ref", "actor_id", "message", "text"}
