"""Tax Compliance agent implementation."""

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

# Indian tax deadlines (day of month)
GST_FILING_DEADLINES = {
    "GSTR1": 11,
    "GSTR3B": 20,
    "GSTR9": 31,  # December (annual)
}
TDS_QUARTERLY_DEADLINES = {1: 31, 2: 31, 3: 31, 4: 30}  # July, Oct, Jan, May
# Mismatch tolerance between books and GSTR-2A/26AS
MISMATCH_TOLERANCE_PCT = 1.0
# HITL always required before actual filing
HITL_BEFORE_FILING = True


@AgentRegistry.register
class TaxComplianceAgent(BaseAgent):
    agent_type = "tax_compliance"
    domain = "finance"
    confidence_floor = 0.92
    prompt_file = "tax_compliance.prompt.txt"

    async def execute(self, task):
        """Check filing deadlines, prepare return data, validate against 2A/26AS, HITL before filing."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            return_type = inputs.get("return_type", "GSTR3B")
            period = inputs.get("period", "")
            gstin = inputs.get("gstin", "")
            tan = inputs.get("tan", "")
            trace.append(f"Tax compliance: type={return_type}, period={period}, GSTIN={gstin}")

            # --- Step 1: Check filing deadline ---
            deadline_info = self._check_deadline(return_type, period)
            trace.append(f"Deadline: {deadline_info['deadline']}, days_remaining={deadline_info['days_remaining']}")

            # --- Step 2: Fetch data from books ---
            if return_type.startswith("GSTR"):
                # Fetch GST data from Tally
                books_result = await self._safe_tool_call(
                    "tally", "get_gst_summary",
                    {"gstin": gstin, "period": period, "return_type": return_type},
                    trace, tool_calls,
                )
                books_data = books_result if books_result and "error" not in books_result else {}

                # Fetch GSTR-2A from GST portal for validation
                portal_result = await self._safe_tool_call(
                    "gst_portal", "get_gstr2a",
                    {"gstin": gstin, "period": period},
                    trace, tool_calls,
                )
                portal_data = portal_result if portal_result and "error" not in portal_result else {}
            elif return_type in ("TDS_24Q", "TDS_26Q", "TDS_27Q"):
                # Fetch TDS data
                books_result = await self._safe_tool_call(
                    "tally", "get_tds_summary",
                    {"tan": tan, "period": period, "form": return_type},
                    trace, tool_calls,
                )
                books_data = books_result if books_result and "error" not in books_result else {}

                # Fetch 26AS for validation
                portal_result = await self._safe_tool_call(
                    "traces_portal", "get_26as",
                    {"tan": tan, "period": period},
                    trace, tool_calls,
                )
                portal_data = portal_result if portal_result and "error" not in portal_result else {}
            else:
                books_data = {}
                portal_data = {}
                trace.append(f"Unsupported return type: {return_type}")

            # --- Step 3: Validate books against portal data ---
            mismatches: list[dict] = []
            total_books = float(books_data.get("total_tax", books_data.get("total_liability", 0)))
            total_portal = float(portal_data.get("total_tax", portal_data.get("total_credit", 0)))

            if total_books > 0 and total_portal > 0:
                diff_pct = abs(total_books - total_portal) / total_books * 100
                if diff_pct > MISMATCH_TOLERANCE_PCT:
                    mismatches.append({
                        "type": "total_mismatch",
                        "books_value": total_books,
                        "portal_value": total_portal,
                        "diff_pct": round(diff_pct, 2),
                    })
                    trace.append(f"Mismatch: books={total_books:.2f} vs portal={total_portal:.2f} ({diff_pct:.2f}%)")

            # Line-item validation if available
            books_items = books_data.get("line_items", [])
            portal_items = portal_data.get("line_items", [])
            if books_items and portal_items:
                portal_by_gstin = {
                    item.get("supplier_gstin", ""): item for item in portal_items
                }
                for b_item in books_items:
                    supplier_gstin = b_item.get("supplier_gstin", "")
                    p_item = portal_by_gstin.get(supplier_gstin)
                    if not p_item:
                        mismatches.append({
                            "type": "missing_in_portal",
                            "supplier_gstin": supplier_gstin,
                            "books_value": b_item.get("taxable_value", 0),
                        })
                    elif p_item:
                        b_val = float(b_item.get("taxable_value", 0))
                        p_val = float(p_item.get("taxable_value", 0))
                        if b_val > 0 and abs(b_val - p_val) / b_val * 100 > MISMATCH_TOLERANCE_PCT:
                            mismatches.append({
                                "type": "value_mismatch",
                                "supplier_gstin": supplier_gstin,
                                "books_value": b_val,
                                "portal_value": p_val,
                            })

            trace.append(f"Validation done: {len(mismatches)} mismatches found")

            # --- Step 4: Compute tax liability ---
            liability = {
                "cgst": float(books_data.get("cgst", 0)),
                "sgst": float(books_data.get("sgst", 0)),
                "igst": float(books_data.get("igst", 0)),
                "cess": float(books_data.get("cess", 0)),
                "total_output": float(books_data.get("total_output", 0)),
                "total_itc": float(books_data.get("total_itc", total_portal)),
                "net_liability": float(books_data.get("net_liability", total_books - total_portal)),
            }

            # --- Step 5: Compute confidence ---
            confidence_factors: list[float] = []
            # Data availability
            if books_data and "error" not in books_data:
                confidence_factors.append(0.9)
            else:
                confidence_factors.append(0.3)
            if portal_data and "error" not in portal_data:
                confidence_factors.append(0.9)
            else:
                confidence_factors.append(0.4)
            # Mismatch penalty
            if not mismatches:
                confidence_factors.append(0.98)
            elif len(mismatches) <= 3:
                confidence_factors.append(0.75)
            else:
                confidence_factors.append(0.50)
            # Deadline pressure
            if deadline_info["days_remaining"] < 2:
                confidence_factors.append(0.70)
            elif deadline_info["days_remaining"] < 5:
                confidence_factors.append(0.85)
            else:
                confidence_factors.append(0.95)

            confidence = round(sum(confidence_factors) / len(confidence_factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: HITL always required before filing ---
            hitl_reasons: list[str] = ["filing approval required by policy"]
            if mismatches:
                hitl_reasons.append(f"{len(mismatches)} mismatches with portal data")
            if deadline_info["days_remaining"] < 3:
                hitl_reasons.append(f"only {deadline_info['days_remaining']} days to deadline")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} below floor")

            output = {
                "status": "prepared",
                "return_type": return_type,
                "period": period,
                "gstin": gstin,
                "deadline": deadline_info,
                "liability": liability,
                "mismatches": mismatches,
                "mismatch_count": len(mismatches),
                "confidence": confidence,
                "hitl_required": True,
            }

            trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
            hitl_request = HITLRequest(
                hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                trigger_condition="; ".join(hitl_reasons),
                trigger_type="tax_filing_approval",
                decision_required=DecisionRequired(
                    question=(
                        f"{return_type} for {period}: net liability INR {liability['net_liability']:,.2f}, "
                        f"{len(mismatches)} mismatches. Approve filing?"
                    ),
                    options=[
                        DecisionOption(id="file", label="Approve and file", action="proceed"),
                        DecisionOption(id="review", label="Review mismatches first", action="defer"),
                        DecisionOption(id="reject", label="Reject — rework needed", action="reject"),
                    ],
                ),
                context=HITLContext(
                    summary=f"Tax return {return_type} ready for {period}",
                    recommendation="file" if not mismatches else "review",
                    agent_confidence=confidence,
                    supporting_data={
                        "liability": liability,
                        "mismatches_count": len(mismatches),
                        "deadline_days": deadline_info["days_remaining"],
                    },
                ),
                assignee=HITLAssignee(
                    role="tax_partner",
                    notify_channels=["email", "slack"],
                    escalation_chain=["finance_head", "managing_partner"],
                ),
            )
            return self._make_result(
                task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                hitl_request=hitl_request, start=start,
            )

        except Exception as e:
            logger.error("tax_compliance_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "TAX_ERR", "message": str(e)}, start=start,
            )

    @staticmethod
    def _check_deadline(return_type: str, period: str) -> dict[str, Any]:
        """Compute deadline date and days remaining for a return type."""
        now = datetime.now(tz=UTC)
        # Parse period like "2026-03" or "Q4-2026"
        try:
            if return_type in GST_FILING_DEADLINES:
                day = GST_FILING_DEADLINES[return_type]
                if "-" in period and len(period) == 7:
                    year, month = period.split("-")
                    # Filing is due in the next month
                    filing_month = int(month) + 1
                    filing_year = int(year)
                    if filing_month > 12:
                        filing_month = 1
                        filing_year += 1
                    deadline = datetime(filing_year, filing_month, min(day, 28), tzinfo=UTC)
                else:
                    deadline = now
            else:
                deadline = now
        except (ValueError, TypeError):
            deadline = now

        days_remaining = max((deadline - now).days, 0)
        return {
            "deadline": deadline.strftime("%Y-%m-%d"),
            "days_remaining": days_remaining,
            "is_overdue": days_remaining == 0 and deadline < now,
        }

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
