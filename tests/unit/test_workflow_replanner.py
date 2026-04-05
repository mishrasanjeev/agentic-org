"""Tests for dynamic workflow re-planning (Section 9)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflows.replanner import (
    MAX_REPLAN_ATTEMPTS,
    ReplanError,
    build_replan_event,
    replan_workflow,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DEFINITION = {
    "name": "Invoice Processing",
    "replan_on_failure": True,
    "steps": [
        {"id": "s1", "type": "agent", "agent_type": "ap_processor", "action": "extract"},
        {"id": "s2", "type": "agent", "agent_type": "payment_gateway", "action": "pay"},
        {"id": "s3", "type": "notify", "connector": "slack"},
    ],
}

COMPLETED_STEPS = [
    {"id": "s1", "status": "completed", "output": {"invoice_id": "INV-001", "amount": 50000}},
]

FAILED_STEP = {
    "id": "s2",
    "error": "PineLabs gateway timeout",
    "type": "agent",
    "agent_type": "payment_gateway",
    "action": "pay",
}

REMAINING_STEPS = [
    {"id": "s3", "type": "notify", "connector": "slack"},
]

VALID_LLM_RESPONSE = json.dumps([
    {"id": "s2_alt", "type": "agent", "agent_type": "neft_processor", "action": "pay_neft"},
    {"id": "s3", "type": "notify", "connector": "slack"},
])


# ---------------------------------------------------------------------------
# test_replan_generates_valid_steps
# ---------------------------------------------------------------------------

class TestReplanGeneratesValidSteps:
    @pytest.mark.asyncio
    async def test_replan_generates_valid_steps(self):
        """LLM returns valid replacement steps that pass validation."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = VALID_LLM_RESPONSE

            result = await replan_workflow(
                original_definition=SAMPLE_DEFINITION,
                completed_steps=COMPLETED_STEPS,
                failed_step=FAILED_STEP,
                remaining_steps=REMAINING_STEPS,
            )

            assert isinstance(result, list)
            assert len(result) == 2
            assert result[0]["id"] == "s2_alt"
            assert result[0]["type"] == "agent"
            assert result[1]["id"] == "s3"
            mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# test_replan_validates_step_types
# ---------------------------------------------------------------------------

