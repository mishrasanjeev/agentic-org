"""CRM Intelligence agent implementation.

The CRM Intelligence agent is deterministic and vendor-neutral: it analyzes
structured CRM, pipeline, and lifecycle inputs supplied by connectors or
tests, emits contract-shaped CMO outputs, and fails closed before any active
CRM write (lead score push, lifecycle stage change, segment/list change,
target account change, or bulk CRM update). Production claims require real
CRM/intent connector readiness, policy/approval/audit evidence, confirmed
external writes, and pilot proof — none of which is supplied by this module
alone.
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

CRM_INTELLIGENCE_AGENT_MATURITY = "beta"
CRM_INTELLIGENCE_CONFIDENCE_FLOOR = 0.85

READ_ONLY_ACTIONS = {
    "account_deal_health_summary",
    "account_health_summary",
    "analyze_pipeline_velocity",
    "churn_risk_signal_extraction",
    "extract_churn_signals",
    "funnel_conversion_analysis",
    "lead_scoring_refresh",
    "pipeline_velocity_analysis",
    "promote_to_sql",
    "recommend_segments",
    "refresh_lead_scores",
    "score_lead",
    "segment_recommendation",
    "sql_promotion_criteria",
}
WRITE_ACTIONS = {
    "bulk_crm_update",
    "change_segment_membership",
    "change_target_accounts",
    "promote_lifecycle_stage",
    "update_crm",
    "update_lead_scores",
    "update_lifecycle_stage",
    "update_segment_membership",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

# Lead scoring weights (sum to 1.0). Deterministic, explainable.
LEAD_SCORE_WEIGHTS: dict[str, float] = {
    "firmographic_fit": 0.30,
    "title_seniority": 0.20,
    "engagement_recency": 0.20,
    "engagement_depth": 0.15,
    "intent_signal": 0.10,
    "demo_request": 0.05,
}
TITLE_SENIORITY_WEIGHTS: dict[str, float] = {
    "cxo": 1.0,
    "vp": 0.9,
    "director": 0.75,
    "manager": 0.55,
    "ic": 0.3,
    "intern": 0.05,
}
MQL_SCORE_FLOOR = 55.0
SQL_SCORE_FLOOR = 75.0
CHURN_RISK_FLOOR = 0.6
# Pipeline velocity bottleneck: any stage whose average days-in-stage is
# >= this multiple of the median is flagged.
VELOCITY_BOTTLENECK_MULTIPLIER = 1.75
# Minimum funnel sample size before conversion rates are considered
# statistically meaningful.
MIN_FUNNEL_SAMPLE = 25


@AgentRegistry.register
class CrmIntelligenceAgent(BaseAgent):
    agent_type = "crm_intelligence"
    domain = "marketing"
    confidence_floor = CRM_INTELLIGENCE_CONFIDENCE_FLOOR
    prompt_file = "crm_intelligence.prompt.txt"

    async def execute(self, task):
        """Run deterministic CRM/pipeline analysis or guarded CRM writes."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"CRM Intelligence action={action}, mode={mode}")

            if action in READ_ONLY_ACTIONS:
                output = self._read_only_action(task, action, inputs, mode, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_crm_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported CRM Intelligence action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_crm_intelligence_action",
                        "reason": "CRM Intelligence only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported CRM Intelligence action '{action}'."],
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
                error={
                    "code": "CRM_INTELLIGENCE_ACTION_UNSUPPORTED",
                    "message": output["blocked_reasons"][0],
                },
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("crm_intelligence_agent_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "CRM_INTELLIGENCE_AGENT_ERR", "message": str(exc)},
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
        pipeline_velocity = _pipeline_velocity_analysis(inputs)
        funnel_conversion = _funnel_conversion_analysis(inputs)
        scored_contacts = _lead_scoring_refresh(inputs)
        churn_signals = _churn_risk_signals(inputs)
        segments = _segment_recommendations(scored_contacts, inputs)
        sql_classification = _sql_promotion_classification(scored_contacts, inputs)
        account_health = _account_deal_health_summary(inputs)
        degraded = _read_degraded_reasons(inputs)
        confidence = _analysis_confidence(
            pipeline_velocity,
            funnel_conversion,
            scored_contacts,
            churn_signals,
            account_health,
            degraded,
        )
        source_refs = _source_refs(
            inputs,
            scored_contacts,
            churn_signals["scored_accounts"],
            pipeline_velocity["deals"],
        )
        summary = {
            "pipeline_velocity": pipeline_velocity,
            "funnel_conversion": funnel_conversion,
            "lead_scoring": scored_contacts,
            "churn_signals": churn_signals,
            "segments": segments,
            "sql_promotion": sql_classification,
            "account_health": account_health,
        }
        trace.append(
            "CRM Intelligence analysis deals="
            f"{pipeline_velocity['deal_count']}, scored_contacts={len(scored_contacts['contacts'])}, "
            f"at_risk_accounts={len(churn_signals['at_risk_accounts'])}, degraded={len(degraded)}"
        )

        status = "crm_analysis_degraded" if degraded else "crm_analysis_ready"
        rationale = (
            "Built deterministic CRM/pipeline intelligence from structured deal, "
            "contact, account, and funnel inputs."
        )
        extra: dict[str, Any] = {"crm_analysis": summary}
        recommended = _default_recommendations(degraded)

        if action in {"pipeline_velocity_analysis", "analyze_pipeline_velocity"}:
            status = "pipeline_velocity_analyzed" if not degraded else "pipeline_velocity_degraded"
            rationale = "Computed deterministic stage velocity, bottlenecks, and stuck deals."
            recommended = pipeline_velocity["recommended_actions"] or recommended
            extra.update({"pipeline_velocity": pipeline_velocity})
        elif action == "funnel_conversion_analysis":
            status = "funnel_conversion_computed" if not degraded else "funnel_conversion_degraded"
            rationale = "Computed safe stage-to-stage conversion rates with low-sample handling."
            recommended = funnel_conversion["recommended_actions"] or recommended
            extra.update({"funnel_conversion": funnel_conversion})
        elif action in {"lead_scoring_refresh", "refresh_lead_scores", "score_lead"}:
            status = "lead_scores_refreshed" if not degraded else "lead_scoring_degraded"
            rationale = "Refreshed deterministic, explainable lead scores from canonical signals."
            recommended = scored_contacts["recommended_actions"] or recommended
            extra.update({"lead_scoring": scored_contacts})
        elif action in {"churn_risk_signal_extraction", "extract_churn_signals"}:
            status = "churn_signals_extracted" if not degraded else "churn_signals_degraded"
            rationale = (
                "Extracted churn risk signals from login activity, support load, NPS, "
                "payment health, and usage trend."
            )
            recommended = churn_signals["recommended_actions"] or recommended
            extra.update({"churn_signals": churn_signals})
        elif action in {"segment_recommendation", "recommend_segments"}:
            status = "segments_recommended" if not degraded else "segments_degraded"
            rationale = "Recommended deterministic segments by lifecycle, fit, and engagement."
            recommended = segments["recommended_actions"] or recommended
            extra.update({"segments": segments})
        elif action in {"sql_promotion_criteria", "promote_to_sql"}:
            status = "sql_promotion_classified" if not degraded else "sql_promotion_degraded"
            rationale = "Classified contacts as promotable or non-promotable to SQL."
            recommended = sql_classification["recommended_actions"] or recommended
            extra.update({"sql_promotion": sql_classification})
        elif action in {"account_health_summary", "account_deal_health_summary"}:
            status = "account_health_summarized" if not degraded else "account_health_degraded"
            rationale = "Summarized composite account/deal health from CRM-shaped inputs."
            recommended = account_health["recommended_actions"] or recommended
            extra.update({"account_health": account_health})

        return self._base_output(
            task,
            action=action,
            status=status,
            confidence=confidence,
            rationale=rationale,
            recommended_actions=recommended,
            policy_context={"workflow_id": "crm_pipeline_intelligence", "workflow_mode": mode},
            source_refs=source_refs,
            degraded_reasons=degraded,
            extra=extra,
        )

    async def _guarded_crm_write(
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
            blocked_reasons.append("Shadow/draft/internal CRM Intelligence workflows are read-only.")
            trace.append("CRM Intelligence write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.76,
                rationale="Prepared CRM change recommendation without mutating the CRM.",
                recommended_actions=[
                    {
                        "action": "create_internal_approval",
                        "reason": "Review the CRM change before any active external write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons,
                external_write_status="shadow_only",
                external_write_required=True,
                extra={"write_payload": _crm_write_payload(action, inputs)},
            )

        write_safe, write_reason = _connector_write_safe(inputs)
        if not write_safe:
            blocked_reasons.append(write_reason)
        if policy.get("decision") in {"requires_approval", "requires_escalation"} and not _has_approval(inputs):
            hitl_reasons.append(
                str(policy.get("reason") or "CRM Intelligence write requires approval.")
            )
        if policy.get("decision") in {"blocked", "missing_policy"}:
            blocked_reasons.append(
                str(policy.get("reason") or "Marketing policy blocks this CRM action.")
            )
        escalation_needs_review = escalation.get("decision") not in {None, "", "no_escalation", "notify_owner"}
        if escalation_needs_review and not _has_escalation_approval(inputs):
            hitl_reasons.append("CRM Intelligence write requires escalation review.")

        if blocked_reasons or hitl_reasons:
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale="CRM Intelligence external action failed closed before vendor mutation.",
                recommended_actions=[
                    {
                        "action": "resolve_crm_write_prerequisites",
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
                extra={"write_payload": _crm_write_payload(action, inputs)},
            )

        result = _dict_or_none(inputs.get("external_write_result"))
        connector, tool = _connector_tool(action, inputs)
        idempotency_key = str(
            inputs.get("idempotency_key")
            or inputs.get("request_fingerprint")
            or f"crm_intelligence:{task.correlation_id}:{task.step_id}:{action}"
        )
        if result is None:
            result = await self._safe_tool_call(
                connector,
                tool,
                _crm_write_payload(action, inputs),
                trace,
                tool_calls,
                idempotency_key=idempotency_key,
            )

        write_status = _external_write_confirmation_status(result)
        write_confirmed = _is_confirmed_external_write(result)
        external_ref = _external_write_ref(result)
        blocked_reasons = [] if write_confirmed else [
            "CRM Intelligence write is unconfirmed and cannot be marked complete."
        ]
        trace.append(f"CRM Intelligence write status={write_status}, confirmed={write_confirmed}")
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "write_unconfirmed",
            confidence=0.9 if write_confirmed else 0.0,
            rationale=(
                "CRM Intelligence external write was confirmed by the connector."
                if write_confirmed
                else "CRM Intelligence external write did not return confirmed vendor evidence."
            ),
            recommended_actions=[
                {
                    "action": (
                        "monitor_lifecycle_after_change"
                        if write_confirmed
                        else "verify_crm_write_before_retry"
                    ),
                    "reason": (
                        "Track lifecycle/segment impact after the confirmed CRM change."
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
            extra={"write_payload": _crm_write_payload(action, inputs)},
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
                "workflow_id": "crm_pipeline_intelligence",
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
            "audit_ref": f"crm_intelligence:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": CRM_INTELLIGENCE_AGENT_MATURITY,
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


# ---------------------------------------------------------------------------
# Task / input helpers
# ---------------------------------------------------------------------------


def _task_inputs(task) -> dict[str, Any]:
    payload = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
    return payload if isinstance(payload, dict) else {}


def _task_action(task) -> str:
    return str(
        task.task.action
        if hasattr(task.task, "action")
        else task.task.get("action", "pipeline_velocity_analysis")
    )


def _normalize_action(action: str) -> str:
    normalized = _normalize(action)
    aliases = {
        "analyze_funnel": "funnel_conversion_analysis",
        "funnel_analysis": "funnel_conversion_analysis",
        "funnel_conversion": "funnel_conversion_analysis",
        "lead_score_refresh": "lead_scoring_refresh",
        "lead_scoring": "lead_scoring_refresh",
        "pipeline_velocity": "pipeline_velocity_analysis",
        "promote_lead_to_sql": "sql_promotion_criteria",
        "promote_sql": "sql_promotion_criteria",
        "segment": "segment_recommendation",
        "segment_plan": "segment_recommendation",
        "sql_promotion": "sql_promotion_criteria",
        "update_lead_score": "update_lead_scores",
        "update_lifecycle": "update_lifecycle_stage",
        "update_segment": "update_segment_membership",
        "target_account_list_change": "change_target_accounts",
        "update_target_accounts": "change_target_accounts",
        "churn_extraction": "extract_churn_signals",
        "churn_risk": "extract_churn_signals",
        "promote_to_sql_classification": "promote_to_sql",
    }
    return aliases.get(normalized, normalized)


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return _normalize(
        inputs.get("workflow_mode")
        or inputs.get("mode")
        or inputs.get("readiness")
        or inputs.get("capability_state")
        or "shadow"
    )


# ---------------------------------------------------------------------------
# Domain logic — pipeline velocity / funnel conversion
# ---------------------------------------------------------------------------


def _pipeline_velocity_analysis(inputs: dict[str, Any]) -> dict[str, Any]:
    rows = _dict_list(inputs.get("deals") or inputs.get("pipeline_deals") or inputs.get("opportunities"))
    stage_totals: dict[str, float] = {}
    stage_counts: dict[str, int] = {}
    stuck: list[dict[str, Any]] = []
    stuck_threshold = _float(inputs.get("stuck_deal_threshold_days"), 30.0) or 30.0
    for deal in rows:
        stage = _normalize(deal.get("stage") or deal.get("pipeline_stage") or "unknown")
        days = deal.get("days_in_stage")
        if days is None:
            continue
        days_value = _float(days, None)
        if days_value is None:
            continue
        stage_totals[stage] = stage_totals.get(stage, 0.0) + days_value
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        if days_value >= stuck_threshold and stage not in {"closed_won", "closed_lost"}:
            stuck.append(
                {
                    "deal_id": str(deal.get("id") or deal.get("deal_id") or "deal"),
                    "stage": stage,
                    "days_in_stage": round(days_value, 2),
                    "amount": _float(deal.get("amount") or deal.get("value"), 0.0),
                }
            )

    avg_by_stage = {
        stage: round(stage_totals[stage] / stage_counts[stage], 2)
        for stage in stage_totals
        if stage_counts[stage] > 0
    }
    bottlenecks: list[dict[str, Any]] = []
    if avg_by_stage:
        values = sorted(avg_by_stage.values())
        mid = len(values) // 2
        median = (
            values[mid]
            if len(values) % 2
            else (values[mid - 1] + values[mid]) / 2
        )
        threshold = max(median * VELOCITY_BOTTLENECK_MULTIPLIER, median + 1)
        for stage, avg in avg_by_stage.items():
            if avg >= threshold:
                bottlenecks.append(
                    {
                        "stage": stage,
                        "avg_days_in_stage": avg,
                        "median_days_in_stage": round(median, 2),
                        "multiplier": round(avg / max(median, 0.01), 2),
                    }
                )

    recommended_actions: list[dict[str, Any]] = [
        {
            "action": "investigate_pipeline_bottleneck",
            "stage": bottleneck["stage"],
            "reason": (
                f"avg {bottleneck['avg_days_in_stage']}d >= "
                f"{VELOCITY_BOTTLENECK_MULTIPLIER}x median {bottleneck['median_days_in_stage']}d"
            ),
        }
        for bottleneck in bottlenecks
    ]
    if stuck:
        recommended_actions.append(
            {
                "action": "review_stuck_deals",
                "reason": f"{len(stuck)} deal(s) idle >= {stuck_threshold:.0f} days.",
            }
        )

    return {
        "deal_count": sum(stage_counts.values()),
        "deals": rows,
        "stage_counts": stage_counts,
        "avg_days_in_stage": avg_by_stage,
        "bottlenecks": bottlenecks,
        "stuck_deals": stuck,
        "recommended_actions": recommended_actions,
    }


def _funnel_conversion_analysis(inputs: dict[str, Any]) -> dict[str, Any]:
    raw = inputs.get("funnel_stage_counts") or inputs.get("stage_counts") or {}
    if not isinstance(raw, dict):
        raw = {}
    ordered: list[tuple[str, int]] = []
    for key, value in raw.items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            count = 0
        ordered.append((str(key), max(count, 0)))

    rates: dict[str, float | None] = {}
    for i in range(1, len(ordered)):
        prev_name, prev_count = ordered[i - 1]
        curr_name, curr_count = ordered[i]
        key = f"{prev_name}->{curr_name}"
        if prev_count <= 0:
            rates[key] = None
            continue
        rates[key] = round(curr_count / prev_count, 4)

    total = sum(count for _, count in ordered)
    sufficient_sample = total >= MIN_FUNNEL_SAMPLE
    recommended_actions: list[dict[str, Any]] = []
    if not sufficient_sample:
        recommended_actions.append(
            {
                "action": "increase_funnel_sample_size",
                "reason": (
                    f"Total funnel volume {total} < {MIN_FUNNEL_SAMPLE} minimum; "
                    "rates remain directional only."
                ),
            }
        )
    for key, rate in rates.items():
        if rate is not None and rate < 0.10:
            recommended_actions.append(
                {
                    "action": "investigate_low_funnel_conversion",
                    "transition": key,
                    "rate": rate,
                }
            )
    return {
        "stage_counts": dict(ordered),
        "conversion_rates": rates,
        "total_leads": total,
        "sufficient_sample": sufficient_sample,
        "min_required_sample": MIN_FUNNEL_SAMPLE,
        "recommended_actions": recommended_actions,
    }


# ---------------------------------------------------------------------------
# Lead scoring / SQL promotion / segments
# ---------------------------------------------------------------------------


def _lead_scoring_refresh(inputs: dict[str, Any]) -> dict[str, Any]:
    rows = _dict_list(inputs.get("contacts") or inputs.get("leads"))
    scored: list[dict[str, Any]] = []
    promotions: list[dict[str, Any]] = []
    for contact in rows:
        score, breakdown = _score_contact(contact)
        previous = _float(contact.get("lead_score"), 0.0) or 0.0
        delta = round(score - previous, 2)
        qualification = _qualification_from_score(score, contact)
        entry = {
            "contact_id": str(contact.get("id") or contact.get("contact_id") or "contact"),
            "score": round(score, 2),
            "previous_score": round(previous, 2),
            "delta": delta,
            "qualification": qualification,
            "breakdown": breakdown,
            "explanation": _scoring_explanation(breakdown, qualification, score),
        }
        scored.append(entry)
        if qualification in {"mql", "sql"} and previous < MQL_SCORE_FLOOR:
            promotions.append(
                {
                    "contact_id": entry["contact_id"],
                    "to": qualification,
                    "score": entry["score"],
                }
            )
    return {
        "contacts": scored,
        "promotions": promotions,
        "weights": dict(LEAD_SCORE_WEIGHTS),
        "thresholds": {"mql": MQL_SCORE_FLOOR, "sql": SQL_SCORE_FLOOR},
        "recommended_actions": [
            {
                "action": "promote_lifecycle_stage",
                "contact_id": p["contact_id"],
                "to": p["to"],
                "reason": f"score crossed {p['to'].upper()} floor",
            }
            for p in promotions
        ],
    }


def _score_contact(contact: dict[str, Any]) -> tuple[float, dict[str, float]]:
    breakdown: dict[str, float] = {}
    fit = max(0.0, min(_float(contact.get("firmographic_fit"), 0.0) or 0.0, 1.0))
    breakdown["firmographic_fit"] = round(fit * LEAD_SCORE_WEIGHTS["firmographic_fit"] * 100.0, 2)

    title = _normalize(contact.get("title_seniority"))
    title_factor = TITLE_SENIORITY_WEIGHTS.get(title, 0.4)
    breakdown["title_seniority"] = round(title_factor * LEAD_SCORE_WEIGHTS["title_seniority"] * 100.0, 2)

    days = _float(contact.get("days_since_last_activity"), 999.0) or 999.0
    if days <= 1:
        recency = 1.0
    elif days <= 7:
        recency = 0.85
    elif days <= 30:
        recency = 0.55
    elif days <= 90:
        recency = 0.25
    else:
        recency = 0.0
    breakdown["engagement_recency"] = round(recency * LEAD_SCORE_WEIGHTS["engagement_recency"] * 100.0, 2)

    depth = max(0.0, min(_float(contact.get("engagement_depth"), 0.0) or 0.0, 1.0))
    breakdown["engagement_depth"] = round(depth * LEAD_SCORE_WEIGHTS["engagement_depth"] * 100.0, 2)

    intent = max(0.0, min((_float(contact.get("intent_score"), 0.0) or 0.0) / 100.0, 1.0))
    breakdown["intent_signal"] = round(intent * LEAD_SCORE_WEIGHTS["intent_signal"] * 100.0, 2)

    breakdown["demo_request"] = (
        round(LEAD_SCORE_WEIGHTS["demo_request"] * 100.0, 2)
        if bool(contact.get("demo_request"))
        else 0.0
    )

    score = sum(breakdown.values())
    return score, breakdown


def _qualification_from_score(score: float, contact: dict[str, Any]) -> str:
    if score >= SQL_SCORE_FLOOR and bool(contact.get("demo_request")):
        return "sql"
    if score >= MQL_SCORE_FLOOR:
        return "mql"
    return "lead"


def _scoring_explanation(breakdown: dict[str, float], qualification: str, score: float) -> str:
    sorted_components = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)
    top_two = ", ".join(f"{name}={value}" for name, value in sorted_components[:2])
    return f"Composite score {round(score, 2)} -> {qualification.upper()} (top drivers: {top_two})."


def _sql_promotion_classification(
    lead_scoring: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    contacts = _dict_list(inputs.get("contacts") or inputs.get("leads"))
    promotable: list[dict[str, Any]] = []
    non_promotable: list[dict[str, Any]] = []
    scored_by_id = {row["contact_id"]: row for row in lead_scoring.get("contacts", [])}
    for contact in contacts:
        contact_id = str(contact.get("id") or contact.get("contact_id") or "contact")
        scored = scored_by_id.get(contact_id)
        if scored is not None:
            score = float(scored["score"])
        else:
            score, _ = _score_contact(contact)
        days_since = _float(contact.get("days_since_last_activity"), 999.0) or 999.0
        recent_engagement = bool(contact.get("recent_engagement")) or days_since <= 7
        demo_request = bool(contact.get("demo_request"))
        intent_score = _float(contact.get("intent_score"), 0.0) or 0.0
        strong_intent = intent_score >= 60
        reasons: list[str] = []
        if score < SQL_SCORE_FLOOR:
            reasons.append(f"score {score:.1f} below SQL floor {SQL_SCORE_FLOOR}")
        if not recent_engagement:
            reasons.append("no recent engagement in last 7 days")
        if not (demo_request or strong_intent):
            reasons.append("no demo request and intent < 60")
        entry = {
            "contact_id": contact_id,
            "score": round(score, 2),
            "demo_request": demo_request,
            "intent_score": intent_score,
            "reasons": reasons,
        }
        if not reasons:
            promotable.append(entry)
        else:
            non_promotable.append(entry)
    return {
        "promotable": promotable,
        "non_promotable": non_promotable,
        "criteria": {
            "min_score": SQL_SCORE_FLOOR,
            "max_days_since_activity": 7,
            "min_intent_score_or_demo_request": 60,
        },
        "recommended_actions": [
            {
                "action": "promote_to_sql",
                "contact_id": entry["contact_id"],
                "reason": "Meets SQL promotion criteria.",
            }
            for entry in promotable
        ],
    }


def _segment_recommendations(
    lead_scoring: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    contacts = _dict_list(inputs.get("contacts") or inputs.get("leads"))
    by_industry: dict[str, list[str]] = {}
    by_size: dict[str, list[str]] = {}
    by_behaviour: dict[str, list[str]] = {}
    for contact in contacts:
        contact_id = str(contact.get("id") or contact.get("contact_id") or "contact")
        industry = _normalize(contact.get("industry") or contact.get("vertical")) or "unknown"
        size = _normalize(contact.get("company_size") or contact.get("segment_size")) or "unknown"
        engagement_raw = contact.get("engagement_score")
        if engagement_raw is None:
            engagement = (_float(contact.get("engagement_depth"), 0.0) or 0.0) * 100.0
        else:
            engagement = _float(engagement_raw, 0.0) or 0.0
        if engagement >= 70:
            behaviour = "high_intent"
        elif engagement >= 40:
            behaviour = "warm"
        else:
            behaviour = "dormant"
        by_industry.setdefault(industry, []).append(contact_id)
        by_size.setdefault(size, []).append(contact_id)
        by_behaviour.setdefault(behaviour, []).append(contact_id)

    recommended_actions: list[dict[str, Any]] = []
    if by_behaviour.get("high_intent"):
        recommended_actions.append(
            {
                "action": "enroll_in_nurture",
                "segment": "high_intent",
                "contact_ids": by_behaviour["high_intent"],
            }
        )
    if by_behaviour.get("warm"):
        recommended_actions.append(
            {
                "action": "schedule_followup",
                "segment": "warm",
                "contact_ids": by_behaviour["warm"],
            }
        )
    if by_behaviour.get("dormant"):
        recommended_actions.append(
            {
                "action": "win_back_campaign",
                "segment": "dormant",
                "contact_ids": by_behaviour["dormant"],
            }
        )
    return {
        "by_industry": by_industry,
        "by_size": by_size,
        "by_behaviour": by_behaviour,
        "recommended_actions": recommended_actions,
    }


# ---------------------------------------------------------------------------
# Churn risk / account health
# ---------------------------------------------------------------------------


def _churn_risk_signals(inputs: dict[str, Any]) -> dict[str, Any]:
    rows = _dict_list(inputs.get("accounts") or inputs.get("customer_accounts"))
    scored: list[dict[str, Any]] = []
    at_risk: list[dict[str, Any]] = []
    signal_summary: dict[str, int] = {}
    for account in rows:
        score, factors = _churn_score(account)
        for factor in factors:
            signal_summary[factor] = signal_summary.get(factor, 0) + 1
        entry = {
            "account_id": str(account.get("id") or account.get("account_id") or "account"),
            "score": round(score, 3),
            "factors": factors,
        }
        scored.append(entry)
        if score >= CHURN_RISK_FLOOR:
            at_risk.append(entry)
    return {
        "scored_accounts": scored,
        "at_risk_accounts": at_risk,
        "risk_floor": CHURN_RISK_FLOOR,
        "signal_summary": signal_summary,
        "recommended_actions": [
            {
                "action": "open_retention_play",
                "account_id": entry["account_id"],
                "reasons": entry["factors"],
            }
            for entry in at_risk
        ],
    }


def _churn_score(account: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    factors: list[str] = []
    days_inactive = _float(account.get("days_since_last_login"), 0.0) or 0.0
    if days_inactive >= 30:
        score += 0.3
        factors.append(f"login_inactive_{int(days_inactive)}d")
    elif days_inactive >= 14:
        score += 0.15
        factors.append(f"login_inactive_{int(days_inactive)}d")

    support = int(_float(account.get("open_support_tickets"), 0.0) or 0.0)
    if support >= 5:
        score += 0.25
        factors.append(f"support_tickets_{support}")

    nps = _float(account.get("nps"), 0.0) or 0.0
    if nps <= -50:
        score += 0.2
        factors.append(f"nps_detractor_{nps:.0f}")
    elif nps <= 0:
        score += 0.1
        factors.append(f"nps_passive_{nps:.0f}")

    payment_health = _normalize(account.get("payment_health") or "ok")
    if payment_health != "ok":
        score += 0.25
        factors.append(f"payment_{payment_health}")

    usage_trend = _normalize(account.get("usage_trend") or "stable")
    if usage_trend in {"declining", "dropping"}:
        score += 0.2
        factors.append("usage_declining")
    return min(score, 1.0), factors


def _account_deal_health_summary(inputs: dict[str, Any]) -> dict[str, Any]:
    rows = _dict_list(inputs.get("accounts") or inputs.get("customer_accounts"))
    summary: list[dict[str, Any]] = []
    unhealthy: list[dict[str, Any]] = []
    for account in rows:
        mrr = _float(account.get("mrr"), 0.0) or 0.0
        engagement = max(
            0.0,
            min((_float(account.get("engagement_score"), 0.0) or 0.0) / 100.0, 1.0),
        )
        support = int(_float(account.get("open_support_tickets"), 0.0) or 0.0)
        nps = _float(account.get("nps"), 0.0) or 0.0
        payment_health = _normalize(account.get("payment_health") or "ok")
        support_component = 1.0 - min(support / 10.0, 1.0)
        nps_component = max(0.0, min((nps + 100.0) / 200.0, 1.0))
        payment_component = 1.0 if payment_health == "ok" else 0.4
        health = round(
            0.35 * engagement
            + 0.25 * support_component
            + 0.20 * nps_component
            + 0.20 * payment_component,
            3,
        )
        entry = {
            "account_id": str(account.get("id") or account.get("account_id") or "account"),
            "health_score": health,
            "mrr": round(mrr, 2),
            "components": {
                "engagement": round(engagement, 3),
                "support": round(support_component, 3),
                "nps": round(nps_component, 3),
                "payment": round(payment_component, 3),
            },
        }
        summary.append(entry)
        if health < 0.5:
            unhealthy.append(entry)
    return {
        "accounts": summary,
        "unhealthy_accounts": unhealthy,
        "recommended_actions": [
            {"action": "csm_intervention", "account_id": entry["account_id"]}
            for entry in unhealthy
        ],
    }


# ---------------------------------------------------------------------------
# Readiness, confidence, source refs
# ---------------------------------------------------------------------------


def _read_degraded_reasons(inputs: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if inputs.get("connector_read_ready") is False or inputs.get("connector_degraded") is True:
        reasons.append("CRM connector is degraded or unavailable.")
    freshness = _normalize(inputs.get("data_freshness_status") or inputs.get("freshness_status"))
    if freshness in {"stale", "partial", "degraded"}:
        reasons.append(f"CRM source data is {freshness}.")
    if inputs.get("stale_data") is True:
        reasons.append("CRM source data is stale.")
    if inputs.get("partial_data") is True:
        reasons.append("CRM source data is partial.")
    mapping_status = _normalize(inputs.get("mapping_status") or inputs.get("field_mapping_status"))
    if mapping_status in {"invalid", "missing", "unmapped", "partial", "stale"}:
        reasons.append(f"CRM field mapping is {mapping_status}.")
    if inputs.get("missing_mapping") is True:
        reasons.append("Required CRM field mapping is missing.")
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        relevant = [c for c in contracts if _is_crm_read_contract(c)]
        if relevant and not any(bool(c.get("read_ready", c.get("configured"))) for c in relevant):
            reasons.append("CRM source connector is not read-ready.")
        if any(
            c.get("degraded") or c.get("health_status") in {"stale", "degraded"}
            for c in relevant
        ):
            reasons.append("One or more CRM source connectors are degraded or stale.")
        if any(c.get("mock_or_test_double") or c.get("stub_only") for c in relevant):
            reasons.append("Mock, test-double, or stub CRM connector proof is not production lineage.")
    return _dedupe(reasons)


def _analysis_confidence(
    pipeline_velocity: dict[str, Any],
    funnel_conversion: dict[str, Any],
    lead_scoring: dict[str, Any],
    churn_signals: dict[str, Any],
    account_health: dict[str, Any],
    degraded: list[str],
) -> float:
    signal_count = (
        pipeline_velocity.get("deal_count", 0)
        + funnel_conversion.get("total_leads", 0)
        + len(lead_scoring.get("contacts", []))
        + len(churn_signals.get("scored_accounts", []))
        + len(account_health.get("accounts", []))
    )
    if signal_count == 0:
        base = 0.52
    elif signal_count < 6:
        base = 0.74
    else:
        base = 0.88
    if not funnel_conversion.get("sufficient_sample", True):
        base = min(base, 0.78)
    if degraded:
        base -= 0.22
    return round(max(0.0, min(base, 0.94)), 3)


def _default_recommendations(degraded: list[str]) -> list[dict[str, Any]]:
    if degraded:
        return [{"action": "restore_crm_data_readiness", "reason": "; ".join(degraded)}]
    return [{"action": "continue_crm_monitoring", "reason": "No material CRM blocker was detected."}]


def _source_refs(
    inputs: dict[str, Any],
    lead_scoring: dict[str, Any],
    accounts: list[dict[str, Any]],
    deals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _dict_list(inputs.get("source_refs"))
    for entry in lead_scoring.get("contacts", [])[:10]:
        refs.append({"type": "crm_contact", "ref_id": entry["contact_id"]})
    for entry in accounts[:10]:
        refs.append({"type": "crm_account", "ref_id": entry["account_id"]})
    for deal in deals[:10]:
        refs.append(
            {
                "type": "crm_deal",
                "ref_id": str(deal.get("id") or deal.get("deal_id") or "deal"),
            }
        )
    return _dedupe_refs(refs)


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _policy_context_for_write(action: str, inputs: dict[str, Any], mode: str) -> dict[str, Any]:
    context: dict[str, Any] = {
        "workflow_id": "crm_pipeline_intelligence",
        "workflow_mode": mode,
        "action": action,
        "external_write_required": True,
        "customer_facing": True,
        "channel": _normalize(inputs.get("channel") or inputs.get("connector_key") or "crm"),
    }
    if action == "change_target_accounts":
        context.update(
            {
                "target_account_change": True,
                "target_account_delta": _int(
                    inputs.get("target_account_delta")
                    or inputs.get("target_account_change_count")
                    or inputs.get("affected_accounts"),
                    0,
                ),
            }
        )
    if action in {"update_lifecycle_stage", "promote_lifecycle_stage", "update_lead_scores"}:
        context["audience_size"] = _int(
            inputs.get("affected_contacts") or inputs.get("audience_size"), 0
        )
    if action in {"bulk_crm_update", "update_crm"}:
        context["audience_size"] = _int(
            inputs.get("affected_contacts") or inputs.get("audience_size"), 0
        )
    return context


def _escalation_for_write(
    task,
    action: str,
    inputs: dict[str, Any],
    policy_result: dict[str, Any],
) -> dict[str, Any]:
    return evaluate_marketing_escalation(
        {
            "workflow_id": "crm_pipeline_intelligence",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "event_type": "approval_timeout",
            "target_account_change": action == "change_target_accounts"
            or bool(inputs.get("target_account_change")),
            "severity": str(inputs.get("severity") or "medium"),
            "reason": "CRM Intelligence external write requires owner routing.",
            "marketing_policy_decision": policy_result,
        }
    )


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "CRM connector is not write-safe."
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        for contract in contracts:
            if not _is_crm_write_contract(contract):
                continue
            if contract.get("mock_or_test_double"):
                return False, "Test-double connector proof cannot satisfy active CRM writes."
            if bool(contract.get("write_safe", contract.get("write_ready"))):
                return True, ""
            status = str(contract.get("write_status") or contract.get("contract_state") or "unknown")
            return False, f"CRM connector contract is not write-safe ({status})."
        return False, "No CRM connector contract is write-safe for CRM Intelligence action."
    return False, "CRM connector write-readiness evidence is missing."


def _is_crm_read_contract(contract: dict[str, Any]) -> bool:
    category = _normalize(contract.get("category"))
    key = _normalize(contract.get("connector_key") or contract.get("key"))
    return category in {"crm", "abm", "intent"} or key in {
        "hubspot",
        "salesforce",
        "bombora",
        "g2",
        "trustradius",
    }


def _is_crm_write_contract(contract: dict[str, Any]) -> bool:
    category = _normalize(contract.get("category"))
    key = _normalize(contract.get("connector_key") or contract.get("key"))
    return category in {"crm", "abm"} or key in {"hubspot", "salesforce"}


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
    explicit = _normalize(inputs.get("connector_key") or inputs.get("connector"))
    if action == "update_lead_scores":
        return explicit or "hubspot", "update_lead_scores"
    if action in {"update_lifecycle_stage", "promote_lifecycle_stage"}:
        return explicit or "hubspot", "update_lifecycle_stage"
    if action in {"change_segment_membership", "update_segment_membership"}:
        return explicit or "hubspot", "update_segment_membership"
    if action == "change_target_accounts":
        return explicit or "salesforce", "update_target_accounts"
    if action == "bulk_crm_update":
        return explicit or "hubspot", "bulk_crm_update"
    return explicit or "hubspot", "apply_crm_change"


def _crm_write_payload(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "contact_ids": _string_list(inputs.get("contact_ids") or inputs.get("contacts")),
        "account_ids": _string_list(inputs.get("account_ids") or inputs.get("accounts")),
        "lifecycle_stage": inputs.get("lifecycle_stage") or inputs.get("to_stage"),
        "lead_score_updates": inputs.get("lead_score_updates"),
        "segment_id": inputs.get("segment_id"),
        "target_account_delta": inputs.get("target_account_delta")
        or inputs.get("target_account_change_count"),
        "affected_contacts": inputs.get("affected_contacts"),
        "affected_accounts": inputs.get("affected_accounts"),
        "source_refs": _dict_list(inputs.get("source_refs")),
        "rollback_plan": inputs.get("rollback_plan")
        or "revert CRM lifecycle / score / segment / target-account change",
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
        reasons = [str(output["policy_result"].get("reason") or "CRM Intelligence action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "revops_lead"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "CRM Intelligence review required.",
        trigger_type="crm_intelligence_review",
        decision_required=DecisionRequired(
            question="Review CRM Intelligence recommendation or CRM-write safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "CRM Intelligence review",
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


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("id") or item.get("contact_id") or "").strip()
            else:
                text = str(item).strip()
            if text:
                result.append(text)
        return result
    return [str(value).strip()] if str(value).strip() else []


def _float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
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
