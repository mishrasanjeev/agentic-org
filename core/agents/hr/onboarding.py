"""Onboarding agent implementation."""

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

# Onboarding checklist steps
ONBOARDING_STEPS = [
    "create_hrms_record",
    "provision_email",
    "provision_slack",
    "enroll_payroll",
    "assign_buddy",
    "enroll_mandatory_training",
    "setup_laptop",
    "notify_manager",
    "send_welcome_kit",
]


@AgentRegistry.register
class OnboardingAgentAgent(BaseAgent):
    agent_type = "onboarding_agent"
    domain = "hr"
    confidence_floor = 0.95
    prompt_file = "onboarding_agent.prompt.txt"

    async def execute(self, task):
        """Provision accounts, create in HRMS, enroll training, assign buddy, notify manager."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            employee = inputs.get("employee", inputs)
            name = employee.get("name", "")
            email = employee.get("personal_email", employee.get("email", ""))
            designation = employee.get("designation", employee.get("role", ""))
            department = employee.get("department", "")
            manager = employee.get("manager", employee.get("reporting_to", ""))
            doj = employee.get("date_of_joining", employee.get("doj", ""))
            ctc = float(employee.get("ctc", 0))
            trace.append(f"Onboarding: {name}, {designation}, dept={department}, DOJ={doj}")

            step_results: dict[str, dict] = {}
            failed_steps: list[str] = []
            work_email = ""

            # --- Step 1: Create HRMS record ---
            hrms_result = await self._safe_tool_call(
                "greythr", "create_employee",
                {
                    "name": name,
                    "email": email,
                    "designation": designation,
                    "department": department,
                    "reporting_to": manager,
                    "date_of_joining": doj,
                    "ctc": ctc,
                },
                trace, tool_calls,
            )
            if hrms_result and "error" not in hrms_result:
                employee_id = hrms_result.get("employee_id", "")
                step_results["create_hrms_record"] = {"status": "completed", "employee_id": employee_id}
                trace.append(f"HRMS record created: {employee_id}")
            else:
                step_results["create_hrms_record"] = {"status": "failed", "error": hrms_result.get("error", "")}
                failed_steps.append("create_hrms_record")
                employee_id = ""

            # --- Step 2: Provision email ---
            first_name = name.split()[0].lower() if name else "user"
            last_name = name.split()[-1].lower() if name and len(name.split()) > 1 else ""
            work_email = f"{first_name}.{last_name}@{inputs.get('domain', 'company.com')}".strip(".")

            email_result = await self._safe_tool_call(
                "google_workspace", "create_user",
                {
                    "email": work_email,
                    "first_name": name.split()[0] if name else "",
                    "last_name": name.split()[-1] if name and len(name.split()) > 1 else "",
                    "department": department,
                    "title": designation,
                },
                trace, tool_calls,
            )
            if email_result and "error" not in email_result:
                work_email = email_result.get("email", work_email)
                step_results["provision_email"] = {"status": "completed", "email": work_email}
            else:
                step_results["provision_email"] = {"status": "failed", "error": email_result.get("error", "")}
                failed_steps.append("provision_email")

            # --- Step 3: Provision Slack ---
            slack_result = await self._safe_tool_call(
                "slack", "invite_user",
                {
                    "email": work_email or email,
                    "channels": [f"#{department.lower()}", "#general", "#new-joiners"],
                    "real_name": name,
                },
                trace, tool_calls,
            )
            if slack_result and "error" not in slack_result:
                step_results["provision_slack"] = {"status": "completed"}
            else:
                step_results["provision_slack"] = {"status": "failed", "error": slack_result.get("error", "")}
                failed_steps.append("provision_slack")

            # --- Step 4: Enroll in payroll ---
            payroll_result = await self._safe_tool_call(
                "greythr", "enroll_payroll",
                {
                    "employee_id": employee_id,
                    "ctc": ctc,
                    "bank_account": employee.get("bank_account", ""),
                    "pan": employee.get("pan", ""),
                    "uan": employee.get("uan", ""),
                },
                trace, tool_calls,
            )
            if payroll_result and "error" not in payroll_result:
                step_results["enroll_payroll"] = {"status": "completed"}
            else:
                step_results["enroll_payroll"] = {"status": "failed", "error": payroll_result.get("error", "")}
                failed_steps.append("enroll_payroll")

            # --- Step 5: Assign buddy ---
            buddy = inputs.get("buddy", "")
            if not buddy:
                # Auto-assign buddy from the department
                buddy_result = await self._safe_tool_call(
                    "greythr", "get_team_members",
                    {"department": department, "exclude": name},
                    trace, tool_calls,
                )
                if buddy_result and "error" not in buddy_result:
                    members = buddy_result.get("members", [])
                    if members:
                        buddy = members[0].get("name", "")

            if buddy:
                step_results["assign_buddy"] = {"status": "completed", "buddy": buddy}
                trace.append(f"Buddy assigned: {buddy}")
            else:
                step_results["assign_buddy"] = {"status": "skipped", "reason": "no buddy available"}

            # --- Step 6: Enroll mandatory training ---
            training_result = await self._safe_tool_call(
                "google_classroom", "enroll_student",
                {
                    "email": work_email or email,
                    "courses": ["company_induction", "code_of_conduct", "info_security", "posh_training"],
                },
                trace, tool_calls,
            )
            if training_result and "error" not in training_result:
                step_results["enroll_mandatory_training"] = {"status": "completed"}
            else:
                step_results["enroll_mandatory_training"] = {
                    "status": "failed", "error": training_result.get("error", ""),
                }
                failed_steps.append("enroll_mandatory_training")

            # --- Step 7: IT asset setup request ---
            it_result = await self._safe_tool_call(
                "freshdesk", "create_ticket",
                {
                    "subject": f"Laptop setup for {name} ({designation})",
                    "description": f"New joiner: {name}, DOJ: {doj}, Dept: {department}",
                    "category": "IT",
                    "priority": "high",
                    "requester_email": work_email or email,
                },
                trace, tool_calls,
            )
            if it_result and "error" not in it_result:
                step_results["setup_laptop"] = {
                    "status": "completed",
                    "ticket_id": it_result.get("ticket_id", it_result.get("id", "")),
                }
            else:
                step_results["setup_laptop"] = {"status": "failed", "error": it_result.get("error", "")}
                failed_steps.append("setup_laptop")

            # --- Step 8: Notify manager ---
            notify_result = await self._safe_tool_call(
                "slack", "send_message",
                {
                    "channel": f"@{manager}" if manager else "#hr-notifications",
                    "text": (
                        f"New team member joining: {name} as {designation} on {doj}. "
                        f"Work email: {work_email}. Buddy: {buddy or 'TBD'}."
                    ),
                },
                trace, tool_calls,
            )
            if notify_result and "error" not in notify_result:
                step_results["notify_manager"] = {"status": "completed"}
            else:
                step_results["notify_manager"] = {"status": "failed", "error": notify_result.get("error", "")}
                failed_steps.append("notify_manager")

            # --- Step 9: Send welcome email ---
            welcome_result = await self._safe_tool_call(
                "sendgrid", "send_email",
                {
                    "to": email,
                    "template": "welcome_new_joiner",
                    "subject": f"Welcome to the team, {name.split()[0] if name else 'there'}!",
                    "variables": {
                        "name": name,
                        "designation": designation,
                        "department": department,
                        "doj": doj,
                        "work_email": work_email,
                        "buddy": buddy,
                    },
                },
                trace, tool_calls,
            )
            if welcome_result and "error" not in welcome_result:
                step_results["send_welcome_kit"] = {"status": "completed"}
            else:
                step_results["send_welcome_kit"] = {"status": "failed", "error": welcome_result.get("error", "")}
                failed_steps.append("send_welcome_kit")

            # --- Step 10: Compute confidence ---
            completed_count = sum(1 for v in step_results.values() if v.get("status") == "completed")
            total_steps = len(ONBOARDING_STEPS)
            completion_rate = completed_count / total_steps if total_steps else 0

            # Critical steps: HRMS, email, payroll
            critical_ok = all(
                step_results.get(s, {}).get("status") == "completed"
                for s in ["create_hrms_record", "provision_email", "enroll_payroll"]
            )

            confidence = round(completion_rate * 0.7 + (0.3 if critical_ok else 0.0), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(
                f"Onboarding: {completed_count}/{total_steps} steps complete, "
                f"critical_ok={critical_ok}, confidence={confidence}"
            )

            # --- Step 11: HITL if critical steps failed ---
            hitl_reasons: list[str] = []
            if not critical_ok:
                hitl_reasons.append("critical onboarding steps failed")
            if len(failed_steps) >= 3:
                hitl_reasons.append(f"{len(failed_steps)} steps failed: {', '.join(failed_steps)}")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "onboarded" if not hitl_reasons else "partial",
                "employee_name": name,
                "employee_id": employee_id,
                "work_email": work_email,
                "department": department,
                "buddy": buddy,
                "steps_completed": completed_count,
                "steps_total": total_steps,
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
                    trigger_type="onboarding_incomplete",
                    decision_required=DecisionRequired(
                        question=f"Onboarding for {name}: {'; '.join(hitl_reasons)}. Action needed.",
                        options=[
                            DecisionOption(id="retry", label="Retry failed steps", action="retry"),
                            DecisionOption(id="manual", label="Complete manually", action="proceed"),
                            DecisionOption(id="defer", label="Defer to DOJ", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"Onboarding incomplete for {name}",
                        recommendation="retry",
                        agent_confidence=confidence,
                        supporting_data={"failed_steps": failed_steps},
                    ),
                    assignee=HITLAssignee(role="hr_lead", notify_channels=["email", "slack"]),
                )
                return self._make_result(
                    task, msg_id, "hitl_triggered", output, confidence, trace, tool_calls,
                    hitl_request=hitl_request, start=start,
                )

            return self._make_result(
                task, msg_id, "completed", output, confidence, trace, tool_calls, start=start,
            )

        except Exception as e:
            logger.error("onboarding_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "ONBOARD_ERR", "message": str(e)}, start=start,
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
