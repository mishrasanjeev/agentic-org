# SPDX-License-Identifier: Apache-2.0
"""PRD F-1c — a grant denial ends the run as failed and never leaks into later turns.

Acceptance criteria covered here:

* with the default confidence floor (0.88) and a checkpointer, a run whose
  tool call grant enforcement refused ends ``failed`` - it is not routed to
  human review and does not pause on an interrupt;
* a thread reused for a later turn (voice ``voice:{call_sid}``) does not
  inherit an earlier turn's ``grant_denial``, and neither does a resume.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.test_doubles.scripted_model import final, tool_call

TENANT = str(uuid.UUID(int=0x1F5A))
AGENT = str(uuid.UUID(int=0xC1))
DENIED = "placeholder-denied-grant"  # noqa: S105 - not a credential
ALLOWED = "placeholder-allowed-grant"  # noqa: S105 - not a credential


def _enforcer() -> MagicMock:
    client = MagicMock()

    def _enforce(*, grant_token: str, **_: Any) -> SimpleNamespace:
        allowed = grant_token == ALLOWED
        return SimpleNamespace(
            allowed=allowed,
            reason="" if allowed else "No scope grants access",
            reason_code="" if allowed else "tool_not_granted",
            sub_reason="",
            grant_id="grnt_placeholder",
        )

    client.enforce.side_effect = _enforce
    return client


def _state(token: str) -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="send the reminder")],
        "agent_id": AGENT,
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": token,
        "grant_denial": {},
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def test_a_denied_run_fails_instead_of_going_to_human_review_at_the_default_floor(scripted_model):
    from core.langgraph.agent_graph import build_agent_graph

    scripted_model([tool_call("gmail__send_email", to="ap@example.com")])
    executed = AsyncMock(return_value={"status": "sent"})
    with (
        patch("core.langgraph.tool_adapter._execute_connector_tool", executed),
        patch("core.langgraph.agent_graph.get_grantex_client", return_value=_enforcer()),
    ):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.88,
            run_grant=RunGrant(mode=EnforcementMode.DENY, token=DENIED, source="minted"),
        )
        compiled = graph.compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "f1c-deny-floor"}}
        result = await compiled.ainvoke(_state(DENIED), config)
        snapshot = compiled.get_state(config)

    assert executed.await_count == 0
    assert "__interrupt__" not in result
    assert result["status"] == "failed"
    assert result["hitl_trigger"] == ""
    assert result["grant_denial"]["reason"] == "tool_not_granted"
    assert snapshot.next == ()


async def _run_turn(runner: Any, run_grant: RunGrant, thread_id: str) -> dict[str, Any]:
    return await runner.run_agent(
        agent_id=AGENT,
        agent_type="analyst",
        domain="ops",
        tenant_id=TENANT,
        system_prompt="scripted",
        authorized_tools=["gmail:send_email"],
        task_input={"action": "process"},
        confidence_floor=0.0,
        connector_config={},
        connector_names=["gmail"],
        thread_id=thread_id,
        run_grant=run_grant,
    )


async def test_a_reused_thread_does_not_inherit_an_earlier_turns_denial(scripted_model):
    from core.langgraph import runner

    scripted_model(
        [
            tool_call("gmail__send_email", to="ap@example.com"),
            tool_call("gmail__send_email", to="ap@example.com", call_id="call-turn-2"),
            final({"status": "completed", "confidence": 0.95}),
        ]
    )
    executed = AsyncMock(return_value={"status": "sent"})
    thread_id = f"voice:{uuid.uuid4().hex}"
    with (
        patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)),
        patch("core.billing.metering.meter_agent_run", AsyncMock()),
        patch("core.database.get_tenant_session", MagicMock(side_effect=RuntimeError("no database in unit tests"))),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
        patch.object(runner, "generate_explanation", AsyncMock(return_value={})),
        patch.object(runner, "_checkpointer", MemorySaver()),
        patch("core.langgraph.tool_adapter._execute_connector_tool", executed),
        patch("core.langgraph.agent_graph.get_grantex_client", return_value=_enforcer()),
    ):
        first = await _run_turn(runner, RunGrant(mode=EnforcementMode.DENY, token=DENIED, source="minted"), thread_id)
        second = await _run_turn(runner, RunGrant(mode=EnforcementMode.DENY, token=ALLOWED, source="minted"), thread_id)

    assert first["status"] == "failed" and first["grant_denial"]["reason"] == "tool_not_granted"
    assert second["status"] == "completed"
    assert "grant_denial" not in second
    assert executed.await_count == 1


async def test_resume_clears_any_denial_on_the_thread():
    from core.langgraph import runner

    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, command, config=None):
            captured["command"] = command
            return {"status": "completed", "messages": []}

    graph = MagicMock()
    graph.compile.return_value = _Compiled()
    grant = RunGrant(mode=EnforcementMode.DENY, token=ALLOWED, source="minted")
    with (
        patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
    ):
        await runner.resume_agent(
            agent_id=AGENT,
            thread_id="t-1",
            decision={"action": "approve"},
            system_prompt="scripted",
            authorized_tools=[],
            tenant_id=TENANT,
            run_grant=grant,
        )
    assert captured["command"].update == {"grant_token": ALLOWED, "grant_denial": {}}
