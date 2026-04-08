"""IT Operations agent implementation."""

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

# Severity classification
SEVERITY_KEYWORDS = {
    "SEV1": ["outage", "down", "data loss", "security breach", "all users affected", "production down"],
    "SEV2": ["degraded", "partial outage", "significant impact", "multiple users", "service impaired"],
    "SEV3": ["intermittent", "slow", "single user", "non-critical", "workaround available"],
    "SEV4": ["cosmetic", "minor", "low impact", "informational", "maintenance"],
}

# Runbook mapping: alert type -> auto-remediation action
RUNBOOKS = {
    "high_cpu": {"action": "restart_service", "connector": "aws", "tool": "restart_instance"},
    "high_memory": {"action": "restart_service", "connector": "aws", "tool": "restart_instance"},
    "disk_full": {"action": "cleanup_logs", "connector": "aws", "tool": "run_command"},
    "ssl_expiry": {"action": "renew_certificate", "connector": "aws", "tool": "renew_ssl"},
    "health_check_failed": {"action": "restart_service", "connector": "aws", "tool": "restart_instance"},
    "deployment_failed": {"action": "rollback", "connector": "github", "tool": "rollback_deployment"},
    "database_connection_pool": {"action": "restart_pool", "connector": "aws", "tool": "run_command"},
}

# MTTR tracking
MTTR_TARGET_MINUTES = {"SEV1": 30, "SEV2": 120, "SEV3": 480, "SEV4": 1440}


