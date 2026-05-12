"""Aishwarya 12 May 2026 agent-runtime reopen regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_tds_high_value_hitl_uses_transaction_amount_not_tds_amount() -> None:
    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query=(
                "Calculate TDS for contractor payment of INR 12,50,000 "
                "under Section 194C for Q2 FY 2026-27."
            ),
        )
    )

    assert result is not None
    assert result["hitl_trigger"]
    assert "transaction_amount" in result["hitl_trigger"]
    assert "500000" in result["hitl_trigger"]

    tool_call = result["tool_calls"][0]
    assert tool_call["arguments"]["amount"] == 1_250_000.0
    assert tool_call["result"]["tds_amount"] == 12_500.0
    assert tool_call["governance"]["transaction_amount"] == 1_250_000.0

    answer = result["answer"].lower()
    assert "hitl triggered" in answer
    assert "gross transaction amount" in answer
    assert "no filing" in answer
    assert "auto-submitted" in answer


def test_challan_281_extracts_amount_and_does_not_reask_it() -> None:
    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query="Generate Challan 281 for April 2026 TDS payment of INR 1,25,000.",
        )
    )

    assert result is not None
    assert result["tool_calls"] == []
    assert result["hitl_trigger"] == "challan_281_payment_requires_partner_review"
    assert result["hitl_context"]["tds_amount"] == 125_000.0

    answer = result["answer"]
    lower = answer.lower()
    assert "125,000" in answer or "125000" in answer
    assert "april 2026" in lower
    assert "section" in lower
    assert "tan" in lower
    assert "pan" in lower
    for forbidden in (
        "amount of tds to be paid",
        "please provide the amount",
        "what is the amount",
    ):
        assert forbidden not in lower


def test_tds_late_filing_route_uses_234e_and_201_1a_logic() -> None:
    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query=(
                "Compute late filing interest and penalty for delayed Form 26Q "
                "filing for Q4 FY 2025-26."
            ),
        )
    )

    assert result is not None
    answer = result["answer"]
    lower = answer.lower()
    assert "234e" in lower
    assert "201(1a)" in lower
    assert "q4 fy 2025-26" in lower
    assert "2026-05-31" in lower
    assert "tds amount payable" in lower
    assert "actual delay" in lower
    assert "cannot directly compute" not in lower


def test_tds_late_filing_computes_when_amount_and_delay_are_present() -> None:
    from api.v1._tds_routing import try_tds_deterministic_route

    result = asyncio.run(
        try_tds_deterministic_route(
            agent_type="tds_compliance_agent",
            query=(
                "Compute Section 234E and 201(1A) for delayed Form 26Q "
                "filing for Q4 FY 2025-26 with TDS amount INR 1,00,000 "
                "delayed by 10 days."
            ),
        )
    )

    assert result is not None
    answer = result["answer"]
    assert "2,000.00" in answer
    assert "1,500.00" in answer
    assert result["hitl_context"]["tds_amount"] == 100_000.0
    assert result["hitl_context"]["delay_days"] == 10


def test_shadow_accuracy_excludes_tool_failure_samples() -> None:
    from api.v1.agents import _is_shadow_accuracy_measurable

    assert (
        _is_shadow_accuracy_measurable(
            task_status="completed",
            task_confidence=0.92,
            tool_calls=[{"tool": "get_ledger_balance", "status": "success"}],
        )
        is True
    )
    assert (
        _is_shadow_accuracy_measurable(
            task_status="completed",
            task_confidence=0.50,
            tool_calls=[{"tool": "get_ledger_balance", "status": "error"}],
        )
        is False
    )
    assert (
        _is_shadow_accuracy_measurable(
            task_status="completed",
            task_confidence=0.92,
            tool_calls=[{"tool": "get_ledger_balance", "result": {"error": "timeout"}}],
        )
        is False
    )


def test_tds_prompt_pins_high_value_gate_to_gross_transaction_amount() -> None:
    prompt = (REPO / "core" / "agents" / "packs" / "ca" / "prompts" / "tds_compliance.prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "Gross transaction/payment amount > INR 5,00,000" in prompt
    assert "not the computed TDS amount" in prompt

    pack = (REPO / "core" / "agents" / "packs" / "ca" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "HIGH-VALUE HITL" in pack
    assert "not the computed TDS amount" in pack
