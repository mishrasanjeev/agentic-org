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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from auth.grant_enforcement import EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant, refresh_run_grant
from core.governance.action_policy import ActionDomain, CapabilityAuthorization
from core.langgraph.grantex_auth import get_grantex_client
from core.langgraph.llm_factory import (
    create_chat_model,
    snapshot_prefetched_llm_credentials,
    use_prefetched_llm_credentials,
)
from core.langgraph.state import AgentState
from core.langgraph.tool_adapter import (
    _actual_tool_name,
    _build_tool_index,
    build_tools_for_agent,
)

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


async def validate_tool_scopes(
    state: AgentState,
    tool_refs: Mapping[str, tuple[str, str]] | None = None,
    run_grant: RunGrant | None = None,
) -> dict[str, Any]:
    """Enforce Grantex scopes before tool execution.

    Uses grantex.enforce() which:
    1. Verifies the grant token JWT offline (JWKS cached, <1ms)
    2. Looks up the tool's required permission from loaded manifests
    3. Checks if the granted scope level covers the required permission

    No online API calls — enforce() validates the JWT signature locally
    using the cached JWKS key set.

    PRD F-1: the run's ``grants.enforce_closed`` mode (``run_grant.mode``)
    decides what a missing or insufficient grant means. ``off`` (or no
    ``run_grant``) is the legacy path below, unchanged. ``warn`` and ``deny``
    go through ``_enforce_tool_grants``, checking ``state["grant_token"]``.
    ``tool_refs`` maps registered tool names to ``(connector, tool)``.
    """
    if run_grant is not None and run_grant.mode is not EnforcementMode.OFF:
        return await _enforce_tool_grants(state, run_grant, tool_refs or {})

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
    index = _build_tool_index(include_connector_aliases=True)

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
        actual_tool_name = _actual_tool_name(tool_name)

        # One call — Grantex handles JWT verification + manifest lookup + permission check
        result = grantex.enforce(
            grant_token=grant_token,
            connector=connector_name,
            tool=actual_tool_name,
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
                "messages": [
                    AIMessage(
                        content=f"Access denied: {result.reason}. "
                        f"Tool '{actual_tool_name}' on '{connector_name}' is not permitted "
                        "by your current authorization."
                    )
                ],
                "status": "failed",
                "error": f"Scope denied: {result.reason}",
            }

    return {}  # All tool calls approved


