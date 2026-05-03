"""NL query chat endpoint — routes user questions to domain agents."""

from __future__ import annotations

import json as _json
import re
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import get_current_tenant
from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _DOMAIN_DEFAULT_TOOLS
from core.config import redis_socket_timeout_kwargs, redis_url_from_env
from core.database import get_tenant_session
from core.models.agent import Agent

router = APIRouter()
_log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Domain keyword routing (heuristic — augmented by dynamic DB lookup)
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "finance": [
        "invoice", "payment", "revenue", "expense", "profit", "loss",
        "tax", "gst", "tds", "balance sheet", "p&l", "ledger", "accounts",
        "receivable", "payable", "cash flow", "audit", "billing", "salary",
        "reimbursement", "budget", "forecast", "reconciliation", "bank",
        # TC_004: a CFO-sense query like "what is our cash runway" never
        # matched because "cash flow" is a distinct bigram from "cash" and
        # "runway". Adding the standalone finance lexicon so the common
        # metrics questions route to the CFO agent.
        "cash", "runway", "burn", "burn rate", "liquidity", "working capital",
        "receivables", "payables", "arr", "mrr", "ebitda", "gross margin",
        "net income", "financials",
    ],
    "hr": [
        "employee", "leave", "attendance", "payroll", "hiring", "recruit",
        "onboarding", "performance", "appraisal", "resign", "termination",
        "headcount", "attrition", "training", "compliance", "policy", "hr",
    ],
    "marketing": [
        "campaign", "lead", "seo", "social media", "content", "brand",
        "advertising", "conversion", "funnel", "email marketing", "analytics",
        "engagement", "traffic", "impression", "click", "ctr", "ad spend",
    ],
    "operations": [
        "inventory", "supply chain", "logistics", "warehouse", "shipping",
        "vendor", "procurement", "order", "fulfillment", "delivery", "sla",
        "ops", "operations", "workflow", "process", "ticket",
    ],
    "sales": [
        "deal", "pipeline", "quota", "crm", "prospect", "close", "opportunity",
        "commission", "territory", "forecast", "customer", "client", "contract",
    ],
    "communications": [
        "email", "gmail", "inbox", "outbox", "mail", "slack", "notification",
        "message", "announcement", "memo", "newsletter", "whatsapp", "sms",
        "calendar", "meeting", "schedule", "send email", "read email",
    ],
}

# Maps keyword-routing domain names to DB domain values
_DOMAIN_TO_DB_DOMAIN: dict[str, str] = {
    "finance": "finance",
    "hr": "hr",
    "marketing": "marketing",
    "operations": "ops",
    "sales": "marketing",
    "communications": "comms",
}


def _classify_domain(query: str) -> str:
    """Return the best-matching domain for a query using keyword overlap."""
    query_lower = query.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", query_lower))
        if score > 0:
            scores[domain] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


async def _find_agent_for_domain(
    domain: str, tenant_id: str,
) -> tuple[str, str | None, str | None, list[str]]:
    """Find the best active agent for a domain from the DB.

    Returns (agent_display_name, agent_id_str, agent_type, authorized_tools)
    or falls back to a default.
    """
    db_domain = _DOMAIN_TO_DB_DOMAIN.get(domain, domain)
    try:
        tid = _uuid.UUID(tenant_id)
        async with get_tenant_session(tid) as session:
            result = await session.execute(
                select(Agent)
                .where(
                    Agent.tenant_id == tid,
                    Agent.domain == db_domain,
                    Agent.status.in_(["active", "shadow"]),
                )
                .order_by(Agent.status.asc(), Agent.created_at.desc())
                .limit(1)
            )
            agent = result.scalar_one_or_none()
            if agent:
                display = agent.employee_name or agent.name
                tools = agent.authorized_tools or []
                if not tools:
                    tools = _AGENT_TYPE_DEFAULT_TOOLS.get(
                        agent.agent_type,
                        _DOMAIN_DEFAULT_TOOLS.get(db_domain, []),
                    )
                return display, str(agent.id), agent.agent_type, tools
    except Exception:
        _log.warning("chat_agent_lookup_failed", domain=domain)

    # Fallback display names when no DB agents exist
    fallback_agents: dict[str, str] = {
        "finance": "CFO Agent (Ananya)",
        "hr": "CHRO Agent (Priya)",
        "marketing": "CMO Agent (Rahul)",
        "operations": "COO Agent (Vijay)",
        "sales": "Sales Agent (Meera)",
        "communications": "Comms Agent (Arjun)",
    }
    fallback_name = fallback_agents.get(domain, "General Assistant")
    fallback_tools = _DOMAIN_DEFAULT_TOOLS.get(db_domain, [])
    return fallback_name, None, None, fallback_tools


