"""Payroll Engine agent implementation."""

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

# Indian statutory deduction rates
PF_RATE = 0.12  # 12% of basic
ESI_RATE = 0.0075  # 0.75% employee share (employer 3.25%)
ESI_WAGE_CEILING = 21_000  # Monthly gross ceiling for ESI applicability
# Professional Tax (Karnataka/Maharashtra typical monthly slab)
PT_SLABS = [
    (0, 15_000, 0),
    (15_001, 25_000, 150),
    (25_001, float("inf"), 200),
]
# Income tax slabs (new regime FY 2025-26)
TAX_SLABS_NEW_REGIME = [
    (0, 300_000, 0.0),
    (300_001, 700_000, 0.05),
    (700_001, 1_000_000, 0.10),
    (1_000_001, 1_200_000, 0.15),
    (1_200_001, 1_500_000, 0.20),
    (1_500_001, float("inf"), 0.30),
]
# HITL if total payroll change >10% from last month
PAYROLL_CHANGE_THRESHOLD_PCT = 10.0


@AgentRegistry.register
class PayrollEngineAgent(BaseAgent):
    agent_type = "payroll_engine"
    domain = "hr"
    confidence_floor = 0.99
    prompt_file = "payroll_engine.prompt.txt"

    async def execute(self, task):
        """Fetch attendance, compute gross, calculate deductions (PF/ESI/PT/TDS), generate payslips."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            period = inputs.get("period", "")  # e.g. "2026-03"
            company = inputs.get("company", "")
            employees = inputs.get("employees", [])
            trace.append(f"Payroll run: period={period}, company={company}, employees={len(employees)}")

            # --- Step 1: Fetch employee list if not provided ---
            if not employees:
                emp_result = await self._safe_tool_call(
                    "greythr", "get_active_employees",
                    {"company": company, "status": "active"},
                    trace, tool_calls,
                )
                if emp_result and "error" not in emp_result:
                    employees = emp_result.get("employees", [])
                    trace.append(f"Fetched {len(employees)} active employees")
                else:
                    trace.append("Failed to fetch employees")

            # --- Step 2: Fetch attendance data ---
            attendance_result = await self._safe_tool_call(
                "greythr", "get_attendance_summary",
                {"period": period, "company": company},
                trace, tool_calls,
            )
            attendance_map: dict[str, dict] = {}
            if attendance_result and "error" not in attendance_result:
                for att in attendance_result.get("records", []):
                    emp_id = att.get("employee_id", "")
                    attendance_map[emp_id] = att
                trace.append(f"Attendance loaded for {len(attendance_map)} employees")

            # --- Step 3: Fetch previous month total for comparison ---
            prev_total_result = await self._safe_tool_call(
                "greythr", "get_payroll_summary",
                {"period": self._prev_period(period), "company": company},
                trace, tool_calls,
            )
            prev_total = 0.0
            if prev_total_result and "error" not in prev_total_result:
                prev_total = float(prev_total_result.get("total_net_pay", 0))

            # --- Step 4: Compute payroll for each employee ---
            payslips: list[dict] = []
            total_gross = 0.0
            total_net = 0.0
            total_pf = 0.0
            total_esi = 0.0
            total_pt = 0.0
            total_tds = 0.0
            errors: list[dict] = []

            for emp in employees:
                try:
                    emp_id = emp.get("employee_id", emp.get("id", ""))
                    emp_name = emp.get("name", "")
                    ctc_annual = float(emp.get("ctc", 0))
                    basic_monthly = float(emp.get("basic", ctc_annual * 0.40 / 12))
                    hra_monthly = float(emp.get("hra", basic_monthly * 0.50))
                    special_allowance = float(
                        emp.get("special_allowance", (ctc_annual / 12) - basic_monthly - hra_monthly)
                    )

                    # Attendance adjustment
                    att = attendance_map.get(emp_id, {})
                    working_days = float(att.get("working_days", 30))
                    present_days = float(att.get("present_days", att.get("days_worked", working_days)))
                    lop_days = float(att.get("lop_days", working_days - present_days))

                    day_rate = (basic_monthly + hra_monthly + special_allowance) / working_days if working_days else 0
                    lop_deduction = day_rate * lop_days
                    gross_salary = basic_monthly + hra_monthly + special_allowance - lop_deduction

                    # PF: 12% of basic
                    pf_deduction = round(basic_monthly * PF_RATE, 2)

                    # ESI: applicable if gross <= ceiling
                    esi_deduction = 0.0
                    if gross_salary <= ESI_WAGE_CEILING:
                        esi_deduction = round(gross_salary * ESI_RATE, 2)

                    # Professional Tax
                    pt_deduction = 0.0
                    for low, high, pt_amt in PT_SLABS:
                        if low <= gross_salary <= high:
                            pt_deduction = pt_amt
                            break

                    # TDS: estimate monthly TDS from annual income tax
                    annual_taxable = ctc_annual - (pf_deduction * 12) - 50_000  # Standard deduction
                    annual_taxable = max(annual_taxable, 0)
                    annual_tax = self._compute_income_tax(annual_taxable)
                    tds_monthly = round(annual_tax / 12, 2)

                    total_deductions = pf_deduction + esi_deduction + pt_deduction + tds_monthly
                    net_salary = round(gross_salary - total_deductions, 2)

                    payslip = {
                        "employee_id": emp_id,
                        "employee_name": emp_name,
                        "period": period,
                        "earnings": {
                            "basic": round(basic_monthly, 2),
                            "hra": round(hra_monthly, 2),
                            "special_allowance": round(max(special_allowance, 0), 2),
                            "gross_salary": round(gross_salary, 2),
                        },
                        "attendance": {
                            "working_days": working_days,
                            "present_days": present_days,
                            "lop_days": lop_days,
                            "lop_deduction": round(lop_deduction, 2),
                        },
                        "deductions": {
                            "pf": pf_deduction,
                            "esi": esi_deduction,
                            "professional_tax": pt_deduction,
                            "tds": tds_monthly,
                            "total_deductions": round(total_deductions, 2),
                        },
                        "net_salary": net_salary,
                    }
                    payslips.append(payslip)

                    total_gross += gross_salary
                    total_net += net_salary
                    total_pf += pf_deduction
                    total_esi += esi_deduction
                    total_pt += pt_deduction
                    total_tds += tds_monthly

                except Exception as calc_err:
                    errors.append({"employee": emp.get("name", ""), "error": str(calc_err)})
                    trace.append(f"Payroll calc error for {emp.get('name', '')}: {calc_err}")

            trace.append(
                f"Payroll computed: {len(payslips)} payslips, "
                f"gross={total_gross:,.2f}, net={total_net:,.2f}, errors={len(errors)}"
            )

            # --- Step 5: Compute confidence ---
            if len(employees) > 0:
                success_rate = len(payslips) / len(employees)
            else:
                success_rate = 0
            attendance_available = len(attendance_map) > 0

            factors: list[float] = []
            factors.append(success_rate * 0.95)
            factors.append(0.90 if attendance_available else 0.60)
            factors.append(0.95 if not errors else max(0.50, 1.0 - len(errors) * 0.1))

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 6: Check payroll change vs last month ---
            hitl_reasons: list[str] = []
            if prev_total > 0:
                change_pct = abs(total_net - prev_total) / prev_total * 100
                if change_pct > PAYROLL_CHANGE_THRESHOLD_PCT:
                    hitl_reasons.append(
                        f"payroll change {change_pct:.1f}% vs last month (threshold {PAYROLL_CHANGE_THRESHOLD_PCT}%)"
                    )
                trace.append(f"Payroll change from last month: {change_pct:.1f}%")

            if errors:
                hitl_reasons.append(f"{len(errors)} employees had calculation errors")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor {self.confidence_floor}")

            output = {
                "status": "computed",
                "period": period,
                "company": company,
                "summary": {
                    "total_employees": len(employees),
                    "payslips_generated": len(payslips),
                    "total_gross": round(total_gross, 2),
                    "total_net": round(total_net, 2),
                    "total_pf": round(total_pf, 2),
                    "total_esi": round(total_esi, 2),
                    "total_pt": round(total_pt, 2),
                    "total_tds": round(total_tds, 2),
                    "errors": len(errors),
                },
                "payslips": payslips,
                "errors": errors,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="payroll_approval",
                    decision_required=DecisionRequired(
                        question=(
                            f"Payroll for {period}: {len(payslips)} payslips, "
                            f"net INR {total_net:,.2f}. {'; '.join(hitl_reasons)}. Approve?"
                        ),
                        options=[
                            DecisionOption(id="approve", label="Approve and process", action="proceed"),
                            DecisionOption(id="review", label="Review details", action="defer"),
                            DecisionOption(id="reject", label="Reject — recalculate", action="retry"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Payroll approval for {period}",
                        recommendation="review" if len(errors) > 0 else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "total_net": round(total_net, 2),
                            "previous_net": round(prev_total, 2),
                            "error_count": len(errors),
                        },
                    ),
                    assignee=HITLAssignee(
                        role="hr_head",
                        notify_channels=["email"],
                        escalation_chain=["cfo"],
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
            logger.error("payroll_engine_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "PAYROLL_ERR", "message": str(e)}, start=start,
            )

    @staticmethod
    def _compute_income_tax(annual_taxable: float) -> float:
        """Compute annual income tax using new regime slabs."""
        tax = 0.0
        for low, high, rate in TAX_SLABS_NEW_REGIME:
            if annual_taxable <= 0:
                break
            slab_amount = min(annual_taxable, high - low + 1)
            tax += slab_amount * rate
            annual_taxable -= slab_amount
        # Health and education cess 4%
        tax *= 1.04
        return round(tax, 2)

    @staticmethod
    def _prev_period(period: str) -> str:
        """Get previous month period string (YYYY-MM)."""
        try:
            parts = period.split("-")
            year, month = int(parts[0]), int(parts[1])
            month -= 1
            if month == 0:
                month = 12
                year -= 1
            return f"{year:04d}-{month:02d}"
        except (ValueError, IndexError):
            return period

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
