"""Month-End Close agent implementation."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.finance._pnl_chain import fetch_pnl_via_chain
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

# Close checklist items — each must be completed
CLOSE_CHECKLIST = [
    "accruals",
    "provisions",
    "depreciation",
    "prepaid_amortization",
    "intercompany_elimination",
    "bank_reconciliation",
    "inventory_adjustment",
    "revenue_recognition",
]
# Trial balance tolerance for debit-credit mismatch
TB_TOLERANCE = 1.0  # INR 1


@AgentRegistry.register
class CloseAgentAgent(BaseAgent):
    agent_type = "close_agent"
    domain = "finance"
    confidence_floor = 0.8
    prompt_file = "close_agent.prompt.txt"

    async def execute(self, task):
        """Run month-end close: checklist, trial balance validation, P&L generation."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            period = inputs.get("period", "")
            company = inputs.get("company", "")
            trace.append(f"Month-end close: period={period}, company={company}")

            checklist_results: dict[str, dict] = {}
            failed_items: list[str] = []

            # --- Step 1: Run each checklist item ---
            for item in CLOSE_CHECKLIST:
                trace.append(f"Processing checklist item: {item}")
                result = await self._run_checklist_item(item, period, company, trace, tool_calls)
                checklist_results[item] = result
                if result.get("status") != "completed":
                    failed_items.append(item)

            completed_count = len(CLOSE_CHECKLIST) - len(failed_items)
            trace.append(
                f"Checklist: {completed_count}/{len(CLOSE_CHECKLIST)} completed, "
                f"failed: {failed_items or 'none'}"
            )

            # --- Step 2: Validate trial balance ---
            tb_result = await self._safe_tool_call(
                "tally", "get_trial_balance",
                {"period": period, "company": company},
                trace, tool_calls,
            )

            tb_valid = False
            tb_summary: dict[str, Any] = {}
            if tb_result and "error" not in tb_result:
                total_debit = float(tb_result.get("total_debit", 0))
                total_credit = float(tb_result.get("total_credit", 0))
                diff = abs(total_debit - total_credit)
                tb_valid = diff <= TB_TOLERANCE
                tb_summary = {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "difference": round(diff, 2),
                    "balanced": tb_valid,
                }
                trace.append(
                    f"Trial balance: debit={total_debit:,.2f}, credit={total_credit:,.2f}, "
                    f"diff={diff:.2f}, balanced={tb_valid}"
                )
            else:
                trace.append("Trial balance fetch failed")
                tb_summary = {"error": "fetch_failed"}

            # --- Step 3: Generate P&L ---
            # Ramesh/Uday 2026-04-28: tally has no `get_profit_and_loss`
            # tool. Use the same connector chain as the FP&A agent so
            # whichever P&L connector the tenant has wired (Zoho/QB/
            # Tally trial-balance) supplies the numbers.
            pnl_result, pnl_source = await fetch_pnl_via_chain(
                self, period, company, trace, tool_calls,
            )

            pnl_summary: dict[str, Any] = {}
            if pnl_result:
                revenue = float(pnl_result.get("revenue", 0))
                # Expenses ≈ revenue - net_profit when expenses isn't reported
                # directly; fall back to opex/COGS sum if available.
                if "expenses" in pnl_result:
                    expenses = float(pnl_result["expenses"])
                else:
                    expenses = float(
                        pnl_result.get("opex", 0) + pnl_result.get("cogs", 0)
                    ) or max(revenue - float(pnl_result.get("net_profit", 0)), 0)
                net_profit = float(pnl_result.get("net_profit", revenue - expenses))
                pnl_summary = {
                    "revenue": revenue,
                    "expenses": expenses,
                    "net_profit": net_profit,
                    "margin_pct": round((net_profit / revenue * 100) if revenue else 0, 2),
                    "source": pnl_source,
                }
                trace.append(
                    f"P&L from {pnl_source}: revenue={revenue:,.2f}, "
                    f"expenses={expenses:,.2f}, net={net_profit:,.2f}"
                )
            else:
                trace.append(
                    "P&L generation failed: no P&L connector wired "
                    "(expected zoho_books / quickbooks / tally)"
                )
                pnl_summary = {"error": "generation_failed"}

            # --- Step 4: Generate balance sheet ---
            # tally has no `get_balance_sheet`. Try Zoho / QuickBooks
            # which both register the tool, in priority order.
            bs_summary: dict[str, Any] = {}
            for bs_connector in ("zoho_books", "quickbooks"):
                bs_result = await self._safe_tool_call(
                    bs_connector, "get_balance_sheet",
                    {"period": period, "company": company},
                    trace, tool_calls,
                )
                if bs_result and "error" not in bs_result:
                    total_assets = float(bs_result.get("total_assets", 0))
                    total_liabilities = float(bs_result.get("total_liabilities", 0))
                    equity = float(bs_result.get("equity", total_assets - total_liabilities))
                    bs_summary = {
                        "total_assets": total_assets,
                        "total_liabilities": total_liabilities,
                        "equity": equity,
                        "source": bs_connector,
                    }
                    trace.append(
                        f"Balance sheet from {bs_connector}: "
                        f"assets={total_assets:,.2f}, liabilities={total_liabilities:,.2f}"
                    )
                    break
            else:
                trace.append(
                    "Balance sheet fetch failed: no balance-sheet "
                    "connector wired (expected zoho_books / quickbooks)"
                )

            # --- Step 5: Compute confidence ---
            factors: list[float] = []
            # Checklist completion
            checklist_score = completed_count / len(CLOSE_CHECKLIST) if CLOSE_CHECKLIST else 0
            factors.append(checklist_score)
            # TB validation
            factors.append(0.95 if tb_valid else 0.40)
            # P&L availability
            factors.append(0.90 if pnl_summary and "error" not in pnl_summary else 0.50)
            # BS availability
            factors.append(0.90 if bs_summary and "error" not in bs_summary else 0.50)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: HITL ---
            hitl_reasons: list[str] = []
            if failed_items:
                hitl_reasons.append(f"checklist items failed: {', '.join(failed_items)}")
            if not tb_valid:
                hitl_reasons.append("trial balance not balanced")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "closed" if not hitl_reasons else "pending_review",
                "period": period,
                "company": company,
                "checklist": {
                    "total": len(CLOSE_CHECKLIST),
                    "completed": completed_count,
                    "failed": failed_items,
                    "details": checklist_results,
                },
                "trial_balance": tb_summary,
                "profit_and_loss": pnl_summary,
                "balance_sheet": bs_summary,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="close_review",
                    decision_required=DecisionRequired(
                        question=f"Month-end close for {period}: {'; '.join(hitl_reasons)}. Review required.",
                        options=[
                            DecisionOption(id="approve", label="Approve close", action="proceed"),
                            DecisionOption(id="fix", label="Fix and re-run", action="retry"),
                            DecisionOption(id="defer", label="Defer to next period", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Month-end close for {period}",
                        recommendation="fix" if not tb_valid else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "checklist_score": round(checklist_score, 2),
                            "tb_balanced": tb_valid,
                            "failed_items": failed_items,
                        },
                    ),
                    assignee=HITLAssignee(role="finance_controller", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("close_agent_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "CLOSE_ERR", "message": str(e)}, start=start,
            )

    async def _run_checklist_item(
        self,
        item: str,
        period: str,
        company: str,
        trace: list[str],
        tool_calls: list[ToolCallRecord],
    ) -> dict[str, Any]:
        """Run a single close checklist item and return status."""
        base = {"period": period, "company": company}
        tool_mapping = {
            "accruals": ("tally", "post_journal_entries", {**base, "type": "accrual"}),
            "provisions": ("tally", "post_journal_entries", {**base, "type": "provision"}),
            "depreciation": ("tally", "run_depreciation", base),
            "prepaid_amortization": (
                "tally", "post_journal_entries", {**base, "type": "prepaid_amortization"},
            ),
            "intercompany_elimination": (
                "tally", "post_journal_entries", {**base, "type": "intercompany"},
            ),
            "bank_reconciliation": ("tally", "get_bank_reconciliation_status", base),
            "inventory_adjustment": ("tally", "post_inventory_adjustment", base),
            "revenue_recognition": (
                "tally", "post_journal_entries", {**base, "type": "revenue_recognition"},
            ),
        }

        if item not in tool_mapping:
            return {"status": "skipped", "reason": f"unknown item: {item}"}

        connector, tool, params = tool_mapping[item]
        result = await self._safe_tool_call(connector, tool, params, trace, tool_calls)

        if result and "error" not in result:
            return {"status": "completed", "detail": result}
        else:
            return {"status": "failed", "error": result.get("error", "unknown")}

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
