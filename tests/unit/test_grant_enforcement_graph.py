# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — grant enforcement through a real agent graph.

A scripted model drives ``reason -> validate_scopes -> execute_tools`` so the
checks run exactly where agent tool calls are dispatched. The connector call
itself is a mock; everything between the model's tool call and the connector
is the production graph.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage
from structlog.testing import capture_logs

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.langgraph.agent_graph import build_agent_graph
from core.test_doubles.scripted_model import final, tool_call

TENANT = str(uuid.UUID(int=0x1F1C))
PLACEHOLDER_TOKEN = "placeholder-grant-token"  # noqa: S105 - not a credential


def _state(grant_token: str = "") -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="send the invoice reminder")],
        "agent_id": "agent-f1-graph",
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": grant_token,
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _run(run_grant: RunGrant | None, steps: list[Any], scripted_model: Any, grant_token: str = ""):
    scripted_model(steps)
    executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
    with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed), capture_logs() as logs:
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.5,
            run_grant=run_grant,
        )
        result = await graph.compile().ainvoke(_state(grant_token))
    return result, executed, logs


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


async def test_off_mode_runs_the_tool_without_a_grant_and_records_nothing(scripted_model):
    result, executed, logs = await _run(
        RunGrant(mode=EnforcementMode.OFF),
        [tool_call("gmail__send_email", to="ap@example.com"), final({"status": "completed", "confidence": 0.95})],
        scripted_model,
    )
    assert executed.await_count == 1
    assert result["status"] == "completed"
    assert not [entry for entry in logs if str(entry["event"]).startswith("grant_enforcement")]


async def test_warn_mode_runs_the_tool_and_records_the_would_deny(scripted_model):
    result, executed, logs = await _run(
        RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="minting_unconfigured"),
        [tool_call("gmail__send_email", to="ap@example.com"), final({"status": "completed", "confidence": 0.95})],
        scripted_model,
    )
    assert executed.await_count == 1
    assert result["status"] == "completed"
    events = _events(logs, "grant_enforcement_would_deny")
    assert [(e["reason"], e["connector"], e["tool"], e["tenant_id"]) for e in events] == [
        ("grant_missing", "gmail", "send_email", TENANT)
    ]


async def test_warn_mode_with_a_valid_grant_records_nothing(scripted_model):
    client = MagicMock()
    client.enforce.return_value = MagicMock(allowed=True, reason="", reason_code="", grant_id="grnt_placeholder")
    with patch("core.langgraph.agent_graph.get_grantex_client", return_value=client):
        result, executed, logs = await _run(
            RunGrant(mode=EnforcementMode.WARN, token=PLACEHOLDER_TOKEN, source="minted"),
            [tool_call("gmail__send_email", to="ap@example.com"), final({"status": "completed", "confidence": 0.95})],
            scripted_model,
            grant_token=PLACEHOLDER_TOKEN,
        )
    assert executed.await_count == 1
    assert client.enforce.call_args.kwargs["grant_token"] == PLACEHOLDER_TOKEN
    assert not _events(logs, "grant_enforcement_would_deny")
    assert result["status"] == "completed"


async def test_deny_mode_stops_the_tool_call_before_the_connector(scripted_model):
    client = MagicMock()
    client.enforce.return_value = MagicMock(
        allowed=False,
        reason="No scope grants access to connector 'gmail'.",
        reason_code="tool_not_granted",
        sub_reason="",
        grant_id="grnt_placeholder",
    )
    with patch("core.langgraph.agent_graph.get_grantex_client", return_value=client):
        result, executed, logs = await _run(
            RunGrant(mode=EnforcementMode.DENY, token=PLACEHOLDER_TOKEN, source="minted"),
            [tool_call("gmail__send_email", to="ap@example.com")],
            scripted_model,
            grant_token=PLACEHOLDER_TOKEN,
        )
    assert executed.await_count == 0
    assert result["error"] == "grant_denied: tool_not_granted"
    assert [(e["reason"], e["grant_id"]) for e in _events(logs, "grant_enforcement_denied")] == [
        ("tool_not_granted", "grnt_placeholder")
    ]
