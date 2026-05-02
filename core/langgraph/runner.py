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

import asyncio
import os
import time
import uuid
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt

from core.explainer import generate_explanation
from core.feedback.analyzer import format_amendments_for_prompt
from core.langgraph.agent_graph import build_agent_graph
from core.langgraph.state import AgentState
from core.pii.redactor import PIIRedactor

logger = structlog.get_logger()

# Resource limits — prevent runaway agents from exhausting budget or the
# checkpoint store. Tuned to cover the 99th percentile of legitimate runs;
# see docs/PERFORMANCE.md for baselines.
MAX_AGENT_DURATION_SEC = int(os.getenv("AGENTICORG_MAX_AGENT_DURATION_SEC", "1800"))  # 30 min
MAX_AGENT_STEPS = int(os.getenv("AGENTICORG_MAX_AGENT_STEPS", "200"))

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
    connector_names: list[str] | None = None,
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
    # --- Step 1: Load prompt amendments (self-improving agents) ---
    prompt_amendments: list[str] = []
    try:
        import uuid as _uuid_mod

        from core.database import get_tenant_session as _get_session

        if tenant_id:
            _tid = _uuid_mod.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
            async with _get_session(_tid) as _sess:
                from sqlalchemy import text as _sql_text

                _row = await _sess.execute(
                    _sql_text(
                        "SELECT prompt_amendments FROM agents WHERE id = :aid AND tenant_id = :tid"
                    ),
                    {"aid": agent_id, "tid": str(_tid)},
                )
                _result = _row.fetchone()
                if _result and _result[0]:
                    import json as _json_mod

                    raw = _result[0]
                    if isinstance(raw, str):
                        raw = _json_mod.loads(raw)
                    if isinstance(raw, list):
                        prompt_amendments = [str(a) for a in raw]
    except Exception:
        logger.debug("prompt_amendments_load_skipped", agent_id=agent_id)

    # Prepend learned rules to system prompt
    amended_prompt = system_prompt
    if prompt_amendments:
        amendments_block = format_amendments_for_prompt(prompt_amendments)
        amended_prompt = amendments_block + system_prompt
        logger.info("prompt_amendments_applied", agent_id=agent_id, count=len(prompt_amendments))

    # Build the graph
    graph = build_agent_graph(
        system_prompt=amended_prompt,
        authorized_tools=authorized_tools,
        llm_model=llm_model,
        confidence_floor=confidence_floor,
        hitl_condition=hitl_condition,
        connector_config=connector_config,
        connector_names=connector_names,
    )

    # Compile with checkpointer
    compiled = graph.compile(checkpointer=_checkpointer)

    # P1.2: PII redaction MUST happen before any LLM input. Raise loud error
    # if production has redaction disabled — never silently send PII to LLMs.
    pii_redactor = PIIRedactor()
    pii_mode = pii_redactor.mode
    env = os.getenv("AGENTICORG_ENV", "production").lower()
    if pii_mode == "disabled" and env == "production":
        raise RuntimeError(
            "PII redaction is disabled in production. Set "
            "AGENTICORG_PII_REDACTION_MODE=before_llm. Refusing to send "
            "user data to LLM without sanitization."
        )

    # Build user message FROM ALREADY-REDACTED task_input
    # Apply redaction at the source (each task_input field) so no concatenation
    # ever sees raw PII.
    pii_token_map: dict[str, str] = {}
    if pii_mode in ("before_llm", "before_log"):
        # Recursively redact all string values in task_input
        from copy import deepcopy
        redacted_input = deepcopy(task_input) if isinstance(task_input, dict) else task_input
        if isinstance(redacted_input, dict):
            for k, v in list(redacted_input.items()):
                if isinstance(v, str):
                    new_v, tokens = pii_redactor.redact(v)
                    redacted_input[k] = new_v
                    pii_token_map.update(tokens)
        user_message = _build_user_message(redacted_input)
        # Defense in depth: redact the assembled message too
        user_message, extra_tokens = pii_redactor.redact(user_message)
        pii_token_map.update(extra_tokens)
        if pii_token_map:
            logger.info("pii_redacted_before_llm", entities=len(pii_token_map), agent_id=agent_id)
    else:
        user_message = _build_user_message(task_input)

    initial_state: AgentState = {
        "messages": [
            SystemMessage(content=amended_prompt),
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

    # Execute the graph — bounded by MAX_AGENT_DURATION_SEC so a runaway
    # agent can't burn through the tenant's budget. LangGraph's recursion
    # limit caps the step count.
    t0 = time.perf_counter()
    try:
        invoke_config = {**config, "recursion_limit": MAX_AGENT_STEPS}
        result = await asyncio.wait_for(
            compiled.ainvoke(initial_state, config=invoke_config),  # type: ignore[call-overload]
            timeout=MAX_AGENT_DURATION_SEC,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Extract token usage from AI messages
        tokens_used = 0
        for msg in result.get("messages", []):
            if isinstance(msg, AIMessage):
                # LangChain standard: usage_metadata (works for OpenAI, Anthropic)
                usage = getattr(msg, "usage_metadata", None)
                if usage:
                    # usage_metadata can be a dict or object depending on provider
                    if isinstance(usage, dict):
                        total = usage.get("total_tokens", 0) or (
                            (usage.get("input_tokens", 0) or 0)
                            + (usage.get("output_tokens", 0) or 0)
                        )
                    else:
                        total = getattr(usage, "total_tokens", 0) or (
                            (getattr(usage, "input_tokens", 0) or 0)
                            + (getattr(usage, "output_tokens", 0) or 0)
                        )
                    tokens_used += total
                    continue
                # Gemini/Google GenAI: response_metadata carries token counts
                resp_meta = getattr(msg, "response_metadata", None) or {}
                if isinstance(resp_meta, dict):
                    usage_meta = resp_meta.get("usage_metadata") or resp_meta.get("token_usage") or {}
                    if isinstance(usage_meta, dict):
                        total = usage_meta.get("total_token_count", 0) or usage_meta.get("total_tokens", 0) or (
                            (usage_meta.get("prompt_token_count", 0) or usage_meta.get("input_tokens", 0) or 0)
                            + (usage_meta.get("candidates_token_count", 0) or usage_meta.get("output_tokens", 0) or 0)
                        )
                        tokens_used += total

        # Estimate cost (Gemini 2.5 Flash pricing: $0.15/1M input, $0.60/1M output)
        cost_usd = round(tokens_used * 0.000375 / 1000, 6) if tokens_used else 0

        # --- Step 6: PII de-anonymization (after LLM) ---
        if pii_token_map:
            output = result.get("output", {})
            if isinstance(output, dict):
                import json as _json

                raw = _json.dumps(output, default=str)
                restored = pii_redactor.deanonymize(raw, pii_token_map)
                try:
                    output = _json.loads(restored)
                except Exception:
                    output = {"raw": restored}
                result["output"] = output
            elif isinstance(output, str):
                result["output"] = pii_redactor.deanonymize(output, pii_token_map)

            # Also deanonymize reasoning trace
            trace = result.get("reasoning_trace", [])
            if trace:
                result["reasoning_trace"] = [
                    pii_redactor.deanonymize(t, pii_token_map) if isinstance(t, str) else t
                    for t in trace
                ]
            logger.info("pii_deanonymized_after_llm", entities=len(pii_token_map), agent_id=agent_id)

        # --- Step 7: Content safety check (if enabled in connector_config) ---
        content_safety_result: dict[str, Any] = {}
        _cs_config = (connector_config or {}).get("content_safety")
        if _cs_config and isinstance(_cs_config, dict):
            try:
                from core.content_safety.checker import check_content_safety

                # Build text to check from output
                _cs_output = result.get("output", {})
                if isinstance(_cs_output, dict):
                    import json as _cs_json
                    _cs_text = _cs_json.dumps(_cs_output, default=str)
                else:
                    _cs_text = str(_cs_output)

                content_safety_result = await check_content_safety(_cs_text, _cs_config)

                if not content_safety_result.get("safe", True):
                    logger.warning(
                        "content_safety_flagged",
                        agent_id=agent_id,
                        issues=len(content_safety_result.get("issues", [])),
                        scores=content_safety_result.get("scores", {}),
                    )
            except Exception as _cs_exc:
                logger.debug("content_safety_check_skipped", error=str(_cs_exc))

        # --- Step 8: Generate explanation (skip for hitl_triggered) ---
        run_status = result.get("status", "completed")
        explanation: dict[str, Any] = {}
        if run_status in ("completed", "failed"):
            try:
                trace = result.get("reasoning_trace", [])
                out = result.get("output", {})
                tools = [
                    tc.get("tool", "") for tc in result.get("tool_calls_log", [])
                    if isinstance(tc, dict) and tc.get("tool")
                ]
                explanation = await generate_explanation(trace, out, tools)
            except Exception as exc:
                logger.warning("explanation_generation_failed", error=str(exc))

        # BUG-11 (RU-May01 verification, 2026-05-02): the api response
        # serializer at api/v1/agents.py:run_agent reads
        # ``lg_result.get("tool_calls", [])`` while we emit the
        # log under the key ``tool_calls_log``. Result: every agent
        # run response showed ``tool_calls: []`` even when tools
        # actually executed (verified live against Zoho Books on
        # 2026-05-02 — the run wrote a ``list_invoices_response`` to
        # output but the response field was empty). Dual-emit both
        # keys so the api serializer stays accurate without a
        # cross-module rename. ``tool_calls_log`` stays the canonical
        # internal name.
        tool_log = result.get("tool_calls_log", [])
        return {
            "status": run_status,
            "output": result.get("output", {}),
            "confidence": result.get("confidence", 0.0),
            "reasoning_trace": result.get("reasoning_trace", []),
            "tool_calls_log": tool_log,
            "tool_calls": tool_log,
            "hitl_trigger": result.get("hitl_trigger", ""),
            "error": result.get("error", ""),
            "explanation": explanation,
            "content_safety": content_safety_result,
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

        # BUG-11 dual-emit (see comment above): keep both keys.
        hitl_tool_log = state_values.get("tool_calls_log", [])
        return {
            "status": state_values.get("status", "hitl_triggered"),
            "output": state_values.get("output", {}),
            "confidence": state_values.get("confidence", 0.0),
            "reasoning_trace": state_values.get("reasoning_trace", []),
            "tool_calls_log": hitl_tool_log,
            "tool_calls": hitl_tool_log,
            "hitl_trigger": hitl_trigger,
            "error": "",
            "thread_id": config["configurable"]["thread_id"],
            "performance": {
                "total_latency_ms": latency_ms,
                "llm_tokens_used": 0,
                "llm_cost_usd": 0,
            },
        }

    except TimeoutError:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.warning(
            "langgraph_agent_timeout",
            agent_id=agent_id,
            max_duration_sec=MAX_AGENT_DURATION_SEC,
        )
        return {
            "status": "failed",
            "output": {},
            "confidence": 0.0,
            "reasoning_trace": ["Agent exceeded maximum allowed duration"],
            "tool_calls_log": [],
            "tool_calls": [],  # BUG-11 dual-emit
            "hitl_trigger": "",
            "error": f"timeout: agent exceeded {MAX_AGENT_DURATION_SEC}s",
            "explanation": {},
            "performance": {
                "total_latency_ms": latency_ms,
                "llm_tokens_used": 0,
                "llm_cost_usd": 0,
            },
        }

    except Exception as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.error("langgraph_agent_failed", agent_id=agent_id, error=str(e))

        # Generate explanation for failed runs too
        fail_trace = [f"Agent execution failed: {type(e).__name__}"]
        fail_explanation: dict[str, Any] = {}
        try:
            fail_explanation = await generate_explanation(fail_trace, {}, [])
        except Exception:
            logger.debug("fail_explanation_skipped", agent_id=agent_id)

        return {
            "status": "failed",
            "output": {},
            "confidence": 0.0,
            "reasoning_trace": fail_trace,
            "tool_calls_log": [],
            "tool_calls": [],  # BUG-11 dual-emit
            "hitl_trigger": "",
            "error": str(e),
            "explanation": fail_explanation,
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
    connector_names: list[str] | None = None,
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
        connector_names=connector_names,
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
