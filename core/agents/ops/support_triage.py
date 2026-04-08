"""Support Triage agent implementation."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry
from core.schemas.messages import (
    DecisionOption,
    DecisionRequired,
    HITLAssignee,
    HITLContext,
    HITLRequest,
    ToolCallRecord,
)

logger = structlog.get_logger()

# Ticket category keywords
CATEGORY_KEYWORDS = {
    "billing": [
        "invoice", "payment", "charge", "refund", "billing", "subscription",
        "plan", "upgrade", "downgrade", "credit", "debit", "pricing",
    ],
    "technical": [
        "error", "bug", "crash", "slow", "timeout", "500", "404", "api",
        "integration", "login", "password", "ssl", "certificate", "down",
    ],
    "account": [
        "account", "profile", "settings", "permission", "access", "role",
        "user", "team", "organization", "delete", "deactivate",
    ],
    "feature": [
        "feature", "request", "suggestion", "enhancement", "improve", "add",
        "support for", "ability to", "would like", "wish",
    ],
}

# Priority keywords
P1_KEYWORDS = ["down", "outage", "critical", "data loss", "security breach", "production", "all users"]
P2_KEYWORDS = ["error", "bug", "broken", "cannot", "fails", "urgent", "blocking"]
P3_KEYWORDS = ["slow", "intermittent", "sometimes", "minor", "workaround"]

# Customer tiers for priority boost
CUSTOMER_TIERS = {"enterprise": 1, "business": 0, "starter": 0, "free": -1}

# KB match threshold
KB_MATCH_THRESHOLD = 0.85
# HITL for P1 tickets or unknown category
HITL_PRIORITIES = {"P1"}


@AgentRegistry.register
class SupportTriageAgent(BaseAgent):
    agent_type = "support_triage"
    domain = "ops"
    confidence_floor = 0.85
    prompt_file = "support_triage.prompt.txt"

    async def execute(self, task):
        """Classify ticket, assign priority, route to team, auto-respond with KB if match >85%."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            ticket = inputs.get("ticket", inputs)
            ticket_id = ticket.get("ticket_id", ticket.get("id", ""))
            subject = str(ticket.get("subject", "")).strip()
            description = str(ticket.get("description", ticket.get("body", ""))).strip()
            customer_tier = ticket.get("customer_tier", ticket.get("tier", "business")).lower()
            trace.append(f"Triage ticket: id={ticket_id}, subject='{subject[:60]}', tier={customer_tier}")

            combined_text = f"{subject} {description}".lower()

            # --- Step 1: Classify ticket category ---
            category_scores: dict[str, int] = {}
            for cat, keywords in CATEGORY_KEYWORDS.items():
                score = sum(1 for kw in keywords if kw in combined_text)
                if score > 0:
                    category_scores[cat] = score

            if category_scores:
                category = max(category_scores, key=category_scores.get)
                category_confidence = min(category_scores[category] / 3.0, 1.0)
            else:
                category = "other"
                category_confidence = 0.3

            trace.append(f"Category: {category} (scores: {category_scores})")

            # --- Step 2: Assign priority ---
            base_priority = "P3"  # Default
            if any(kw in combined_text for kw in P1_KEYWORDS):
                base_priority = "P1"
            elif any(kw in combined_text for kw in P2_KEYWORDS):
                base_priority = "P2"
            elif any(kw in combined_text for kw in P3_KEYWORDS):
                base_priority = "P3"
            else:
                base_priority = "P4"

            # Adjust priority by customer tier
            tier_boost = CUSTOMER_TIERS.get(customer_tier, 0)
            priority_levels = ["P1", "P2", "P3", "P4"]
            current_idx = priority_levels.index(base_priority)
            adjusted_idx = max(0, min(current_idx - tier_boost, len(priority_levels) - 1))
            priority = priority_levels[adjusted_idx]

            trace.append(f"Priority: {priority} (base={base_priority}, tier_boost={tier_boost})")

            # --- Step 3: Route to team ---
            routing_map = {
                "billing": "billing-support",
                "technical": "engineering-support",
                "account": "customer-success",
                "feature": "product-team",
                "other": "general-support",
            }
            assigned_team = routing_map.get(category, "general-support")

            # Update ticket in helpdesk
            update_result = await self._safe_tool_call(
                "freshdesk", "update_ticket",
                {
                    "ticket_id": ticket_id,
                    "category": category,
                    "priority": priority,
                    "group": assigned_team,
                    "tags": [category, priority, customer_tier],
                },
                trace, tool_calls,
            )
            ticket_updated = update_result and "error" not in update_result

            # --- Step 4: Search KB for auto-response ---
            kb_article = None
            kb_match_score = 0.0

            kb_result = await self._safe_tool_call(
                "freshdesk", "search_solutions",
                {"query": f"{subject} {category}", "limit": 3},
                trace, tool_calls,
            )
            if kb_result and "error" not in kb_result:
                articles = kb_result.get("articles", kb_result.get("results", []))
                if articles:
                    top_article = articles[0]
                    kb_match_score = float(top_article.get("score", top_article.get("relevance", 0)))
                    if kb_match_score >= KB_MATCH_THRESHOLD:
                        kb_article = {
                            "title": top_article.get("title", ""),
                            "url": top_article.get("url", top_article.get("link", "")),
                            "excerpt": top_article.get("excerpt", top_article.get("snippet", ""))[:300],
                        }
                        trace.append(f"KB match: '{kb_article['title']}' (score={kb_match_score:.2f})")

            # --- Step 5: Auto-respond if KB match is strong ---
            auto_responded = False
            if kb_article and kb_match_score >= KB_MATCH_THRESHOLD:
                reply_result = await self._safe_tool_call(
                    "freshdesk", "reply_to_ticket",
                    {
                        "ticket_id": ticket_id,
                        "body": (
                            f"Thank you for reaching out. Based on your query, this article "
                            f"may help:\n\n**{kb_article['title']}**\n{kb_article['url']}\n\n"
                            f"{kb_article['excerpt']}\n\n"
                            f"If this doesn't resolve your issue, a team member from "
                            f"{assigned_team} will follow up shortly."
                        ),
                        "private": False,
                    },
                    trace, tool_calls,
                )
                auto_responded = reply_result and "error" not in reply_result
                if auto_responded:
                    trace.append("Auto-response sent with KB article")

            # --- Step 6: Notify assigned team ---
            await self._safe_tool_call(
                "slack", "send_message",
                {
                    "channel": f"#{assigned_team}",
                    "text": (
                        f"New {priority} ticket #{ticket_id}: {subject}\n"
                        f"Category: {category} | Tier: {customer_tier}\n"
                        f"{'KB auto-response sent' if auto_responded else 'No KB match — manual response needed'}"
                    ),
                },
                trace, tool_calls,
            )

            # --- Step 7: Compute confidence ---
            factors: list[float] = []
            factors.append(min(category_confidence + 0.3, 1.0))  # Category classification
            factors.append(0.90 if ticket_updated else 0.50)  # Ticket update success
            factors.append(0.95 if kb_article else 0.70)  # KB match available
            if auto_responded:
                factors.append(0.90)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 8: HITL for P1 or unknown category ---
            hitl_reasons: list[str] = []
            if priority in HITL_PRIORITIES:
                hitl_reasons.append(f"{priority} ticket requires immediate human attention")
            if category == "other":
                hitl_reasons.append("unable to classify ticket category")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "triaged",
                "ticket_id": ticket_id,
                "category": category,
                "category_scores": category_scores,
                "priority": priority,
                "assigned_team": assigned_team,
                "customer_tier": customer_tier,
                "kb_article": kb_article,
                "kb_match_score": round(kb_match_score, 2),
                "auto_responded": auto_responded,
                "ticket_updated": ticket_updated,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="support_escalation",
                    decision_required=DecisionRequired(
                        question=f"Ticket #{ticket_id} ({priority}): {'; '.join(hitl_reasons)}",
                        options=[
                            DecisionOption(id="acknowledge", label="Acknowledge and handle", action="proceed"),
                            DecisionOption(id="reclassify", label="Reclassify ticket", action="retry"),
                            DecisionOption(id="escalate", label="Escalate to management", action="escalate"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"{priority} support ticket needs attention",
                        recommendation="acknowledge" if priority == "P1" else "review",
                        agent_confidence=confidence,
                        supporting_data={
                            "category": category,
                            "priority": priority,
                            "subject": subject[:100],
                        },
                    ),
                    assignee=HITLAssignee(
                        role="support_lead" if priority != "P1" else "engineering_lead",
                        notify_channels=["slack", "pagerduty"] if priority == "P1" else ["slack"],
                    ),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("support_triage_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "TRIAGE_ERR", "message": str(e)}, start=start,
            )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        call_start = time.monotonic()
        try:
            result = await self._call_tool(
                connector_name=connector, tool_name=tool, params=params,
            )
            latency = int((time.monotonic() - call_start) * 1000)
            status = "error" if "error" in result else "success"
            trace.append(f"[tool] {connector}.{tool} -> {status} ({latency}ms)")
            tool_records.append(ToolCallRecord(
                tool_name=f"{connector}.{tool}", status=status, latency_ms=latency,
            ))
            return result
        except Exception as exc:
            latency = int((time.monotonic() - call_start) * 1000)
            trace.append(f"[tool] {connector}.{tool} -> exception: {exc} ({latency}ms)")
            tool_records.append(ToolCallRecord(
                tool_name=f"{connector}.{tool}", status="error", latency_ms=latency,
            ))
            return {"error": str(exc)}