# ---------------------------------------------------------------------------
# Session history — Redis-backed with in-memory fallback
# ---------------------------------------------------------------------------

_sessions: dict[str, list[dict]] = {}
_SESSION_TTL_SECONDS = 86400  # 24 hours

_redis_client: Any = None

try:
    import redis.asyncio as _aioredis

    _redis_url = redis_url_from_env(default_db=0)
    _redis_client = _aioredis.from_url(
        _redis_url,
        decode_responses=True,
        **redis_socket_timeout_kwargs(),
    )
except Exception:
    _log.info("chat_redis_unavailable_using_memory_fallback")


def _session_key(tenant_id: str, company_id: str, agent_id: str = "") -> str:
    """Compose the Redis bucket key for chat history.

    Root-cause fix for Codex 2026-04-22 isolation gap: without
    ``agent_id`` in the key, every agent you talked to under one company
    shared the same bucket — a support agent's chat would leak into the
    accounting agent's sidebar. When the caller provides an agent id,
    scope the history to it. Callers that omit ``agent_id`` continue to
    use the legacy bucket so we don't orphan existing sessions.
    """
    base = f"{tenant_id}:{company_id}"
    return f"{base}:{agent_id}" if agent_id else base


async def _load_session(key: str) -> list[dict]:
    """Load session history from Redis, falling back to in-memory dict."""
    if _redis_client is not None:
        try:
            raw = await _redis_client.get(f"chat:session:{key}")
            if raw:
                return _json.loads(raw)
            return []
        except Exception:
            _log.debug("chat_redis_load_fallback", key=key)
    return _sessions.get(key, [])


async def _save_session(key: str, entries: list[dict]) -> None:
    """Persist session history to Redis, falling back to in-memory dict."""
    if _redis_client is not None:
        try:
            await _redis_client.set(
                f"chat:session:{key}",
                _json.dumps(entries, default=str),
                ex=_SESSION_TTL_SECONDS,
            )
            return
        except Exception:
            _log.debug("chat_redis_save_fallback", key=key)
    _sessions[key] = entries

# ---------------------------------------------------------------------------
# Output formatting helper (BUG TC-002)
# ---------------------------------------------------------------------------

# Internal/security fields that should never appear in chat output
_INTERNAL_FIELDS = {
    "status", "confidence", "trace", "tool_calls", "signature",
    "sig_hash", "hash", "hmac", "token", "access_token", "refresh_token",
    "secret", "password", "api_key", "correlation_id", "thread_id",
    "trace_id", "request_id", "tenant_id", "agent_id",
}


_READABLE_KEYS = ("text", "content", "answer", "response", "message", "summary", "result")


def _extract_readable(val: Any) -> str | None:
    """Best-effort extract of user-facing text from a nested LLM payload.

    TC_008 (Aishwarya 2026-04-24): agents sometimes return a structured
    block like ``{"type": "text", "text": "...", "extras": {"signature":
    "..."}}`` as the ``answer``. The old code did ``str(val)`` on the
    dict, which produces Python repr with SINGLE quotes and leaks the
    raw block into chat bubbles. Recurse through known text-carrying
    keys instead.

    Returns ``None`` when no readable text can be extracted so the
    caller can fall back to its own formatting.
    """
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s if s else None
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, dict):
        for key in _READABLE_KEYS:
            inner = val.get(key)
            text = _extract_readable(inner)
            if text:
                return text
        return None
    if isinstance(val, (list, tuple)):
        parts: list[str] = []
        for item in val:
            text = _extract_readable(item)
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    return None


