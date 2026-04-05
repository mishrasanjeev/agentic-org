"""Tests for the Explainable AI panel (PRD v4 Section 6).

Covers: bullet generation, plain-English output, tool citation,
failed run explanation, and readability grading.

All LLM calls are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ═══════════════════════════════════════════════════════════════════════════
# test_explanation_has_bullets
# ═══════════════════════════════════════════════════════════════════════════


class TestExplanationHasBullets:
    def test_explanation_has_bullets_from_llm(self):
        """When LLM is available, explanation should contain bullet points."""
        mock_response = MagicMock()
        mock_response.content = (
            '{"bullets": ["The agent processed the invoice.",'
            ' "It verified the amount against the PO.",'
            ' "Final status: approved."]}'
        )

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.llm_factory.create_chat_model", return_value=mock_llm):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["Step 1: read invoice", "Step 2: match PO", "Step 3: approve"],
                output={"status": "approved", "confidence": 0.95},
                tools_used=["get_invoice", "match_po"],
            ))

        assert "bullets" in result
        assert isinstance(result["bullets"], list)
        assert len(result["bullets"]) >= 1
        assert len(result["bullets"]) <= 5

    def test_explanation_has_bullets_fallback(self):
        """When LLM is unavailable, fallback bullets are still generated."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=ImportError("no LLM")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=[
                    "The agent loaded the bank statement and identified 3 unmatched transactions.",
                    "Reconciliation completed with 97% match rate.",
                    "Two items flagged for manual review.",
                ],
                output={"status": "completed"},
                tools_used=["fetch_bank_statement"],
            ))

        assert "bullets" in result
        assert isinstance(result["bullets"], list)
        assert len(result["bullets"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# test_explanation_plain_english
# ═══════════════════════════════════════════════════════════════════════════


class TestExplanationPlainEnglish:
    def test_bullets_are_plain_english_strings(self):
        """Each bullet should be a plain string, not JSON or code."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=ImportError("no LLM")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=[
                    "Agent identified the vendor as Acme Corp from the invoice header.",
                    "Payment amount of INR 45,000 was validated against the purchase order.",
                ],
                output={"status": "completed", "confidence": 0.92},
                tools_used=[],
            ))

        for bullet in result["bullets"]:
            assert isinstance(bullet, str)
            # Should not be raw JSON
            assert not bullet.startswith("{")
            assert not bullet.startswith("[")
            # Should end with punctuation
            assert bullet.strip()[-1] in (".", "!", "?", ":")


# ═══════════════════════════════════════════════════════════════════════════
# test_explanation_cites_tools
# ═══════════════════════════════════════════════════════════════════════════


class TestExplanationCitesTools:
    def test_tools_cited_matches_used_tools(self):
        """tools_cited should only contain tools that appear in the bullets."""
        mock_response = MagicMock()
        mock_response.content = (
            '{"bullets": ["Used get_contact to find the customer.",'
            ' "Verified using check_balance.",'
            ' "Approved the transaction."]}'
        )

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("core.langgraph.llm_factory.create_chat_model", return_value=mock_llm):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["looked up contact", "checked balance"],
                output={"status": "approved"},
                tools_used=["get_contact", "check_balance", "post_voucher"],
            ))

        # get_contact and check_balance are mentioned in bullets
        assert "get_contact" in result["tools_cited"]
        assert "check_balance" in result["tools_cited"]
        # post_voucher is NOT mentioned in any bullet
        assert "post_voucher" not in result["tools_cited"]

    def test_tools_cited_is_list(self):
        """tools_cited should always be a list."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("fail")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["step 1"],
                output={},
                tools_used=["some_tool"],
            ))

        assert isinstance(result["tools_cited"], list)


# ═══════════════════════════════════════════════════════════════════════════
# test_failed_run_explains_failure
# ═══════════════════════════════════════════════════════════════════════════


class TestFailedRunExplainsFailure:
    def test_failed_run_generates_explanation(self):
        """A failed run should still produce bullets explaining the failure."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=ImportError("no LLM")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["Agent execution failed: ConnectionError"],
                output={},
                tools_used=[],
            ))

        assert "bullets" in result
        assert len(result["bullets"]) >= 1
        # At least one bullet should mention the failure
        combined = " ".join(result["bullets"]).lower()
        assert "failed" in combined or "error" in combined or "agent" in combined

    def test_failed_run_has_zero_or_low_confidence(self):
        """Failed runs should not report high confidence."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=Exception("fail")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["Agent execution failed: TimeoutError"],
                output={},
                tools_used=[],
            ))

        # Confidence should be derived heuristically — with only 1 trace entry
        # it should be low
        assert result["confidence"] <= 0.6


# ═══════════════════════════════════════════════════════════════════════════
# test_readability_grade_under_10
# ═══════════════════════════════════════════════════════════════════════════


class TestReadabilityGrade:
    def test_readability_grade_is_number(self):
        """readability_grade should be a float."""
        from core.explainer import compute_flesch_kincaid_grade

        grade = compute_flesch_kincaid_grade("The agent processed the invoice successfully.")
        assert isinstance(grade, float)

    def test_readability_grade_under_10_for_simple_text(self):
        """Simple plain-English text should have a grade level under 10."""
        from core.explainer import compute_flesch_kincaid_grade

        simple_text = (
            "The agent checked the bank balance. "
            "It found three new payments. "
            "All payments were matched to invoices. "
            "The task was completed."
        )
        grade = compute_flesch_kincaid_grade(simple_text)
        assert grade < 10.0, f"Grade {grade} is too high for simple text"

    def test_readability_grade_in_explanation(self):
        """The explanation result should include a readability_grade field."""
        with patch("core.langgraph.llm_factory.create_chat_model", side_effect=ImportError("no LLM")):
            from core.explainer import generate_explanation

            result = _run(generate_explanation(
                reasoning_trace=["Agent completed the reconciliation task."],
                output={"status": "completed"},
                tools_used=[],
            ))

        assert "readability_grade" in result
        assert isinstance(result["readability_grade"], float)
        # For simple fallback bullets, grade should be reasonable
        assert result["readability_grade"] < 15.0

    def test_empty_text_grade_zero(self):
        """Empty text should return grade 0."""
        from core.explainer import compute_flesch_kincaid_grade

        assert compute_flesch_kincaid_grade("") == 0.0

    def test_grade_clamped_to_range(self):
        """Grade should be between 0 and 20."""
        from core.explainer import compute_flesch_kincaid_grade

        grade = compute_flesch_kincaid_grade("Antidisestablishmentarianism.")
        assert 0.0 <= grade <= 20.0
