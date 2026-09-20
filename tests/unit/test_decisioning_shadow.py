from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.config import settings
from core.decisioning import shadow as shadow_module
from core.decisioning.contracts import DecisionAnswer, DecisionResponse
from core.decisioning.shadow import ShadowObservation, observe_tool_routing


class FakeDecisionProvider:
    def __init__(self, response: DecisionResponse) -> None:
        self.response = response
        self.request = None

    async def decide(self, request):
        self.request = request
        return self.response


def _response(value: str = "tool_call") -> DecisionResponse:
    return DecisionResponse(
        provider="jev",
        model="jev-latest",
        answers={
            "route": DecisionAnswer(
                type="choice",
                value=value,
                confidence=0.93,
            )
        },
    )


@pytest.mark.asyncio
async def test_shadow_observes_route_without_sending_task_inputs() -> None:
    provider = FakeDecisionProvider(_response())
    with patch.object(settings, "jev_mode", "shadow"):
        result = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[{"connector": "shopify", "tool": "search", "description": "private"}],
            requested_tools=[
                {"connector": "shopify", "tool": "search", "params": {"email": "private@example.test"}}
            ],
            provider=provider,
        )

    assert result.outcome == "observed"
    assert result.agreement is True
    assert provider.request is not None
    assert provider.request.state["proposed_tool_names"] == ["shopify.search"]
    assert "private@example.test" not in str(provider.request.state)
    assert provider.request.tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_shadow_never_runs_when_off() -> None:
    provider = AsyncMock()
    with patch.object(settings, "jev_mode", "off"):
        result = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )

    assert result.outcome == "disabled"
    provider.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_provider_failure_is_non_fatal() -> None:
    provider = AsyncMock()
    provider.decide.side_effect = RuntimeError("upstream details")
    with patch.object(settings, "jev_mode", "shadow"):
        result = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )

    assert result.outcome == "invalid"
    assert result.agreement is None


@pytest.mark.asyncio
async def test_shadow_sampling_and_call_budget_prevent_provider_calls() -> None:
    provider = AsyncMock()
    with (
        patch.object(settings, "jev_mode", "shadow"),
        patch.object(settings, "jev_shadow_sample_rate", 0.0),
        patch.object(settings, "jev_shadow_max_calls_per_process", 100),
        patch.object(shadow_module, "_shadow_calls", 0),
    ):
        sampled_out = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )
    assert sampled_out.outcome == "sampled_out"
    provider.decide.assert_not_awaited()

    with (
        patch.object(settings, "jev_mode", "shadow"),
        patch.object(settings, "jev_shadow_sample_rate", 1.0),
        patch.object(settings, "jev_shadow_max_calls_per_process", 0),
        patch.object(shadow_module, "_shadow_calls", 0),
    ):
        budget_exhausted = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )
    assert budget_exhausted.outcome == "budget_exhausted"


@pytest.mark.asyncio
async def test_shadow_failure_opens_circuit() -> None:
    provider = AsyncMock()
    provider.decide.side_effect = RuntimeError("upstream")
    with (
        patch.object(settings, "jev_mode", "shadow"),
        patch.object(settings, "jev_shadow_sample_rate", 1.0),
        patch.object(settings, "jev_shadow_max_calls_per_process", 100),
        patch.object(settings, "jev_shadow_failure_threshold", 1),
        patch.object(settings, "jev_shadow_cooldown_seconds", 60.0),
        patch.object(shadow_module, "_shadow_calls", 0),
        patch.object(shadow_module, "_shadow_consecutive_failures", 0),
        patch.object(shadow_module, "_shadow_circuit_open_until", 0.0),
    ):
        first = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )
        second = await observe_tool_routing(
            tenant_id="tenant-1",
            agent_type="sales",
            domain="commerce",
            action="find_product",
            available_tools=[],
            requested_tools=[],
            provider=provider,
        )
    assert first.outcome == "invalid"
    assert second.outcome == "circuit_open"
    assert provider.decide.await_count == 1


@pytest.mark.asyncio
async def test_base_agent_records_shadow_observation_without_changing_execution() -> None:
    from core.agents.base import BaseAgent
    from core.schemas.messages import TargetAgent, TaskAssignment, TaskInput

    agent = BaseAgent(agent_id="agent-1", tenant_id="tenant-1")
    task = TaskAssignment(
        message_id="message-1",
        correlation_id="correlation-1",
        workflow_run_id="run-1",
        workflow_definition_id="definition-1",
        step_id="step-1",
        step_index=0,
        total_steps=1,
        target_agent=TargetAgent(agent_id="agent-1", agent_type="custom", agent_token="token"),
        task=TaskInput(action="summarize", inputs={"secret": "must-not-leave-process"}),
    )

    with (
        patch.object(agent, "_reason", new=AsyncMock(return_value={"status": "completed", "confidence": 0.99})),
        patch(
            "core.decisioning.shadow.observe_tool_routing",
            new=AsyncMock(return_value=ShadowObservation(outcome="observed", agreement=True)),
        ) as observer,
    ):
        result = await agent.execute(task)

    assert result.status == "completed"
    assert any("Jev shadow routing: observed" in item for item in result.reasoning_trace)
    observer.assert_awaited_once()
    assert "must-not-leave-process" not in str(observer.await_args.kwargs)
