"""AP Processor agent implementation."""

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

# Indian threshold: INR 5,00,000
HITL_AMOUNT_THRESHOLD = 500_000
MATCH_TOLERANCE_PCT = 2.0


@AgentRegistry.register
class ApProcessorAgent(BaseAgent):
    agent_type = "ap_processor"
    domain = "finance"
    confidence_floor = 0.88
    prompt_file = "ap_processor.prompt.txt"

    async def execute(self, task):
        """Process accounts-payable invoices with 3-way match, confidence scoring, and HITL."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})

            invoice = inputs.get("invoice", inputs)
            amount = float(invoice.get("amount", 0))
            vendor = invoice.get("vendor", "")
            po_number = invoice.get("po_number", "")
            grn_number = invoice.get("grn_number", "")
            invoice_number = invoice.get("invoice_number", "")
            trace.append(f"Processing invoice: vendor={vendor}, amount={amount}, PO={po_number}")

            # --- Step 1: Validate vendor exists in books ---
            vendor_valid = False
            vendor_result = await self._safe_tool_call(
                "tally", "get_ledger_balance", {"ledger": vendor},
                trace, tool_calls,
            )
            if vendor_result and "error" not in vendor_result:
                vendor_valid = True
                trace.append(f"Vendor '{vendor}' verified in books")
            else:
                trace.append(f"Vendor '{vendor}' not found or tool error; proceeding with lower confidence")

            # --- Step 2: 3-way match (PO, GRN, Invoice) ---
            po_match = False
            grn_match = False
            match_pct = 0.0

            if po_number:
                po_result = await self._safe_tool_call(
                    "tally", "get_voucher", {"number": po_number, "type": "purchase_order"},
                    trace, tool_calls,
                )
                if po_result and "error" not in po_result:
                    po_amount = float(po_result.get("amount", 0))
                    if po_amount > 0:
                        diff_pct = abs(po_amount - amount) / po_amount * 100
                        po_match = diff_pct <= MATCH_TOLERANCE_PCT
                        match_pct = max(match_pct, 100 - diff_pct)
                        trace.append(f"PO match: diff={diff_pct:.2f}%, matched={po_match}")
                    else:
                        trace.append("PO amount is zero; cannot compute match")
                else:
                    trace.append(f"PO {po_number} lookup failed")

            if grn_number:
                grn_result = await self._safe_tool_call(
                    "tally", "get_voucher", {"number": grn_number, "type": "goods_receipt"},
                    trace, tool_calls,
                )
                if grn_result and "error" not in grn_result:
                    grn_qty = float(grn_result.get("quantity", 0))
                    invoice_qty = float(invoice.get("quantity", grn_qty))
                    if grn_qty > 0:
                        qty_diff_pct = abs(grn_qty - invoice_qty) / grn_qty * 100
                        grn_match = qty_diff_pct <= MATCH_TOLERANCE_PCT
                        trace.append(f"GRN match: qty_diff={qty_diff_pct:.2f}%, matched={grn_match}")
                    else:
                        trace.append("GRN quantity is zero; cannot compute match")
                else:
                    trace.append(f"GRN {grn_number} lookup failed")

            # --- Step 3: Compute confidence from match results ---
            confidence_factors: list[float] = []
            if vendor_valid:
                confidence_factors.append(0.95)
            else:
                confidence_factors.append(0.50)

            if po_number:
                if po_match:
                    confidence_factors.append(0.98)
                elif match_pct > 90:
                    confidence_factors.append(0.80)
                else:
                    confidence_factors.append(0.50)
            if grn_number:
                confidence_factors.append(0.98 if grn_match else 0.55)

            confidence = sum(confidence_factors) / len(confidence_factors) if confidence_factors else 0.5
            confidence = round(min(max(confidence, 0.0), 1.0), 3)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 4: Determine HITL ---
            hitl_reasons: list[str] = []
            if amount > HITL_AMOUNT_THRESHOLD:
                hitl_reasons.append(f"amount {amount:,.0f} > threshold {HITL_AMOUNT_THRESHOLD:,}")
            if confidence < 0.8:
                hitl_reasons.append(f"confidence {confidence:.3f} < 0.80")
            if not vendor_valid:
                hitl_reasons.append("vendor not verified")

            hitl_required = len(hitl_reasons) > 0

            if hitl_required:
                trace.append(f"HITL required: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="ap_threshold_breach",
                    decision_required=DecisionRequired(
                        question=(
                            f"Invoice {invoice_number} for {vendor} "
                            f"(INR {amount:,.2f}) needs review: {'; '.join(hitl_reasons)}"
                        ),
                        options=[
                            DecisionOption(id="approve", label="Approve and post", action="proceed"),
                            DecisionOption(id="reject", label="Reject invoice", action="reject"),
                            DecisionOption(id="hold", label="Hold for investigation", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"AP invoice requires approval: {'; '.join(hitl_reasons)}",
                        recommendation="review" if confidence < 0.7 else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "vendor": vendor,
                            "amount": amount,
                            "po_match": po_match,
                            "grn_match": grn_match,
                            "match_pct": round(match_pct, 2),
                        },
                    ),
                    assignee=HITLAssignee(role="finance_lead", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered",
                    {
                        "status": "pending_approval",
                        "invoice_number": invoice_number,
                        "vendor": vendor,
                        "amount": amount,
                        "confidence": confidence,
                        "hitl_required": True,
                        "hitl_reasons": hitl_reasons,
                        "match_result": {
                            "vendor_valid": vendor_valid,
                            "po_match": po_match,
                            "grn_match": grn_match,
                            "match_pct": round(match_pct, 2),
                        },
                    },
                    confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            # --- Step 5: Auto-post voucher ---
            post_result = await self._safe_tool_call(
                "tally", "post_voucher",
                {
                    "type": "purchase",
                    "vendor": vendor,
                    "amount": amount,
                    "reference": invoice_number,
                    "po_number": po_number,
                },
                trace, tool_calls,
            )
            posted = post_result and "error" not in post_result
            if posted:
                trace.append(f"Voucher posted successfully for invoice {invoice_number}")
            else:
                trace.append("Voucher posting failed; marking as pending")

            return self._make_result(
                task, msg_id, "completed",
                {
                    "status": "posted" if posted else "post_failed",
                    "invoice_number": invoice_number,
                    "vendor": vendor,
                    "amount": amount,
                    "confidence": confidence,
                    "hitl_required": False,
                    "match_result": {
                        "vendor_valid": vendor_valid,
                        "po_match": po_match,
                        "grn_match": grn_match,
                        "match_pct": round(match_pct, 2),
                    },
                    "voucher_posted": posted,
                    "voucher_ref": post_result.get("voucher_id", "") if posted else "",
                },
                confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("ap_processor_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "AP_ERR", "message": str(e)}, start=start,
            )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
    ) -> dict[str, Any]:
        """Call a tool with error handling, logging trace and tool records."""
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
