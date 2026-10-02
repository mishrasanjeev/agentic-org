# SPDX-License-Identifier: Apache-2.0
"""Every reasoning turn of an agent run is admitted under the per-model limits and gives its slot back."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant
from core.governance import model_gateway as gw
from core.governance.model_gateway_limits import Lease
from core.test_doubles.scripted_model import final, tool_call

TENANT = str(uuid.UUID(int=0x1F5B))
AGENT = str(uuid.UUID(int=0xC2))


def _state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="send the reminder")],
        "agent_id": AGENT,
        "agent_type": "analyst",
        "domain": "ops",
        "tenant_id": TENANT,
        "grant_token": "",
        "grant_denial": {},
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _run_two_turns(scripted_model) -> None:
    from core.langgraph.agent_graph import build_agent_graph

    scripted_model([tool_call("gmail__send_email", to="ap@example.com"), final({"status": "sent"})])
    with patch("core.langgraph.tool_adapter._execute_connector_tool", AsyncMock(return_value={"status": "sent"})):
        graph = build_agent_graph(
            system_prompt="scripted",
            authorized_tools=["gmail:send_email"],
            connector_config={},
            connector_names=["gmail"],
            confidence_floor=0.5,
            run_grant=RunGrant(mode=EnforcementMode.OFF, token="", source="minted"),
        )
        compiled = graph.compile(checkpointer=MemorySaver())
        await compiled.ainvoke(_state(), {"configurable": {"thread_id": "turns-1"}})


async def test_each_turn_is_admitted_against_the_bound_route_and_released(scripted_model):
    decision = gw.RouteDecision(
        provider="gemini",
        model="gemini-2.5-flash",
        correlation_id="c-turns",
        reason="p",
        gated=True,
        tenant_id=TENANT,
        use_case="agent_run",
    )
    leases = [Lease(lease_id="l1", keys=("k",)), Lease(lease_id="l2", keys=("k",))]
    admit = AsyncMock(side_effect=list(leases))
    release = AsyncMock()
    token = gw.bind_route(decision, use_case="agent_run", agent_id=AGENT)
    try:
        with (
            patch("core.langgraph.agent_graph.gateway_admit", admit),
            patch("core.langgraph.agent_graph.gateway_release", release),
        ):
            await _run_two_turns(scripted_model)
    finally:
        gw.reset_route(token)
    assert admit.await_count == 2 and all(call.args[0] is decision for call in admit.await_args_list)
    assert [call.args[0] for call in release.await_args_list] == leases


async def test_without_a_bound_route_no_turn_is_admitted(scripted_model):
    admit = AsyncMock()
    release = AsyncMock()
    with (
        patch("core.langgraph.agent_graph.gateway_admit", admit),
        patch("core.langgraph.agent_graph.gateway_release", release),
    ):
        await _run_two_turns(scripted_model)
    admit.assert_not_called()
    assert [call.args[0] for call in release.await_args_list] == [None, None]