async def _enforce_tool_grants(
    state: AgentState,
    run_grant: RunGrant,
    tool_refs: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    """Check every requested tool call against the run grant (warn / deny).

    Every call that the grant does not cover — no grant, invalid or revoked
    token, tool or permission not granted, enforcement unavailable — is
    recorded. In ``warn`` the calls then run — unless the token was supplied
    by the caller or configured on the agent, which the legacy path already
    enforced (``RunGrant.call_mode``); in ``deny`` the first such call stops
    the batch with its reason code.
    """
    messages = state["messages"]
    if not messages:
        return {}
    last_ai = messages[-1]
    if not isinstance(last_ai, AIMessage) or not last_ai.tool_calls:
        return {}

    context = GrantCallContext(
        tenant_id=str(state.get("tenant_id") or ""),
        agent_id=str(state.get("agent_id") or ""),
        agent_type=str(state.get("agent_type") or ""),
        runtime="langgraph",
        grant_source=run_grant.source,
    )
    index: dict[str, tuple[str, str]] | None = None

    for tc in last_ai.tool_calls:
        if isinstance(tc, dict):
            tool_name = tc.get("name", "")
            args = tc.get("args") or {}
        else:
            tool_name = getattr(tc, "name", "")
            args = getattr(tc, "args", None) or {}
        if not tool_name:
            continue

        ref = tool_refs.get(tool_name)
        if ref is None:
            if index is None:
                try:
                    index = _build_tool_index(include_connector_aliases=True)
                # enterprise-gate: broad-except-ok reason=index-failure-falls-back-to-unknown-connector-enforce-denies
                except Exception as exc:
                    logger.warning("grant_enforcement_tool_index_failed", error_type=type(exc).__name__)
                    index = {}
            match = index.get(tool_name)
            ref = (match[0] if match else "unknown", _actual_tool_name(tool_name))
        connector_name, actual_tool_name = ref

        amount = args.get("amount") if isinstance(args, dict) else None
        check = await check_run_grant(
            replace(run_grant, token=str(state.get("grant_token") or "")),
            connector=connector_name,
            tool=actual_tool_name,
            context=context,
            amount=amount if isinstance(amount, int | float) and not isinstance(amount, bool) else None,
            client_factory=get_grantex_client,
        )
        if check.dispatch_allowed or check.denial is None:
            continue

        reason = check.denial.reason.value
        return {
            "messages": [
                AIMessage(
                    content=f"Access denied ({reason}). Tool '{actual_tool_name}' on '{connector_name}' "
                    "is not permitted by this agent's grant."
                )
            ],
            "status": "failed",
            "error": f"grant_denied: {reason}",
        }

    return {}


def build_agent_graph(
    system_prompt: str,
    authorized_tools: list[str],
    llm_model: str = "",
    confidence_floor: float = 0.88,
    hitl_condition: str = "",
    connector_config: dict[str, Any] | None = None,
    connector_names: list[str] | None = None,
    tenant_id: str | None = None,
    company_id: str | None = None,
    domain: ActionDomain | str | None = None,
    capability_authorization: CapabilityAuthorization | None = None,
    pii_token_map: dict[str, str] | None = None,
    llm_provider: str | None = None,
    context_guard: Callable[[Sequence[Any]], None] | None = None,
    *,
    run_grant: RunGrant | None,
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
        llm_provider: Explicit catalog provider id pinned on the agent
            (``agents.llm_provider``, else ``llm_config["provider"]``).
            ``None`` keeps the legacy model-name inference for old rows.
        run_grant: Required. The run's resolved grant and
            ``grants.enforce_closed`` mode (``auth/run_grants.py``). Only tests
            of graph mechanics pass ``NO_RUN_GRANT_FOR_TESTS`` (``None``), which
            keeps the legacy scope validation.
        context_guard: Called with the full message list before every model
            call; raising stops the run before anything is sent. Governed case
            agents pass ``UntrustedTextRegistry.guard_messages`` so untrusted
            source text can never reach the model
            (``docs/security/untrusted-content.md``). ``None`` keeps the
            existing behaviour.

    Returns:
        A compiled LangGraph graph ready for invocation.
    """
    # Build LangChain tools from authorized tools
    tools = build_tools_for_agent(
        authorized_tools,
        connector_config,
        connector_names,
        tenant_id=tenant_id,
        company_id=company_id,
        domain=domain,
        capability_authorization=capability_authorization,
        pii_token_map=pii_token_map,
    )

    # Bug sheet #14 (2026-09-14): ``ToolNode`` dispatches by exact name. A
    # model that spells a registered ``gmail__send_email`` as
    # ``gmail.send_email`` / ``gmail:send_email`` (or the bare
    # ``send_email`` when that is unambiguous) got "is not a valid tool".
    # ``reason`` rewrites tool-call names through this alias map as soon as
    # the model answers, so scope validation, execution, the checkpoint and
    # the tool-call log all see the registered name. Names outside the map
    # still fail closed inside ToolNode.
    tool_aliases = _tool_call_alias_map(tools)

    # LLM is created lazily on first call to avoid API key validation at build time
    _llm_cache: dict[str, Any] = {}
    # The runner resolves the tenant-aware credential with ``await`` before
    # building the graph; the model itself is created later inside a node,
    # after that context is gone, so carry a snapshot into ``_get_llm``.
    prefetched_credentials = snapshot_prefetched_llm_credentials()

    def _get_llm():
        if "instance" not in _llm_cache:
            # Bug sheet 2026-09-14 #31/#38: the pinned provider and tenant must
            # reach the factory, otherwise ``o1-mini`` falls back to Gemini and
            # ``openai_compatible`` never resolves the tenant's base_url.
            with use_prefetched_llm_credentials(prefetched_credentials):
                llm = create_chat_model(model=llm_model, tenant_id=tenant_id or None, provider=llm_provider)
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

        if context_guard is not None:
            context_guard(messages)
        trace.append(f"Calling LLM ({llm_model or 'default'})")
        response = await _get_llm().ainvoke(messages)
        if isinstance(response, AIMessage) and response.tool_calls:
            response = _rewrite_tool_call_names(response, tool_aliases)
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
        any_tool_failed = any((isinstance(entry, dict) and entry.get("status") == "error") for entry in tool_calls_log)
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

        output_incomplete = not output or output.get("status") == "error"

        # Use LLM-reported confidence if present, otherwise compute from signals
        confidence = _extract_confidence(output, content_length=len(content))

        # Adjust based on tool execution signals
        if tool_msg_count > 0:
            tool_success_rate = 1.0 - (tool_error_count / tool_msg_count)
            # Weight: 60% LLM confidence, 40% tool success rate
            confidence = (confidence * 0.6) + (tool_success_rate * 0.4)
            try:
                from observability.metrics import tool_success_rate as tool_success_rate_metric

                tool_success_rate_metric.labels(agent_type=str(state.get("agent_type") or "unknown")).set(
                    tool_success_rate
                )
            except (RuntimeError, ValueError, TypeError):
                logger.debug("tool_success_rate_metric_update_failed")

        # Hard caps for failures
        if any_tool_failed:
            confidence = min(confidence, 0.5)
            trace.append("Confidence capped to 0.5 (tool_call_failed)")
        elif output_incomplete:
            confidence = min(confidence, 0.5)
            trace.append("Confidence capped to 0.5 (output_incomplete)")

        trace.append(f"Confidence: {confidence:.3f}")
        try:
            from observability.metrics import confidence_avg

            confidence_avg.labels(agent_type=str(state.get("agent_type") or "unknown")).set(confidence)
        except (RuntimeError, ValueError, TypeError):
            logger.debug("confidence_metric_update_failed")

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
        decision = interrupt(
            {
                "type": "hitl_approval",
                "trigger": trigger,
                "hitl_trigger": trigger,
                "confidence": confidence,
                "output": output,
                "agent_id": state.get("agent_id", ""),
                "agent_type": state.get("agent_type", ""),
            }
        )

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
        tool_refs = _tool_grant_refs(tools)
        grant_holder: list[RunGrant | None] = [run_grant]

        async def validate_scopes(state: AgentState) -> dict[str, Any]:
            current = grant_holder[0]
            update: dict[str, Any] = {}
            if current is not None and current.mode is not EnforcementMode.OFF:
                # Long runs: swap in a fresh pool grant before this one expires.
                refreshed = await refresh_run_grant(replace(current, token=str(state.get("grant_token") or "")))
                if refreshed.token != state.get("grant_token"):
                    update["grant_token"] = refreshed.token
                    state = {**state, "grant_token": refreshed.token}  # type: ignore[typeddict-item]
                grant_holder[0] = current = refreshed
            return {**update, **await validate_tool_scopes(state, tool_refs, current)}

        graph.add_node("validate_scopes", validate_scopes)
        graph.add_conditional_edges(
            "reason",
            should_use_tools,
            {
                "execute_tools": "validate_scopes",
                "evaluate": "evaluate",
            },
        )
        graph.add_conditional_edges(
            "validate_scopes",
            scopes_passed,
            {
                "execute_tools": "execute_tools",
                "evaluate": "evaluate",
            },
        )
        graph.add_edge("execute_tools", "reason")
    else:
        graph.add_edge("reason", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        should_escalate,
        {
            "hitl_gate": "hitl_gate",
            END: END,
        },
    )
    graph.add_edge("hitl_gate", END)

    return graph


# --- Helper functions ---


def _tool_grant_refs(tools: list[Any]) -> dict[str, tuple[str, str]]:
    """Registered tool name -> ``(connector, tool)`` from the adapter's metadata."""
    refs: dict[str, tuple[str, str]] = {}
    for tool in tools:
        meta = getattr(tool, "metadata", None) or {}
        connector = meta.get("connector") if isinstance(meta, dict) else None
        bare = meta.get("tool") if isinstance(meta, dict) else None
        name = str(getattr(tool, "name", ""))
        if name and isinstance(connector, str) and connector and isinstance(bare, str) and bare:
            refs[name] = (connector, bare)
    return refs


def _tool_call_alias_map(tools: list[Any]) -> dict[str, str]:
    """Map alternate tool-call spellings to the registered tool name.

    Bug sheet #14 (2026-09-14). For a tool registered as
    ``gmail__send_email`` (connector ``gmail``, tool ``send_email`` from
    the adapter's metadata) this yields ``gmail:send_email``,
    ``gmail.send_email`` and — only when no other tool claims it — the
    bare ``send_email``. Registered names always win over aliases and an
    alias claimed by two tools is dropped, so nothing dispatches
    ambiguously.
    """
    registered = {str(getattr(t, "name", "")) for t in tools}
    aliases: dict[str, str] = {}
    conflicts: set[str] = set()
    for tool in tools:
        name = str(getattr(tool, "name", ""))
        meta = getattr(tool, "metadata", None) or {}
        connector = meta.get("connector") if isinstance(meta, dict) else None
        bare = meta.get("tool") if isinstance(meta, dict) else None
        if not (connector and bare) and "__" in name:
            connector, bare = name.split("__", 1)
        if not (connector and bare):
            continue
        for alt in (f"{connector}:{bare}", f"{connector}.{bare}", f"{connector}__{bare}", bare):
            if alt == name or alt in registered:
                continue
            if alt in aliases and aliases[alt] != name:
                conflicts.add(alt)
            else:
                aliases[alt] = name
    for alt in conflicts:
        aliases.pop(alt, None)
    return aliases


def _rewrite_tool_call_names(message: AIMessage, aliases: dict[str, str]) -> AIMessage:
    """Return ``message`` with aliased tool-call names replaced by the registered name.

    Returns the same object when nothing changes. Anthropic-style
    ``tool_use`` content blocks are rewritten alongside ``tool_calls`` so
    the history sent back to the provider stays consistent.
    """
    if not aliases:
        return message
    renamed: dict[str, str] = {}
    new_calls: list[Any] = []
    for tc in message.tool_calls:
        if isinstance(tc, dict):
            requested = tc.get("name")
            target = aliases.get(requested) if isinstance(requested, str) else None
            if target and target != requested:
                logger.info("tool_call_name_aliased", requested=requested, resolved=target)
                tc = {**tc, "name": target}
                renamed[str(tc.get("id"))] = target
        new_calls.append(tc)
    if not renamed:
        return message
    content: Any = message.content
    if isinstance(content, list):
        content = [
            {**block, "name": renamed[str(block.get("id"))]}
            if isinstance(block, dict) and block.get("type") == "tool_use" and str(block.get("id")) in renamed
            else block
            for block in content
        ]
    return message.model_copy(update={"tool_calls": new_calls, "content": content})


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

    if hitl_condition:
        # Shared fail-closed evaluator: BoolOp/Compare/in on str, num, bool.
        # Parse failure or a missing referenced field triggers HITL.
        from core.langgraph.hitl_condition import evaluate_hitl_condition

        triggered, reason = evaluate_hitl_condition(hitl_condition, output, confidence)
        if triggered:
            return reason

    return ""
