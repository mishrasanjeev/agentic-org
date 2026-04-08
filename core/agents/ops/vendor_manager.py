"""Vendor Manager agent implementation."""

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

# Vendor scoring weights
SCORING_WEIGHTS = {
    "delivery": 0.30,
    "quality": 0.30,
    "price": 0.20,
    "responsiveness": 0.10,
    "compliance": 0.10,
}
# SLA breach threshold
SLA_BREACH_SCORE = 60  # Vendor score below this triggers SLA breach flag
# Contract expiry warning days
CONTRACT_EXPIRY_WARNING_DAYS = 60
# HITL for SLA breaches or large PO mismatches
HITL_PO_MISMATCH_PCT = 5.0


@AgentRegistry.register
class VendorManagerAgent(BaseAgent):
    agent_type = "vendor_manager"
    domain = "ops"
    confidence_floor = 0.88
    prompt_file = "vendor_manager.prompt.txt"

    async def execute(self, task):
        """Track contract expiry, match PO to invoice, compute vendor score, flag SLA breaches."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            action = task.task.action if hasattr(task.task, "action") else task.task.get("action", "review_vendor")
            vendor_id = inputs.get("vendor_id", inputs.get("vendor", ""))
            vendor_name = inputs.get("vendor_name", vendor_id)
            trace.append(f"Vendor management: vendor={vendor_name}, action={action}")

            now = datetime.now(tz=UTC)

            # --- Step 1: Fetch vendor data ---
            vendor_result = await self._safe_tool_call(
                "tally", "get_ledger_details",
                {"ledger": vendor_name, "type": "sundry_creditor"},
                trace, tool_calls,
            )
            vendor_data: dict[str, Any] = {}
            if vendor_result and "error" not in vendor_result:
                vendor_data = vendor_result
                trace.append(f"Vendor data loaded: balance={vendor_data.get('closing_balance', 0)}")

            # --- Step 2: Check contract expiry ---
            contract_result = await self._safe_tool_call(
                "google_sheets", "get_range",
                {
                    "spreadsheet_id": inputs.get("contracts_sheet_id", ""),
                    "range": "Contracts!A:Z",
                },
                trace, tool_calls,
            )

            contracts: list[dict] = []
            expiring_contracts: list[dict] = []
            if contract_result and "error" not in contract_result:
                rows = contract_result.get("values", contract_result.get("rows", []))
                for row in rows:
                    if isinstance(row, dict):
                        contract_vendor = str(row.get("vendor", "")).lower()
                        if vendor_name.lower() in contract_vendor or not vendor_name:
                            end_date_str = row.get("end_date", row.get("expiry_date", ""))
                            try:
                                end_date = datetime.fromisoformat(end_date_str) if end_date_str else None
                            except (ValueError, TypeError):
                                end_date = None

                            contract = {
                                "contract_id": row.get("contract_id", row.get("id", "")),
                                "vendor": row.get("vendor", ""),
                                "end_date": end_date_str,
                                "value": float(row.get("value", row.get("amount", 0))),
                                "status": row.get("status", "active"),
                            }
                            contracts.append(contract)

                            if end_date:
                                days_to_expiry = (end_date - now).days
                                if 0 < days_to_expiry <= CONTRACT_EXPIRY_WARNING_DAYS:
                                    contract["days_to_expiry"] = days_to_expiry
                                    expiring_contracts.append(contract)
                                elif days_to_expiry <= 0:
                                    contract["days_to_expiry"] = days_to_expiry
                                    contract["status"] = "expired"
                                    expiring_contracts.append(contract)

                trace.append(f"Contracts: {len(contracts)} total, {len(expiring_contracts)} expiring/expired")

            # --- Step 3: Match PO to invoices ---
            po_result = await self._safe_tool_call(
                "tally", "get_purchase_orders",
                {"vendor": vendor_name, "status": "open"},
                trace, tool_calls,
            )

            invoice_result = await self._safe_tool_call(
                "tally", "get_purchase_vouchers",
                {"vendor": vendor_name, "period": inputs.get("period", "")},
                trace, tool_calls,
            )

            po_matches: list[dict] = []
            po_mismatches: list[dict] = []
            pos = po_result.get("orders", []) if po_result and "error" not in po_result else []
            invoices = invoice_result.get("vouchers", []) if invoice_result and "error" not in invoice_result else []

            po_by_number: dict[str, dict] = {}
            for po in pos:
                po_num = po.get("number", po.get("po_number", ""))
                if po_num:
                    po_by_number[po_num] = po

            for inv in invoices:
                ref_po = inv.get("po_number", inv.get("reference", ""))
                inv_amount = float(inv.get("amount", 0))
                if ref_po and ref_po in po_by_number:
                    po = po_by_number[ref_po]
                    po_amount = float(po.get("amount", 0))
                    diff_pct = abs(inv_amount - po_amount) / po_amount * 100 if po_amount else 0
                    match_record = {
                        "po_number": ref_po,
                        "po_amount": po_amount,
                        "invoice_amount": inv_amount,
                        "diff_pct": round(diff_pct, 2),
                        "matched": diff_pct <= HITL_PO_MISMATCH_PCT,
                    }
                    if match_record["matched"]:
                        po_matches.append(match_record)
                    else:
                        po_mismatches.append(match_record)

            trace.append(f"PO matching: {len(po_matches)} matched, {len(po_mismatches)} mismatched")

            # --- Step 4: Compute vendor score ---
            score_inputs = inputs.get("performance_data", {})
            delivery_score = float(score_inputs.get("on_time_delivery_pct", 85))
            quality_score = float(score_inputs.get("quality_acceptance_pct", 90))
            price_score = float(score_inputs.get("price_competitiveness", 75))
            responsiveness_score = float(score_inputs.get("responsiveness_rating", 80))
            compliance_score = float(score_inputs.get("compliance_rating", 85))

            # If not in inputs, try fetching historical data
            perf_result: dict[str, Any] = {}
            if not score_inputs:
                perf_result = await self._safe_tool_call(
                    "google_sheets", "get_range",
                    {
                        "spreadsheet_id": inputs.get("vendor_scores_sheet_id", ""),
                        "range": "Scores!A:Z",
                    },
                    trace, tool_calls,
                )
                if perf_result and "error" not in perf_result:
                    rows = perf_result.get("values", [])
                    for row in rows:
                        if isinstance(row, dict) and vendor_name.lower() in str(row.get("vendor", "")).lower():
                            delivery_score = float(row.get("delivery", delivery_score))
                            quality_score = float(row.get("quality", quality_score))
                            price_score = float(row.get("price", price_score))
                            responsiveness_score = float(row.get("responsiveness", responsiveness_score))
                            compliance_score = float(row.get("compliance", compliance_score))
                            break

            weighted_score = round(
                delivery_score * SCORING_WEIGHTS["delivery"]
                + quality_score * SCORING_WEIGHTS["quality"]
                + price_score * SCORING_WEIGHTS["price"]
                + responsiveness_score * SCORING_WEIGHTS["responsiveness"]
                + compliance_score * SCORING_WEIGHTS["compliance"],
                1,
            )

            score_breakdown = {
                "delivery": {"score": delivery_score, "weight": SCORING_WEIGHTS["delivery"]},
                "quality": {"score": quality_score, "weight": SCORING_WEIGHTS["quality"]},
                "price": {"score": price_score, "weight": SCORING_WEIGHTS["price"]},
                "responsiveness": {"score": responsiveness_score, "weight": SCORING_WEIGHTS["responsiveness"]},
                "compliance": {"score": compliance_score, "weight": SCORING_WEIGHTS["compliance"]},
                "weighted_total": weighted_score,
            }

            sla_breached = weighted_score < SLA_BREACH_SCORE
            trace.append(f"Vendor score: {weighted_score}/100, SLA breach={sla_breached}")

            # --- Step 5: Compute confidence ---
            factors: list[float] = []
            factors.append(0.90 if vendor_data else 0.50)
            factors.append(0.85 if contracts else 0.60)
            factors.append(0.90 if pos or invoices else 0.60)
            factors.append(0.85 if score_inputs or (perf_result and "error" not in perf_result) else 0.60)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: Notifications ---
            if expiring_contracts:
                await self._safe_tool_call(
                    "slack", "send_message",
                    {
                        "channel": "#procurement",
                        "text": (
                            f"Contract alert for {vendor_name}: "
                            f"{len(expiring_contracts)} contracts expiring within {CONTRACT_EXPIRY_WARNING_DAYS} days"
                        ),
                    },
                    trace, tool_calls,
                )

            if sla_breached:
                await self._safe_tool_call(
                    "slack", "send_message",
                    {
                        "channel": "#procurement",
                        "text": (
                            f"SLA breach alert: {vendor_name} "
                            f"score {weighted_score}/100 (threshold: {SLA_BREACH_SCORE})"
                        ),
                    },
                    trace, tool_calls,
                )

            # --- Step 7: HITL ---
            hitl_reasons: list[str] = []
            if sla_breached:
                hitl_reasons.append(f"vendor score {weighted_score} < SLA threshold {SLA_BREACH_SCORE}")
            if po_mismatches:
                hitl_reasons.append(f"{len(po_mismatches)} PO-invoice mismatches")
            if any(c.get("status") == "expired" for c in expiring_contracts):
                hitl_reasons.append("expired contracts found")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "reviewed",
                "vendor": vendor_name,
                "vendor_score": score_breakdown,
                "sla_breached": sla_breached,
                "contracts": {
                    "total": len(contracts),
                    "expiring": len(expiring_contracts),
                    "details": expiring_contracts,
                },
                "po_matching": {
                    "matched": len(po_matches),
                    "mismatched": len(po_mismatches),
                    "mismatches": po_mismatches,
                },
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="vendor_review",
                    decision_required=DecisionRequired(
                        question=f"Vendor {vendor_name}: {'; '.join(hitl_reasons)}. Action needed.",
                        options=[
                            DecisionOption(id="renew", label="Renew contracts", action="proceed"),
                            DecisionOption(id="negotiate", label="Renegotiate terms", action="defer"),
                            DecisionOption(id="replace", label="Find alternative vendor", action="replace"),
                            DecisionOption(id="accept", label="Accept current state", action="proceed"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Vendor review for {vendor_name}",
                        recommendation="negotiate" if sla_breached else "renew",
                        agent_confidence=confidence,
                        supporting_data={
                            "vendor_score": weighted_score,
                            "expiring_contracts": len(expiring_contracts),
                            "po_mismatches": len(po_mismatches),
                        },
                    ),
                    assignee=HITLAssignee(role="procurement_head", notify_channels=["email"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("vendor_manager_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "VENDOR_ERR", "message": str(e)}, start=start,
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
