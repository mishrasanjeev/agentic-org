"""Social Media agent implementation."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
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

SOCIAL_AGENT_MATURITY = "beta"
SOCIAL_CONFIDENCE_FLOOR = 0.82
DEFAULT_CHANNELS = ("linkedin", "twitter", "youtube")
CHANNEL_LIMITS = {
    "twitter": 280,
    "x": 280,
    "linkedin": 1300,
    "facebook": 1200,
    "instagram": 1000,
    "youtube": 1000,
}
CHANNEL_CONNECTORS = {
    "buffer": ("buffer", "create_post"),
    "linkedin": ("buffer", "create_post"),
    "facebook": ("buffer", "create_post"),
    "instagram": ("buffer", "create_post"),
    "twitter": ("twitter", "create_tweet"),
    "x": ("twitter", "create_tweet"),
    "youtube": ("youtube", "create_community_post"),
}
READ_ONLY_ACTIONS = {
    "classify_reply_risk",
    "content_calendar",
    "draft_social_post",
    "engagement_triage",
    "generate_content_calendar",
    "get_weekly_engagement",
    "optimize_posting_schedule",
    "posting_schedule_optimization",
    "reply_risk_classification",
    "social_post_draft",
    "triage_engagement",
}
WRITE_ACTIONS = {
    "publish_post",
    "reply_to_mention",
    "schedule_campaign_posts",
    "schedule_content_promotion",
    "schedule_post",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

CRISIS_TERMS = (
    "breach",
    "boycott",
    "crisis",
    "data leak",
    "fraud",
    "lawsuit",
    "outage",
    "regulator",
    "scam",
    "unsafe",
)
LEGAL_PRICING_TERMS = (
    "compliance",
    "contract",
    "guarantee",
    "guaranteed",
    "legal",
    "pricing",
    "refund",
    "risk-free",
    "terms",
)
COMPARATIVE_TERMS = ("competitor", "versus", " vs ", "better than", "cheaper than")
EXECUTIVE_TERMS = ("board", "ceo", "cfo", "cmo", "founder", "minister", "press")
NEGATIVE_TERMS = (
    "angry",
    "bad",
    "broken",
    "cancel",
    "complaint",
    "hate",
    "poor",
    "terrible",
    "unhappy",
)


@AgentRegistry.register
class SocialMediaAgent(BaseAgent):
    agent_type = "social_media"
    domain = "marketing"
    confidence_floor = SOCIAL_CONFIDENCE_FLOOR
    prompt_file = "social_media.prompt.txt"

    async def execute(self, task):
        """Run deterministic social planning, triage, drafting, or guarded publishing."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"Social Media action={action}, mode={mode}")

            if action == "content_calendar":
                output = self._build_content_calendar(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action == "posting_schedule_optimization":
                output = self._optimize_posting_schedule(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action == "engagement_triage":
                output = self._triage_engagement(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action == "reply_risk_classification":
                output = self._classify_reply_risk(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action == "social_post_draft":
                output = self._draft_social_post(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_social_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported Social Media action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_social_action",
                        "reason": "The Social Media agent only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported Social Media action '{action}'."],
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
                error={"code": "SOCIAL_ACTION_UNSUPPORTED", "message": output["blocked_reasons"][0]},
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("social_media_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "SOCIAL_MEDIA_ERR", "message": str(exc)},
                start=start,
            )

    def _build_content_calendar(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        channels = _channels(inputs)
        weeks = max(1, min(_int(inputs.get("weeks"), 2), 8))
        start_date = _parse_date(inputs.get("start_date")) or datetime(2026, 5, 25, tzinfo=UTC)
        themes = _string_list(inputs.get("themes")) or [
            inputs.get("campaign_theme") or inputs.get("topic") or "brand trust"
        ]
        audience = str(inputs.get("target_audience") or "marketing audience")
        calendar: list[dict[str, Any]] = []
        for week in range(weeks):
            for index, channel in enumerate(channels):
                theme = str(themes[(week + index) % len(themes)])
                slot = DEFAULT_BEST_SLOTS.get(channel, DEFAULT_BEST_SLOTS["linkedin"])
                scheduled_for = start_date + timedelta(days=week * 7 + index)
                calendar.append(
                    {
                        "date": scheduled_for.date().isoformat(),
                        "channel": channel,
                        "slot": slot,
                        "theme": theme,
                        "post_type": _post_type(channel, index),
                        "objective": _objective_for_theme(theme),
                        "approval_required": False,
                    }
                )
        trace.append(f"Generated {len(calendar)} calendar items for {len(channels)} channels")
        return self._base_output(
            task,
            action="generate_content_calendar",
            status="calendar_generated",
            confidence=0.86,
            rationale=f"Built a {weeks}-week social calendar for {audience} across {len(channels)} channels.",
            recommended_actions=[
                {"action": "review_calendar", "reason": "Confirm channel mix and campaign themes."},
                {"action": "draft_posts", "reason": "Generate drafts for approved calendar slots."},
            ],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "social_publishing"},
            extra={"content_calendar": calendar, "channels": channels, "weeks": weeks},
        )

    def _optimize_posting_schedule(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        channels = _channels(inputs)
        history = _dict_list(inputs.get("historical_engagement"))
        recommendations: list[dict[str, Any]] = []
        for channel in channels:
            rows = [row for row in history if _normalize_channel(row.get("channel")) == channel]
            if rows:
                best = max(rows, key=lambda row: _float(row.get("engagement_rate"), 0.0))
                slot = f"{best.get('day', 'Tue')} {best.get('hour', '10:00')}"
                score = round(_float(best.get("engagement_rate"), 0.0), 4)
                source = "historical_engagement"
            else:
                slot = DEFAULT_BEST_SLOTS.get(channel, DEFAULT_BEST_SLOTS["linkedin"])
                score = DEFAULT_SLOT_SCORES.get(channel, 0.04)
                source = "default_benchmark"
            recommendations.append(
                {
                    "channel": channel,
                    "recommended_slot": slot,
                    "expected_engagement_rate": score,
                    "source": source,
                }
            )
        recommendations.sort(key=lambda row: (-row["expected_engagement_rate"], row["channel"]))
        trace.append(f"Optimized posting schedule for {len(recommendations)} channels")
        return self._base_output(
            task,
            action="optimize_posting_schedule",
            status="schedule_recommended",
            confidence=0.84 if history else 0.72,
            rationale=(
                "Selected highest observed engagement slots where history exists; "
                "used conservative defaults otherwise."
            ),
            recommended_actions=[
                {
                    "action": "apply_recommended_slots",
                    "reason": "Use these slots when scheduling drafts after approval.",
                }
            ],
            degraded_reasons=[] if history else ["No historical engagement rows supplied; benchmark defaults used."],
            source_refs=[{"kind": "fixture", "source": "historical_engagement", "count": len(history)}],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "social_publishing"},
            extra={"schedule_recommendations": recommendations},
        )

    def _triage_engagement(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        mentions = _mention_rows(inputs)
        triaged = []
        for mention in mentions:
            triaged.append({**mention, **_classify_social_risk(str(mention.get("text") or ""))})
        counts = _risk_counts(triaged)
        high_risk = [row for row in triaged if row["severity"] in {"high", "critical"}]
        trace.append(f"Triaged {len(triaged)} mentions; high_risk={len(high_risk)}")
        escalation = _escalation_for_rows(task, triaged)
        hitl_reasons = _hitl_reasons_for_risks(triaged)
        return self._base_output(
            task,
            action="triage_engagement",
            status="engagement_triaged",
            confidence=0.88 if triaged else 0.55,
            rationale=f"Classified {len(triaged)} social mentions by reply and escalation risk.",
            recommended_actions=_triage_recommendations(triaged),
            approval_required=bool(hitl_reasons),
            hitl_required=bool(hitl_reasons),
            escalation=escalation,
            source_refs=[{"kind": "social_mentions", "count": len(triaged)}],
            policy_context={
                "workflow_mode": _workflow_mode(inputs),
                "workflow_id": "social_engagement_triage",
                "risk_flags": _risk_flags(triaged),
                "crisis_response": any(row["risk_type"] == "crisis" for row in triaged),
            },
            extra={
                "mentions": triaged,
                "risk_counts": counts,
                "hitl_reasons": hitl_reasons,
            },
        )

    def _classify_reply_risk(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        text = str(inputs.get("reply_text") or inputs.get("text") or inputs.get("mention_text") or "")
        classification = _classify_social_risk(text)
        trace.append(f"Reply risk classified as {classification['severity']}:{classification['risk_type']}")
        escalation = _escalation_for_rows(task, [{"text": text, **classification}])
        hitl_reasons = _hitl_reasons_for_risks([{**classification, "text": text}])
        return self._base_output(
            task,
            action="classify_reply_risk",
            status="risk_classified",
            confidence=classification["confidence"],
            rationale=f"Reply risk is {classification['severity']} because {classification['reason']}",
            recommended_actions=[
                {
                    "action": classification["next_action"],
                    "reason": classification["reason"],
                }
            ],
            approval_required=bool(hitl_reasons),
            hitl_required=bool(hitl_reasons),
            escalation=escalation,
            policy_context={
                "workflow_mode": _workflow_mode(inputs),
                "workflow_id": "social_reply_review",
                "risk_flags": classification["risk_flags"],
                "crisis_response": classification["risk_type"] == "crisis",
                "pricing_claim": "pricing_or_legal" in classification["risk_flags"],
                "comparative_claim": "comparative_claim" in classification["risk_flags"],
            },
            extra={"classification": classification, "hitl_reasons": hitl_reasons},
        )

    def _draft_social_post(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        channel = _channels(inputs)[0]
        topic = str(inputs.get("topic") or inputs.get("campaign_theme") or "customer trust")
        audience = str(inputs.get("target_audience") or "operators")
        cta = str(inputs.get("cta") or "Review the full update")
        claims = _string_list(inputs.get("claims"))
        draft = _compose_post(topic, audience, cta, claims, channel)
        classification = _classify_social_risk(draft)
        hitl_reasons = _hitl_reasons_for_risks([{**classification, "text": draft}])
        trace.append(f"Drafted social post for {channel} with risk={classification['severity']}")
        return self._base_output(
            task,
            action="draft_social_post",
            status="draft_created",
            confidence=min(0.9, classification["confidence"] + 0.05),
            rationale=f"Drafted a {channel} post for {audience}; risk classification is {classification['severity']}.",
            recommended_actions=[
                {
                    "action": "review_draft" if hitl_reasons else "approve_calendar_slot",
                    "reason": "; ".join(hitl_reasons) if hitl_reasons else "Draft is recommendation-only.",
                }
            ],
            approval_required=bool(hitl_reasons),
            hitl_required=bool(hitl_reasons),
            escalation=_escalation_for_rows(task, [{"text": draft, **classification}]),
            policy_context={
                "workflow_mode": _workflow_mode(inputs),
                "workflow_id": "social_post_draft",
                "risk_flags": classification["risk_flags"],
                "pricing_claim": "pricing_or_legal" in classification["risk_flags"],
                "comparative_claim": "comparative_claim" in classification["risk_flags"],
            },
            extra={
                "draft": draft,
                "channel": channel,
                "classification": classification,
                "hitl_reasons": hitl_reasons,
            },
        )

    async def _guarded_social_write(
        self,
        task,
        action: str,
        inputs: dict[str, Any],
        mode: str,
        trace: list[str],
        tool_calls: list[ToolCallRecord],
    ) -> dict[str, Any]:
        channel = _channels(inputs)[0]
        text = str(inputs.get("post_text") or inputs.get("reply_text") or inputs.get("draft") or "")
        if not text:
            text = _compose_post(
                str(inputs.get("topic") or "company update"),
                str(inputs.get("target_audience") or "followers"),
                str(inputs.get("cta") or "Read more"),
                _string_list(inputs.get("claims")),
                channel,
            )
        classification = _classify_social_risk(text)
        policy_context = {
            "workflow_id": "social_publishing",
            "workflow_mode": mode,
            "action": (
                "schedule_post"
                if action in {"schedule_campaign_posts", "schedule_content_promotion"}
                else action
            ),
            "external_write_required": True,
            "customer_facing": True,
            "risk_flags": classification["risk_flags"],
            "crisis_response": classification["risk_type"] == "crisis",
            "public_response": action in {"public_response", "reply_to_mention"},
            "pricing_claim": "pricing_or_legal" in classification["risk_flags"],
            "legal_claim": "pricing_or_legal" in classification["risk_flags"],
            "comparative_claim": "comparative_claim" in classification["risk_flags"],
        }
        policy = evaluate_marketing_policy(policy_context)
        hitl_reasons = _hitl_reasons_for_risks([{**classification, "text": text}])
        blocked_reasons: list[str] = []
        degraded_reasons: list[str] = []

        if mode in SHADOW_MODES:
            blocked_reasons.append("Shadow/draft/internal Social Media workflows are read-only.")
            trace.append("Social write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.78,
                rationale="Prepared social recommendation without external publishing because workflow is read-only.",
                recommended_actions=[
                    {
                        "action": "create_internal_approval",
                        "reason": "Review the draft before any active social write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                blocked_reasons=blocked_reasons,
                external_write_status="not_required",
                external_write_required=False,
                escalation=_escalation_for_rows(task, [{"text": text, **classification}]),
                extra={"draft": text, "channel": channel, "classification": classification},
            )

        connector_safe, connector_reason = _connector_write_safe(inputs)
        if not connector_safe:
            blocked_reasons.append(connector_reason)
        if policy.get("decision") in {"requires_approval", "requires_escalation"} and not _has_approval(inputs):
            hitl_reasons.append(str(policy.get("reason") or "Social publishing requires approval."))
        if policy.get("decision") in {"blocked", "missing_policy", "read_only_only"}:
            blocked_reasons.append(str(policy.get("reason") or "Marketing policy blocks this social write."))

        escalation = _escalation_for_rows(task, [{"text": text, **classification}], policy_result=policy)
        if escalation and escalation.get("decision") != "no_escalation":
            if policy.get("decision") == "requires_escalation" and not _has_escalation_approval(inputs):
                hitl_reasons.append(str(escalation.get("reason") or "Social write requires escalation."))

        if blocked_reasons or hitl_reasons:
            trace.append(f"Social write blocked or approval-gated: {blocked_reasons + hitl_reasons}")
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.66,
                rationale=(
                    "Social write is blocked until connector, policy, approval, "
                    "and escalation prerequisites pass."
                ),
                recommended_actions=[
                    {
                        "action": "resolve_social_write_prerequisites",
                        "reason": "; ".join(blocked_reasons + hitl_reasons),
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                blocked_reasons=blocked_reasons,
                degraded_reasons=degraded_reasons,
                external_write_status="write_unconfirmed",
                external_write_required=True,
                escalation=escalation,
                extra={
                    "draft": text,
                    "channel": channel,
                    "classification": classification,
                    "hitl_reasons": hitl_reasons,
                },
            )

        write_result = _dict_or_none(inputs.get("external_write_result"))
        if write_result is None:
            connector, tool = _connector_tool(inputs, channel)
            write_result = await self._safe_tool_call(
                connector,
                tool,
                {
                    "text": text,
                    "channel": channel,
                    "scheduled_at": inputs.get("scheduled_at") or inputs.get("publish_at"),
                    "profile_id": inputs.get("profile_id"),
                },
                trace,
                tool_calls,
                idempotency_key=str(inputs.get("idempotency_key") or ""),
            )

        write_status = _external_write_confirmation_status(write_result)
        write_confirmed = _is_confirmed_external_write(write_result)
        if not write_confirmed:
            blocked_reasons.append("External social write is unconfirmed and cannot be marked published.")
        trace.append(f"Social write result status={write_status}, confirmed={write_confirmed}")
        external_ref = _external_write_ref(write_result) if write_confirmed else None
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "blocked",
            confidence=0.9 if write_confirmed else 0.62,
            rationale=(
                "Social write was confirmed by the connector."
                if write_confirmed
                else "Social write did not return confirmed external object evidence."
            ),
            recommended_actions=[
                {
                    "action": "monitor_social_post" if write_confirmed else "verify_connector_write",
                    "reason": (
                        "Track engagement and replies."
                        if write_confirmed
                        else "Check vendor state using the idempotency key before retrying."
                    ),
                }
            ],
            approval_required=False,
            hitl_required=not write_confirmed,
            policy_result=policy,
            approval_satisfied=_has_approval(inputs),
            blocked_reasons=blocked_reasons,
            external_write_status=write_status,
            external_write_required=True,
            external_write_ref=external_ref,
            escalation=escalation,
            extra={"draft": text, "channel": channel, "classification": classification},
        )

    async def _safe_tool_call(
        self,
        connector: str,
        tool: str,
        params: dict[str, Any],
        trace: list[str],
        tool_records: list[ToolCallRecord],
        *,
        idempotency_key: str = "",
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
                "workflow_id": "social_media",
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
            "audit_ref": f"social_media:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": SOCIAL_AGENT_MATURITY,
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
            hitl = _hitl_request(task, output)
            return self._make_result(
                task,
                msg_id,
                "hitl_triggered",
                output,
                float(output.get("confidence") or 0.0),
                trace,
                tool_calls,
                hitl_request=hitl,
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


DEFAULT_BEST_SLOTS = {
    "linkedin": "Tue 10:00",
    "twitter": "Wed 11:00",
    "x": "Wed 11:00",
    "facebook": "Thu 14:00",
    "instagram": "Fri 12:00",
    "youtube": "Tue 16:00",
}
DEFAULT_SLOT_SCORES = {
    "linkedin": 0.058,
    "twitter": 0.046,
    "x": 0.046,
    "facebook": 0.035,
    "instagram": 0.052,
    "youtube": 0.041,
}


def _task_inputs(task) -> dict[str, Any]:
    payload = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
    return payload if isinstance(payload, dict) else {}


def _task_action(task) -> str:
    return str(task.task.action if hasattr(task.task, "action") else task.task.get("action", "triage_engagement"))


def _normalize_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    aliases = {
        "classify_reply": "reply_risk_classification",
        "classify_reply_risk": "reply_risk_classification",
        "content_calendar": "content_calendar",
        "draft_post": "social_post_draft",
        "draft_social_post": "social_post_draft",
        "generate_calendar": "content_calendar",
        "generate_content_calendar": "content_calendar",
        "get_weekly_engagement": "engagement_triage",
        "optimize_posting_schedule": "posting_schedule_optimization",
        "publish": "publish_post",
        "recommend_reply": "social_post_draft",
        "schedule_content_promotion": "schedule_content_promotion",
        "triage_engagement": "engagement_triage",
        "triage_mentions": "engagement_triage",
    }
    return aliases.get(normalized, normalized)


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return str(inputs.get("workflow_mode") or inputs.get("mode") or "shadow").strip().lower()


def _channels(inputs: dict[str, Any]) -> list[str]:
    raw = inputs.get("channels") or inputs.get("platforms") or inputs.get("channel") or list(DEFAULT_CHANNELS)
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list | tuple | set):
        values = [str(item) for item in raw]
    else:
        values = list(DEFAULT_CHANNELS)
    normalized = [_normalize_channel(value) for value in values if str(value).strip()]
    return normalized or list(DEFAULT_CHANNELS)


def _normalize_channel(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return "twitter" if normalized in {"x", "twitter/x"} else normalized


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _post_type(channel: str, index: int) -> str:
    if channel in {"twitter", "x"}:
        return "short_thread" if index % 2 else "single_update"
    if channel == "youtube":
        return "community_post"
    if channel == "instagram":
        return "carousel_caption"
    return "thought_leadership"


def _objective_for_theme(theme: str) -> str:
    lower = theme.lower()
    if "case" in lower or "proof" in lower:
        return "build_trust"
    if "event" in lower or "webinar" in lower:
        return "drive_registration"
    if "launch" in lower:
        return "announce_launch"
    return "educate_audience"


def _compose_post(topic: str, audience: str, cta: str, claims: list[str], channel: str) -> str:
    claim_text = f" {' '.join(claims)}" if claims else ""
    post = f"{topic}: a practical update for {audience}.{claim_text} {cta}."
    limit = CHANNEL_LIMITS.get(channel, 600)
    if len(post) > limit:
        return post[: max(limit - 1, 0)].rstrip() + "."
    return post


def _classify_social_risk(text: str) -> dict[str, Any]:
    lower = f" {text.lower()} "
    flags: list[str] = []
    matched: list[str] = []
    if _contains_any(lower, CRISIS_TERMS, matched):
        flags.append("crisis_public_response")
        return {
            "risk_type": "crisis",
            "severity": "critical",
            "confidence": 0.93,
            "risk_flags": flags,
            "matched_terms": matched,
            "reason": "crisis or public trust terms were detected",
            "next_action": "escalate_crisis_response_to_exec",
        }
    if _contains_any(lower, LEGAL_PRICING_TERMS, matched):
        flags.append("pricing_or_legal")
    if _contains_any(lower, COMPARATIVE_TERMS, matched):
        flags.append("comparative_claim")
    if flags:
        return {
            "risk_type": "pricing_legal_or_comparative",
            "severity": "high",
            "confidence": 0.9,
            "risk_flags": flags,
            "matched_terms": matched,
            "reason": "pricing, legal, compliance, or comparative claim terms were detected",
            "next_action": "request_legal_review",
        }
    if _contains_any(lower, EXECUTIVE_TERMS, matched):
        return {
            "risk_type": "executive_or_press",
            "severity": "high",
            "confidence": 0.86,
            "risk_flags": ["executive_mention"],
            "matched_terms": matched,
            "reason": "executive, board, press, or public official mention detected",
            "next_action": "route_to_comms_lead",
        }
    if _contains_any(lower, NEGATIVE_TERMS, matched):
        return {
            "risk_type": "negative_feedback",
            "severity": "medium",
            "confidence": 0.82,
            "risk_flags": ["negative_sentiment"],
            "matched_terms": matched,
            "reason": "negative sentiment terms were detected",
            "next_action": "draft_service_recovery_reply",
        }
    return {
        "risk_type": "normal",
        "severity": "low",
        "confidence": 0.8,
        "risk_flags": [],
        "matched_terms": [],
        "reason": "no high-risk social reply signals were detected",
        "next_action": "reply_from_standard_playbook",
    }


def _contains_any(text: str, terms: tuple[str, ...], matched: list[str]) -> bool:
    found = False
    for term in terms:
        if term in text:
            matched.append(term.strip())
            found = True
    return found


def _mention_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inputs.get("mentions") or inputs.get("engagement_items") or []
    if isinstance(raw, str):
        return [{"mention_id": "mention-1", "text": raw, "channel": _channels(inputs)[0]}]
    rows = _dict_list(raw)
    if rows:
        return [
            {
                "mention_id": str(row.get("mention_id") or row.get("id") or f"mention-{idx + 1}"),
                "text": str(row.get("text") or row.get("body") or row.get("message") or ""),
                "channel": _normalize_channel(row.get("channel") or _channels(inputs)[0]),
                "author": row.get("author"),
                "url": row.get("url"),
            }
            for idx, row in enumerate(rows)
        ]
    return []


def _risk_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for row in rows:
        severity = str(row.get("severity") or "low")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _triage_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return [{"action": "connect_social_mentions", "reason": "No mentions were supplied for triage."}]
    recs = []
    for row in rows:
        recs.append(
            {
                "action": row["next_action"],
                "mention_id": row.get("mention_id"),
                "severity": row["severity"],
                "reason": row["reason"],
            }
        )
    return recs


def _hitl_reasons_for_risks(rows: list[dict[str, Any]]) -> list[str]:
    reasons = []
    for row in rows:
        severity = str(row.get("severity") or "low")
        if severity == "critical":
            reasons.append("crisis/public response requires escalation")
        elif severity == "high":
            reasons.append("high-risk social reply requires approval")
    return _dedupe(reasons)


def _risk_flags(rows: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for row in rows:
        flags.extend(str(flag) for flag in row.get("risk_flags") or [])
    return _dedupe(flags)


def _escalation_for_rows(
    task,
    rows: list[dict[str, Any]],
    *,
    policy_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flags = _risk_flags(rows)
    crisis = any(row.get("risk_type") == "crisis" for row in rows)
    high = any(row.get("severity") in {"high", "critical"} for row in rows)
    if not crisis and not high and not policy_result:
        return evaluate_marketing_escalation(
            {
                "workflow_id": "social_media",
                "workflow_run_id": getattr(task, "workflow_run_id", None),
                "step_id": getattr(task, "step_id", None),
                "event_type": "social_engagement_triage",
            }
        )
    context = {
        "workflow_id": "social_media",
        "workflow_run_id": getattr(task, "workflow_run_id", None),
        "step_id": getattr(task, "step_id", None),
        "event_type": "crisis_public_response" if crisis else "high_risk_copy",
        "crisis_response": crisis,
        "public_response": crisis,
        "pricing_claim": "pricing_or_legal" in flags,
        "legal_claim": "pricing_or_legal" in flags,
        "comparative_claim": "comparative_claim" in flags,
        "severity": "critical" if crisis else ("high" if high else "medium"),
        "reason": "Social Media risk classification requires escalation review.",
    }
    if policy_result:
        context["marketing_policy_decision"] = policy_result
    return evaluate_marketing_escalation(context)


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "Social connector is not write-safe."
    contract = _dict_or_none(inputs.get("connector_contract")) or _first_dict(inputs.get("connector_contracts"))
    if contract:
        if contract.get("mock_or_test_double"):
            return False, "Test-double connector proof cannot satisfy active Social Media writes."
        write_safe = bool(contract.get("write_safe", contract.get("write_ready")))
        health = str(
            contract.get("health_status")
            or contract.get("contract_state")
            or contract.get("write_status")
            or ""
        )
        if write_safe:
            return True, ""
        return False, f"Social connector contract is not write-safe ({health or 'unknown'})."
    return False, "Social connector write-readiness evidence is missing."


def _has_approval(inputs: dict[str, Any]) -> bool:
    return bool(
        inputs.get("approved")
        or inputs.get("approval_ref")
        or inputs.get("approval_result_ref")
        or inputs.get("policy_approval_ref")
    )


def _has_escalation_approval(inputs: dict[str, Any]) -> bool:
    return bool(inputs.get("escalation_ref") or inputs.get("escalation_result_ref") or inputs.get("approved"))


def _connector_tool(inputs: dict[str, Any], channel: str) -> tuple[str, str]:
    connector_key = str(inputs.get("connector_key") or inputs.get("connector") or "").strip().lower()
    if connector_key and connector_key in CHANNEL_CONNECTORS:
        return CHANNEL_CONNECTORS[connector_key]
    return CHANNEL_CONNECTORS.get(channel, CHANNEL_CONNECTORS["buffer"])


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
    reasons = output.get("blocked_reasons") or output.get("hitl_reasons") or []
    if not reasons and output.get("policy_result"):
        reasons = [str(output["policy_result"].get("reason") or "Social Media action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "social_media_lead"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "Social Media review required.",
        trigger_type="social_media_review",
        decision_required=DecisionRequired(
            question="Review Social Media recommendation or publishing safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "Social Media review",
            recommendation=output.get("recommended_actions", [{}])[0].get("action", "review"),
            agent_confidence=float(output.get("confidence") or 0.0),
            supporting_data={
                "policy_decision": (output.get("policy_result") or {}).get("decision"),
                "blocked_reasons": output.get("blocked_reasons") or [],
                "degraded_reasons": output.get("degraded_reasons") or [],
                "external_write_status": output.get("external_write_confirmation_status"),
            },
        ),
        assignee=HITLAssignee(role=str(role), notify_channels=["slack", "email"]),
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _first_dict(value: Any) -> dict[str, Any] | None:
    rows = _dict_list(value)
    return rows[0] if rows else None


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
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