@AgentRegistry.register
class ItOperationsAgent(BaseAgent):
    agent_type = "it_operations"
    domain = "ops"
    confidence_floor = 0.88
    prompt_file = "it_operations.prompt.txt"

    async def execute(self, task):
        """Parse incident, classify severity, check runbook, auto-remediate or create PagerDuty incident."""
        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
            alert = inputs.get("alert", inputs)
            alert_type = alert.get("type", alert.get("alert_type", "unknown"))
            alert_source = alert.get("source", alert.get("monitor", ""))
            service = alert.get("service", alert.get("host", ""))
            message = str(alert.get("message", alert.get("description", ""))).strip()
            metric_value = alert.get("metric_value", "")
            threshold = alert.get("threshold", "")
            trace.append(f"IT Ops alert: type={alert_type}, source={alert_source}, service={service}")

            combined_text = f"{alert_type} {message}".lower()

            # --- Step 1: Classify severity ---
            severity = "SEV3"  # Default
            for sev, keywords in SEVERITY_KEYWORDS.items():
                if any(kw in combined_text for kw in keywords):
                    severity = sev
                    break

            trace.append(f"Severity classified: {severity}")

            # --- Step 2: Check runbook for auto-remediation ---
            runbook = RUNBOOKS.get(alert_type.lower())
            auto_remediated = False
            remediation_result: dict[str, Any] = {}

            if runbook and severity not in ("SEV1",):
                # Attempt auto-remediation (skip for SEV1 — needs human)
                trace.append(f"Runbook found for {alert_type}: {runbook['action']}")

                params = {
                    "service": service,
                    "action": runbook["action"],
                    "alert_type": alert_type,
                }

                # Special params for specific actions
                if runbook["action"] == "cleanup_logs":
                    params["command"] = "find /var/log -name '*.log' -mtime +7 -delete"
                elif runbook["action"] == "rollback":
                    params["environment"] = alert.get("environment", "production")

                fix_result = await self._safe_tool_call(
                    runbook["connector"], runbook["tool"],
                    params, trace, tool_calls,
                )

                if fix_result and "error" not in fix_result:
                    auto_remediated = True
                    remediation_result = {
                        "action": runbook["action"],
                        "status": "success",
                        "details": fix_result,
                    }
                    trace.append(f"Auto-remediation successful: {runbook['action']}")

                    # Verify the fix
                    verify_result = await self._safe_tool_call(
                        "datadog", "check_monitor",
                        {"monitor_id": alert.get("monitor_id", ""), "service": service},
                        trace, tool_calls,
                    )
                    if verify_result and "error" not in verify_result:
                        monitor_status = verify_result.get("status", "unknown")
                        remediation_result["verification"] = monitor_status
                        if monitor_status != "OK":
                            auto_remediated = False
                            trace.append(f"Verification failed: monitor still {monitor_status}")
                else:
                    remediation_result = {
                        "action": runbook["action"],
                        "status": "failed",
                        "error": fix_result.get("error", ""),
                    }
                    trace.append(f"Auto-remediation failed: {fix_result.get('error', '')}")
            else:
                if not runbook:
                    trace.append(f"No runbook for alert type: {alert_type}")
                else:
                    trace.append(f"Skipping auto-remediation for {severity} — requires human")

            # --- Step 3: Create PagerDuty incident if not auto-fixed ---
            pagerduty_incident = None
            if not auto_remediated:
                pd_result = await self._safe_tool_call(
                    "pagerduty", "create_incident",
                    {
                        "title": f"[{severity}] {alert_type}: {service}",
                        "description": message,
                        "severity": severity.lower(),
                        "service_id": alert.get("pagerduty_service_id", service),
                        "urgency": "high" if severity in ("SEV1", "SEV2") else "low",
                        "details": {
                            "alert_type": alert_type,
                            "source": alert_source,
                            "metric_value": metric_value,
                            "threshold": threshold,
                            "auto_remediation_attempted": runbook is not None,
                            "auto_remediation_result": remediation_result.get("status", "not_attempted"),
                        },
                    },
                    trace, tool_calls,
                )
                if pd_result and "error" not in pd_result:
                    pagerduty_incident = {
                        "incident_id": pd_result.get("incident_id", pd_result.get("id", "")),
                        "url": pd_result.get("url", pd_result.get("html_url", "")),
                    }
                    trace.append(f"PagerDuty incident created: {pagerduty_incident['incident_id']}")

            # --- Step 4: Notify via Slack ---
            channel = "#incidents-critical" if severity in ("SEV1", "SEV2") else "#incidents"
            await self._safe_tool_call(
                "slack", "send_message",
                {
                    "channel": channel,
                    "text": (
                        f"{'RESOLVED' if auto_remediated else 'ALERT'} [{severity}] "
                        f"{alert_type} on {service}\n"
                        f"Message: {message[:200]}\n"
                        f"{'Auto-remediated: ' + remediation_result.get('action', '') if auto_remediated else ''}\n"
                        f"{'PagerDuty: ' + pagerduty_incident.get('url', '') if pagerduty_incident else ''}"
                    ),
                },
                trace, tool_calls,
            )

            # --- Step 5: Track MTTR ---
            elapsed_minutes = round((time.monotonic() - start) / 60, 1)
            target_mttr = MTTR_TARGET_MINUTES.get(severity, 480)
            within_mttr = elapsed_minutes <= target_mttr

            # --- Step 6: Compute confidence ---
            factors: list[float] = []
            # Severity classification confidence
            sev_match_count = sum(
                1 for kw_list in SEVERITY_KEYWORDS.values()
                for kw in kw_list if kw in combined_text
            )
            factors.append(min(0.6 + sev_match_count * 0.1, 0.95))
            # Remediation confidence
            if auto_remediated:
                factors.append(0.95)
            elif pagerduty_incident:
                factors.append(0.85)
            else:
                factors.append(0.50)
            # Runbook availability
            factors.append(0.90 if runbook else 0.70)

            confidence = round(sum(factors) / len(factors), 3)
            confidence = min(max(confidence, 0.0), 1.0)
            trace.append(f"Computed confidence: {confidence}")

            # --- Step 7: HITL for SEV1 or failed auto-remediation ---
            hitl_reasons: list[str] = []
            if severity == "SEV1":
                hitl_reasons.append("SEV1 incident requires human coordination")
            if runbook and not auto_remediated:
                hitl_reasons.append("auto-remediation failed")
            if confidence < self.confidence_floor:
                hitl_reasons.append(f"confidence {confidence:.3f} < floor")

            output = {
                "status": "resolved" if auto_remediated else "escalated",
                "alert_type": alert_type,
                "service": service,
                "severity": severity,
                "auto_remediated": auto_remediated,
                "remediation": remediation_result,
                "pagerduty_incident": pagerduty_incident,
                "mttr": {
                    "elapsed_minutes": elapsed_minutes,
                    "target_minutes": target_mttr,
                    "within_target": within_mttr,
                },
                "confidence": confidence,
                "hitl_required": len(hitl_reasons) > 0,
            }

            if hitl_reasons:
                trace.append(f"HITL triggered: {'; '.join(hitl_reasons)}")
                hitl_request = HITLRequest(
                    hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
                    trigger_condition="; ".join(hitl_reasons),
                    trigger_type="incident_escalation",
                    decision_required=DecisionRequired(
                        question=f"[{severity}] {alert_type} on {service}: {'; '.join(hitl_reasons)}",
                        options=[
                            DecisionOption(id="investigate", label="Investigate manually", action="proceed"),
                            DecisionOption(id="retry_fix", label="Retry auto-remediation", action="retry"),
                            DecisionOption(id="rollback", label="Rollback deployment", action="rollback"),
                            DecisionOption(id="acknowledge", label="Acknowledge and monitor", action="defer"),
                        ],
                    ),
                    context=HITLContext(
                        summary=f"{severity} incident: {alert_type} on {service}",
                        recommendation="investigate" if severity == "SEV1" else "retry_fix",
                        agent_confidence=confidence,
                        supporting_data={
                            "severity": severity,
                            "auto_remediation_attempted": runbook is not None,
                            "pagerduty_id": pagerduty_incident.get("incident_id", "") if pagerduty_incident else "",
                        },
                    ),
                    assignee=HITLAssignee(
                        role="sre_lead" if severity in ("SEV1", "SEV2") else "ops_lead",
                        notify_channels=["pagerduty", "slack"],
                        escalation_chain=["vp_engineering"] if severity == "SEV1" else [],
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
            logger.error("it_operations_error", agent=self.agent_id, error=str(e))
            trace.append(f"Error: {e}")
            return self._make_result(
                task, msg_id, "failed", {}, 0.0, trace, tool_calls,
                error={"code": "ITOPS_ERR", "message": str(e)}, start=start,
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
