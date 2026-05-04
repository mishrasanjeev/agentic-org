"""LangGraph agent graph builder.

Builds a compiled StateGraph for any agent type:

    START -> reason -> (tool_calls? -> execute_tools -> reason) -> evaluate -> (HITL | END)

The graph supports:
  - Multi-model LLM (Gemini/Claude/GPT via LangChain)
  - Tool calling via 54 connectors wrapped as LangChain tools
  - HITL interruption via LangGraph interrupt()
  - Checkpointed state for pause/resume
  - Confidence scoring and escalation
"""

from __future__ import annotations

import ast
import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from core.langgraph.grantex_auth import get_grantex_client
from core.langgraph.llm_factory import create_chat_model
from core.langgraph.state import AgentState
from core.langgraph.tool_adapter import _build_tool_index, build_tools_for_agent

logger = structlog.get_logger()


# ----------------------------------------------------------------------
# Issue #450 — structured tool-result classification.
#
# The previous heuristic (``"error" in msg_content.lower() or "failed"
# in msg_content.lower()``) was a substring scan against the entire
# ToolMessage content. That misclassified clean tool responses as
# failures any time the response carried the word "error" or "failed"
# anywhere — for example, Zoho responses with empty ``validation_errors``
# fields, AR shadow samples on a tenant with zero overdue invoices,
# or any data containing those terms in normal field names. The cap
# then dropped confidence to 0.5 → 0.24 floor → BUG-11 stayed open
# for GST/FP&A/AR even though the tool actually executed correctly.
#
# The fix below replaces the substring scan with structured signals
# that match how tools actually fail in this stack:
#
#   1. Tool raised an exception → ToolNode wraps the message with
#      ``Error: <ExceptionClassName>(...)`` (LangGraph convention).
#      That exact prefix or a Python traceback marker is a real
#      failure.
#   2. Tool returned a dict with explicit ``status="error"`` or a
#      truthy ``error`` field → the connector author's signal that
#      the call failed.
#   3. Empty data structures (``[]``, ``{}``) are SUCCESS — the tool
#      executed correctly and the answer is "no data". The agent
#      should learn from that, not be penalized.
# ----------------------------------------------------------------------


_EXPLICIT_ERROR_PREFIXES = (
    "Error: ",
    # LangGraph ToolNode emits these for invocation/argument-validation
    # failures BEFORE the tool body runs — "Error invoking tool X with
    # error: …" / "Error executing tool …". Codex P1 on PR #452: the
    # initial prefix list missed these and would have classified
    # ToolInvocationError as success, inflating shadow scoring on bad
    # arg shapes.
    "Error invoking tool",
    "Error executing tool",
    "Error in tool call",
    "Exception: ",
    "Traceback (most recent call last):",
    # langchain-core
    "ToolException",
    "ToolInvocationError",
    # pydantic — surfaces when an LLM-supplied arg fails schema validation
    "ValidationError",
    # httpx / aiohttp / asyncpg surface these directly
    "HTTPStatusError",
    "ClientError",
    "ConnectError",
)


def _tool_message_indicates_failure(msg_content: str | None) -> bool:
    """Decide whether a ToolMessage represents a failed tool call.

    Returns ``True`` only when the content carries an explicit error
    signal — exception wrapper from LangGraph's ToolNode, or a
    structured-result dict whose author marked it as an error. Empty
    / no-data results return ``False`` (those are successful tool
    runs).

    Issue #450: the prior substring-scan heuristic ("error" / "failed"
    anywhere in the content) caused brittle false positives that
    capped confidence on legitimately empty responses.
    """
    if not isinstance(msg_content, str) or not msg_content.strip():
        # Treat truly empty content as suspicious — a tool that
        # returned literally nothing didn't communicate a result.
        return True

    stripped = msg_content.strip()

    # 1) Exception wrapper from ToolNode / Python traceback.
    if any(stripped.startswith(prefix) for prefix in _EXPLICIT_ERROR_PREFIXES):
        return True

    # 2) Structured-result dict (JSON or Python repr). Try both
    # parsers — connectors return Python dicts that ToolNode str()'s.
    parsed: Any = None
    if stripped.startswith(("{", "[")):
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(stripped)
            except (ValueError, SyntaxError):
                continue
            else:
                break

    if isinstance(parsed, dict):
        status = parsed.get("status")
        if isinstance(status, str) and status.lower() == "error":
            return True
        # ``error`` field with a truthy non-empty value (string, dict,
        # non-empty list). Booleans / 0 / None do not count.
        err = parsed.get("error")
        if isinstance(err, str) and err.strip():
            return True
        if isinstance(err, dict) and err:
            return True
        if isinstance(err, list) and err:
            return True

    # 3) Anything else is success — including empty lists, empty
    # dicts, and rich responses that happen to contain the substring
    # "error" or "failed" in normal field names.
    return False


