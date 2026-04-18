"""Canonical AgentRunResult contract regression test.

Asserts both /agents/{id}/run and /a2a/tasks return every canonical
field documented in docs/api/agent-run-contract.md, with the same
shape. Prevents silent drift between the two code paths.

We build response payloads directly via the endpoint handlers' known
shapes (no LLM calls / live backend required) — this is a contract
test, not an end-to-end smoke test.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

# Load the repo's sdk/agenticorg/client.py directly rather than whatever
# `agenticorg` happens to be installed in site-packages — this is a
# contract test on *this checkout*, not on the published package.
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_client_path = _ROOT / "sdk" / "agenticorg" / "client.py"
_spec = importlib.util.spec_from_file_location(
    "_repo_sdk_client", _client_path,
)
assert _spec and _spec.loader, f"cannot load {_client_path}"
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_repo_sdk_client"] = _mod
_spec.loader.exec_module(_mod)
AgentRunResult = _mod.AgentRunResult
_to_agent_run_result = _mod._to_agent_run_result

# ---------------------------------------------------------------------------
# Canonical field set
# ---------------------------------------------------------------------------
CANONICAL_FIELDS: set[str] = {
    "run_id",
    "agent_id",
    "agent_type",
    "correlation_id",
    "status",
    "output",
    "confidence",
    "reasoning_trace",
    "tool_calls",
    "runtime",
    "performance",
    "explanation",
    "hitl_trigger",
    "error",
}


def _build_agents_run_canonical() -> dict[str, Any]:
    """Mirrors the shape emitted by api/v1/agents.py:execute_agent (PR-A)."""
    return {
        "run_id": "run_abc",
        "task_id": "run_abc",  # deprecated alias
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "agent_type": None,
        "correlation_id": "corr_abc",
        "status": "completed",
        "output": {"invoice_id": "INV-001", "amount": 1000},
        "confidence": 0.92,
        "reasoning_trace": ["loaded invoice", "matched PO"],
        "tool_calls": [{"tool": "ocr", "args": {}, "result": None}],
        "runtime": "langgraph",
        "explanation": {"bullets": ["3-way match passed"]},
        "performance": {"total_latency_ms": 1200, "llm_tokens_used": 800, "llm_cost_usd": 0.0024},
        "hitl_trigger": None,
        "error": None,
    }


def _build_a2a_task_canonical() -> dict[str, Any]:
    """Mirrors the shape emitted by api/v1/a2a.py:create_task (PR-A)."""
    return {
        "run_id": "a2a_xyz",
        "id": "a2a_xyz",  # deprecated alias
        "agent_id": None,
        "agent_type": "ap_processor",
        "correlation_id": "corr_xyz",
        "status": "completed",
        "output": {"result": "ok"},
        "confidence": 0.88,
        "reasoning_trace": [],
        "tool_calls": [],
        "runtime": "a2a",
        "performance": None,
        "explanation": None,
        "hitl_trigger": None,
        "error": None,
        # Deprecated nested form for legacy clients.
        "result": {"output": {"result": "ok"}, "confidence": 0.88},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload_factory",
    [_build_agents_run_canonical, _build_a2a_task_canonical],
    ids=["/agents/{id}/run", "/a2a/tasks"],
)
def test_every_canonical_field_present(payload_factory):
    payload = payload_factory()
    missing = CANONICAL_FIELDS - set(payload.keys())
    assert not missing, f"Canonical fields missing: {missing}"


def test_sdk_normalizes_agents_run_shape_to_canonical():
    payload = _build_agents_run_canonical()
    result = _to_agent_run_result(payload)
    assert isinstance(result, AgentRunResult)
    assert result.run_id == "run_abc"
    assert result.agent_id == "00000000-0000-0000-0000-000000000001"
    assert result.agent_type is None
    assert result.status == "completed"
    assert result.output == {"invoice_id": "INV-001", "amount": 1000}
    assert result.confidence == 0.92
    assert result.runtime == "langgraph"
    assert result.performance is not None
    assert result.performance["total_latency_ms"] == 1200


def test_sdk_normalizes_a2a_task_shape_to_canonical():
    payload = _build_a2a_task_canonical()
    result = _to_agent_run_result(payload)
    assert isinstance(result, AgentRunResult)
    assert result.run_id == "a2a_xyz"
    assert result.agent_id is None
    assert result.agent_type == "ap_processor"
    assert result.status == "completed"
    assert result.output == {"result": "ok"}
    assert result.confidence == 0.88
    assert result.runtime == "a2a"


def test_sdk_normalizes_legacy_agents_run_shape():
    """Pre-PR-A /agents/{id}/run shape (no run_id, just task_id)."""
    legacy = {
        "task_id": "legacy_task_123",
        "agent_id": "agent-uuid",
        "status": "completed",
        "output": {"ok": True},
        "confidence": 0.8,
        "reasoning_trace": [],
        "runtime": "langgraph",
    }
    result = _to_agent_run_result(legacy)
    assert result.run_id == "legacy_task_123"
    assert result.agent_id == "agent-uuid"
    assert result.output == {"ok": True}


def test_sdk_normalizes_legacy_a2a_wrapped_shape():
    """Pre-PR-A /a2a/tasks shape with nested result.{output,confidence}."""
    legacy = {
        "id": "legacy_a2a_456",
        "status": "completed",
        "agent_type": "ap_processor",
        "result": {"output": {"legacy_field": "ok"}, "confidence": 0.75},
    }
    result = _to_agent_run_result(legacy)
    assert result.run_id == "legacy_a2a_456"
    assert result.agent_type == "ap_processor"
    assert result.output == {"legacy_field": "ok"}
    assert result.confidence == 0.75


def test_sdk_preserves_raw_payload():
    payload = _build_agents_run_canonical()
    result = _to_agent_run_result(payload)
    assert result.raw == payload
