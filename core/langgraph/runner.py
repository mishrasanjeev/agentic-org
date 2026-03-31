"""Agent runner — compile and execute LangGraph agent graphs.

This is the public API that the FastAPI endpoint calls to run an agent.
It handles:
  - Building the graph from agent config
  - Setting up PostgreSQL checkpointing
  - Registering the agent on Grantex if needed
  - Executing with proper state initialization
  - Returning results compatible with the existing TaskResult schema
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from core.langgraph.agent_graph import build_agent_graph
from core.langgraph.state import AgentState

logger = structlog.get_logger()

# In-memory checkpointer for now — will switch to PostgreSQL in production
_checkpointer = MemorySaver()


async def run_agent(
    agent_id: str,
    agent_type: str,
    domain: str,
    tenant_id: str,
    system_prompt: str,
    authorized_tools: list[str],
    task_input: dict[str, Any],
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    grant_token: str = "",
    connector_config: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run a LangGraph agent and return the result.

    This is the main entry point called by the API endpoint.
    It replaces the old BaseAgent.execute() method.

    Args:
        agent_id: Platform agent UUID.
        agent_type: Agent type key (e.g., "ap_processor").
        domain: Business domain.
        tenant_id: Tenant UUID for multi-tenant isolation.
        system_prompt: The agent's full system prompt.
        authorized_tools: List of tool names the agent can call.
        task_input: The task payload (action, inputs, context).
        llm_model: LLM model override.
        confidence_floor: Minimum confidence before HITL.
        hitl_condition: Additional HITL condition expression.
        grant_token: Grantex grant JWT for authorization.
        connector_config: Config for connectors (auth, secrets).
        thread_id: Conversation thread ID for checkpointing.

    Returns:
        Dict with: status, output, confidence, reasoning_trace,
        tool_calls_log, hitl_trigger, error.
    """
    # Build the graph
    graph = build_agent_graph(
        system_prompt=system_prompt,
        authorized_tools=authorized_tools,
        llm_model=llm_model,
        confidence_floor=confidence_floor,
        hitl_condition=hitl_condition,
        connector_config=connector_config,
    )

    # Compile with checkpointer
    compiled = graph.compile(checkpointer=_checkpointer)

    # Build initial state
    user_message = _build_user_message(task_input)
    initial_state: AgentState = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ],
        "agent_id": agent_id,
        "agent_type": agent_type,
        "domain": domain,
        "tenant_id": tenant_id,
        "grant_token": grant_token,
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }

    # Config for checkpointing
    config = {
        "configurable": {
            "thread_id": thread_id or f"{agent_id}:{uuid.uuid4().hex[:8]}",
        }
    }

    # Execute the graph
    try:
        result = await compiled.ainvoke(initial_state, config=config)

        return {
            "status": result.get("status", "completed"),
            "output": result.get("output", {}),
            "confidence": result.get("confidence", 0.0),
            "reasoning_trace": result.get("reasoning_trace", []),
            "tool_calls_log": result.get("tool_calls_log", []),
            "hitl_trigger": result.get("hitl_trigger", ""),
            "error": result.get("error", ""),
        }

    except Exception as e:
        logger.error("langgraph_agent_failed", agent_id=agent_id, error=str(e))
        return {
            "status": "failed",
            "output": {},
            "confidence": 0.0,
            "reasoning_trace": [f"Agent execution failed: {type(e).__name__}"],
            "tool_calls_log": [],
            "hitl_trigger": "",
            "error": str(e),
        }


async def resume_agent(
    agent_id: str,
    thread_id: str,
    decision: dict[str, Any],
    system_prompt: str,
    authorized_tools: list[str],
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    connector_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a paused agent after HITL decision.

    Uses LangGraph's Command(resume=...) to continue from the
    interrupt point with the human's decision.
    """
    from langgraph.types import Command

    graph = build_agent_graph(
        system_prompt=system_prompt,
        authorized_tools=authorized_tools,
        llm_model=llm_model,
        confidence_floor=confidence_floor,
        hitl_condition=hitl_condition,
        connector_config=connector_config,
    )
    compiled = graph.compile(checkpointer=_checkpointer)

    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await compiled.ainvoke(
            Command(resume=decision),
            config=config,
        )
        return {
            "status": result.get("status", "completed"),
            "output": result.get("output", {}),
            "confidence": result.get("confidence", 0.0),
            "reasoning_trace": result.get("reasoning_trace", []),
        }
    except Exception as e:
        logger.error("langgraph_resume_failed", agent_id=agent_id, error=str(e))
        return {"status": "failed", "error": str(e)}


def _build_user_message(task_input: dict[str, Any]) -> str:
    """Build the user message from task input."""
    import json

    action = task_input.get("action", "process")
    inputs = task_input.get("inputs", {})
    context = task_input.get("context", {})

    parts = [f"Action: {action}"]
    if inputs:
        parts.append(f"Inputs: {json.dumps(inputs, default=str)}")
    if context:
        parts.append(f"Context: {json.dumps(context, default=str)}")

    return "\n".join(parts)
