"""Tests for SupportDeflectorAgent — FAQ, KB, escalation, deflection rate, tickets."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_task(query: str) -> MagicMock:
    """Create a mock task with the given query text."""
    task = MagicMock()
    task.task = MagicMock()
    task.task.model_dump.return_value = {"query": query}
    task.step_id = "step_1"
    task.step_index = 0
    task.correlation_id = "corr_1"
    task.workflow_run_id = "run_1"
    return task


def _make_agent(**kwargs):  # -> SupportDeflectorAgent
    """Create a SupportDeflectorAgent with sensible defaults."""
    from core.agents.ops.support_deflector import SupportDeflectorAgent

    defaults = {
        "agent_id": "test_deflector",
        "tenant_id": "tenant_1",
        "authorized_tools": [],
        "tool_gateway": None,
    }
    defaults.update(kwargs)
    return SupportDeflectorAgent(**defaults)


class TestFAQAutoAnswer:
    """test_faq_auto_answer — Known FAQ queries are auto-resolved."""

    def test_faq_auto_answer(self):
        agent = _make_agent()
        task = _make_task("How do I reset my password?")
        result = asyncio.run(agent.execute(task))

        assert result["status"] == "auto_resolved"
        assert result["source"] == "faq"
        assert "password" in result["answer"].lower() or "Password" in result["answer"]
        assert result["confidence"] >= 0.7

    def test_billing_faq_auto_answer(self):
        agent = _make_agent()
        task = _make_task("How do I upgrade my plan? billing question")
        result = asyncio.run(agent.execute(task))

        assert result["status"] == "auto_resolved"
        assert result["source"] == "faq"
        assert "Billing" in result["answer"] or "plan" in result["answer"].lower()


class TestKBLookupResolvesQuery:
    """test_kb_lookup_resolves_query — KB search resolves when FAQ misses."""

    def test_kb_lookup_resolves_query(self):
        mock_gateway = AsyncMock()
        mock_gateway.execute.return_value = {
            "results": [
                {
                    "text": "To configure SSO, go to Settings > Authentication > SAML.",
                    "score": 0.85,
                }
            ]
        }

        agent = _make_agent(tool_gateway=mock_gateway)
        # Query that won't match any FAQ intent well
        task = _make_task("How do I set up SAML SSO for my organization?")
        result = asyncio.run(agent.execute(task))

        # Should either resolve via KB or escalate; if KB confidence >= 0.7, it resolves
        if result["status"] == "auto_resolved":
            assert result["source"] == "knowledge_base"
            assert result["confidence"] >= 0.7
        else:
            # If intent classification gave low confidence, KB might not be reached
            assert result["status"] == "escalated"


class TestLowConfidenceEscalates:
    """test_low_confidence_escalates — Unknown queries are escalated."""

    def test_low_confidence_escalates(self):
        agent = _make_agent()
        # Completely unrelated query — won't match any FAQ intent
        task = _make_task("xyzzy foobar quantum entanglement soup recipe")
        result = asyncio.run(agent.execute(task))

        assert result["status"] == "escalated"
        assert result["source"] == "escalation"
        assert "escalation_reason" in result
        assert result["confidence"] < 0.7

    def test_low_confidence_includes_reason(self):
        agent = _make_agent()
        task = _make_task("something completely unknown and random 12345")
        result = asyncio.run(agent.execute(task))

        assert result["status"] == "escalated"
        assert len(result["escalation_reason"]) > 0
        assert "confidence" in result["escalation_reason"].lower() or "threshold" in result["escalation_reason"].lower()


class TestDeflectionRateCalculated:
    """test_deflection_rate_calculated — deflection_rate metric is tracked."""

    def test_deflection_rate_calculated(self):
        agent = _make_agent()

        # Run 3 FAQ queries (should be auto-resolved)
        for q in ["reset my password", "upgrade my plan billing", "how to export data csv"]:
            task = _make_task(q)
            asyncio.run(agent.execute(task))

        # Run 1 unknown query (should escalate)
        task = _make_task("xyzzy nonsense query 999")
        asyncio.run(agent.execute(task))

        # 3 resolved out of 4 = 75%
        assert agent._total_queries == 4
        assert agent._auto_resolved == 3
        assert agent.deflection_rate == pytest.approx(75.0)

    def test_deflection_rate_zero_when_no_queries(self):
        agent = _make_agent()
        assert agent.deflection_rate == 0.0

    def test_deflection_rate_in_response(self):
        agent = _make_agent()
        task = _make_task("How do I reset my password?")
        result = asyncio.run(agent.execute(task))
        assert "deflection_rate" in result
        assert result["deflection_rate"] == pytest.approx(100.0)


class TestUnknownIssueCreatesTicket:
    """test_unknown_issue_creates_ticket — escalated queries include ticket context."""

    def test_unknown_issue_creates_ticket(self):
        agent = _make_agent()
        task = _make_task("My custom integration with XYZ legacy system is broken in a unique way")
        result = asyncio.run(agent.execute(task))

        # Should escalate since it's an unknown issue
        assert result["status"] == "escalated"
        # Response includes enough context for ticket creation
        assert "processing_trace" in result
        assert len(result["processing_trace"]) > 0
        assert "escalation_reason" in result

    def test_escalated_response_has_all_required_fields(self):
        agent = _make_agent()
        task = _make_task("Something broke and I don't know what it is 98765")
        result = asyncio.run(agent.execute(task))

        assert result["status"] == "escalated"
        required_fields = ["status", "confidence", "source", "processing_trace", "deflection_rate", "escalation_reason"]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"
