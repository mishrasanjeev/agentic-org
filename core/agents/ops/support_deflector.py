"""Support Deflector agent — auto-resolve common support queries.

Decision flow:
  1. Classify intent of the incoming support query.
  2. Check FAQ for exact/near matches.
  3. Search knowledge base (RAG) if FAQ miss.
  4. Auto-respond if confidence >= threshold, else escalate to human.

Tracks deflection_rate metric: auto_resolved / total * 100.
"""

from __future__ import annotations

from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry

logger = structlog.get_logger()

# Default confidence threshold for auto-response
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

# Built-in FAQ entries (production would load from DB/Redis)
_FAQ: list[dict[str, str]] = [
    {
        "intent": "password_reset",
        "question": "How do I reset my password?",
        "answer": (
            "Go to Settings > Security > Change Password, or use the "
            "'Forgot Password' link on the login page."
        ),
    },
    {
        "intent": "billing_inquiry",
        "question": "How do I upgrade my plan?",
        "answer": (
            "Navigate to Dashboard > Billing, pick a plan, and click Upgrade. "
            "We accept credit cards (Stripe) and UPI/net-banking (PineLabs) for India."
        ),
    },
    {
        "intent": "connector_setup",
        "question": "How do I connect Salesforce?",
        "answer": (
            "Go to Connectors > New, select Salesforce, paste your OAuth credentials, "
            "and click Test Connection."
        ),
    },
    {
        "intent": "agent_creation",
        "question": "How do I create a new agent?",
        "answer": (
            "Navigate to Agents > New Agent, choose a template or upload an SOP, "
            "configure tools and scope, then Activate."
        ),
    },
    {
        "intent": "data_export",
        "question": "How do I export my data?",
        "answer": (
            "Go to Settings > Data Management > Export. Select date range and format "
            "(CSV/JSON). Large exports are emailed as a zip file."
        ),
    },
]


