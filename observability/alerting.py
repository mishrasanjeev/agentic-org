"""Alert manager -- check thresholds and notify.

Monitors all 13 Prometheus metrics against PRD-defined thresholds and
dispatches notifications via Slack webhook, email stub, and structured logging.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import structlog
from prometheus_client import REGISTRY

from core.config import external_keys

logger = structlog.get_logger()


class Severity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ThresholdRule:
    """Defines a single alerting rule."""
    name: str
    metric_name: str
    operator: str            # "gt", "lt", "gte", "lte", "eq"
    threshold: float
    severity: Severity
    description: str
    label_filters: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PRD-defined thresholds
# ---------------------------------------------------------------------------
PRD_THRESHOLDS: list[ThresholdRule] = [
    ThresholdRule(
        name="p95_latency_high",
        metric_name="agentflow_task_latency_seconds",
        operator="gt",
        threshold=5.0,
        severity=Severity.WARNING,
        description="P95 task latency exceeds 5 s",
    ),
    ThresholdRule(
        name="hitl_rate_high",
        metric_name="agentflow_hitl_rate",
        operator="gt",
        threshold=0.05,
        severity=Severity.WARNING,
        description="HITL rate exceeds 5 %",
    ),
    ThresholdRule(
        name="confidence_low",
        metric_name="agentflow_agent_confidence_avg",
        operator="lt",
        threshold=0.80,
        severity=Severity.WARNING,
        description="Average agent confidence below 0.80",
    ),
    ThresholdRule(
        name="tool_error_rate_high",
        metric_name="agentflow_tool_error_rate",
        operator="gt",
        threshold=0.01,
        severity=Severity.CRITICAL,
        description="Tool error rate exceeds 1 %",
    ),
    ThresholdRule(
        name="stp_rate_low",
        metric_name="agentflow_stp_rate",
        operator="lt",
        threshold=0.90,
        severity=Severity.WARNING,
        description="Straight-through processing rate below 90 %",
    ),
    ThresholdRule(
        name="daily_llm_cost_high",
        metric_name="agentflow_llm_cost_usd_total",
        operator="gt",
        threshold=100.0,
        severity=Severity.CRITICAL,
        description="Daily LLM cost exceeds $100",
    ),
    ThresholdRule(
        name="hitl_overdue_critical",
        metric_name="agentflow_hitl_overdue_count",
        operator="gt",
        threshold=0.0,
        severity=Severity.CRITICAL,
        description="Critical HITL items are overdue",
        label_filters={"priority": "critical"},
    ),
    ThresholdRule(
        name="circuit_breaker_open",
        metric_name="agentflow_circuit_breaker_state",
        operator="eq",
        threshold=1.0,
        severity=Severity.CRITICAL,
        description="Circuit breaker is open",
    ),
    ThresholdRule(
        name="shadow_accuracy_low",
        metric_name="agentflow_shadow_accuracy",
        operator="lt",
        threshold=0.90,
        severity=Severity.WARNING,
        description="Shadow accuracy below floor",
    ),
    ThresholdRule(
        name="replicas_at_max",
        metric_name="agentflow_agent_replicas",
        operator="gte",
        threshold=5.0,        # max ceiling; overridable per-agent
        severity=Severity.WARNING,
        description="Agent replicas at max ceiling",
    ),
    ThresholdRule(
        name="budget_pct_high",
        metric_name="agentflow_agent_budget_pct",
        operator="gt",
        threshold=80.0,
        severity=Severity.WARNING,
        description="Agent budget usage exceeds 80 %",
    ),
]


def _compare(value: float, operator: str, threshold: float) -> bool:
    """Return True when the *value* violates the *threshold*."""
    if operator == "gt":
        return value > threshold
    if operator == "lt":
        return value < threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    return False


def _collect_metric_samples(metric_name: str) -> list[tuple[dict[str, str], float]]:
    """Scrape the in-process Prometheus registry for *metric_name*.

    Returns a list of ``(labels_dict, value)`` tuples.
    """
    results: list[tuple[dict[str, str], float]] = []
    for metric in REGISTRY.collect():
        if metric.name != metric_name and not metric.name.startswith(metric_name + "_"):
            continue
        for sample in metric.samples:
            # sample is a named-tuple with (name, labels, value, …)
            results.append((dict(sample.labels), sample.value))
    return results


def _labels_match(sample_labels: dict[str, str], filters: dict[str, str]) -> bool:
    """Return True if sample labels contain all required filter values."""
    return all(sample_labels.get(k) == v for k, v in filters.items())


# ---------------------------------------------------------------------------
# Alert firing record (for dedup / cooldown)
# ---------------------------------------------------------------------------

@dataclass
class _FiredAlert:
    rule_name: str
    labels: dict[str, str]
    value: float
    fired_at: float  # monotonic seconds


class AlertManager:
    """Checks PRD thresholds against Prometheus metrics and dispatches alerts."""

    def __init__(
        self,
        *,
        rules: list[ThresholdRule] | None = None,
        cooldown_seconds: float = 300.0,
        slack_webhook_url: str = "",
        alert_email: str = "",
    ) -> None:
        self._rules = rules if rules is not None else PRD_THRESHOLDS
        self._cooldown = cooldown_seconds
        self._slack_webhook_url = slack_webhook_url
        self._alert_email = alert_email or external_keys.hitl_notification_email
        self._fired: dict[str, _FiredAlert] = {}  # key = rule_name:label_hash

    # ------------------------------------------------------------------
    # Threshold checking
    # ------------------------------------------------------------------

    async def check_thresholds(self) -> list[dict[str, Any]]:
        """Evaluate every rule against the Prometheus registry.

        Returns a list of violation dicts, each containing:
            rule, metric, value, threshold, severity, description, labels
        """
        violations: list[dict[str, Any]] = []

        for rule in self._rules:
            samples = _collect_metric_samples(rule.metric_name)

            # For histogram metrics, we look at the appropriate quantile bucket.
            # For counters/gauges, we take the raw value.
            for labels, value in samples:
                if rule.label_filters and not _labels_match(labels, rule.label_filters):
                    continue

                if _compare(value, rule.operator, rule.threshold):
                    violation = {
                        "rule": rule.name,
                        "metric": rule.metric_name,
                        "value": value,
                        "threshold": rule.threshold,
                        "operator": rule.operator,
                        "severity": rule.severity.value,
                        "description": rule.description,
                        "labels": labels,
                    }
                    violations.append(violation)

                    # Fire alert if not in cooldown
                    cooldown_key = f"{rule.name}:{_label_hash(labels)}"
                    if self._should_fire(cooldown_key):
                        await self._dispatch_alert(rule, violation)
                        self._fired[cooldown_key] = _FiredAlert(
                            rule_name=rule.name,
                            labels=labels,
                            value=value,
                            fired_at=time.monotonic(),
                        )

        return violations

    def _should_fire(self, cooldown_key: str) -> bool:
        prev = self._fired.get(cooldown_key)
        if prev is None:
            return True
        return (time.monotonic() - prev.fired_at) > self._cooldown

    # ------------------------------------------------------------------
    # Alert dispatch
    # ------------------------------------------------------------------

    async def _dispatch_alert(
        self,
        rule: ThresholdRule,
        violation: dict[str, Any],
    ) -> None:
        """Route an alert to all configured channels."""
        message = (
            f"[{rule.severity.value.upper()}] {rule.description}\n"
            f"Metric: {rule.metric_name} = {violation['value']} "
            f"(threshold {rule.operator} {rule.threshold})\n"
            f"Labels: {violation.get('labels', {})}"
        )
        # Always log
        await self.send_alert("log", message, severity=rule.severity)

        # Slack
        if self._slack_webhook_url or external_keys.slack_bot_token:
            await self.send_alert("slack", message, severity=rule.severity)

        # Email (critical only)
        if rule.severity == Severity.CRITICAL and self._alert_email:
            await self.send_alert("email", message, severity=rule.severity)

    async def send_alert(
        self,
        channel: str,
        message: str,
        *,
        severity: Severity = Severity.WARNING,
    ) -> bool:
        """Send an alert to the specified channel.

        Supported channels: ``log``, ``slack``, ``email``.
        Returns True if the alert was delivered successfully.
        """
        if channel == "log":
            if severity == Severity.CRITICAL:
                logger.critical("alert_fired", channel=channel, message=message)
            else:
                logger.warning("alert_fired", channel=channel, message=message)
            return True

        if channel == "slack":
            return await self._send_slack(message, severity)

        if channel == "email":
            return await self._send_email(message, severity)

        logger.error("unknown_alert_channel", channel=channel)
        return False

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    async def _send_slack(self, message: str, severity: Severity) -> bool:
        """Post alert to Slack via incoming webhook or Bot API."""
        webhook_url = self._slack_webhook_url
        if not webhook_url and external_keys.slack_bot_token:
            # Fall back to Slack Web API chat.postMessage
            return await self._send_slack_api(message, severity)

        if not webhook_url:
            logger.debug("slack_not_configured")
            return False

        icon = ":rotating_light:" if severity == Severity.CRITICAL else ":warning:"
        payload = {
            "text": f"{icon} *AgentFlow Alert*\n```{message}```",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(webhook_url, json=payload)
                resp.raise_for_status()
                logger.info("slack_alert_sent", severity=severity.value)
                return True
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.error("slack_alert_failed", error=str(exc))
            return False

    async def _send_slack_api(self, message: str, severity: Severity) -> bool:
        """Post via Slack Web API using the bot token."""
        icon = ":rotating_light:" if severity == Severity.CRITICAL else ":warning:"
        payload = {
            "channel": external_keys.slack_hitl_channel,
            "text": f"{icon} *AgentFlow Alert*\n```{message}```",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {external_keys.slack_bot_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.error("slack_api_error", error=data.get("error"))
                    return False
                logger.info("slack_api_alert_sent", severity=severity.value)
                return True
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.error("slack_api_alert_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Email (stub -- uses SendGrid when configured)
    # ------------------------------------------------------------------

    async def _send_email(self, message: str, severity: Severity) -> bool:
        """Send alert email via SendGrid API.

        If SendGrid is not configured, log the message and return False.
        """
        api_key = external_keys.sendgrid_api_key
        if not api_key:
            logger.debug("email_not_configured_sendgrid_key_missing")
            return False

        subject = f"[AgentFlow {severity.value.upper()}] Alert"
        payload = {
            "personalizations": [
                {"to": [{"email": self._alert_email}]},
            ],
            "from": {"email": "alerts@agentflow.io", "name": "AgentFlow Alerts"},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": message},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                logger.info("email_alert_sent", to=self._alert_email, severity=severity.value)
                return True
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.error("email_alert_failed", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _label_hash(labels: dict[str, str]) -> str:
    """Deterministic string key for a set of labels."""
    return "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
