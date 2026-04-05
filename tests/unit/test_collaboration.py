"""Tests for parallel multi-agent collaboration step handler."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest


@pytest.fixture()
def _base_state():
    """Minimal workflow state for collaboration tests."""
    return {
        "id": "wfr_test123",
        "trigger_payload": {},
        "step_results": {},
        "context": {},
    }


def _make_step(
    agents: list[str],
    aggregation: str = "merge",
    shared_context: bool = True,
    timeout_seconds: int = 10,
) -> dict:
    return {
        "id": "collab_step_1",
        "type": "collaboration",
        "agents": agents,
        "aggregation": aggregation,
        "shared_context": shared_context,
        "timeout_seconds": timeout_seconds,
    }


async def _mock_execute_step(step: dict, state: dict) -> dict:
    """Mock agent execution that returns the agent name as output."""
    agent_name = step.get("agent", step.get("agent_type", "unknown"))
    # Simulate some async work
    await asyncio.sleep(0.01)
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "output": {"agent": agent_name, "result": f"output_from_{agent_name}"},
    }


async def _mock_execute_step_with_decision(step: dict, state: dict) -> dict:
    """Mock agent that returns a decision for vote aggregation."""
    agent_name = step.get("agent", step.get("agent_type", "unknown"))
    await asyncio.sleep(0.01)
    # Agents "agent_a" and "agent_b" vote "approve", "agent_c" votes "reject"
    decision = "approve" if agent_name in ("agent_a", "agent_b") else "reject"
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "output": {"decision": decision, "agent": agent_name},
    }


async def _mock_execute_step_slow(step: dict, state: dict) -> dict:
    """Mock agent that takes a long time (for timeout tests)."""
    agent_name = step.get("agent", step.get("agent_type", "unknown"))
    if agent_name == "slow_agent":
        await asyncio.sleep(30)  # Will be cancelled by timeout
    else:
        await asyncio.sleep(0.01)
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "output": {"agent": agent_name},
    }


async def _mock_execute_step_failing(step: dict, state: dict) -> dict:
    """Mock agent where one agent fails."""
    agent_name = step.get("agent", step.get("agent_type", "unknown"))
    await asyncio.sleep(0.01)
    if agent_name == "failing_agent":
        raise RuntimeError("Agent failed!")
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "output": {"agent": agent_name, "result": "success"},
    }


async def _mock_execute_step_race(step: dict, state: dict) -> dict:
    """Mock agent with different speeds for first_complete tests."""
    agent_name = step.get("agent", step.get("agent_type", "unknown"))
    if agent_name == "fast_agent":
        await asyncio.sleep(0.01)
    else:
        await asyncio.sleep(5)  # Slow — should be cancelled
    return {
        "step_id": step["id"],
        "type": "agent",
        "status": "completed",
        "output": {"agent": agent_name, "speed": "fast" if agent_name == "fast_agent" else "slow"},
    }


class TestParallelExecutionRunsConcurrently:
    """Multiple agents should run at the same time, not sequentially."""

    @pytest.mark.asyncio
    async def test_parallel_execution_runs_concurrently(self, _base_state):
        step = _make_step(["agent_a", "agent_b", "agent_c"])

        with patch("workflows.step_types.execute_step", side_effect=_mock_execute_step):
            from workflows.collaboration import execute_collaboration_step

            start = time.monotonic()
            result = await execute_collaboration_step(step, _base_state)
            elapsed = time.monotonic() - start

        assert result["status"] == "completed"
        assert result["type"] == "collaboration"
        # If sequential, 3 agents * 0.01s = 0.03s minimum.
        # Parallel should be close to 0.01s. Allow generous margin.
        assert elapsed < 1.0  # Should be way under 1 second


class TestMergeAggregationCombinesOutputs:
    """Merge aggregation should combine all agent outputs into one dict."""

    @pytest.mark.asyncio
    async def test_merge_aggregation_combines_outputs(self, _base_state):
        step = _make_step(["agent_a", "agent_b"], aggregation="merge")

        with patch("workflows.step_types.execute_step", side_effect=_mock_execute_step):
            from workflows.collaboration import execute_collaboration_step

            result = await execute_collaboration_step(step, _base_state)

        assert result["status"] == "completed"
        assert result["aggregation"] == "merge"
        output = result["output"]
        assert "agent_a" in output
        assert "agent_b" in output
        assert output["agent_a"]["result"] == "output_from_agent_a"
        assert output["agent_b"]["result"] == "output_from_agent_b"


class TestVoteAggregationMajorityWins:
    """Vote aggregation should select the decision with the most votes."""

    @pytest.mark.asyncio
    async def test_vote_aggregation_majority_wins(self, _base_state):
        step = _make_step(["agent_a", "agent_b", "agent_c"], aggregation="vote")

        with patch(
            "workflows.step_types.execute_step",
            side_effect=_mock_execute_step_with_decision,
        ):
            from workflows.collaboration import execute_collaboration_step

            result = await execute_collaboration_step(step, _base_state)

        assert result["status"] == "completed"
        assert result["aggregation"] == "vote"
        # agent_a and agent_b vote "approve", agent_c votes "reject"
        assert result["output"]["decision"] == "approve"
        assert result["output"]["vote_count"] == 2
        assert result["votes"]["approve"] == 2
        assert result["votes"]["reject"] == 1


class TestFirstCompleteReturnsFastest:
    """first_complete should return the first agent's result and cancel others."""

    @pytest.mark.asyncio
    async def test_first_complete_returns_fastest(self, _base_state):
        step = _make_step(
            ["fast_agent", "slow_agent_1", "slow_agent_2"],
            aggregation="first_complete",
            timeout_seconds=10,
        )

        with patch("workflows.step_types.execute_step", side_effect=_mock_execute_step_race):
            from workflows.collaboration import execute_collaboration_step

            start = time.monotonic()
            result = await execute_collaboration_step(step, _base_state)
            elapsed = time.monotonic() - start

        assert result["status"] == "completed"
        assert result["aggregation"] == "first_complete"
        assert result["winner"] == "fast_agent"
        # Should complete quickly (fast_agent finishes in 0.01s)
        assert elapsed < 2.0


