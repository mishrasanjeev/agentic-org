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

    _redis_url = __import__("os").environ.get("REDIS_URL", "redis://localhost:6379/0")
    _redis_client = _aioredis.from_url(_redis_url, decode_responses=True)
except Exception:
    _log.info("chat_redis_unavailable_using_memory_fallback")


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


def _format_agent_output(output: dict | str | Any) -> str:
    """Convert a LangGraph agent output dict into a human-readable string.

    The agent runner may return:
      * a free-text string,
      * a dict containing ``raw_output`` (LLM didn't return JSON),
      * a dict with standard ``answer``/``response`` keys, or
      * a dict with arbitrary structured fields.

    Never returns a raw JSON dict as the user-facing answer.
    """
    if isinstance(output, str):
        return output
    if not isinstance(output, dict):
        return str(output)
    # Free-text response (LLM didn't return JSON)
    if "raw_output" in output and output["raw_output"]:
        raw = output["raw_output"]
        # The raw_output may itself be a JSON string — try to parse and
        # extract a human-readable answer from it.
        if isinstance(raw, str):
            try:
                parsed = __import__("json").loads(raw)
                if isinstance(parsed, dict):
                    for key in ("answer", "response", "message", "summary", "result"):
                        if key in parsed and parsed[key]:
                            return str(parsed[key])
                    return _format_agent_output(parsed)
            except (ValueError, TypeError):
                pass
        return str(raw)
    # Standard answer/response keys
    for key in ("answer", "response", "message", "summary", "result"):
        if key in output and output[key]:
            val = output[key]
            return val if isinstance(val, str) else str(val)
    # Format remaining structured output as readable text
    lines = []
    for k, v in output.items():
        if k in ("status", "confidence", "trace", "tool_calls"):
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
    domain = _classify_domain(body.query)
    agent_name, agent_id, agent_type, agent_tools = await _find_agent_for_domain(
        domain, tenant_id,
    )
    # Default confidence based on whether we matched a domain at all
    tools_used = False
    confidence = 0.6 if domain == "general" else 0.85

    # Build connector config from request state or default to empty dict (BUG #20)
    connector_config: dict[str, Any] = {}
    if hasattr(request.state, "connector_config") and request.state.connector_config:
        connector_config = request.state.connector_config

    # Resolve agent_type: prefer DB value, fall back to domain keyword (BUG #3)
    resolved_agent_type = agent_type or domain

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
                    f"You are {agent_name}, a domain expert for {domain}. "
                    f"Answer the user's question concisely and helpfully."
                ),
                authorized_tools=resolved_tools,
                task_input={"action": "query", "inputs": {"query": body.query}, "context": {}},
                llm_model="",
                confidence_floor=0.88,
                grant_token=grant_token,
                connector_config=connector_config,
            )
            if lg_result.get("status") == "completed" and lg_result.get("output"):
                output = lg_result["output"]
                answer = _format_agent_output(output)
                # Extract real confidence from LLM response if available (BUG #21)
                confidence = lg_result.get("confidence", 0.85 if resolved_tools else 0.6)
                tools_used = bool(lg_result.get("tools_called"))
        except Exception:
            _log.warning("chat_langgraph_fallback", domain=domain, agent_id=agent_id)

    # Adjust confidence based on tool execution success (BUG #21)
    if answer and tools_used:
        confidence = max(confidence, 0.85)
    elif answer and not tools_used:
        confidence = min(confidence, 0.6)

    # Fallback: generate a contextual response based on domain
    if not answer:
        answer = (
            f"[{agent_name}] I've analyzed your query about '{body.query}' "
            f"in the {domain} domain. Let me look into the relevant data and "
            f"get back to you with specific details. What aspect would you "
            f"like me to focus on?"
        )
        confidence = 0.6 if domain == "general" else 0.7

    # Store in session history (Redis-backed, BUG #22)
    session_key = f"{tenant_id}:{body.company_id}"
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
    tenant_id: str = Depends(get_current_tenant),
):
    """Return chat history for the current session (Redis-backed)."""
    session_key = f"{tenant_id}:{company_id}"
    entries = await _load_session(session_key)
    return [ChatMessage(**e) for e in entries]
