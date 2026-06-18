"""Brand Monitor agent implementation.

The Brand Monitor is deterministic and vendor-neutral: it analyzes structured
mentions supplied by connectors or tests, emits contract-shaped CMO outputs,
and fails closed before any public/customer-facing response.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from core.agents.base import BaseAgent
from core.agents.registry import AgentRegistry
from core.marketing.escalation_matrix import evaluate_marketing_escalation
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.schemas.messages import (
    DecisionOption,
    DecisionRequired,
    HITLAssignee,
    HITLContext,
    HITLRequest,
    ToolCallRecord,
)

logger = structlog.get_logger()

BRAND_MONITOR_AGENT_MATURITY = "beta"
BRAND_MONITOR_CONFIDENCE_FLOOR = 0.85

READ_ONLY_ACTIONS = {
    "aggregate_mentions",
    "classify_crisis_severity",
    "classify_sentiment",
    "competitor_brand_grouping",
    "detect_crisis",
    "detect_negative_spike",
    "escalation_recommendation",
    "false_positive_suppression",
    "get_sentiment",
    "group_mentions",
    "mention_aggregation",
    "monitor_mentions",
    "negative_spike_detection",
    "recommend_response_playbook",
    "response_playbook_recommendation",
    "sentiment_trend",
    "suppress_false_positives",
}
WRITE_ACTIONS = {
    "brand_claim",
    "comparative_claim",
    "crisis_response",
    "pricing_claim",
    "public_response",
    "publish_brand_response",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

POSITIVE_TERMS = (
    "amazing",
    "excellent",
    "fast",
    "good",
    "great",
    "happy",
    "love",
    "recommended",
    "reliable",
    "resolved",
)
NEGATIVE_TERMS = (
    "angry",
    "bad",
    "broken",
    "complaint",
    "down",
    "failed",
    "fraud",
    "hate",
    "lawsuit",
    "legal",
    "outage",
    "poor",
    "refund",
    "scam",
    "security",
    "unacceptable",
)
CRISIS_TERMS = (
    "boycott",
    "breach",
    "class action",
    "crisis",
    "data leak",
    "lawsuit",
    "outage",
    "regulator",
    "safety",
    "security incident",
    "viral",
)
DEFAULT_FALSE_POSITIVE_TERMS = (
    "brand new",
    "personal brand",
    "wrong company",
    "not our brand",
    "unrelated",
)


@AgentRegistry.register
class BrandMonitorAgent(BaseAgent):
    agent_type = "brand_monitor"
    domain = "marketing"
    confidence_floor = BRAND_MONITOR_CONFIDENCE_FLOOR
    prompt_file = "brand_monitor.prompt.txt"

    async def execute(self, task):
        """Run deterministic brand monitoring or guarded public-response logic."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"Brand Monitor action={action}, mode={mode}")

            if action in READ_ONLY_ACTIONS:
                output = self._read_only_action(task, action, inputs, mode, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_brand_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported Brand Monitor action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_brand_monitor_action",
                        "reason": "Brand Monitor only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported Brand Monitor action '{action}'."],
                policy_context={"workflow_mode": mode},
            )
            return self._make_result(
                task,
                msg_id,
                "failed",
                output,
                0.0,
                trace,
                tool_calls,
                error={"code": "BRAND_MONITOR_ACTION_UNSUPPORTED", "message": output["blocked_reasons"][0]},
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("brand_monitor_agent_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "BRAND_MONITOR_AGENT_ERR", "message": str(exc)},
                start=start,
            )

    def _read_only_action(
        self,
        task,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        trace: list[str],
    ) -> dict[str, Any]:
        mentions = _normalized_mentions(inputs)
        retained, suppressed = _suppress_false_positives(mentions, inputs)
        groups = _mention_groups(retained)
        sentiment = _sentiment_trend(retained, inputs)
        spike = _negative_spike(sentiment, inputs)
        crisis = _crisis_classification(retained, sentiment, spike, inputs)
        playbook = _playbook_recommendations(crisis, spike, groups, inputs)
        degraded = _read_degraded_reasons(inputs)
        confidence = _monitor_confidence(retained, degraded, crisis)
        needs_escalation = crisis["severity"] in {"high", "critical"} or spike["triggered"]
        escalation = (
            _escalation_for_brand_event(task, action, inputs, crisis=crisis, spike=spike)
            if needs_escalation
            else {}
        )
        summary = {
            "total_mentions": len(mentions),
            "retained_mentions": len(retained),
            "suppressed_mentions": len(suppressed),
            "sentiment_trend": sentiment,
            "negative_spike": spike,
            "crisis_severity": crisis,
            "mention_groups": groups,
            "playbook": playbook,
        }
        trace.append(
            "Brand monitoring mentions="
            f"{len(mentions)}, retained={len(retained)}, severity={crisis['severity']}, degraded={len(degraded)}"
        )

        status = "monitoring_degraded" if degraded else "monitoring_ready"
        rationale = "Built a deterministic brand monitoring summary from structured mentions."
        extra: dict[str, Any] = {"brand_monitoring": summary}
        if action in {"aggregate_mentions", "mention_aggregation", "monitor_mentions"}:
            extra.update({"mentions": retained, "suppressed_mentions": suppressed, "mention_groups": groups})
            status = "mentions_aggregated" if not degraded else "mentions_degraded"
            rationale = "Aggregated mentions by source, channel, topic, and brand/competitor grouping."
        elif action in {"get_sentiment", "classify_sentiment", "sentiment_trend"}:
            extra.update({"sentiment_trend": sentiment})
            status = "sentiment_trend_ready" if not degraded else "sentiment_trend_degraded"
            rationale = "Calculated deterministic positive, neutral, and negative sentiment distribution."
        elif action in {"detect_negative_spike", "negative_spike_detection"}:
            extra.update({"negative_spike": spike})
            status = "negative_spike_detected" if spike["triggered"] else "negative_spike_clear"
            rationale = "Compared current negative mention volume against baseline and threshold policy."
        elif action in {"false_positive_suppression", "suppress_false_positives"}:
            extra.update({"mentions": retained, "suppressed_mentions": suppressed})
            status = "false_positives_suppressed"
            rationale = "Suppressed irrelevant mentions before calculating sentiment and crisis risk."
        elif action == "classify_crisis_severity":
            extra.update({"crisis_severity": crisis})
            status = "crisis_classified"
            rationale = "Classified crisis severity from volume, sentiment, source, and crisis terms."
        elif action in {"group_mentions", "competitor_brand_grouping"}:
            extra.update({"mention_groups": groups})
            status = "mentions_grouped"
            rationale = "Grouped brand and competitor mentions from structured input."
        elif action in {"recommend_response_playbook", "response_playbook_recommendation", "escalation_recommendation"}:
            extra.update({"response_playbook": playbook})
            status = "playbook_recommended"
            rationale = "Recommended a deterministic response playbook for the current brand-risk level."
        elif action == "detect_crisis":
            extra.update({"crisis_severity": crisis, "negative_spike": spike})
            status = "crisis_detected" if crisis["severity"] in {"high", "critical"} else "crisis_clear"
            rationale = "Evaluated mentions for crisis signals and public-response escalation needs."

        return self._base_output(
            task,
            action=action,
            status=status,
            confidence=confidence,
            rationale=rationale,
            recommended_actions=playbook,
            approval_required=needs_escalation,
            hitl_required=needs_escalation,
            policy_context={
                "workflow_id": "brand_crisis_response",
                "workflow_mode": mode,
                "crisis_response": needs_escalation,
                "public_response": False,
            },
            escalation=escalation,
            source_refs=_source_refs(inputs, retained),
            degraded_reasons=degraded,
            extra=extra,
        )

    async def _guarded_brand_write(
        self,
        task,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        trace: list[str],
        tool_calls: list[ToolCallRecord],
    ) -> dict[str, Any]:
        policy_context = _policy_context_for_write(action, inputs, mode)
        policy = evaluate_marketing_policy(policy_context)
        escalation = _escalation_for_write(task, action, inputs, policy)
        blocked_reasons: list[str] = []
        hitl_reasons: list[str] = []

        if mode in SHADOW_MODES:
            blocked_reasons.append("Shadow/draft/internal Brand Monitor workflows are read-only.")
            trace.append("Brand Monitor write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.78,
                rationale="Prepared brand response recommendation without publishing or changing external systems.",
                recommended_actions=[
                    {
                        "action": "review_brand_response_recommendation",
                        "reason": "Review the response before any active public write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons,
                external_write_status="shadow_only",
                external_write_required=True,
                extra={"write_payload": _brand_write_payload(action, inputs)},
            )

        write_safe, write_reason = _connector_write_safe(inputs)
        if not write_safe:
            blocked_reasons.append(write_reason)
        if not _has_approval(inputs):
            hitl_reasons.append(str(policy.get("reason") or "Brand public response requires approval."))
        if policy.get("decision") in {"blocked", "missing_policy"}:
            blocked_reasons.append(str(policy.get("reason") or "Marketing policy blocks this brand action."))
        escalation_needs_review = escalation.get("decision") not in {None, "", "no_escalation", "notify_owner"}
        if escalation_needs_review and not _has_escalation_approval(inputs):
            hitl_reasons.append("Brand response requires escalation review.")

        if blocked_reasons or hitl_reasons:
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale="Brand Monitor external action failed closed before vendor mutation.",
                recommended_actions=[
                    {
                        "action": "resolve_brand_response_prerequisites",
                        "reason": "; ".join(_dedupe(blocked_reasons + hitl_reasons)),
                    }
                ],
                approval_required=bool(hitl_reasons),
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons + hitl_reasons,
                external_write_status="write_unconfirmed",
                external_write_required=True,
                extra={"write_payload": _brand_write_payload(action, inputs)},
            )

        result = _dict_or_none(inputs.get("external_write_result"))
        connector, tool = _connector_tool(action, inputs)
        idempotency_key = str(
            inputs.get("idempotency_key")
            or inputs.get("request_fingerprint")
            or f"brand_monitor:{task.correlation_id}:{task.step_id}:{action}"
        )
        if result is None:
            result = await self._safe_tool_call(
                connector,
                tool,
                _brand_write_payload(action, inputs),
                trace,
                tool_calls,
                idempotency_key=idempotency_key,
            )

        write_status = _external_write_confirmation_status(result)
        write_confirmed = _is_confirmed_external_write(result)
        external_ref = _external_write_ref(result)
        blocked_reasons = [] if write_confirmed else ["Brand response write is unconfirmed and cannot complete."]
        trace.append(f"Brand Monitor write status={write_status}, confirmed={write_confirmed}")
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "write_unconfirmed",
            confidence=0.9 if write_confirmed else 0.0,
            rationale=(
                "Brand Monitor external write was confirmed by the connector."
                if write_confirmed
                else "Brand Monitor external write did not return confirmed vendor evidence."
            ),
            recommended_actions=[
                {
                    "action": "monitor_brand_response" if write_confirmed else "verify_brand_write_before_retry",
                    "reason": (
                        "Track follow-up sentiment and crisis volume."
                        if write_confirmed
                        else "Check vendor state with the idempotency key before retrying."
                    ),
                }
            ],
            approval_required=False,
            hitl_required=not write_confirmed,
            policy_result=policy,
            approval_satisfied=_has_approval(inputs),
            escalation=escalation,
            blocked_reasons=blocked_reasons,
            external_write_status=write_status,
            external_write_required=True,
            external_write_ref=external_ref,
            extra={"write_payload": _brand_write_payload(action, inputs)},
        )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        call_start = time.monotonic()
        try:
            result = await self._call_tool(
                connector_name=connector,
                tool_name=tool,
                params=params,
                idempotency_key=idempotency_key,
            )
            latency = int((time.monotonic() - call_start) * 1000)
            status = "error" if "error" in result else "success"
            trace.append(f"[tool] {connector}.{tool} -> {status} ({latency}ms)")
            tool_records.append(ToolCallRecord(tool_name=f"{connector}.{tool}", status=status, latency_ms=latency))
            return result
        # enterprise-gate: broad-except-ok reason=agent-tool-call-failure-returns-explicit-error-result
        except Exception as exc:
            latency = int((time.monotonic() - call_start) * 1000)
            trace.append(f"[tool] {connector}.{tool} -> exception: {exc} ({latency}ms)")
            tool_records.append(ToolCallRecord(tool_name=f"{connector}.{tool}", status="error", latency_ms=latency))
            return {"error": str(exc)}

    def _base_output(
        self,
        task,
        *,
        action: str,
        status: str,
        confidence: float,
        rationale: str,
        recommended_actions: list[dict[str, Any]],
        approval_required: bool = False,
        hitl_required: bool = False,
        policy_context: dict[str, Any] | None = None,
        policy_result: dict[str, Any] | None = None,
        approval_satisfied: bool = False,
        escalation: dict[str, Any] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        degraded_reasons: list[str] | None = None,
        blocked_reasons: list[str] | None = None,
        external_write_status: str = "not_required",
        external_write_required: bool = False,
        external_write_ref: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = policy_result or evaluate_marketing_policy(
            {
                "workflow_id": "brand_crisis_response",
                "workflow_mode": "shadow",
                "action": action,
                "external_write_required": external_write_required,
                **(policy_context or {}),
            }
        )
        escalation_ref = None
        if escalation and escalation.get("decision") != "no_escalation":
            escalation_ref = escalation.get("decision_audit_ref") or escalation.get("audit_reference")
        policy_needs_review = (
            policy.get("decision") in {"requires_approval", "requires_escalation", "read_only_only"}
            and not approval_satisfied
        )
        output: dict[str, Any] = {
            "status": status,
            "agent_type": self.agent_type,
            "action": action,
            "confidence": round(max(0.0, min(confidence, 1.0)), 3),
            "rationale": rationale,
            "recommended_actions": recommended_actions,
            "source_refs": source_refs or [],
            "policy_result": policy,
            "policy_ref": policy.get("decision_audit_ref") or policy.get("policy_id"),
            "approval_required": approval_required or policy_needs_review,
            "hitl_required": hitl_required,
            "escalation_ref": escalation_ref,
            "escalation_result": escalation,
            "audit_ref": f"brand_monitor:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": BRAND_MONITOR_AGENT_MATURITY,
        }
        if extra:
            output.update(extra)
        return output

    def _complete_or_hitl(
        self,
        task,
        msg_id: str,
        output: dict[str, Any],
        trace: list[str],
        tool_calls: list[ToolCallRecord],
        start: float,
    ):
        if output.get("hitl_required") or output.get("approval_required") or output.get("blocked_reasons"):
            return self._make_result(
                task,
                msg_id,
                "hitl_triggered",
                output,
                float(output.get("confidence") or 0.0),
                trace,
                tool_calls,
                hitl_request=_hitl_request(task, output),
                start=start,
            )
        return self._make_result(
            task,
            msg_id,
            "completed",
            output,
            float(output.get("confidence") or 0.0),
            trace,
            tool_calls,
            start=start,
        )


def _task_inputs(task) -> dict[str, Any]:
    payload = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
    return payload if isinstance(payload, dict) else {}


def _task_action(task) -> str:
    return str(task.task.action if hasattr(task.task, "action") else task.task.get("action", "monitor_mentions"))


def _normalize_action(action: str) -> str:
    normalized = _normalize(action)
    aliases = {
        "aggregate": "mention_aggregation",
        "aggregate_brand_mentions": "mention_aggregation",
        "brand_claim_review": "brand_claim",
        "brand_sentiment": "sentiment_trend",
        "crisis_severity": "classify_crisis_severity",
        "negative_volume_spike": "detect_negative_spike",
        "playbook": "recommend_response_playbook",
        "publish_response": "publish_brand_response",
        "response_recommendation": "recommend_response_playbook",
        "sentiment": "sentiment_trend",
    }
    return aliases.get(normalized, normalized)


def _normalized_mentions(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _dict_list(
        inputs.get("mentions")
        or inputs.get("brand_mentions")
        or inputs.get("social_mentions")
        or inputs.get("news_mentions")
    )
    return [_normalize_mention(row, index) for index, row in enumerate(rows)]


def _normalize_mention(row: dict[str, Any], index: int) -> dict[str, Any]:
    text = str(row.get("text") or row.get("body") or row.get("content") or row.get("summary") or "").strip()
    explicit_sentiment = str(row.get("sentiment") or "").strip().lower()
    sentiment = _classify_sentiment(text, explicit_sentiment)
    entity = str(row.get("entity") or row.get("brand") or row.get("company") or "brand").strip() or "brand"
    entity_type = str(row.get("entity_type") or row.get("type") or "").strip().lower()
    if not entity_type:
        entity_type = "competitor" if row.get("competitor") or row.get("competitor_name") else "brand"
    topic = str(row.get("topic") or row.get("category") or _topic_from_text(text)).strip().lower()
    return {
        "mention_id": str(row.get("mention_id") or row.get("id") or f"mention-{index + 1}"),
        "text": text,
        "source": str(row.get("source") or row.get("connector_key") or "provided_input").strip().lower(),
        "channel": str(row.get("channel") or row.get("platform") or row.get("source") or "unknown").strip().lower(),
        "topic": topic or "general",
        "entity": entity,
        "entity_type": entity_type,
        "competitor": str(row.get("competitor") or row.get("competitor_name") or "").strip(),
        "sentiment": sentiment,
        "sentiment_score": _sentiment_score(sentiment, row.get("sentiment_score")),
        "influence": str(row.get("influence") or row.get("author_tier") or "normal").strip().lower(),
        "created_at": str(row.get("created_at") or row.get("observed_at") or row.get("timestamp") or ""),
        "source_url": row.get("source_url") or row.get("url"),
        "false_positive": bool(row.get("false_positive") or row.get("irrelevant") or row.get("is_false_positive")),
    }


def _classify_sentiment(text: str, explicit: str) -> str:
    if explicit in {"positive", "neutral", "negative"}:
        return explicit
    normalized = text.lower()
    negative_score = sum(1 for term in NEGATIVE_TERMS if term in normalized)
    positive_score = sum(1 for term in POSITIVE_TERMS if term in normalized)
    if negative_score > positive_score:
        return "negative"
    if positive_score > negative_score:
        return "positive"
    return "neutral"


def _sentiment_score(sentiment: str, explicit: Any) -> float:
    if explicit is not None:
        return _float(explicit, {"positive": 0.7, "neutral": 0.0, "negative": -0.7}.get(sentiment, 0.0))
    return {"positive": 0.75, "neutral": 0.0, "negative": -0.75}.get(sentiment, 0.0)


def _suppress_false_positives(
    mentions: list[dict[str, Any]],
    inputs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    terms = [term.lower() for term in _string_list(inputs.get("false_positive_terms"))]
    if not terms:
        terms = list(DEFAULT_FALSE_POSITIVE_TERMS)
    retained: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    brand_terms = [term.lower() for term in _string_list(inputs.get("brand_terms") or inputs.get("brand_names"))]
    for mention in mentions:
        text = str(mention.get("text") or "").lower()
        explicit = bool(mention.get("false_positive"))
        term_match = any(term and term in text for term in terms)
        missing_brand_when_required = bool(brand_terms) and not any(term in text for term in brand_terms)
        if explicit or term_match or missing_brand_when_required:
            suppressed.append({**mention, "suppression_reason": "false_positive_or_irrelevant"})
        else:
            retained.append(mention)
    return retained, suppressed


def _mention_groups(mentions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_source": _count_by(mentions, "source"),
        "by_channel": _count_by(mentions, "channel"),
        "by_topic": _count_by(mentions, "topic"),
        "by_entity": _count_by(mentions, "entity_type"),
        "competitors": _count_by(
            [mention for mention in mentions if mention.get("competitor")],
            "competitor",
        ),
    }


def _sentiment_trend(mentions: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
    counts = {
        "positive": sum(1 for mention in mentions if mention.get("sentiment") == "positive"),
        "neutral": sum(1 for mention in mentions if mention.get("sentiment") == "neutral"),
        "negative": sum(1 for mention in mentions if mention.get("sentiment") == "negative"),
    }
    total = sum(counts.values())
    distribution = {key: _pct(value, total) for key, value in counts.items()}
    previous = _previous_sentiment(inputs)
    delta = {key: counts[key] - int(previous.get(key, 0)) for key in counts}
    baseline_negative = _float(inputs.get("baseline_negative_mentions"), float(previous.get("negative", 0)))
    if baseline_negative <= 0:
        baseline_negative = _float(inputs.get("baseline_negative_volume"), 0.0)
    return {
        "counts": counts,
        "total": total,
        "distribution": distribution,
        "negative_ratio": distribution["negative"],
        "previous_counts": previous,
        "delta": delta,
        "baseline_negative_mentions": baseline_negative,
    }


def _negative_spike(sentiment: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    current = int((sentiment.get("counts") or {}).get("negative") or 0)
    baseline = _float(sentiment.get("baseline_negative_mentions"), 0.0)
    threshold_multiplier = _float(inputs.get("negative_spike_multiplier"), 2.0)
    min_count = int(_float(inputs.get("negative_spike_min_count"), 5.0))
    threshold = max(float(min_count), baseline * threshold_multiplier)
    triggered = current >= threshold and current >= min_count
    ratio = _float(sentiment.get("negative_ratio"), 0.0)
    severity = "none"
    if triggered:
        if current >= 10 and ((baseline and current >= baseline * 4) or ratio >= 0.65):
            severity = "critical"
        elif current >= 5 and ((baseline and current >= baseline * 3) or ratio >= 0.45):
            severity = "high"
        else:
            severity = "medium"
    return {
        "triggered": triggered,
        "current_negative_mentions": current,
        "baseline_negative_mentions": baseline,
        "threshold": round(threshold, 2),
        "threshold_multiplier": threshold_multiplier,
        "severity": severity,
    }


def _crisis_classification(
    mentions: list[dict[str, Any]],
    sentiment: dict[str, Any],
    spike: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    text = " ".join(str(mention.get("text") or "").lower() for mention in mentions)
    crisis_term_hits = sorted({term for term in CRISIS_TERMS if term in text})
    executive_mentions = sum(
        1
        for mention in mentions
        if mention.get("influence") in {"executive", "journalist", "analyst"}
    )
    negative = int((sentiment.get("counts") or {}).get("negative") or 0)
    negative_ratio = _float(sentiment.get("negative_ratio"), 0.0)
    if inputs.get("crisis_severity"):
        severity = str(inputs["crisis_severity"]).strip().lower()
    elif inputs.get("crisis_response") or len(crisis_term_hits) >= 2 or spike.get("severity") == "critical":
        severity = "critical"
    elif crisis_term_hits or spike.get("severity") == "high" or (negative >= 8 and negative_ratio >= 0.45):
        severity = "high"
    elif spike.get("triggered") or negative >= 3 or negative_ratio >= 0.25:
        severity = "medium"
    else:
        severity = "low"
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "medium"
    return {
        "severity": severity,
        "negative_mentions": negative,
        "negative_ratio": round(negative_ratio, 3),
        "crisis_terms": crisis_term_hits,
        "executive_or_media_mentions": executive_mentions,
        "public_response_required": severity in {"high", "critical"},
        "escalation_required": severity in {"high", "critical"},
    }


def _playbook_recommendations(
    crisis: dict[str, Any],
    spike: dict[str, Any],
    groups: dict[str, Any],
    _inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    severity = str(crisis.get("severity") or "low")
    if severity == "critical":
        return [
            {
                "action": "escalate_brand_crisis",
                "reason": "Critical brand risk requires Brand Comms, CMO, Legal, and CEO review.",
            },
            {
                "action": "prepare_approved_holding_statement",
                "reason": "Public response must be reviewed before publication.",
            },
            {
                "action": "pause_scheduled_sensitive_posts",
                "reason": "Avoid amplifying brand risk while crisis response is reviewed.",
            },
        ]
    if severity == "high":
        return [
            {
                "action": "escalate_to_brand_comms_and_cmo",
                "reason": "High brand risk needs owner routing and response approval.",
            },
            {
                "action": "draft_response_options",
                "reason": "Prepare reviewed response variants without publishing.",
            },
        ]
    if severity == "medium" or spike.get("triggered"):
        return [
            {
                "action": "assign_brand_owner_review",
                "reason": "Negative mention volume is elevated and should be reviewed.",
            },
            {
                "action": "monitor_followup_mentions",
                "reason": f"Track channels: {', '.join(sorted((groups.get('by_channel') or {}).keys())) or 'unknown'}.",
            },
        ]
    return [
        {
            "action": "continue_brand_monitoring",
            "reason": "Mention volume and sentiment are within normal operating range.",
        }
    ]


def _read_degraded_reasons(inputs: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if inputs.get("connector_read_ready") is False or inputs.get("connector_degraded") is True:
        reasons.append("Brand monitoring connector is degraded or unavailable.")
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        relevant = [contract for contract in contracts if _is_brand_contract(contract)]
        if relevant and not any(bool(contract.get("read_ready", contract.get("configured"))) for contract in relevant):
            reasons.append("Brand monitoring source connector is not read-ready.")
        if any(contract.get("degraded") or contract.get("health_status") == "degraded" for contract in relevant):
            reasons.append("One or more brand monitoring sources are degraded.")
    return _dedupe(reasons)


def _monitor_confidence(
    retained_mentions: list[dict[str, Any]],
    degraded_reasons: list[str],
    crisis: dict[str, Any],
) -> float:
    if not retained_mentions:
        base = 0.58
    elif len(retained_mentions) < 3:
        base = 0.72
    else:
        base = 0.88
    if crisis.get("severity") in {"high", "critical"}:
        base = min(0.92, base + 0.04)
    if degraded_reasons:
        base -= 0.22
    return max(0.0, min(base, 1.0))


def _policy_context_for_write(action: str, inputs: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "workflow_id": "brand_crisis_response",
        "workflow_mode": mode,
        "action": action,
        "external_write_required": True,
        "public_response": action in {"crisis_response", "public_response", "publish_brand_response"},
        "crisis_response": (
            action in {"crisis_response", "publish_brand_response"}
            or bool(inputs.get("crisis_response"))
        ),
        "brand_claim": action == "brand_claim",
        "comparative_claim": action == "comparative_claim",
        "pricing_claim": action == "pricing_claim",
        "legal_claim": bool(inputs.get("legal_claim")),
    }


def _escalation_for_brand_event(
    task,
    action: str,
    inputs: dict[str, Any],
    *,
    crisis: dict[str, Any],
    spike: dict[str, Any],
) -> dict[str, Any]:
    severity = str(crisis.get("severity") or spike.get("severity") or "medium")
    return evaluate_marketing_escalation(
        {
            "workflow_id": "brand_crisis_response",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "event_type": "crisis_public_response",
            "crisis_response": severity in {"high", "critical"},
            "public_response": False,
            "severity": severity,
            "reason": str(inputs.get("escalation_reason") or "Brand monitoring signal requires owner routing."),
        }
    )


def _escalation_for_write(
    task,
    action: str,
    inputs: dict[str, Any],
    policy_result: dict[str, Any],
) -> dict[str, Any]:
    return evaluate_marketing_escalation(
        {
            "workflow_id": "brand_crisis_response",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "public_response": action in {"crisis_response", "public_response", "publish_brand_response"},
            "crisis_response": (
                action in {"crisis_response", "publish_brand_response"}
                or bool(inputs.get("crisis_response"))
            ),
            "brand_claim": action == "brand_claim",
            "comparative_claim": action == "comparative_claim",
            "pricing_claim": action == "pricing_claim",
            "legal_claim": bool(inputs.get("legal_claim")),
            "severity": str(inputs.get("severity") or inputs.get("crisis_severity") or "high"),
            "reason": "Brand Monitor external action requires owner routing.",
            "marketing_policy_decision": policy_result,
        }
    )


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "Brand Monitor connector is not write-safe."
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        for contract in contracts:
            if not _is_brand_write_contract(contract):
                continue
            if contract.get("mock_or_test_double"):
                return False, "Test-double connector proof cannot satisfy active Brand Monitor writes."
            if bool(contract.get("write_safe", contract.get("write_ready"))):
                return True, ""
            status = str(contract.get("write_status") or contract.get("contract_state") or "unknown")
            return False, f"Brand Monitor connector contract is not write-safe ({status})."
        return False, "No Social/Brand connector contract is write-safe for Brand Monitor action."
    return False, "Brand Monitor connector write-readiness evidence is missing."


def _is_brand_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"brand", "social"} or key in {"brandwatch", "buffer", "twitter", "x", "youtube"}


def _is_brand_write_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"social", "brand"} or key in {"brandwatch", "buffer", "twitter", "x"}


def _has_approval(inputs: dict[str, Any]) -> bool:
    return bool(
        inputs.get("approved")
        or inputs.get("approval_ref")
        or inputs.get("approval_result_ref")
        or inputs.get("policy_approval_ref")
    )


def _has_escalation_approval(inputs: dict[str, Any]) -> bool:
    return bool(inputs.get("escalation_ref") or inputs.get("escalation_result_ref") or inputs.get("approved"))


def _connector_tool(action: str, inputs: dict[str, Any]) -> tuple[str, str]:
    explicit = str(inputs.get("connector_key") or inputs.get("connector") or "").strip().lower()
    if action in {"crisis_response", "public_response", "publish_brand_response"}:
        return explicit or "buffer", "publish_brand_response"
    return explicit or "brandwatch", "record_brand_claim"


def _brand_write_payload(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "message": inputs.get("message") or inputs.get("copy") or inputs.get("claim"),
        "severity": inputs.get("severity") or inputs.get("crisis_severity"),
        "source_refs": _dict_list(inputs.get("source_refs")),
        "rollback_plan": inputs.get("rollback_plan") or "retract or pause public response after review",
    }


def _external_write_confirmation_status(result: dict[str, Any] | None) -> str:
    if not result:
        return "write_unconfirmed"
    return str(
        result.get("external_write_confirmation_status")
        or result.get("write_confirmation_status")
        or result.get("status")
        or "write_unconfirmed"
    )


def _is_confirmed_external_write(result: dict[str, Any] | None) -> bool:
    if not result or "error" in result:
        return False
    status = _external_write_confirmation_status(result)
    return status == "write_confirmed" and bool(
        result.get("external_object_id") or result.get("external_id") or result.get("confirmed_at")
    )


def _external_write_ref(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    return {
        "connector_key": result.get("connector_key"),
        "external_object_id": result.get("external_object_id") or result.get("external_id"),
        "source_url": result.get("source_url") or result.get("url"),
        "idempotency_key": result.get("idempotency_key"),
        "confirmed_at": result.get("confirmed_at"),
        "audit_ref": result.get("audit_ref") or result.get("audit_reference"),
    }


def _hitl_request(task, output: dict[str, Any]) -> HITLRequest:
    reasons = output.get("blocked_reasons") or []
    if not reasons and output.get("policy_result"):
        reasons = [str(output["policy_result"].get("reason") or "Brand Monitor action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "cmo"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "Brand Monitor review required.",
        trigger_type="brand_monitor_review",
        decision_required=DecisionRequired(
            question="Review brand monitoring recommendation or public-response safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "Brand Monitor review",
            recommendation=output.get("recommended_actions", [{}])[0].get("action", "review"),
            agent_confidence=float(output.get("confidence") or 0.0),
            supporting_data={
                "policy_decision": (output.get("policy_result") or {}).get("decision"),
                "blocked_reasons": output.get("blocked_reasons") or [],
                "external_write_status": output.get("external_write_confirmation_status"),
                "crisis_severity": (
                    (output.get("brand_monitoring") or {}).get("crisis_severity") or {}
                ).get("severity"),
            },
        ),
        assignee=HITLAssignee(role=str(role), notify_channels=["slack", "email"]),
    )


def _source_refs(inputs: dict[str, Any], mentions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = _dict_list(inputs.get("source_refs"))
    for mention in mentions:
        refs.append(
            {
                "type": str(mention.get("source") or "brand_source"),
                "ref_id": str(mention.get("mention_id") or "provided_input"),
                "source_url": mention.get("source_url"),
            }
        )
    return _dedupe_refs(refs)


def _previous_sentiment(inputs: dict[str, Any]) -> dict[str, int]:
    direct = inputs.get("previous_sentiment_counts") or inputs.get("baseline_sentiment_counts")
    if isinstance(direct, dict):
        return {
            "positive": int(_float(direct.get("positive"), 0.0)),
            "neutral": int(_float(direct.get("neutral"), 0.0)),
            "negative": int(_float(direct.get("negative"), 0.0)),
        }
    previous_mentions = [
        _normalize_mention(row, index)
        for index, row in enumerate(_dict_list(inputs.get("previous_mentions")))
    ]
    return {
        "positive": sum(1 for mention in previous_mentions if mention.get("sentiment") == "positive"),
        "neutral": sum(1 for mention in previous_mentions if mention.get("sentiment") == "neutral"),
        "negative": sum(1 for mention in previous_mentions if mention.get("sentiment") == "negative"),
    }


def _topic_from_text(text: str) -> str:
    normalized = text.lower()
    if any(term in normalized for term in ("price", "pricing", "cost")):
        return "pricing"
    if any(term in normalized for term in ("outage", "down", "broken")):
        return "reliability"
    if any(term in normalized for term in ("security", "breach", "data")):
        return "security"
    if any(term in normalized for term in ("support", "service", "response")):
        return "support"
    return "general"


def _count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "unknown").strip().lower() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _pct(value: int, total: int) -> float:
    return round(value / total, 3) if total else 0.0


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return _normalize(
        inputs.get("workflow_mode")
        or inputs.get("mode")
        or inputs.get("readiness")
        or inputs.get("capability_state")
        or "shadow"
    )


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            str(ref.get("type") or ref.get("connector_key") or ""),
            str(ref.get("ref_id") or ref.get("object") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result
