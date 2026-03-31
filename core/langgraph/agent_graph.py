"""LangGraph agent graph builder.

Builds a compiled StateGraph for any agent type:

    START -> reason -> (tool_calls? -> execute_tools -> reason) -> evaluate -> (HITL | END)

The graph supports:
  - Multi-model LLM (Gemini/Claude/GPT via LangChain)
  - Tool calling via 42 connectors wrapped as LangChain tools
  - HITL interruption via LangGraph interrupt()
  - Checkpointed state for pause/resume
  - Confidence scoring and escalation
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from core.langgraph.llm_factory import create_chat_model
from core.langgraph.state import AgentState
from core.langgraph.tool_adapter import build_tools_for_agent

logger = structlog.get_logger()


def build_agent_graph(
    system_prompt: str,
    authorized_tools: list[str],
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    connector_config: dict[str, Any] | None = None,
) -> StateGraph:
    """Build a compiled LangGraph agent graph.

    Args:
        system_prompt: The agent's system prompt (from SOP or template).
        authorized_tools: List of tool names the agent can use.
        llm_model: LLM model to use (default: Gemini Flash).
        confidence_floor: Minimum confidence before HITL triggers.
        hitl_condition: Additional HITL condition expression.
        connector_config: Config dict passed to connectors for auth/secrets.

    Returns:
        A compiled LangGraph graph ready for invocation.
    """
    # Build LangChain tools from authorized tools
    tools = build_tools_for_agent(authorized_tools, connector_config)

    # LLM is created lazily on first call to avoid API key validation at build time
    _llm_cache: dict[str, Any] = {}

    def _get_llm():
        if "instance" not in _llm_cache:
            llm = create_chat_model(model=llm_model)
            _llm_cache["instance"] = llm.bind_tools(tools) if tools else llm
        return _llm_cache["instance"]

    # --- Node functions ---

    async def reason(state: AgentState) -> dict[str, Any]:
        """Call the LLM with current messages to reason about the task."""
        messages = state["messages"]
        trace = list(state.get("reasoning_trace") or [])

        # Ensure system prompt is the first message
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt), *messages]

        trace.append(f"Calling LLM ({llm_model or 'default'})")
        response = await _get_llm().ainvoke(messages)
        trace.append(f"LLM responded ({type(response).__name__})")

        return {"messages": [response], "reasoning_trace": trace}

    async def evaluate(state: AgentState) -> dict[str, Any]:
        """Extract structured output and compute confidence from the last AI message."""
        messages = state["messages"]
        trace = list(state.get("reasoning_trace") or [])

        # Find the last AI message
        last_ai = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                last_ai = msg
                break

        if not last_ai:
            return {
                "status": "failed",
                "error": "No AI response received",
                "reasoning_trace": [*trace, "ERROR: No AI message found"],
            }

        # Parse output
        content = last_ai.content or ""
        output = _parse_json_output(content)
        confidence = _extract_confidence(output)
        trace.append(f"Confidence: {confidence:.3f}")

        return {
            "output": output,
            "confidence": confidence,
            "status": "completed",
            "reasoning_trace": trace,
        }

    async def hitl_gate(state: AgentState) -> dict[str, Any]:
        """Interrupt execution for human-in-the-loop approval."""
        confidence = state.get("confidence", 0.0)
        output = state.get("output", {})
        trace = list(state.get("reasoning_trace") or [])

        trigger = _check_hitl_trigger(confidence, confidence_floor, hitl_condition, output)
        if not trigger:
            return {"hitl_trigger": ""}

        trace.append(f"HITL triggered: {trigger}")

        # LangGraph interrupt — pauses execution until human resumes
        decision = interrupt({
            "type": "hitl_approval",
            "trigger": trigger,
            "confidence": confidence,
            "output": output,
            "agent_id": state.get("agent_id", ""),
            "agent_type": state.get("agent_type", ""),
        })

        # Human resumed with a decision
        trace.append(f"HITL decision: {decision}")

        if isinstance(decision, dict) and decision.get("action") == "reject":
            return {
                "status": "failed",
                "hitl_trigger": trigger,
                "error": f"Rejected by human: {decision.get('reason', '')}",
                "reasoning_trace": trace,
            }

        return {
            "hitl_trigger": trigger,
            "reasoning_trace": trace,
        }

    # --- Routing functions ---

    def should_use_tools(state: AgentState) -> str:
        """Route to tools if the LLM requested tool calls, else to evaluate."""
        messages = state["messages"]
        last = messages[-1] if messages else None
        if isinstance(last, AIMessage) and last.tool_calls:
            return "execute_tools"
        return "evaluate"

    def should_escalate(state: AgentState) -> str:
        """Route to HITL if confidence is below floor, else to END."""
        confidence = state.get("confidence", 1.0)
        output = state.get("output", {})
        trigger = _check_hitl_trigger(confidence, confidence_floor, hitl_condition, output)
        if trigger:
            return "hitl_gate"
        return END

    # --- Build the graph ---

    graph = StateGraph(AgentState)

    graph.add_node("reason", reason)
    if tools:
        graph.add_node("execute_tools", ToolNode(tools))
    graph.add_node("evaluate", evaluate)
    graph.add_node("hitl_gate", hitl_gate)

    graph.add_edge(START, "reason")

    if tools:
        graph.add_conditional_edges("reason", should_use_tools, {
            "execute_tools": "execute_tools",
            "evaluate": "evaluate",
        })
        graph.add_edge("execute_tools", "reason")
    else:
        graph.add_edge("reason", "evaluate")

    graph.add_conditional_edges("evaluate", should_escalate, {
        "hitl_gate": "hitl_gate",
        END: END,
    })
    graph.add_edge("hitl_gate", END)

    return graph


# --- Helper functions ---


def _parse_json_output(content: str) -> dict[str, Any]:
    """Parse JSON from LLM output, handling markdown code blocks."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_output": content, "status": "completed"}


def _extract_confidence(output: dict[str, Any]) -> float:
    """Extract confidence score from structured output."""
    raw = output.get("confidence", output.get("agent_confidence", 0.85))
    try:
        return max(0.0, min(1.0, float(raw)))
    except (ValueError, TypeError):
        mapping = {"high": 0.95, "medium": 0.75, "low": 0.5}
        return mapping.get(str(raw).lower().strip(), 0.85)


def _check_hitl_trigger(
    confidence: float,
    confidence_floor: float,
    hitl_condition: str,
    output: dict[str, Any],
) -> str:
    """Check if HITL should be triggered. Returns trigger reason or empty string."""
    if confidence < confidence_floor:
        return f"confidence {confidence:.3f} < floor {confidence_floor}"

    if hitl_condition and output:
        # Evaluate simple threshold expressions like "amount > 500000"
        try:
            import ast
            import operator

            ops = {
                ast.Gt: operator.gt, ast.Lt: operator.lt,
                ast.GtE: operator.ge, ast.LtE: operator.le,
                ast.Eq: operator.eq, ast.NotEq: operator.ne,
            }
            tree = ast.parse(hitl_condition, mode="eval")
            if isinstance(tree.body, ast.Compare) and len(tree.body.comparators) == 1:
                left_name = getattr(tree.body.left, "id", "")
                left_val = output.get(left_name, 0)
                right_node = tree.body.comparators[0]
                right_val = getattr(right_node, "value", getattr(right_node, "n", 0))
                op_fn = ops.get(type(tree.body.ops[0]))
                if op_fn and op_fn(float(left_val), float(right_val)):
                    return f"condition matched: {hitl_condition}"
        except Exception:
            pass  # HITL condition eval is best-effort

    return ""
