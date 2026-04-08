"""Offboarding agent implementation."""

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

# F&F components
FF_COMPONENTS = [
    "pending_salary",
    "earned_leave_encashment",
    "bonus_prorata",
    "gratuity",
    "notice_period_recovery",
    "asset_recovery",
]


@AgentRegistry.register
class OffboardingAgentAgent(BaseAgent):
    agent_type = "offboarding_agent"
    domain = "hr"
    confidence_floor = 0.95
    prompt_file = "offboarding_agent.prompt.txt"

    async def execute(self, task):
        """Initiate exit, revoke access, compute F&F, generate experience letter, initiate EPFO transfer."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            employee = inputs.get("employee", inputs)
            emp_id = employee.get("employee_id", employee.get("id", ""))
            name = employee.get("name", "")
            email = employee.get("work_email", employee.get("email", ""))
            last_working_day = employee.get("last_working_day", employee.get("lwd", ""))
            reason = employee.get("reason", "resignation")
            tenure_years = float(employee.get("tenure_years", 0))
            ctc = float(employee.get("ctc", 0))
            basic_monthly = float(employee.get("basic_monthly", ctc * 0.40 / 12))
            trace.append(f"Offboarding: {name} (ID: {emp_id}), LWD={last_working_day}, reason={reason}")

            step_results: dict[str, dict] = {}
            failed_steps: list[str] = []

            # --- Step 1: Update HRMS status ---
            hrms_result = await self._safe_tool_call(
                "greythr", "update_employee_status",
                {
                    "employee_id": emp_id,
                    "status": "exiting",
                    "last_working_day": last_working_day,
                    "reason": reason,
                },
                trace, tool_calls,
            )
            if hrms_result and "error" not in hrms_result:
                step_results["update_hrms"] = {"status": "completed"}
            else:
                step_results["update_hrms"] = {"status": "failed", "error": hrms_result.get("error", "")}
                failed_steps.append("update_hrms")

            # --- Step 2: Revoke access ---
            # Disable email
            email_revoke = await self._safe_tool_call(
                "google_workspace", "suspend_user",
                {"email": email},
                trace, tool_calls,
            )
            email_revoked = email_revoke and "error" not in email_revoke

            # Deactivate Slack
            slack_revoke = await self._safe_tool_call(
                "slack", "deactivate_user",
                {"email": email},
                trace, tool_calls,
            )
            slack_revoked = slack_revoke and "error" not in slack_revoke

            # Revoke other system access
            systems_revoked: list[str] = []
            for system in ["github", "jira", "aws_iam"]:
                sys_result = await self._safe_tool_call(
                    system, "revoke_access",
                    {"email": email, "employee_id": emp_id},
                    trace, tool_calls,
                )
                if sys_result and "error" not in sys_result:
                    systems_revoked.append(system)

            step_results["revoke_access"] = {
                "status": "completed" if email_revoked else "partial",
                "email_revoked": email_revoked,
                "slack_revoked": slack_revoked,
                "systems_revoked": systems_revoked,
            }
            if not email_revoked:
                failed_steps.append("revoke_email")

            # --- Step 3: Compute Full & Final settlement ---
            # Fetch leave balance
            leave_result = await self._safe_tool_call(
                "greythr", "get_leave_balance",
                {"employee_id": emp_id},
                trace, tool_calls,
            )
            earned_leave_balance = 0.0
            if leave_result and "error" not in leave_result:
                earned_leave_balance = float(leave_result.get("earned_leave", leave_result.get("el_balance", 0)))

            # Fetch pending salary
            salary_result = await self._safe_tool_call(
                "greythr", "get_pending_salary",
                {"employee_id": emp_id, "period": last_working_day},
                trace, tool_calls,
            )
            pending_salary = 0.0
            if salary_result and "error" not in salary_result:
                pending_salary = float(salary_result.get("pending_amount", 0))

            # Compute F&F components
            daily_basic = basic_monthly / 30 if basic_monthly else 0
            el_encashment = round(earned_leave_balance * daily_basic, 2)

            # Gratuity: 15 days basic per year if tenure >= 5 years
            gratuity = 0.0
            if tenure_years >= 5:
                gratuity = round((15 * basic_monthly / 26) * tenure_years, 2)

            # Notice period recovery (if not served)
            notice_served = employee.get("notice_served", True)
            notice_period_days = int(employee.get("notice_period_days", 90))
            notice_recovery = 0.0
            if not notice_served:
                days_not_served = int(employee.get("days_not_served", notice_period_days))
                notice_recovery = round(days_not_served * daily_basic, 2)

            # Bonus pro-rata
            bonus_annual = float(employee.get("bonus_annual", 0))
            months_worked = max(int(employee.get("months_in_fy", 0)), 1)
            bonus_prorata = round(bonus_annual * months_worked / 12, 2)

            # Asset recovery deduction
            asset_result = await self._safe_tool_call(
                "freshdesk", "get_assets_assigned",
                {"employee_id": emp_id},
                trace, tool_calls,
            )
            unreturned_assets: list[str] = []
            asset_deduction = 0.0
            if asset_result and "error" not in asset_result:
                assets = asset_result.get("assets", [])
                unreturned_assets = [a.get("name", "") for a in assets if not a.get("returned", False)]
                asset_deduction = sum(float(a.get("recovery_value", 0)) for a in assets if not a.get("returned", False))

            fnf_settlement = {
                "pending_salary": pending_salary,
                "earned_leave_encashment": el_encashment,
                "bonus_prorata": bonus_prorata,
                "gratuity": gratuity,
                "gross_payable": round(pending_salary + el_encashment + bonus_prorata + gratuity, 2),
                "notice_period_recovery": notice_recovery,
                "asset_recovery": round(asset_deduction, 2),
                "total_deductions": round(notice_recovery + asset_deduction, 2),
                "net_payable": round(
                    pending_salary + el_encashment + bonus_prorata + gratuity - notice_recovery - asset_deduction, 2
                ),
                "unreturned_assets": unreturned_assets,
            }
            step_results["fnf_settlement"] = {"status": "completed", "settlement": fnf_settlement}
            trace.append(f"F&F computed: net payable INR {fnf_settlement['net_payable']:,.2f}")

            # --- Step 4: Generate experience letter ---
            letter_result = await self._safe_tool_call(
                "google_docs", "create_from_template",
                {
                    "template": "experience_letter",
                    "variables": {
                        "employee_name": name,
                        "employee_id": emp_id,
                        "designation": employee.get("designation", ""),
                        "department": employee.get("department", ""),
                        "date_of_joining": employee.get("date_of_joining", ""),
                        "last_working_day": last_working_day,
                        "tenure": f"{tenure_years:.1f} years",
                    },
                },
                trace, tool_calls,
            )
            if letter_result and "error" not in letter_result:
                step_results["experience_letter"] = {
                    "status": "completed",
                    "doc_url": letter_result.get("url", letter_result.get("document_url", "")),
                }
            else:
                step_results["experience_letter"] = {"status": "failed"}
                failed_steps.append("experience_letter")

            # --- Step 5: Initiate EPFO transfer ---
            epfo_result = await self._safe_tool_call(
                "epfo", "initiate_transfer",
                {
                    "uan": employee.get("uan", ""),
                    "employee_id": emp_id,
                    "employee_name": name,
                    "reason": "exit",
                },
                trace, tool_calls,
            )
            if epfo_result and "error" not in epfo_result:
                step_results["epfo_transfer"] = {
                    "status": "initiated",
                    "tracking_id": epfo_result.get("tracking_id", ""),
                }
            else:
                step_results["epfo_transfer"] = {"status": "failed"}
                failed_steps.append("epfo_transfer")

            # --- Step 6: Notify stakeholders ---
            await self._safe_tool_call(
                "slack", "send_message",
                {
                    "channel": "#hr-exits",
                    "text": (
                        f"Exit initiated: {name} (ID: {emp_id}), LWD: {last_working_day}, "
                        f"F&F: INR {fnf_settlement['net_payable']:,.2f}"
                    ),
                },
                trace, tool_calls,
            )

            # --- Step 7: Compute confidence ---
            completed_count = sum(1 for v in step_results.values() if v.get("status") in ("completed", "initiated"))
            total_steps = len(step_results)
            access_revoked = email_revoked and slack_revoked

            factors: list[float] = []
            factors.append(completed_count / total_steps if total_steps else 0.5)
            factors.append(0.95 if access_revoked else 0.50)
            factors.append(0.90 if fnf_settlement["net_payable"] >= 0 else 0.70)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 8: HITL ---
            hitl_reasons: list[str] = []
            if not access_revoked:
                hitl_reasons.append("access not fully revoked")
            if unreturned_assets:
                hitl_reasons.append(f"unreturned assets: {', '.join(unreturned_assets)}")
            if fnf_settlement["net_payable"] > 500_000:
                hitl_reasons.append(f"F&F amount INR {fnf_settlement['net_payable']:,.0f} requires approval")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "processed" if not hitl_reasons else "pending_review",
                "employee_id": emp_id,
                "employee_name": name,
                "last_working_day": last_working_day,
                "reason": reason,
                "access_revocation": step_results.get("revoke_access", {}),
                "fnf_settlement": fnf_settlement,
                "step_details": step_results,
                "failed_steps": failed_steps,
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="offboarding_review",
                    decision_required=DecisionRequired(
                        question=f"Offboarding for {name}: {'; '.join(hitl_reasons)}. Approve F&F?",
                        options=[
                            DecisionOption(id="approve", label="Approve F&F and complete", action="proceed"),
                            DecisionOption(id="hold", label="Hold — pending asset return", action="defer"),
                            DecisionOption(id="adjust", label="Adjust F&F", action="retry"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Offboarding review for {name}",
                        recommendation="hold" if unreturned_assets else "approve",
                        agent_confidence=confidence,
                        supporting_data={
                            "net_payable": fnf_settlement["net_payable"],
                            "unreturned_assets": unreturned_assets,
                            "access_revoked": access_revoked,
                        },
                    ),
                    assignee=HITLAssignee(role="hr_head", notify_channels=["email"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("offboarding_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "OFFBOARD_ERR", "message": str(e)}, start=start,
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
