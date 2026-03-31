"""Typed state for LangGraph agent execution."""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """State shared across all nodes in the agent graph.

    Fields:
        messages: LangChain message history (system + human + AI + tool results).
            Uses add_messages reducer to append, not replace.
        agent_id: Platform agent ID (UUID string).
        agent_type: Agent type key (e.g., "ap_processor").
        domain: Business domain (finance, hr, marketing, ops, backoffice).
        tenant_id: Multi-tenant isolation key.
        grant_token: Grantex grant JWT for scope/budget enforcement.
        confidence: Agent's self-assessed confidence (0.0-1.0).
        status: Execution status (running, completed, failed, hitl_triggered).
        output: Structured output from the agent.
        reasoning_trace: Human-readable execution trace.
        tool_calls_log: Record of tool calls made (connector, tool, latency, status).
        hitl_trigger: If set, the condition that triggered HITL escalation.
        error: Error details if status is "failed".
    """

    messages: Annotated[list, add_messages]
    agent_id: str
    agent_type: str
    domain: str
    tenant_id: str
    grant_token: str
    confidence: float
    status: str
    output: dict[str, Any]
    reasoning_trace: list[str]
    tool_calls_log: list[dict[str, Any]]
    hitl_trigger: str
    error: str
