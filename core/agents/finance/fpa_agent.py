"""FP&A agent implementation."""

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

# Variance thresholds
VARIANCE_WARNING_PCT = 10.0
VARIANCE_ALERT_PCT = 15.0
# HITL if any line item variance > 20% or total variance > 15%
HITL_VARIANCE_PCT = 20.0
HITL_TOTAL_VARIANCE_PCT = 15.0


@AgentRegistry.register
class FpaAgentAgent(BaseAgent):
    agent_type = "fpa_agent"
    domain = "finance"
    confidence_floor = 0.78
    prompt_file = "fpa_agent.prompt.txt"

    async def execute(self, task):
        """Pull actuals from Tally, compare against budget, flag variances >15%, generate MIS."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            period = inputs.get("period", "")
            company = inputs.get("company", "")
            budget_id = inputs.get("budget_id", "")
            trace.append(f"FP&A analysis: period={period}, company={company}")

            # --- Step 1: Fetch actuals from Tally ---
            actuals_result = await self._safe_tool_call(
                "tally", "get_profit_and_loss",
                {"period": period, "company": company},
                trace, tool_calls,
            )

            actuals: dict[str, float] = {}
            if actuals_result and "error" not in actuals_result:
                actuals = {
                    "revenue": float(actuals_result.get("total_revenue", actuals_result.get("income", 0))),
                    "cogs": float(actuals_result.get("cost_of_goods_sold", actuals_result.get("cogs", 0))),
                    "gross_profit": float(actuals_result.get("gross_profit", 0)),
                    "opex": float(actuals_result.get("operating_expenses", actuals_result.get("opex", 0))),
                    "ebitda": float(actuals_result.get("ebitda", 0)),
                    "depreciation": float(actuals_result.get("depreciation", 0)),
                    "interest": float(actuals_result.get("interest_expense", 0)),
                    "tax": float(actuals_result.get("tax_expense", 0)),
                    "net_profit": float(actuals_result.get("net_profit", 0)),
                    "employee_costs": float(actuals_result.get("employee_costs", actuals_result.get("salaries", 0))),
                    "marketing_spend": float(actuals_result.get("marketing_expenses", 0)),
                    "admin_expenses": float(actuals_result.get("admin_expenses", 0)),
                }
                # Compute derived fields if missing
                if not actuals["gross_profit"] and actuals["revenue"]:
                    actuals["gross_profit"] = actuals["revenue"] - actuals["cogs"]
                if not actuals["ebitda"] and actuals["gross_profit"]:
                    actuals["ebitda"] = actuals["gross_profit"] - actuals["opex"]
                trace.append(
                    f"Actuals loaded: revenue={actuals['revenue']:,.2f}, "
                    f"net_profit={actuals['net_profit']:,.2f}"
                )
            else:
                trace.append("Actuals fetch failed")

            # --- Step 2: Fetch budget ---
            budget_result = await self._safe_tool_call(
                "google_sheets", "get_range",
                {
                    "spreadsheet_id": budget_id,
                    "range": f"Budget_{period}!A:Z",
                },
                trace, tool_calls,
            )

            budget: dict[str, float] = {}
            if budget_result and "error" not in budget_result:
                # Parse budget from spreadsheet data
                rows = budget_result.get("values", budget_result.get("rows", []))
                for row in rows:
                    if isinstance(row, dict):
                        line_item = str(row.get("line_item", row.get("category", ""))).lower().replace(" ", "_")
                        value = float(row.get("budget", row.get("amount", 0)))
                        budget[line_item] = value
                    elif isinstance(row, list) and len(row) >= 2:
                        line_item = str(row[0]).lower().replace(" ", "_")
                        try:
                            value = float(row[1])
                        except (ValueError, TypeError):
                            continue
                        budget[line_item] = value
                trace.append(f"Budget loaded: {len(budget)} line items")
            else:
                # Fallback: try fetching budget from Zoho
                budget_result = await self._safe_tool_call(
                    "zoho_books", "get_budget",
                    {"period": period, "company": company},
                    trace, tool_calls,
                )
                if budget_result and "error" not in budget_result:
                    budget = {k: float(v) for k, v in budget_result.items() if isinstance(v, (int, float, str))}
                    trace.append(f"Budget from Zoho: {len(budget)} items")
                else:
                    trace.append("Budget fetch failed")

            # --- Step 3: Compute variances ---
            variances: list[dict] = []
            alerts: list[dict] = []
            total_actual = sum(actuals.values())
            total_budget = sum(budget.values())

            all_items = set(actuals.keys()) | set(budget.keys())
            for item in sorted(all_items):
                actual_val = actuals.get(item, 0)
                budget_val = budget.get(item, 0)
                variance = actual_val - budget_val
                variance_pct = (variance / budget_val * 100) if budget_val else 0

                var_record = {
                    "line_item": item,
                    "actual": round(actual_val, 2),
                    "budget": round(budget_val, 2),
                    "variance": round(variance, 2),
                    "variance_pct": round(variance_pct, 2),
                    "favorable": (
                        variance > 0 if item == "revenue"
                        else variance < 0  # For expenses, negative variance is favorable
                    ),
                }

                # Flag significant variances
                if abs(variance_pct) > VARIANCE_ALERT_PCT:
                    var_record["alert"] = "critical"
                    alerts.append(var_record)
                elif abs(variance_pct) > VARIANCE_WARNING_PCT:
                    var_record["alert"] = "warning"
                    alerts.append(var_record)

                variances.append(var_record)

            total_variance_pct = (
                (total_actual - total_budget) / total_budget * 100 if total_budget else 0
            )

            trace.append(
                f"Variance analysis: {len(variances)} items, {len(alerts)} alerts, "
                f"total variance={total_variance_pct:.2f}%"
            )

            # --- Step 4: Generate MIS report data ---
            mis_report = {
                "period": period,
                "company": company,
                "key_metrics": {
                    "revenue": actuals.get("revenue", 0),
                    "gross_margin_pct": round(
                        (actuals.get("gross_profit", 0) / actuals.get("revenue", 1)) * 100, 2
                    ) if actuals.get("revenue") else 0,
                    "ebitda_margin_pct": round(
                        (actuals.get("ebitda", 0) / actuals.get("revenue", 1)) * 100, 2
                    ) if actuals.get("revenue") else 0,
                    "net_margin_pct": round(
                        (actuals.get("net_profit", 0) / actuals.get("revenue", 1)) * 100, 2
                    ) if actuals.get("revenue") else 0,
                    "opex_ratio": round(
                        (actuals.get("opex", 0) / actuals.get("revenue", 1)) * 100, 2
                    ) if actuals.get("revenue") else 0,
                },
                "actuals_summary": actuals,
                "budget_summary": budget,
                "top_variances": sorted(alerts, key=lambda x: abs(x["variance_pct"]), reverse=True)[:10],
            }

            # --- Step 5: Compute confidence ---
            factors: list[float] = []
            if actuals and actuals.get("revenue", 0) > 0:
                factors.append(0.90)
            else:
                factors.append(0.40)
            if budget and len(budget) > 3:
                factors.append(0.90)
            else:
                factors.append(0.40)
            # Penalize for extreme variances (data quality signal)
            critical_count = sum(1 for a in alerts if a.get("alert") == "critical")
            if critical_count == 0:
                factors.append(0.95)
            elif critical_count <= 3:
                factors.append(0.80)
            else:
                factors.append(0.60)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: HITL for extreme variances ---
            hitl_reasons: list[str] = []
            critical_alerts = [a for a in alerts if a.get("alert") == "critical"]
            if critical_alerts:
                hitl_reasons.append(
                    f"{len(critical_alerts)} line items with variance >{VARIANCE_ALERT_PCT}%"
                )
            if abs(total_variance_pct) > HITL_TOTAL_VARIANCE_PCT:
                hitl_reasons.append(f"total variance {total_variance_pct:.1f}% > {HITL_TOTAL_VARIANCE_PCT}%")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "analyzed",
                "period": period,
                "company": company,
                "variances": variances,
                "alerts": alerts,
                "total_variance_pct": round(total_variance_pct, 2),
                "mis_report": mis_report,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="fpa_variance_review",
                    decision_required=DecisionRequired(
                        question=f"FP&A for {period}: {'; '.join(hitl_reasons)}. Review MIS report?",
                        options=[
                            DecisionOption(id="approve", label="Accept and distribute MIS", action="proceed"),
                            DecisionOption(id="review", label="Review variances", action="defer"),
                            DecisionOption(id="reforecast", label="Trigger reforecast", action="reforecast"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"FP&A variance review for {period}",
                        recommendation="review" if critical_count > 3 else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "total_variance_pct": round(total_variance_pct, 2),
                            "critical_count": critical_count,
                            "top_variance": critical_alerts[0] if critical_alerts else {},
                        },
                    ),
                    assignee=HITLAssignee(role="cfo", notify_channels=["email"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("fpa_agent_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "FPA_ERR", "message": str(e)}, start=start,
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
