"""NL query chat endpoint — routes user questions to domain agents."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_current_tenant

router = APIRouter()

# ---------------------------------------------------------------------------
# Domain keyword routing (MVP heuristic — replaced by LangGraph in prod)
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

_DOMAIN_AGENTS: dict[str, str] = {
    "finance": "CFO Agent (Ananya)",
    "hr": "CHRO Agent (Priya)",
    "marketing": "CMO Agent (Rahul)",
    "operations": "COO Agent (Vijay)",
    "sales": "Sales Agent (Meera)",
    "communications": "Comms Agent (Arjun)",
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
    tenant_id: str = Depends(get_current_tenant),
):
    """Accept a natural-language query, route to domain agent, return answer."""
    domain = _classify_domain(body.query)
    agent = _DOMAIN_AGENTS.get(domain, "General Assistant")
    confidence = 0.92 if domain != "general" else 0.65

    # MVP: structured demo response — in production this calls LangGraph
    if domain == "finance":
        answer = (
            f"Based on the financial data for your company, here is what I found: "
            f"Your query '{body.query}' has been analyzed. The finance team is "
            f"processing the relevant reports. I can pull detailed ledger entries, "
            f"GST filings, or cash flow projections on request."
        )
    elif domain == "hr":
        answer = (
            f"Looking at HR records: your query about '{body.query}' has been routed "
            f"to the HR domain. I can provide details on headcount, attendance trends, "
            f"leave balances, or recruitment pipeline status."
        )
    elif domain == "marketing":
        answer = (
            f"Marketing insights for '{body.query}': I can pull campaign performance, "
            f"lead funnel metrics, SEO rankings, or social media engagement data. "
            f"What specific report would you like?"
        )
    elif domain == "sales":
        answer = (
            f"Sales pipeline update for '{body.query}': I can show deal stages, "
            f"quota attainment, forecast accuracy, or individual rep performance. "
            f"Which metric interests you?"
        )
    elif domain == "operations":
        answer = (
            f"Operations report for '{body.query}': I have visibility into inventory "
            f"levels, vendor SLAs, procurement status, and fulfillment metrics. "
            f"Please specify what you need."
        )
    elif domain == "communications":
        answer = (
            f"Communications summary for '{body.query}': I can check email threads, "
            f"Slack channels, calendar events, or pending notifications. "
            f"Let me know the scope."
        )
    else:
        answer = (
            f"I've received your query: '{body.query}'. I'm routing it to the most "
            f"relevant agent. Could you provide more context so I can narrow down "
            f"the right domain?"
        )

    # Store in session history
    session_key = f"{tenant_id}:{body.company_id}"
    if session_key not in _sessions:
        _sessions[session_key] = []
    now = datetime.now(UTC).isoformat()
    _sessions[session_key].append(
        {"id": str(uuid.uuid4()), "role": "user", "text": body.query, "timestamp": now}
    )
    _sessions[session_key].append(
        {
            "id": str(uuid.uuid4()),
            "role": "agent",
            "text": answer,
            "agent": agent,
            "domain": domain,
            "confidence": confidence,
            "timestamp": now,
        }
    )

    return ChatQueryResponse(
        answer=answer,
        agent=agent,
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
