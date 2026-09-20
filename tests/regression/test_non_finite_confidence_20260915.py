# SPDX-License-Identifier: Apache-2.0
"""Production follow-up 2026-09-15: non-finite model values must fail closed.

The approvals page failed the post-deploy "no NaN" checks in production. The
sibling-path sweep found two real gaps in how agent output reaches approvals:

* ``_extract_confidence`` clamped a model-reported confidence with
  ``max(0.0, min(1.0, float(raw)))``. ``min(1.0, nan)`` is ``1.0``, so a model
  that reported ``NaN`` (bare token or string) got full confidence and the
  human-approval gate was skipped.
* ``json.loads`` accepts bare ``NaN``/``Infinity`` tokens. Those floats made the
  agent output unstorable in JSONB (audit log, HITL context) and compared
  ``False`` in HITL conditions such as ``total > 500000``, skipping review.

Non-finite confidence now counts as zero (review required) and non-finite
numbers in the output become ``null`` before any gate or store sees them.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from core.langgraph.agent_graph import (
    _extract_confidence,
    _replace_non_finite,
    build_agent_graph,
)
from core.test_doubles.scripted_model import final


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "NaN", "nan", "Infinity", "-Infinity"])
def test_non_finite_reported_confidence_fails_closed(raw: Any) -> None:
    assert _extract_confidence({"confidence": raw}) == 0.0
    assert _extract_confidence({"agent_confidence": raw}) == 0.0


@pytest.mark.parametrize(("raw", "expected"), [(0.93, 0.93), ("0.4", 0.4), (1.7, 1.0), (-2, 0.0), ("high", 0.95)])
def test_finite_reported_confidence_is_unchanged(raw: Any, expected: float) -> None:
    assert _extract_confidence({"confidence": raw}) == expected


def test_replace_non_finite_nulls_nested_values_and_keeps_the_rest() -> None:
    cleaned = _replace_non_finite(
        {"total": float("nan"), "lines": [1.5, float("inf"), {"tax": float("-inf")}], "vendor": "NaN Corp", "ok": True}
    )
    assert cleaned == {"total": None, "lines": [1.5, None, {"tax": None}], "vendor": "NaN Corp", "ok": True}
    # Strict JSON (what JSONB and the API response require) now serialises.
    json.dumps(cleaned, allow_nan=False)


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="reconcile invoice INV-0001")],
        "agent_id": "agent-scripted",
        "agent_type": "ap_processor",
        "domain": "finance",
        "tenant_id": "",
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _run(hitl_condition: str = "") -> dict[str, Any]:
    graph = build_agent_graph(
        system_prompt="scripted",
        authorized_tools=[],
        confidence_floor=0.88,
        hitl_condition=hitl_condition,
        run_grant=NO_RUN_GRANT_FOR_TESTS,
    )
    compiled = graph.compile(checkpointer=MemorySaver())
    return await compiled.ainvoke(_state(), {"configurable": {"thread_id": "non-finite"}})


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any]:
    interrupts = result.get("__interrupt__") or []
    assert interrupts, f"expected a HITL interrupt, got status={result.get('status')!r}"
    return interrupts[0].value


@pytest.mark.parametrize("token", ["NaN", '"NaN"', "Infinity"])
async def test_model_reporting_non_finite_confidence_is_sent_for_review(scripted_model: Any, token: str) -> None:
    scripted_model([AIMessage(content=f'{{"status": "completed", "confidence": {token}, "total": 1200}}')])
    result = await _run()

    payload = _interrupt_payload(result)
    assert payload["type"] == "hitl_approval"
    assert result["confidence"] == 0.0
    assert "confidence 0.000 < floor" in payload["hitl_trigger"]


async def test_non_finite_output_number_fails_the_condition_closed_and_is_storable(scripted_model: Any) -> None:
    scripted_model([AIMessage(content='{"status": "completed", "confidence": 0.95, "total": NaN}')])
    result = await _run(hitl_condition="total > 500000")

    payload = _interrupt_payload(result)
    assert "fail closed" in payload["hitl_trigger"]
    assert result["output"]["total"] is None
    # The output goes into JSONB (audit log, HITL context) and API responses.
    json.dumps(result["output"], allow_nan=False)
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in result["output"].values())


async def test_finite_output_still_completes_without_review(scripted_model: Any) -> None:
    scripted_model([final({"status": "completed", "confidence": 0.95, "total": 1200})])
    result = await _run(hitl_condition="total > 500000")

    assert not result.get("__interrupt__")
    assert result["status"] == "completed"
    assert result["output"]["total"] == 1200