@AgentRegistry.register
class SupportDeflectorAgent(BaseAgent):
    """Auto-resolve common support queries before they reach a human."""

    agent_type = "support_deflector"
    domain = "ops"
    confidence_floor = 0.70
    prompt_file = "support_deflector.prompt.txt"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.confidence_threshold = kwargs.get(
            "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD
        )
        # Counters for deflection rate
        self._total_queries = 0
        self._auto_resolved = 0

    # ── Public helpers ────────────────────────────────────────────────

    @property
    def deflection_rate(self) -> float:
        """Return deflection rate as a percentage."""
        if self._total_queries == 0:
            return 0.0
        return (self._auto_resolved / self._total_queries) * 100

    # ── Core execution override ──────────────────────────────────────

    async def execute(self, task: Any) -> Any:
        """Execute support deflection flow."""
        self._total_queries += 1
        trace: list[str] = []

        # Extract query from task
        query = self._extract_query(task)
        trace.append(f"Query: {query[:120]}")

        # Step 1: Classify intent
        intent, intent_confidence = self._classify_intent(query)
        trace.append(f"Intent: {intent} (confidence: {intent_confidence:.2f})")

        # Step 2: Check FAQ
        faq_answer = self._check_faq(intent, query)
        if faq_answer and intent_confidence >= self.confidence_threshold:
            self._auto_resolved += 1
            trace.append("Resolved via FAQ")
            return self._build_response(
                status="auto_resolved",
                answer=faq_answer,
                confidence=intent_confidence,
                source="faq",
                trace=trace,
            )

        # Step 3: Search knowledge base (if tool available)
        kb_answer, kb_confidence = await self._search_knowledge_base(query, trace)
        if kb_answer and kb_confidence >= self.confidence_threshold:
            self._auto_resolved += 1
            trace.append(f"Resolved via KB (confidence: {kb_confidence:.2f})")
            return self._build_response(
                status="auto_resolved",
                answer=kb_answer,
                confidence=kb_confidence,
                source="knowledge_base",
                trace=trace,
            )

        # Step 4: Escalate — confidence too low or no answer found
        trace.append("Escalating to human agent")
        return self._build_response(
            status="escalated",
            answer=None,
            confidence=max(intent_confidence, kb_confidence) if kb_confidence else intent_confidence,
            source="escalation",
            trace=trace,
            escalation_reason=self._get_escalation_reason(intent_confidence, kb_confidence),
        )

    # ── Internal methods ─────────────────────────────────────────────

    def _extract_query(self, task: Any) -> str:
        """Extract the user query from the task payload."""
        if hasattr(task, "task") and hasattr(task.task, "model_dump"):
            data = task.task.model_dump()
        elif hasattr(task, "task") and isinstance(task.task, dict):
            data = task.task
        elif isinstance(task, dict):
            data = task
        else:
            data = {}
        return str(
            data.get("query", data.get("message", data.get("text", str(data))))
        )

    def _classify_intent(self, query: str) -> tuple[str, float]:
        """Classify the intent of a support query.

        Uses keyword matching as a fast first pass. In production the LLM
        call in execute() would provide a richer classification.
        """
        q = query.lower()
        intent_keywords: dict[str, list[str]] = {
            "password_reset": ["password", "reset", "forgot", "login issue", "locked out"],
            "billing_inquiry": ["billing", "invoice", "upgrade", "plan", "payment", "subscription"],
            "connector_setup": ["connector", "salesforce", "integration", "connect", "oauth"],
            "agent_creation": ["create agent", "new agent", "agent setup", "sop upload"],
            "data_export": ["export", "download", "csv", "backup"],
        }

        best_intent = "unknown"
        best_score = 0.0

        for intent, keywords in intent_keywords.items():
            matches = sum(1 for kw in keywords if kw in q)
            if matches > 0:
                score = min(0.5 + (matches * 0.15), 0.95)
                if score > best_score:
                    best_score = score
                    best_intent = intent

        if best_intent == "unknown":
            best_score = 0.3

        return best_intent, best_score

    def _check_faq(self, intent: str, query: str) -> str | None:
        """Look up the FAQ by intent. Returns answer text or None."""
        for entry in _FAQ:
            if entry["intent"] == intent:
                return entry["answer"]
        return None

    async def _search_knowledge_base(
        self, query: str, trace: list[str]
    ) -> tuple[str | None, float]:
        """Search the knowledge base via RAG tool if available.

        Returns (answer, confidence) or (None, 0.0).
        """
        if not self.tool_gateway:
            trace.append("No tool gateway — KB search skipped")
            return None, 0.0

        try:
            result = await self._call_tool(
                connector_name="knowledge_base",
                tool_name="search",
                params={"query": query, "top_k": 3},
            )
            if "error" in result:
                trace.append(f"KB search error: {result['error']}")
                return None, 0.0

            hits = result.get("results", result.get("hits", []))
            if not hits:
                trace.append("KB search: no results")
                return None, 0.0

            top = hits[0] if isinstance(hits, list) else hits
            answer = top.get("text", top.get("content", ""))
            score = float(top.get("score", top.get("confidence", 0.5)))
            return answer, score

        except Exception as exc:
            trace.append(f"KB search exception: {exc}")
            return None, 0.0

    def _get_escalation_reason(
        self, intent_confidence: float, kb_confidence: float | None
    ) -> str:
        """Build a human-readable escalation reason."""
        reasons: list[str] = []
        if intent_confidence < self.confidence_threshold:
            reasons.append(
                f"Intent confidence ({intent_confidence:.2f}) below threshold ({self.confidence_threshold})"
            )
        if kb_confidence is not None and kb_confidence < self.confidence_threshold:
            reasons.append(
                f"KB confidence ({kb_confidence:.2f}) below threshold ({self.confidence_threshold})"
            )
        if not reasons:
            reasons.append("No matching FAQ or KB entry found")
        return "; ".join(reasons)

    def _build_response(
        self,
        status: str,
        answer: str | None,
        confidence: float,
        source: str,
        trace: list[str],
        escalation_reason: str | None = None,
    ) -> dict[str, Any]:
        """Build a structured response dict."""
        resp: dict[str, Any] = {
            "status": status,
            "confidence": confidence,
            "source": source,
            "processing_trace": trace,
            "deflection_rate": self.deflection_rate,
        }
        if answer:
            resp["answer"] = answer
        if escalation_reason:
            resp["escalation_reason"] = escalation_reason
        return resp