def _format_agent_output(output: dict | str | Any) -> str:
    """Convert a LangGraph agent output dict into a human-readable string.

    The agent runner may return:
      * a free-text string,
      * a dict containing ``raw_output`` (LLM didn't return JSON),
      * a dict with standard ``answer``/``response`` keys, or
      * a dict with arbitrary structured fields.

    Never returns a raw JSON/Python dict repr as the user-facing answer.
    """
    if isinstance(output, str):
        return output
    if not isinstance(output, dict):
        text = _extract_readable(output)
        return text if text is not None else str(output)
    # Free-text response (LLM didn't return JSON)
    if "raw_output" in output and output["raw_output"]:
        raw = output["raw_output"]
        # The raw_output may itself be a JSON string — try to parse and
        # extract a human-readable answer from it.
        if isinstance(raw, str):
            try:
                parsed = __import__("json").loads(raw)
                if isinstance(parsed, dict):
                    text = _extract_readable(parsed)
                    if text:
                        return text
                    return _format_agent_output(parsed)
            except (ValueError, TypeError):
                pass
            return raw
        text = _extract_readable(raw)
        if text:
            return text
    # Standard answer/response keys — recurse through nested structured
    # blocks (TC_008 fix) so a payload like ``answer={type, text, ...}``
    # renders as just the text.
    for key in ("answer", "response", "message", "summary", "result"):
        if key in output and output[key]:
            val = output[key]
            if isinstance(val, str):
                return val
            text = _extract_readable(val)
            if text:
                return text
    # Bare text/content block at top level (e.g.
    # ``{"type": "text", "text": "..."}``) — TC_008 fix.
    for key in ("text", "content"):
        if key in output and isinstance(output[key], str) and output[key].strip():
            return output[key]
    # Format remaining structured output as readable text.
    lines = []
    for k, v in output.items():
        if k in _INTERNAL_FIELDS:
            continue
        if isinstance(v, (dict, list)):
            continue  # skip nested structures
        lines.append(f"{k.replace('_', ' ').title()}: {v}")
    return "\n".join(lines) if lines else "Task completed successfully."


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChatQueryRequest(BaseModel):
    query: str
    company_id: str = ""
    agent_id: str = ""


class ChatQueryResponse(BaseModel):
    answer: str
    agent: str
    confidence: float
    domain: str


class ChatMessage(BaseModel):
    id: str
    role: str
    text: str
    agent: str | None = None
    domain: str | None = None
    confidence: float | None = None
    timestamp: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/chat/query", response_model=ChatQueryResponse)
