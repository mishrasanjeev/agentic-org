"""Regression tests for workflow no-false-success step execution."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest


def _strict_workflow_runtime(monkeypatch):
    from workflows import step_types

    monkeypatch.setattr(step_types.settings, "env", "production")
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    monkeypatch.delenv("AGENTICORG_WORKFLOW_ALLOW_STUB_STEPS", raising=False)
    monkeypatch.setattr(step_types.external_keys, "google_gemini_api_key", "")
    monkeypatch.setattr(step_types.external_keys, "anthropic_api_key", "")
    monkeypatch.setattr(step_types.external_keys, "openai_api_key", "")


def _relaxed_stub_runtime(monkeypatch):
    from workflows import step_types

    monkeypatch.setattr(step_types.settings, "env", "test")
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_LLM", raising=False)
    monkeypatch.setenv("AGENTICORG_WORKFLOW_ALLOW_STUB_STEPS", "1")
    monkeypatch.setattr(step_types.external_keys, "google_gemini_api_key", "")
    monkeypatch.setattr(step_types.external_keys, "anthropic_api_key", "")
    monkeypatch.setattr(step_types.external_keys, "openai_api_key", "")


@pytest.mark.asyncio
async def test_no_false_success_missing_agent_config_fails_in_strict_mode(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step({"id": "agent_step", "type": "agent"}, {})

    assert result["status"] == "failed"
    assert result["error"]["code"] == "missing_agent_config"


@pytest.mark.asyncio
async def test_no_false_success_missing_llm_provider_config_fails_in_strict_mode(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step(
        {"id": "agent_step", "type": "agent", "agent": "ap_processor"},
        {},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "missing_llm_provider_config"


@pytest.mark.asyncio
async def test_no_false_success_agent_execution_exception_fails_in_strict_mode(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from core.agents.registry import AgentRegistry
    from workflows import step_types

    monkeypatch.setattr(step_types.external_keys, "google_gemini_api_key", "test-key")

    class BrokenAgent:
        async def execute(self, task):
            raise RuntimeError("agent exploded")

    monkeypatch.setattr(
        AgentRegistry,
        "create_from_config",
        staticmethod(lambda config: BrokenAgent()),
    )

    result = await step_types.execute_step(
        {"id": "agent_step", "type": "agent", "agent": "ap_processor"},
        {},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "agent_execution_failed"
    assert result["error"]["details"]["exception_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_no_false_success_relaxed_stub_is_marked_stubbed_not_completed(monkeypatch):
    _relaxed_stub_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step(
        {"id": "agent_step", "type": "agent", "agent": "ap_processor"},
        {},
    )

    assert result["status"] == "stubbed"
    assert result["stubbed"] is True
    assert result["status"] != "completed"
    assert result["code"] == "missing_llm_provider_config"


@pytest.mark.asyncio
async def test_no_false_success_notify_without_side_effect_fails_in_strict_mode(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step(
        {"id": "notify_step", "type": "notify", "connector": "slack"},
        {},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "notify_side_effect_not_configured"


@pytest.mark.asyncio
async def test_no_false_success_transform_without_config_fails_in_strict_mode(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step({"id": "transform_step", "type": "transform"}, {})

    assert result["status"] == "failed"
    assert result["error"]["code"] == "unsupported_transform_configuration"


@pytest.mark.asyncio
async def test_no_false_success_parallel_all_fails_when_any_child_fails(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step(
        {
            "id": "parallel_step",
            "type": "parallel",
            "wait_for": "all",
            "steps": [
                {"id": "ok", "type": "transform", "operation": "identity"},
                {"id": "bad", "type": "transform"},
            ],
        },
        {"context": {"value": 1}},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "parallel_child_failed"
    assert result["output"]["failed_children"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_no_false_success_parallel_any_fails_when_only_child_fails(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.step_types import execute_step

    result = await execute_step(
        {
            "id": "parallel_step",
            "type": "parallel",
            "wait_for": "any",
            "steps": [{"id": "bad", "type": "transform"}],
        },
        {},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "parallel_child_failed"


@pytest.mark.asyncio
async def test_no_false_success_parallel_exceptions_are_normalized():
    from workflows.parallel_executor import execute_parallel

    async def raises():
        raise ValueError("boom")

    results = await execute_parallel([raises], wait_for="all")

    assert results[0]["status"] == "failed"
    assert results[0]["error"]["code"] == "parallel_child_exception"
    assert not isinstance(results[0], BaseException)


@pytest.mark.asyncio
async def test_no_false_success_failed_step_fails_workflow_in_engine(monkeypatch):
    _strict_workflow_runtime(monkeypatch)
    from workflows.engine import WorkflowEngine
    from workflows.state_store import WorkflowStateStore

    state = {
        "id": "wfr_no_false_success",
        "status": "running",
        "definition": {
            "name": "no_false_success",
            "steps": [{"id": "notify_step", "type": "notify", "connector": "slack"}],
        },
        "trigger_payload": {},
        "steps_total": 1,
        "steps_completed": 0,
        "step_results": {},
        "started_at": datetime.now(UTC).isoformat(),
    }
    store = AsyncMock(spec=WorkflowStateStore)
    store.load = AsyncMock(return_value=state)
    store.save = AsyncMock()

    result = await WorkflowEngine(state_store=store).execute("wfr_no_false_success")

    assert result["status"] == "failed"
    assert result["step_results"]["notify_step"]["status"] == "failed"
    assert (
        result["step_results"]["notify_step"]["error"]["code"]
        == "notify_side_effect_not_configured"
    )
