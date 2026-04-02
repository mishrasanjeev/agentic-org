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

import time
import uuid
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt

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
    t0 = time.perf_counter()
    try:
        result = await compiled.ainvoke(initial_state, config=config)  # type: ignore[call-overload]
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Extract token usage from AI messages
        tokens_used = 0
        for msg in result.get("messages", []):
            if isinstance(msg, AIMessage):
                usage = getattr(msg, "usage_metadata", None)
                if usage:
                    tokens_used += getattr(usage, "total_tokens", 0) or (
                        (getattr(usage, "input_tokens", 0) or 0)
                        + (getattr(usage, "output_tokens", 0) or 0)
                    )

        # Estimate cost (Gemini 2.5 Flash pricing: $0.15/1M input, $0.60/1M output)
        cost_usd = round(tokens_used * 0.000375 / 1000, 6) if tokens_used else 0

        return {
            "status": result.get("status", "completed"),
            "output": result.get("output", {}),
            "confidence": result.get("confidence", 0.0),
            "reasoning_trace": result.get("reasoning_trace", []),
            "tool_calls_log": result.get("tool_calls_log", []),
            "hitl_trigger": result.get("hitl_trigger", ""),
            "error": result.get("error", ""),
            "performance": {
                "total_latency_ms": latency_ms,
                "llm_tokens_used": tokens_used,
                "llm_cost_usd": cost_usd,
            },
        }

    except GraphInterrupt as gi:
        # LangGraph interrupt() raises GraphInterrupt when HITL pauses the graph.
        # Retrieve the latest checkpoint state so we can extract hitl_trigger,
        # confidence, output, etc. that were set before the interrupt.
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("langgraph_hitl_interrupted", agent_id=agent_id)

        # Get the interrupted state from the checkpoint
        try:
            snapshot = compiled.get_state(config)
            state_values = snapshot.values if snapshot else {}
        except Exception:
            state_values = {}

        hitl_trigger = state_values.get("hitl_trigger", "")
        # If we still don't have hitl_trigger, extract from the interrupt payload
        if not hitl_trigger and gi.args:
            for interruption in gi.args:
                if isinstance(interruption, dict):
                    hitl_trigger = interruption.get("hitl_trigger") or interruption.get("trigger", "")
                    if hitl_trigger:
                        break

        return {
            "status": state_values.get("status", "hitl_triggered"),
            "output": state_values.get("output", {}),
            "confidence": state_values.get("confidence", 0.0),
            "reasoning_trace": state_values.get("reasoning_trace", []),
            "tool_calls_log": state_values.get("tool_calls_log", []),
            "hitl_trigger": hitl_trigger,
            "error": "",
            "thread_id": config["configurable"]["thread_id"],
            "performance": {
                "total_latency_ms": latency_ms,
                "llm_tokens_used": 0,
                "llm_cost_usd": 0,
            },
        }

    except Exception as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.error("langgraph_agent_failed", agent_id=agent_id, error=str(e))
        return {
            "status": "failed",
            "output": {},
            "confidence": 0.0,
            "reasoning_trace": [f"Agent execution failed: {type(e).__name__}"],
            "tool_calls_log": [],
            "hitl_trigger": "",
            "error": str(e),
            "performance": {
                "total_latency_ms": latency_ms,
                "llm_tokens_used": 0,
                "llm_cost_usd": 0,
            },
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
        result = await compiled.ainvoke(  # type: ignore[call-overload]
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