async def validate_tool_scopes(state: AgentState) -> dict[str, Any]:
    """Enforce Grantex scopes before tool execution.

    Uses grantex.enforce() which:
    1. Verifies the grant token JWT offline (JWKS cached, <1ms)
    2. Looks up the tool's required permission from loaded manifests
    3. Checks if the granted scope level covers the required permission

    No online API calls — enforce() validates the JWT signature locally
    using the cached JWKS key set.
    """
    messages = state["messages"]
    grant_token = state.get("grant_token", "")
    if not grant_token:
        return {}  # No Grantex token — legacy auth mode, no-op

    if not messages:
        return {}
    last_ai = messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}

    grantex = get_grantex_client()

    # _build_tool_index() returns dict[str, tuple[str, str]]
    # where each value is (connector_name, description)
    index = _build_tool_index()

    for tc in last_ai.tool_calls:
        # tc is normally a dict with 'name'/'args'/'id'; handle legacy
        # tuple/object shapes defensively so scope validation can't crash
        # the whole graph on a provider quirk.
        if isinstance(tc, dict):
            tool_name = tc.get("name", "")
        else:
            tool_name = getattr(tc, "name", "")
        if not tool_name:
            continue

        # Resolve connector name from tool index
        match = index.get(tool_name)
        connector_name = match[0] if match else "unknown"

        # One call — Grantex handles JWT verification + manifest lookup + permission check
        result = grantex.enforce(
            grant_token=grant_token,
            connector=connector_name,
            tool=tool_name,
        )

        if not result.allowed:
            logger.warning(
                "scope_enforcement_denied",
                agent_id=state.get("agent_id"),
                tool=tool_name,
                connector=connector_name,
                reason=result.reason,
            )
            return {
                "messages": [AIMessage(
                    content=f"Access denied: {result.reason}. "
                    f"Tool '{tool_name}' on '{connector_name}' is not permitted by your current authorization."
                )],
                "status": "failed",
                "error": f"Scope denied: {result.reason}",
            }

    return {}  # All tool calls approved