async def chat_query(
    body: ChatQueryRequest,
    request: Request,
    tenant_id: str = Depends(get_current_tenant),
):
    """Accept a natural-language query, route to domain agent, return answer."""
    agent_connector_ids: list[str] = []
    agent_system_prompt = ""
    # If the caller specified an agent_id, look it up directly instead of
    # relying on keyword-based domain classification.
    if body.agent_id:
        try:
            aid = _uuid.UUID(body.agent_id)
            tid = _uuid.UUID(tenant_id)
            async with get_tenant_session(tid) as session:
                agent = (await session.execute(
                    select(Agent).where(Agent.id == aid, Agent.tenant_id == tid)
                )).scalar_one_or_none()
                if agent:
                    domain = agent.domain or "general"
                    agent_name = agent.employee_name or agent.name
                    agent_id: str | None = str(agent.id)
                    agent_type = agent.agent_type
                    agent_tools = agent.authorized_tools or []
                    agent_connector_ids = list(agent.connector_ids or [])
                    agent_system_prompt = agent.system_prompt_text or ""
                    if not agent_tools:
                        agent_tools = _AGENT_TYPE_DEFAULT_TOOLS.get(
                            agent.agent_type,
                            _DOMAIN_DEFAULT_TOOLS.get(domain, []),
                        )
                else:
                    domain = _classify_domain(body.query)
                    agent_name, agent_id, agent_type, agent_tools = await _find_agent_for_domain(domain, tenant_id)
        except (ValueError, Exception):
            domain = _classify_domain(body.query)
            agent_name, agent_id, agent_type, agent_tools = await _find_agent_for_domain(domain, tenant_id)
    else:
        domain = _classify_domain(body.query)
        agent_name, agent_id, agent_type, agent_tools = await _find_agent_for_domain(
            domain, tenant_id,
        )
    # Start without a fixed confidence — it gets set from the real
    # agent signal below. Initializing to a constant here was exactly
    # what kept user-visible confidence pinned at 60% on reopen TC_003
    # (Aishwarya 2026-04-22): downstream code used `min(confidence, 0.6)`
    # for tool-less answers, so any path that routed to a domain agent
    # still showed 60% even when the LLM had high-quality reasoning.
    tools_used = False
    confidence: float | None = None

    # Resolve agent_type: prefer DB value, fall back to domain keyword (BUG #3)
    resolved_agent_type = agent_type or domain

    # Build connector config (Ramesh/Uday 2026-04-28). Previously chat
    # only checked `request.state.connector_config` — but no middleware
    # ever sets that attribute, so it was always {}. Same defect class
    # as the original Ramesh BUG-012 sibling on /agents/{id}/run: tools
    # downstream hit empty auth, producing the "shadow accuracy stuck
    # at 40%" symptom for any agent invoked via chat. Resolve via the
    # canonical helper so chat picks up Zoho/Tally/QuickBooks creds the
    # same way the explicit /run route does.
    connector_config: dict[str, Any] = {}
    connector_names: list[str] | None = None
    if hasattr(request.state, "connector_config") and request.state.connector_config:
        connector_config = dict(request.state.connector_config)

    try:
        from api.v1.agents import (
            _resolve_agent_connector_ids_for_type,
            _resolve_connector_configs,
        )

        connector_ids = agent_connector_ids or await _resolve_agent_connector_ids_for_type(
            tenant_id=tenant_id,
            agent_type=resolved_agent_type,
        )
        if connector_ids:
            connector_config, resolved_names = await _resolve_connector_configs(
                tenant_id=tenant_id,
                connector_ids=connector_ids,
                agent_level_config=connector_config or None,
            )
            connector_names = resolved_names
            if not resolved_names:
                _log.warning(
                    "chat_connector_ids_unresolved_fail_closed",
                    domain=domain,
                    agent_id=agent_id,
                    connector_ids=connector_ids,
                )
    except Exception:  # noqa: BLE001
        # Resolver failures must not block chat — empty config falls
        # through to the legacy behaviour and the agent still runs
        # with whatever LLM-only reasoning it can produce.
        _log.warning("chat_connector_resolve_failed", domain=domain)

    # Resolve authorized_tools: prefer agent's tools from DB lookup (BUG #2)
    resolved_tools = agent_tools or _AGENT_TYPE_DEFAULT_TOOLS.get(
        resolved_agent_type, _DOMAIN_DEFAULT_TOOLS.get(domain, [])
    )

    # Try to execute via LangGraph if an agent was found in DB
    answer: str | None = None
    if agent_id:
        try:
            from core.langgraph.runner import run_agent as langgraph_run

            grant_token = getattr(request.state, "grant_token", None)
            lg_result = await langgraph_run(
                agent_id=agent_id,
                agent_type=resolved_agent_type,
                domain=_DOMAIN_TO_DB_DOMAIN.get(domain, domain),
                tenant_id=tenant_id,
                system_prompt=(
                    agent_system_prompt
                    or (
                        f"You are {agent_name}, a domain expert for {domain}. "
                        "Answer the user's question concisely and helpfully. "
                        "Extract amount, period, section, customer, ledger, "
                        "and filing details already present before asking "
                        "for clarification."
                    )
                ),
                authorized_tools=resolved_tools,
                task_input={"action": "query", "inputs": {"query": body.query}, "context": {}},
                llm_model="",
                confidence_floor=0.88,
                grant_token=grant_token,
                connector_config=connector_config,
                connector_names=connector_names,
            )
            if lg_result.get("status") == "completed" and lg_result.get("output"):
                output = lg_result["output"]
                answer = _format_agent_output(output)
                # Extract real confidence from LLM response (BUG #21).
                # Fall through to the adjustment block if the agent
                # didn't provide one — don't wedge in a constant here.
                raw_confidence = lg_result.get("confidence")
                if isinstance(raw_confidence, (int, float)):
                    confidence = float(raw_confidence)
                tools_used = bool(
                    lg_result.get("tool_calls") or lg_result.get("tool_calls_log")
                )
        except Exception:
            _log.warning("chat_langgraph_fallback", domain=domain, agent_id=agent_id)

    # Confidence adjustment (TC_003 reopen fix, Aishwarya 2026-04-22):
    # the old block did ``min(confidence, 0.6)`` whenever tools weren't
    # called, which capped LLM-only reasoning at 60% forever. An LLM
    # answering "what is our cash runway" from training data + prompt
    # can still be 0.75+ confident; we shouldn't stamp it down to 0.6.
    # New rules:
    #   - tools_used → floor at 0.85 (had supporting evidence).
    #   - no tools + answer exists + LLM gave a confidence → trust it.
    #   - no tools + answer exists + no LLM confidence → floor at 0.75
    #     (the model produced something; we have no signal it's wrong).
    #   - no answer → leave at None, the no-answer block below sets 0.0.
    if answer and tools_used:
        confidence = max(confidence or 0.85, 0.85)
    elif answer and not tools_used and confidence is None:
        confidence = 0.75

    # Root-cause fix for TC_004 / Codex 2026-04-22 review: the old
    # fallback path fabricated a "[AgentName] I've analyzed your query
    # about X..." response with a forced 0.6/0.7 confidence whenever
    # the real agent couldn't produce an answer. That was dishonest —
    # users saw a plausible-looking reply and trusted it as if an agent
    # had actually reasoned about their query. Surface the no-answer
    # condition explicitly so the UI can show a "try again / connect
    # this data source" state instead of a phantom response. The
    # confidence drops to the minimum because the system genuinely
    # has no grounded answer.
    if not answer:
        answer = (
            "No agent was able to answer that query. "
            "Check that the relevant connector is configured (for example, "
            "link Gmail for inbox questions or Tally for GST queries) and "
            "that an agent is deployed in the matching domain. "
            "Rephrasing the question or picking an agent explicitly from "
            "the header bar may also help."
        )
        confidence = 0.0

    # Store in session history (Redis-backed, BUG #22).
    #
    # Root-cause fix for Codex 2026-04-22 review on chat history
    # isolation: the session key was only ``tenant_id:company_id``, so
    # history from agent A leaked into agent B's sidebar when the user
    # switched agents with the same company context. When the caller
    # scopes the query to a specific ``agent_id``, scope the history
    # bucket to it too. The ``agent_id`` suffix is opaque to Redis and
    # costs nothing; callers that don't pass ``agent_id`` keep the old
    # bucket layout.
    session_key = _session_key(tenant_id, body.company_id, body.agent_id)
    entries = await _load_session(session_key)
    now = datetime.now(UTC).isoformat()
    entries.append(
        {"id": str(_uuid.uuid4()), "role": "user", "text": body.query, "timestamp": now}
    )
    entries.append(
        {
            "id": str(_uuid.uuid4()),
            "role": "agent",
            "text": answer,
            "agent": agent_name,
            "domain": domain,
            "confidence": confidence,
            "timestamp": now,
        }
    )
    await _save_session(session_key, entries)

    return ChatQueryResponse(
        answer=answer,
        agent=agent_name,
        confidence=confidence,
        domain=domain,
    )


@router.get("/chat/history", response_model=list[ChatMessage])
async def chat_history(
    company_id: str = "",
    agent_id: str = "",
    tenant_id: str = Depends(get_current_tenant),
):
    """Return chat history for the current session (Redis-backed).

    Root-cause fix for Codex 2026-04-22 chat history isolation gap:
    the session key was just ``tenant_id:company_id``, so switching
    between agents with the same company loaded the wrong history. The
    key now includes ``agent_id`` when provided, matching the ``POST
    /chat/query`` write path so reads and writes agree. Callers that
    omit ``agent_id`` see the legacy tenant+company bucket (no data
    loss for existing sessions).
    """
    session_key = _session_key(tenant_id, company_id, agent_id)
    entries = await _load_session(session_key)
    return [ChatMessage(**e) for e in entries]
