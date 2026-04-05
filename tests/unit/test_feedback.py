"""Tests for the Self-Improving Agents feedback loop (PRD v4 Section 8).

Covers: feedback storage, analysis/amendment generation,
amendment prepending to prompts, rejection rate confidence,
and tenant isolation.

All LLM calls are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════
# test_submit_feedback_stored
# ═══════════════════════════════════════════════════════════════════════════


class TestSubmitFeedbackStored:
    def setup_method(self):
        from core.feedback.collector import clear_in_memory_store
        clear_in_memory_store()

    def test_feedback_stored_in_memory(self):
        """Feedback should be stored in memory when DB is unavailable."""
        from core.feedback.collector import get_in_memory_store, submit_feedback

        result = _run(submit_feedback(
            agent_id="agent-001",
            run_id="run-001",
            feedback_type="thumbs_up",
            text="Great result!",
            tenant_id="tenant-001",
        ))

        assert result["status"] == "stored"
        assert result["storage"] == "memory"
        assert result["feedback_id"]

        # Verify it's actually in the in-memory store
        store = get_in_memory_store()
        key = "tenant-001:agent-001"
        assert key in store
        assert len(store[key]) == 1
        assert store[key][0]["feedback_type"] == "thumbs_up"

    def test_multiple_feedback_entries(self):
        """Multiple feedback entries should all be stored."""
        from core.feedback.collector import get_in_memory_store, submit_feedback

        for i in range(5):
            _run(submit_feedback(
                agent_id="agent-002",
                run_id=f"run-{i}",
                feedback_type="thumbs_down" if i % 2 == 0 else "thumbs_up",
                text=f"Feedback #{i}",
                tenant_id="tenant-001",
            ))

        store = get_in_memory_store()
        assert len(store["tenant-001:agent-002"]) == 5

    def test_invalid_feedback_type_rejected(self):
        """Invalid feedback types should return an error."""
        from core.feedback.collector import submit_feedback

        result = _run(submit_feedback(
            agent_id="agent-001",
            run_id="run-001",
            feedback_type="invalid_type",
            tenant_id="tenant-001",
        ))

        assert result["status"] == "error"
        assert "invalid" in result["message"].lower()

    def test_correction_stores_corrected_output(self):
        """Correction feedback should store the corrected output."""
        from core.feedback.collector import get_in_memory_store, submit_feedback

        corrected = {"amount": 45000, "currency": "INR"}
        _run(submit_feedback(
            agent_id="agent-003",
            run_id="run-003",
            feedback_type="correction",
            text="Wrong currency",
            corrected_output=corrected,
            tenant_id="tenant-001",
        ))

        store = get_in_memory_store()
        entry = store["tenant-001:agent-003"][0]
        assert entry["corrected_output"] == corrected
        assert entry["text"] == "Wrong currency"


# ═══════════════════════════════════════════════════════════════════════════
# test_analyze_generates_amendment
# ═══════════════════════════════════════════════════════════════════════════


class TestAnalyzeGeneratesAmendment:
    def setup_method(self):
        from core.feedback.collector import clear_in_memory_store
        clear_in_memory_store()

    def _seed_feedback(self, agent_id: str, tenant_id: str, count: int = 12):
        """Helper to seed negative feedback entries."""
        from core.feedback.collector import submit_feedback

        for i in range(count):
            _run(submit_feedback(
                agent_id=agent_id,
                run_id=f"run-{i}",
                feedback_type="thumbs_down" if i % 3 != 0 else "correction",
                text=f"Wrong currency used in run {i}. Should be INR not USD.",
                corrected_output={"currency": "INR"} if i % 3 == 0 else None,
                tenant_id=tenant_id,
            ))

    def test_analyze_with_enough_data(self):
        """Analysis with >= 10 entries should generate an amendment."""
        self._seed_feedback("agent-010", "tenant-010", count=12)

        mock_response = MagicMock()
        mock_response.content = (
            '{"amendment": "Always convert amounts to INR",'
            ' "reason": "3/10 runs rejected for wrong currency",'
            ' "confidence": 0.85}'
        )

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.llm_factory.create_chat_model", return_value=mock_llm):
            from core.feedback.analyzer import analyze_feedback

            result = _run(analyze_feedback("agent-010", "tenant-010"))

        assert result["amendment"] == "Always convert amounts to INR"
        assert result["confidence"] == 0.85
        assert "currency" in result["reason"]

    def test_analyze_with_insufficient_data(self):
        """Analysis with < 10 entries should return empty amendment."""
        from core.feedback.collector import submit_feedback

        for i in range(5):
            _run(submit_feedback(
                agent_id="agent-011",
                run_id=f"run-{i}",
                feedback_type="thumbs_down",
                text="Bad result",
                tenant_id="tenant-011",
            ))

        from core.feedback.analyzer import analyze_feedback

        result = _run(analyze_feedback("agent-011", "tenant-011"))

        assert result["amendment"] == ""
        assert "at least" in result["reason"].lower()

    def test_analyze_fallback_without_llm(self):
        """Analysis should fall back to heuristics when LLM fails."""
        self._seed_feedback("agent-012", "tenant-012", count=12)

        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("LLM unavailable")):
            from core.feedback.analyzer import analyze_feedback

            result = _run(analyze_feedback("agent-012", "tenant-012"))

        # Should still produce some result via fallback
        assert "reason" in result
        assert result["confidence"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# test_amendment_prepended_to_prompt
# ═══════════════════════════════════════════════════════════════════════════


class TestAmendmentPrependedToPrompt:
    def test_format_amendments_single(self):
        """Single amendment should be formatted correctly."""
        from core.feedback.analyzer import format_amendments_for_prompt

        result = format_amendments_for_prompt(["Always convert amounts to INR"])
        assert result.startswith("IMPORTANT LEARNED RULES:")
        assert "- Always convert amounts to INR" in result
        assert result.endswith("\n\n")

    def test_format_amendments_multiple(self):
        """Multiple amendments should all be included."""
        from core.feedback.analyzer import format_amendments_for_prompt

        amendments = [
            "Always convert amounts to INR",
            "Include GST in all tax calculations",
            "Use formal tone in customer emails",
        ]
        result = format_amendments_for_prompt(amendments)
        assert "IMPORTANT LEARNED RULES:" in result
        for a in amendments:
            assert f"- {a}" in result

    def test_format_amendments_empty(self):
        """Empty amendments list should return empty string."""
        from core.feedback.analyzer import format_amendments_for_prompt

        assert format_amendments_for_prompt([]) == ""

    def test_amendment_prepended_to_system_prompt(self):
        """When amendments exist, they should be prepended to the system prompt."""
        from core.feedback.analyzer import format_amendments_for_prompt

        original = "You are an AP processor agent."
        amendments = ["Always validate vendor GSTIN"]
        block = format_amendments_for_prompt(amendments)
        final = block + original

        assert final.startswith("IMPORTANT LEARNED RULES:")
        assert "- Always validate vendor GSTIN" in final
        assert final.endswith(original)


# ═══════════════════════════════════════════════════════════════════════════
# test_rejection_rate_lowers_confidence_floor
# ═══════════════════════════════════════════════════════════════════════════


class TestRejectionRateLowersConfidenceFloor:
    def setup_method(self):
        from core.feedback.collector import clear_in_memory_store
        clear_in_memory_store()

    def test_high_rejection_rate_produces_higher_analysis_confidence(self):
        """When rejection rate is high, fallback analysis confidence should reflect that."""
        from core.feedback.collector import submit_feedback

        # Seed 15 entries — all negative
        for i in range(15):
            _run(submit_feedback(
                agent_id="agent-020",
                run_id=f"run-{i}",
                feedback_type="thumbs_down",
                text="Incorrect output",
                tenant_id="tenant-020",
            ))

        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("no LLM")):
            from core.feedback.analyzer import analyze_feedback

            result = _run(analyze_feedback("agent-020", "tenant-020"))

        # With 100% negative feedback, confidence in the amendment should be high
        assert result["confidence"] >= 0.8

    def test_mixed_feedback_produces_moderate_confidence(self):
        """Mixed feedback should produce moderate analysis confidence."""
        from core.feedback.collector import submit_feedback

        # Seed 12 entries — 4 positive, 8 negative
        for i in range(12):
            _run(submit_feedback(
                agent_id="agent-021",
                run_id=f"run-{i}",
                feedback_type="thumbs_up" if i < 4 else "thumbs_down",
                text="Good" if i < 4 else "Bad output",
                tenant_id="tenant-021",
            ))

        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("no LLM")):
            from core.feedback.analyzer import analyze_feedback

            result = _run(analyze_feedback("agent-021", "tenant-021"))

        # 8/12 entries are negative; fallback analyses within negative subset
        # so confidence will be high among the negatives (all same type)
        assert 0.5 <= result["confidence"] <= 0.95


# ═══════════════════════════════════════════════════════════════════════════
# test_tenant_isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestTenantIsolation:
    def setup_method(self):
        from core.feedback.collector import clear_in_memory_store
        clear_in_memory_store()

    def test_feedback_isolated_by_tenant(self):
        """Feedback from different tenants should not leak."""
        from core.feedback.collector import list_feedback, submit_feedback

        # Tenant A submits feedback
        _run(submit_feedback(
            agent_id="agent-shared",
            run_id="run-a1",
            feedback_type="thumbs_up",
            text="Tenant A feedback",
            tenant_id="tenant-A",
        ))

        # Tenant B submits feedback for the SAME agent ID
        _run(submit_feedback(
            agent_id="agent-shared",
            run_id="run-b1",
            feedback_type="thumbs_down",
            text="Tenant B feedback",
            tenant_id="tenant-B",
        ))

        # List feedback for Tenant A
        feedback_a = _run(list_feedback("agent-shared", tenant_id="tenant-A"))
        assert len(feedback_a) == 1
        assert feedback_a[0]["text"] == "Tenant A feedback"

        # List feedback for Tenant B
        feedback_b = _run(list_feedback("agent-shared", tenant_id="tenant-B"))
        assert len(feedback_b) == 1
        assert feedback_b[0]["text"] == "Tenant B feedback"

    def test_analysis_uses_correct_tenant_data(self):
        """Feedback analysis should only consider the requesting tenant's data."""
        from core.feedback.collector import submit_feedback

        # Seed 12 entries for tenant-X
        for i in range(12):
            _run(submit_feedback(
                agent_id="agent-multi",
                run_id=f"run-x-{i}",
                feedback_type="thumbs_down",
                text=f"Tenant X complaint {i}",
                tenant_id="tenant-X",
            ))

        # Seed 3 entries for tenant-Y (not enough for analysis)
        for i in range(3):
            _run(submit_feedback(
                agent_id="agent-multi",
                run_id=f"run-y-{i}",
                feedback_type="thumbs_up",
                text="Tenant Y happy",
                tenant_id="tenant-Y",
            ))

        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("no LLM")):
            from core.feedback.analyzer import analyze_feedback

            # Tenant X should get analysis (>=10 entries)
            result_x = _run(analyze_feedback("agent-multi", "tenant-X"))
            assert result_x["confidence"] > 0

            # Tenant Y should NOT get analysis (< 10 entries)
            result_y = _run(analyze_feedback("agent-multi", "tenant-Y"))
            assert result_y["amendment"] == ""
            assert "at least" in result_y["reason"].lower()