def build_agent_graph(
    system_prompt: str,
    authorized_tools: list[str],
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
) -> StateGraph:
    """Build a compiled LangGraph agent graph.

    Args:
        system_prompt: The agent's system prompt (from SOP or template).
        authorized_tools: List of tool names the agent can use.
        llm_model: LLM model to use (default: Gemini Flash).
        confidence_floor: Minimum confidence before HITL triggers.
        hitl_condition: Additional HITL condition expression.
        connector_config: Config dict passed to connectors for auth/secrets.
        connector_names: BUG-08 fail-closed allow-list. When the runtime
            has resolved the agent's ``connector_ids`` it passes the
            resolved names here so ``list_invoices`` only matches the
            agent's authorized connectors instead of falling through to
            any globally-registered connector with the same tool name.

    Returns:
        A compiled LangGraph graph ready for invocation.
    """
    # Build LangChain tools from authorized tools
    tools = build_tools_for_agent(authorized_tools, connector_config, connector_names)

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
        tool_calls_log = list(state.get("tool_calls_log") or [])

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
                "tool_calls_log": tool_calls_log,
            }

        # Parse output — _parse_json_output guarantees a dict, but be
        # defensive for any future caller that builds output differently.
        content = last_ai.content or ""
        output = _parse_json_output(content)
        if not isinstance(output, dict):
            output = {"raw_output": output, "status": "completed"}

        # Compute variable confidence from observable signals (not a fixed default)
        # Signals: tool success rate, output structure, output length, error presence
        any_tool_failed = any(
            (isinstance(entry, dict) and entry.get("status") == "error")
            for entry in tool_calls_log
        )
        from langchain_core.messages import ToolMessage
        tool_msg_count = 0
        tool_error_count = 0
        # BUG-11 follow-up (2026-05-02): build tool_calls_log from the
        # message stream. LangGraph's prebuilt ``ToolNode`` puts tool
        # results into ``state["messages"]`` as ``ToolMessage`` objects
        # — no other code path ever appended to ``tool_calls_log``, so
        # the API response field stayed empty even when tools fired
        # successfully. Pair each ToolMessage (the result) with its
        # invoking AIMessage tool_call (the args) so the response
        # carries both the call and the outcome.
        ai_tool_calls_by_id: dict[str, dict[str, Any]] = {}
        for msg in messages:
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    raw_id = tc.get("id")
                    if isinstance(raw_id, str) and raw_id:
                        ai_tool_calls_by_id[raw_id] = tc
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_msg_count += 1
                msg_content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Issue #450: structured failure detection. See
                # ``_tool_message_indicates_failure`` docstring. Replaces
                # the prior substring scan that misclassified empty
                # responses (and any field literally named "error_*")
                # as failures, capping confidence on tools that actually
                # ran correctly.
                is_error = _tool_message_indicates_failure(msg_content)
                if is_error:
                    tool_error_count += 1
                    any_tool_failed = True
                tc_id: str | None = getattr(msg, "tool_call_id", None)
                source = ai_tool_calls_by_id.get(tc_id, {}) if tc_id else {}
                tool_calls_log.append(
                    {
                        "tool": getattr(msg, "name", None) or source.get("name", ""),
                        "args": source.get("args", {}),
                        "tool_call_id": tc_id,
                        "status": "error" if is_error else "success",
                        # Cap result body so a chatty connector response
                        # doesn't bloat the agent run record. Operators
                        # who need the full body get it from server logs.
                        "result": (msg_content or "")[:2000],
                    }
                )

        output_incomplete = (
            not output
            or output.get("status") == "error"
        )

        # Use LLM-reported confidence if present, otherwise compute from signals
        confidence = _extract_confidence(output, content_length=len(content))

        # Adjust based on tool execution signals
        if tool_msg_count > 0:
            tool_success_rate = 1.0 - (tool_error_count / tool_msg_count)
            # Weight: 60% LLM confidence, 40% tool success rate
            confidence = (confidence * 0.6) + (tool_success_rate * 0.4)

        # Hard caps for failures
        if any_tool_failed:
            confidence = min(confidence, 0.5)
            trace.append("Confidence capped to 0.5 (tool_call_failed)")
        elif output_incomplete:
            confidence = min(confidence, 0.5)
            trace.append("Confidence capped to 0.5 (output_incomplete)")

        trace.append(f"Confidence: {confidence:.3f}")

        return {
            "output": output,
            "confidence": confidence,
            "status": "completed",
            "reasoning_trace": trace,
            "tool_calls_log": tool_calls_log,
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

        # LangGraph interrupt — pauses execution until human resumes.
        # When interrupt() raises GraphInterrupt, the runner catches it and
        # extracts hitl_trigger from the interrupt payload below.
        decision = interrupt({
            "type": "hitl_approval",
            "trigger": trigger,
            "hitl_trigger": trigger,
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

    # --- Scope validation routing ---

    def scopes_passed(state: AgentState) -> str:
        """Route to execute_tools if scopes OK, else to evaluate (with error)."""
        if state.get("status") == "failed":
            return "evaluate"  # skip tools, go to evaluate which will surface the error
        return "execute_tools"

    graph.add_edge(START, "reason")

    if tools:
        graph.add_node("validate_scopes", validate_tool_scopes)
        graph.add_conditional_edges("reason", should_use_tools, {
            "execute_tools": "validate_scopes",
            "evaluate": "evaluate",
        })
        graph.add_conditional_edges("validate_scopes", scopes_passed, {
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


def _parse_json_output(content: str | list | Any) -> dict[str, Any]:
    """Parse JSON from LLM output, handling markdown code blocks.

    Always returns a dict. If the LLM emitted a valid JSON array or
    scalar (instead of an object), wrap it in ``raw_output`` so every
    downstream call to ``output.get(...)`` is safe. The previous
    version returned whatever ``json.loads`` produced, so a JSON list
    crashed the graph with ``AttributeError: 'list' object has no
    attribute 'get'`` in evaluate/_extract_confidence.
    """
    # Handle list content (multiple messages) — join into single string
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content)
    if not isinstance(content, str):
        content = str(content)
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_output": content, "status": "completed"}
    if isinstance(parsed, dict):
        return parsed
    # Valid JSON but not an object — wrap to preserve our dict contract.
    return {"raw_output": parsed, "status": "completed"}


def _extract_confidence(output: dict[str, Any], content_length: int = 0) -> float:
    """Extract or compute confidence score.

    Priority:
    1. LLM self-reported numeric confidence (0.0-1.0)
    2. LLM categorical confidence (high/medium/low)
    3. Computed from structural signals: output completeness, length, fields

    Never returns a hardcoded default — confidence varies based on real signals.
    """
    if not isinstance(output, dict):
        output = {}
    raw = output.get("confidence") or output.get("agent_confidence")
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw)))
        except (ValueError, TypeError):
            mapping = {"high": 0.95, "medium": 0.75, "low": 0.5}
            mapped = mapping.get(str(raw).lower().strip())
            if mapped is not None:
                return mapped

    # Compute confidence from structural signals
    # Base: 0.6 (neutral)
    confidence = 0.6

    # Bonus for structured output (JSON parsed successfully, multiple fields)
    if isinstance(output, dict) and len(output) > 0 and "raw_output" not in output:
        field_count = len(output)
        confidence += min(0.20, field_count * 0.04)  # +0.04 per field, cap at +0.20

    # Bonus for substantial content length (longer = more thorough)
    if content_length > 500:
        confidence += 0.10
    elif content_length > 100:
        confidence += 0.05

    # Penalty for empty/very short output
    if content_length < 20:
        confidence -= 0.20

    return max(0.0, min(1.0, round(confidence, 3)))


def _check_hitl_trigger(
    confidence: float,
    confidence_floor: float,
    hitl_condition: str,
    output: dict[str, Any],
) -> str:
    """Check if HITL should be triggered. Returns trigger reason or empty string."""
    if confidence < confidence_floor:
        return f"confidence {confidence:.3f} < floor {confidence_floor}"

    # Defensive: every caller passes state.get("output", {}), but a
    # downstream node could legally store a non-dict here (shadow run
    # trace_id=ecc5d00364a0 hit this).
    if not isinstance(output, dict):
        output = {}

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
        except Exception:  # noqa: S110
            pass  # HITL condition eval is best-effort; silent failure is intentional

    return ""