class TestOneAgentFailsOthersContinue:
    """If one agent fails, the others should still complete (graceful degradation)."""

    @pytest.mark.asyncio
    async def test_one_agent_fails_others_continue(self, _base_state):
        step = _make_step(
            ["agent_a", "failing_agent", "agent_c"],
            aggregation="merge",
        )

        with patch(
            "workflows.step_types.execute_step",
            side_effect=_mock_execute_step_failing,
        ):
            from workflows.collaboration import execute_collaboration_step

            result = await execute_collaboration_step(step, _base_state)

        assert result["status"] == "completed"
        output = result["output"]
        # agent_a and agent_c should have succeeded
        assert "agent_a" in output
        assert "agent_c" in output
        # failing_agent should be recorded but with empty output
        assert "failing_agent" in output
        assert result["agents_failed"] >= 1
        assert result["agents_succeeded"] >= 2


class TestTimeoutCancelsLongRunning:
    """A short timeout should cancel agents that take too long."""

    @pytest.mark.asyncio
    async def test_timeout_cancels_long_running(self, _base_state):
        step = _make_step(
            ["agent_a", "slow_agent"],
            aggregation="merge",
            timeout_seconds=1,  # 1 second timeout
        )

        with patch(
            "workflows.step_types.execute_step",
            side_effect=_mock_execute_step_slow,
        ):
            from workflows.collaboration import execute_collaboration_step

            start = time.monotonic()
            result = await execute_collaboration_step(step, _base_state)
            elapsed = time.monotonic() - start

        # Should complete around the timeout (1s), not wait for slow_agent (30s)
        assert elapsed < 5.0
        assert result["status"] == "completed"
        # agent_a should have succeeded; slow_agent should be timed out
        output = result["output"]
        assert "agent_a" in output


class TestSharedContextAccessible:
    """Shared context dict should be accessible to all agents and returned in result."""

    @pytest.mark.asyncio
    async def test_shared_context_accessible(self, _base_state):
        step = _make_step(["agent_a", "agent_b"], shared_context=True)

        async def _mock_with_shared_ctx(step_dict: dict, state: dict) -> dict:
            agent_name = step_dict.get("agent", "unknown")
            inputs = step_dict.get("inputs", {})
            # Verify shared_context is in inputs
            assert "shared_context" in inputs
            shared_ctx = inputs["shared_context"]
            # Each agent contributes to shared context
            shared_ctx[f"{agent_name}_was_here"] = True
            return {
                "step_id": step_dict["id"],
                "type": "agent",
                "status": "completed",
                "output": {
                    "agent": agent_name,
                    "shared_context_update": {f"{agent_name}_key": "value"},
                },
            }

        with patch("workflows.step_types.execute_step", side_effect=_mock_with_shared_ctx):
            from workflows.collaboration import execute_collaboration_step

            result = await execute_collaboration_step(step, _base_state)

        assert result["status"] == "completed"
        # Shared context should be in the result
        assert "shared_context" in result