class TestReplanValidatesStepTypes:
    @pytest.mark.asyncio
    async def test_invalid_step_type_raises_error(self):
        """Steps with invalid types are rejected."""
        invalid_response = json.dumps([
            {"id": "s_bad", "type": "teleport"},  # not a valid step type
        ])
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = invalid_response

            with pytest.raises(ReplanError, match="Invalid step type"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_valid_step_types_accepted(self):
        """All standard step types are accepted."""
        all_types_response = json.dumps([
            {"id": "a1", "type": "agent"},
            {"id": "a2", "type": "condition", "condition": "true"},
            {"id": "a3", "type": "notify", "connector": "email"},
        ])
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = all_types_response

            result = await replan_workflow(
                original_definition=SAMPLE_DEFINITION,
                completed_steps=COMPLETED_STEPS,
                failed_step=FAILED_STEP,
                remaining_steps=REMAINING_STEPS,
            )
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_missing_id_raises_error(self):
        """Steps without an 'id' are rejected."""
        no_id_response = json.dumps([
            {"type": "agent", "agent_type": "test"},
        ])
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = no_id_response

            with pytest.raises(ReplanError, match="must have an 'id'"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_duplicate_ids_rejected(self):
        """Duplicate step IDs are rejected."""
        dup_response = json.dumps([
            {"id": "dup", "type": "agent"},
            {"id": "dup", "type": "notify"},
        ])
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = dup_response

            with pytest.raises(ReplanError, match="Duplicate replanned step id"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_conflict_with_completed_step_ids(self):
        """Replanned step IDs must not conflict with already-completed step IDs."""
        conflict_response = json.dumps([
            {"id": "s1", "type": "agent"},  # s1 already completed
        ])
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = conflict_response

            with pytest.raises(ReplanError, match="conflicts with an already-completed step"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )


# ---------------------------------------------------------------------------
# test_max_3_replanning_attempts_enforced
# ---------------------------------------------------------------------------

class TestMaxReplanAttemptsEnforced:
    def test_max_constant_is_3(self):
        """MAX_REPLAN_ATTEMPTS is set to 3."""
        assert MAX_REPLAN_ATTEMPTS == 3

    @pytest.mark.asyncio
    async def test_engine_blocks_after_max_replans(self):
        """After 3 replans, the engine does not attempt a 4th."""
        from workflows.engine import WorkflowEngine
        from workflows.state_store import WorkflowStateStore

        # Build a mock state store
        store = MagicMock(spec=WorkflowStateStore)

        # State where replan_count is already at MAX
        state = {
            "id": "wfr_test123",
            "definition": {
                "replan_on_failure": True,
                "steps": [
                    {"id": "s1", "type": "agent"},
                    {"id": "s2", "type": "agent"},
                ],
            },
            "status": "running",
            "trigger_payload": {},
            "steps_total": 2,
            "steps_completed": 1,
            "step_results": {"s1": {"output": {}, "status": "completed", "confidence": None}},
            "started_at": "2026-04-04T00:00:00+00:00",
            "replan_count": 3,  # Already at max
            "replan_history": [{}, {}, {}],
        }

        store.load = AsyncMock(return_value=state)
        store.save = AsyncMock()

        engine = WorkflowEngine(state_store=store)

        # Mock execute_step to always raise
        with patch("workflows.engine.execute_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("step failed")

            result = await engine.execute("wfr_test123")

            # Should fail normally without replanning
            assert result["status"] == "failed"
            # replan_count should still be 3, not 4
            assert state["replan_count"] == 3


# ---------------------------------------------------------------------------
# test_replan_disabled_falls_back_to_normal_failure
# ---------------------------------------------------------------------------

class TestReplanDisabledFallback:
    @pytest.mark.asyncio
    async def test_replan_disabled_fails_normally(self):
        """When replan_on_failure is false, step failure leads to workflow failure."""
        from workflows.engine import WorkflowEngine
        from workflows.state_store import WorkflowStateStore

        store = MagicMock(spec=WorkflowStateStore)

        state = {
            "id": "wfr_no_replan",
            "definition": {
                "replan_on_failure": False,
                "steps": [
                    {"id": "s1", "type": "agent"},
                ],
            },
            "status": "running",
            "trigger_payload": {},
            "steps_total": 1,
            "steps_completed": 0,
            "step_results": {},
            "started_at": "2026-04-04T00:00:00+00:00",
            "replan_count": 0,
            "replan_history": [],
        }

        store.load = AsyncMock(return_value=state)
        store.save = AsyncMock()

        engine = WorkflowEngine(state_store=store)

        with patch("workflows.engine.execute_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("payment failed")

            result = await engine.execute("wfr_no_replan")

            assert result["status"] == "failed"
            assert "payment failed" in result["step_results"]["s1"]["error"]
            assert state["replan_count"] == 0

    @pytest.mark.asyncio
    async def test_replan_not_in_definition_fails_normally(self):
        """When replan_on_failure is absent from definition, step failure is normal."""
        from workflows.engine import WorkflowEngine
        from workflows.state_store import WorkflowStateStore

        store = MagicMock(spec=WorkflowStateStore)

        state = {
            "id": "wfr_missing",
            "definition": {
                "steps": [{"id": "s1", "type": "agent"}],
                # No replan_on_failure key at all
            },
            "status": "running",
            "trigger_payload": {},
            "steps_total": 1,
            "steps_completed": 0,
            "step_results": {},
            "started_at": "2026-04-04T00:00:00+00:00",
            "replan_count": 0,
            "replan_history": [],
        }

        store.load = AsyncMock(return_value=state)
        store.save = AsyncMock()

        engine = WorkflowEngine(state_store=store)

        with patch("workflows.engine.execute_step", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("crash")

            result = await engine.execute("wfr_missing")
            assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# test_replan_context_includes_previous_outputs
# ---------------------------------------------------------------------------

class TestReplanContextIncludesPreviousOutputs:
    @pytest.mark.asyncio
    async def test_completed_outputs_passed_to_replanner(self):
        """The replanner receives completed step outputs as context."""
        captured_args: dict = {}

        async def mock_replan(
            original_definition, completed_steps, failed_step, remaining_steps
        ):
            captured_args["completed_steps"] = completed_steps
            captured_args["failed_step"] = failed_step
            captured_args["remaining_steps"] = remaining_steps
            return [{"id": "s3_alt", "type": "notify"}]

        from workflows.engine import WorkflowEngine
        from workflows.state_store import WorkflowStateStore

        store = MagicMock(spec=WorkflowStateStore)

        state = {
            "id": "wfr_ctx",
            "definition": {
                "replan_on_failure": True,
                "steps": [
                    {"id": "s1", "type": "agent"},
                    {"id": "s2", "type": "agent"},
                    {"id": "s3", "type": "notify"},
                ],
            },
            "status": "running",
            "trigger_payload": {"invoice_id": "INV-099"},
            "steps_total": 3,
            "steps_completed": 1,
            "step_results": {
                "s1": {"output": {"amount": 75000}, "status": "completed", "confidence": 0.95},
            },
            "started_at": "2026-04-04T00:00:00+00:00",
            "replan_count": 0,
            "replan_history": [],
        }

        # After replan, the engine re-loads state and tries execute again.
        # We'll make the second execute see a completed state to stop recursion.
        call_count = {"n": 0}

        async def mock_load(run_id):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return state
            # After replan, return completed state to stop recursion
            return {
                **state,
                "status": "completed",
                "steps_completed": 2,
                "step_results": {
                    "s1": {"output": {"amount": 75000}, "status": "completed", "confidence": 0.95},
                    "s2": {
                        "output": None, "status": "replanned",
                        "confidence": None, "error": "gateway timeout",
                        "replanned": True,
                    },
                    "s3_alt": {"output": {}, "status": "completed", "confidence": None},
                },
            }

        store.load = AsyncMock(side_effect=mock_load)
        store.save = AsyncMock()

        engine = WorkflowEngine(state_store=store)

        with (
            patch("workflows.engine.execute_step", new_callable=AsyncMock) as mock_exec,
            patch("workflows.engine.replan_workflow", side_effect=mock_replan),
        ):
            mock_exec.side_effect = RuntimeError("gateway timeout")

            await engine.execute("wfr_ctx")

            # Verify completed steps were passed to the replanner
            assert len(captured_args["completed_steps"]) == 1
            assert captured_args["completed_steps"][0]["id"] == "s1"
            assert captured_args["completed_steps"][0]["output"] == {"amount": 75000}

            # Verify failed step context
            assert captured_args["failed_step"]["id"] == "s2"
            assert "gateway timeout" in captured_args["failed_step"]["error"]

            # Verify remaining steps
            remaining_ids = [s["id"] for s in captured_args["remaining_steps"]]
            assert "s3" in remaining_ids


# ---------------------------------------------------------------------------
# test_replan_history_tracked
# ---------------------------------------------------------------------------

class TestReplanHistoryTracked:
    def test_build_replan_event(self):
        """build_replan_event creates a properly structured event record."""
        event = build_replan_event(
            replan_count=1,
            failed_step_id="s2",
            error="PineLabs timeout",
            new_steps=[
                {"id": "s2_alt", "type": "agent"},
                {"id": "s3", "type": "notify"},
            ],
        )
        assert event["replan_number"] == 1
        assert event["failed_step_id"] == "s2"
        assert event["error"] == "PineLabs timeout"
        assert event["replacement_steps"] == ["s2_alt", "s3"]
        assert event["replacement_count"] == 2
        assert "timestamp" in event

    def test_multiple_events_tracked(self):
        """Multiple replan events can be accumulated in a history list."""
        history = []
        for i in range(1, 4):
            event = build_replan_event(
                replan_count=i,
                failed_step_id=f"step_{i}",
                error=f"error_{i}",
                new_steps=[{"id": f"alt_{i}", "type": "agent"}],
            )
            history.append(event)

        assert len(history) == 3
        assert history[0]["replan_number"] == 1
        assert history[2]["replan_number"] == 3


# ---------------------------------------------------------------------------
# test_invalid_llm_output_raises_replan_error
# ---------------------------------------------------------------------------

class TestInvalidLLMOutputRaisesReplanError:
    @pytest.mark.asyncio
    async def test_non_json_response(self):
        """Non-JSON LLM output raises ReplanError."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Sorry, I cannot help with that."

            with pytest.raises(ReplanError, match="invalid JSON"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_json_object_instead_of_array(self):
        """LLM returning a JSON object instead of array raises ReplanError."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps({"id": "s1", "type": "agent"})

            with pytest.raises(ReplanError, match="Expected list"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_empty_array_raises_error(self):
        """Empty array from LLM raises ReplanError."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "[]"

            with pytest.raises(ReplanError, match="empty step list"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_non_dict_elements_raise_error(self):
        """Array of non-dicts raises ReplanError."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(["step1", "step2"])

            with pytest.raises(ReplanError, match="Step must be a dict"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_llm_unavailable_raises_replan_error(self):
        """When no LLM backend is available, ReplanError is raised."""
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = ReplanError("No LLM backend available for re-planning")

            with pytest.raises(ReplanError, match="No LLM backend"):
                await replan_workflow(
                    original_definition=SAMPLE_DEFINITION,
                    completed_steps=COMPLETED_STEPS,
                    failed_step=FAILED_STEP,
                    remaining_steps=REMAINING_STEPS,
                )

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_parsed(self):
        """LLM response wrapped in markdown code fences is correctly parsed."""
        fenced = "```json\n" + VALID_LLM_RESPONSE + "\n```"
        with patch("workflows.replanner._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = fenced

            result = await replan_workflow(
                original_definition=SAMPLE_DEFINITION,
                completed_steps=COMPLETED_STEPS,
                failed_step=FAILED_STEP,
                remaining_steps=REMAINING_STEPS,
            )
            assert len(result) == 2
            assert result[0]["id"] == "s2_alt"
