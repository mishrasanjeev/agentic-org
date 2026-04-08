"""AR Collections agent implementation."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
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

# Tiered reminder schedule (days overdue)
REMINDER_TIERS = {
    30: {"level": "gentle", "channel": "email", "template": "reminder_gentle"},
    60: {"level": "firm", "channel": "email", "template": "reminder_firm"},
    90: {"level": "escalation", "channel": "email_and_call", "template": "reminder_escalation"},
}
# Escalation threshold: invoices overdue > 90 days go to partner
PARTNER_ESCALATION_DAYS = 90
# HITL threshold: total outstanding > INR 10,00,000
HITL_OUTSTANDING_THRESHOLD = 1_000_000


@AgentRegistry.register
class ArCollectionsAgent(BaseAgent):
    agent_type = "ar_collections"
    domain = "finance"
    confidence_floor = 0.85
    prompt_file = "ar_collections.prompt.txt"

    async def execute(self, task):
        """Check overdue invoices, send tiered reminders, escalate to partner for 90+ days."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            customer = inputs.get("customer", "")
            as_of_date_str = inputs.get("as_of_date", "")
            as_of_date = (
                datetime.fromisoformat(as_of_date_str) if as_of_date_str
                else datetime.now(tz=UTC)
            )
            trace.append(f"AR Collections run for customer='{customer}', as_of={as_of_date.date()}")

            # --- Step 1: Fetch outstanding receivables ---
            receivables_result = await self._safe_tool_call(
                "tally", "get_outstanding_receivables",
                {"customer": customer, "as_of_date": as_of_date.isoformat()},
                trace, tool_calls,
            )

            if receivables_result and "error" not in receivables_result:
                invoices = receivables_result.get("invoices", [])
            else:
                # Fallback: try Zoho Books
                receivables_result = await self._safe_tool_call(
                    "zoho_books", "get_overdue_invoices",
                    {"customer_name": customer},
                    trace, tool_calls,
                )
                invoices = (
                    receivables_result.get("invoices", [])
                    if receivables_result and "error" not in receivables_result
                    else []
                )

            trace.append(f"Found {len(invoices)} outstanding invoices")

            if not invoices:
                return self._make_result(
                    task, msg_id, "completed",
                    {
                        "status": "no_overdue",
                        "customer": customer,
                        "message": "No overdue invoices found",
                        "confidence": 0.95,
                        "hitl_required": False,
                    },
                    0.95, trace, tool_calls, start=start,
                )

            # --- Step 2: Categorize invoices by aging bucket ---
            total_outstanding = 0.0
            aging_buckets: dict[str, list] = {"0-30": [], "31-60": [], "61-90": [], "90+": []}
            reminders_sent: list[dict] = []
            escalations: list[dict] = []

            for inv in invoices:
                inv_amount = float(inv.get("amount", inv.get("balance_due", 0)))
                due_date_str = inv.get("due_date", "")
                inv_number = inv.get("invoice_number", inv.get("number", ""))
                total_outstanding += inv_amount

                if due_date_str:
                    try:
                        due_date = datetime.fromisoformat(due_date_str)
                    except (ValueError, TypeError):
                        due_date = as_of_date
                else:
                    due_date = as_of_date

                days_overdue = (as_of_date - due_date).days
                inv_record = {
                    "invoice_number": inv_number,
                    "amount": inv_amount,
                    "due_date": due_date_str,
                    "days_overdue": days_overdue,
                    "customer": inv.get("customer", customer),
                }

                if days_overdue <= 30:
                    aging_buckets["0-30"].append(inv_record)
                elif days_overdue <= 60:
                    aging_buckets["31-60"].append(inv_record)
                elif days_overdue <= 90:
                    aging_buckets["61-90"].append(inv_record)
                else:
                    aging_buckets["90+"].append(inv_record)

            trace.append(
                f"Aging: 0-30={len(aging_buckets['0-30'])}, 31-60={len(aging_buckets['31-60'])}, "
                f"61-90={len(aging_buckets['61-90'])}, 90+={len(aging_buckets['90+'])}"
            )

            # --- Step 3: Send tiered reminders ---
            for tier_days, tier_config in sorted(REMINDER_TIERS.items()):
                bucket_key = {30: "0-30", 60: "31-60", 90: "61-90"}.get(tier_days)
                if not bucket_key:
                    continue
                for inv_rec in aging_buckets.get(bucket_key, []):
                    if inv_rec["days_overdue"] >= tier_days:
                        contact_email = inv_rec.get("email", inputs.get("contact_email", ""))
                        send_result = await self._safe_tool_call(
                            "sendgrid", "send_email",
                            {
                                "to": contact_email,
                                "template": tier_config["template"],
                                "subject": f"Payment reminder: Invoice {inv_rec['invoice_number']}",
                                "variables": {
                                    "invoice_number": inv_rec["invoice_number"],
                                    "amount": inv_rec["amount"],
                                    "days_overdue": inv_rec["days_overdue"],
                                    "customer": inv_rec.get("customer", customer),
                                },
                            },
                            trace, tool_calls,
                        )
                        sent = send_result and "error" not in send_result
                        reminders_sent.append({
                            "invoice_number": inv_rec["invoice_number"],
                            "tier": tier_config["level"],
                            "sent": sent,
                            "days_overdue": inv_rec["days_overdue"],
                        })

            # --- Step 4: Escalate 90+ day invoices to partner ---
            for inv_rec in aging_buckets.get("90+", []):
                escalations.append({
                    "invoice_number": inv_rec["invoice_number"],
                    "amount": inv_rec["amount"],
                    "days_overdue": inv_rec["days_overdue"],
                })

            if escalations:
                trace.append(f"Escalating {len(escalations)} invoices (90+ days) to partner")
                await self._safe_tool_call(
                    "slack", "send_message",
                    {
                        "channel": "#finance-escalations",
                        "text": (
                            f"AR Escalation: {len(escalations)} invoices for {customer} "
                            f"overdue 90+ days, total INR {sum(e['amount'] for e in escalations):,.2f}"
                        ),
                    },
                    trace, tool_calls,
                )

            # --- Step 5: Compute confidence ---
            data_quality = 0.9 if receivables_result and "error" not in receivables_result else 0.5
            reminder_success_rate = (
                sum(1 for r in reminders_sent if r["sent"]) / len(reminders_sent)
                if reminders_sent else 1.0
            )
            confidence = round((data_quality * 0.6 + reminder_success_rate * 0.4), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: HITL for large outstanding amounts ---
            hitl_required = total_outstanding > HITL_OUTSTANDING_THRESHOLD or len(escalations) > 0

            if hitl_required:
                hitl_reasons = []
                if total_outstanding > HITL_OUTSTANDING_THRESHOLD:
                    hitl_reasons.append(f"outstanding INR {total_outstanding:,.0f} > {HITL_OUTSTANDING_THRESHOLD:,}")
                if escalations:
                    hitl_reasons.append(f"{len(escalations)} invoices overdue 90+ days")

                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="ar_escalation",
                    decision_required=DecisionRequired(
                        question=f"AR collections for {customer}: {'; '.join(hitl_reasons)}. Review required.",
                        options=[
                            DecisionOption(id="approve", label="Approve collection actions", action="proceed"),
                            DecisionOption(id="legal", label="Refer to legal", action="escalate"),
                            DecisionOption(id="write_off", label="Write off", action="write_off"),
                            DecisionOption(id="defer", label="Defer", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"AR escalation: {customer}, outstanding INR {total_outstanding:,.2f}",
                        recommendation="escalate" if len(escalations) > 2 else "review",
                        agent_confidence=confidence,
                        supporting_data={
                            "total_outstanding": total_outstanding,
                            "aging_summary": {k: len(v) for k, v in aging_buckets.items()},
                            "escalations": escalations,
                        },
                    ),
                    assignee=HITLAssignee(role="finance_partner", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered",
                    {
                        "status": "pending_review",
                        "customer": customer,
                        "total_outstanding": total_outstanding,
                        "aging_buckets": {k: len(v) for k, v in aging_buckets.items()},
                        "reminders_sent": reminders_sent,
                        "escalations": escalations,
                        "confidence": confidence,
                        "hitl_required": True,
                    },
                    confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed",
                {
                    "status": "collections_processed",
                    "customer": customer,
                    "total_outstanding": total_outstanding,
                    "aging_buckets": {k: len(v) for k, v in aging_buckets.items()},
                    "reminders_sent": reminders_sent,
                    "escalations": escalations,
                    "confidence": confidence,
                    "hitl_required": False,
                },
                confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("ar_collections_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "AR_ERR", "message": str(e)}, start=start,
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
