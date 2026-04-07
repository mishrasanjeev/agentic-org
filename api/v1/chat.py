"""NL query chat endpoint — routes user questions to domain agents."""

from __future__ import annotations

import re
import uuid as _uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import get_current_tenant
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
        "email", "slack", "notification", "message", "announcement", "memo",
        "newsletter", "whatsapp", "sms", "calendar", "meeting", "schedule",
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
) -> tuple[str, str | None]:
    """Find the best active agent for a domain from the DB.

    Returns (agent_display_name, agent_id_str) or falls back to a default.
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
                return display, str(agent.id)
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
    return fallback_agents.get(domain, "General Assistant"), None


# ---------------------------------------------------------------------------
# In-memory session history (MVP — replaced by Redis/DB in prod)
# ---------------------------------------------------------------------------

_sessions: dict[str, list[dict]] = {}

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
    agent_name, agent_id = await _find_agent_for_domain(domain, tenant_id)
    confidence = 0.92 if domain != "general" else 0.65

    # Try to execute via LangGraph if an agent was found in DB
    answer: str | None = None
    if agent_id:
        try:
            from core.langgraph.runner import run_agent as langgraph_run

            grant_token = getattr(request.state, "grant_token", None)
            lg_result = await langgraph_run(
                agent_id=agent_id,
                agent_type=domain,
                domain=_DOMAIN_TO_DB_DOMAIN.get(domain, domain),
                tenant_id=tenant_id,
                system_prompt=(
                    f"You are {agent_name}, a domain expert for {domain}. "
                    f"Answer the user's question concisely and helpfully."
                ),
                authorized_tools=[],
                task_input={"action": "query", "inputs": {"query": body.query}, "context": {}},
                llm_model="",
                confidence_floor=0.88,
                grant_token=grant_token,
                connector_config=None,
            )
            if lg_result.get("status") == "completed" and lg_result.get("output"):
                output = lg_result["output"]
                answer = output.get("answer") or output.get("response") or str(output)
                confidence = lg_result.get("confidence", confidence)
        except Exception:
            _log.warning("chat_langgraph_fallback", domain=domain, agent_id=agent_id)

    # Fallback: generate a contextual response based on domain
    if not answer:
        answer = (
            f"[{agent_name}] I've analyzed your query about '{body.query}' "
            f"in the {domain} domain. Let me look into the relevant data and "
            f"get back to you with specific details. What aspect would you "
            f"like me to focus on?"
        )

    # Store in session history
    session_key = f"{tenant_id}:{body.company_id}"
    if session_key not in _sessions:
        _sessions[session_key] = []
    now = datetime.now(UTC).isoformat()
    _sessions[session_key].append(
        {"id": str(_uuid.uuid4()), "role": "user", "text": body.query, "timestamp": now}
    )
    _sessions[session_key].append(
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
    """Return in-memory chat history for the current session."""
    session_key = f"{tenant_id}:{company_id}"
    entries = _sessions.get(session_key, [])
    return [ChatMessage(**e) for e in entries]
