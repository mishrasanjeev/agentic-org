"""Competitive Intel agent implementation."""

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

COMPETITIVE_INTEL_AGENT_MATURITY = "beta"
COMPETITIVE_INTEL_CONFIDENCE_FLOOR = 0.82

READ_ONLY_ACTIONS = {
    "change_confidence_score",
    "competitor_profile_normalization",
    "duplicate_change_suppression",
    "evaluate_alert_thresholds",
    "extract_win_loss_signals",
    "feature_capability_diffing",
    "normalize_competitor_profiles",
    "positioning_recommendation",
    "pricing_change_detection",
    "recommend_positioning",
    "suppress_duplicate_changes",
    "weekly_benchmark",
    "weekly_competitor_snapshot",
    "weekly_market_snapshot",
    "win_loss_signal_extraction",
}
WRITE_ACTIONS = {
    "comparative_claim",
    "launch_competitive_campaign",
    "pricing_claim",
    "public_response",
    "publish_competitive_response",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

DEFAULT_ALERT_THRESHOLDS = {"critical": 0.85, "high": 0.7, "medium": 0.5}
MAJOR_LAUNCH_TERMS = ("launch", "announced", "released", "rolled out", "major")
CRISIS_TERMS = ("lawsuit", "breach", "outage", "regulator", "security incident", "crisis")
PRICING_TERMS = ("price", "pricing", "discount", "free", "plan", "package")
WIN_TERMS = ("won", "selected", "replaced", "chose us", "closed won")
LOSS_TERMS = ("lost", "churned", "selected competitor", "closed lost", "switched to")


@AgentRegistry.register
class CompetitiveIntelAgent(BaseAgent):
    agent_type = "competitive_intel"
    domain = "marketing"
    confidence_floor = COMPETITIVE_INTEL_CONFIDENCE_FLOOR
    prompt_file = "competitive_intel.prompt.txt"

    async def execute(self, task):
        """Run deterministic competitive analysis or guarded external actions."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"Competitive Intel action={action}, mode={mode}")

            if action in READ_ONLY_ACTIONS:
                output = self._read_only_action(task, action, inputs, mode, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_competitive_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported Competitive Intel action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_competitive_intel_action",
                        "reason": "The Competitive Intel agent only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported Competitive Intel action '{action}'."],
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
                error={"code": "COMPETITIVE_INTEL_ACTION_UNSUPPORTED", "message": output["blocked_reasons"][0]},
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("competitive_intel_agent_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "COMPETITIVE_INTEL_AGENT_ERR", "message": str(exc)},
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
        profiles = _normalize_profiles(_profiles(inputs))
        previous_profiles = _profile_index(_normalize_profiles(_previous_profiles(inputs)))
        current_profiles = _profile_index(profiles)
        pricing_changes = _pricing_changes(inputs, previous_profiles, current_profiles)
        feature_diffs = _feature_diffs(inputs, previous_profiles, current_profiles)
        win_loss_signals = _win_loss_signals(inputs)
        explicit_changes = _explicit_changes(inputs)
        changes = _dedupe_changes([*explicit_changes, *pricing_changes, *feature_diffs, *win_loss_signals])
        alerts = _alerts(changes, inputs)
        recommendations = _positioning_recommendations(profiles, changes, alerts, inputs)
        degraded = _read_degraded_reasons(inputs)
        confidence = _snapshot_confidence(profiles, changes, degraded)
        major_or_crisis = _has_major_or_crisis_signal(changes, alerts)
        escalation = (
            _escalation_for_competitive_event(task, action, inputs, major_or_crisis=major_or_crisis)
            if major_or_crisis
            else {}
        )
        trace.append(
            "Competitive snapshot profiles="
            f"{len(profiles)}, changes={len(changes)}, alerts={len(alerts)}, degraded={len(degraded)}"
        )

        if action in {"competitor_profile_normalization", "normalize_competitor_profiles"}:
            extra = {"normalized_profiles": profiles}
            status = "profiles_normalized"
            rationale = "Normalized competitor profiles into stable names, domains, segments, pricing, and features."
        elif action == "pricing_change_detection":
            extra = {"pricing_changes": pricing_changes, "change_summary": pricing_changes}
            status = "pricing_changes_detected"
            rationale = "Detected pricing deltas from current and prior competitor snapshots."
        elif action == "feature_capability_diffing":
            extra = {"feature_diffs": feature_diffs, "change_summary": feature_diffs}
            status = "feature_diffs_ready"
            rationale = "Compared current and prior feature sets for each competitor."
        elif action == "win_loss_signal_extraction":
            extra = {"win_loss_signals": win_loss_signals}
            status = "win_loss_signals_extracted"
            rationale = "Extracted deterministic win/loss signals from structured notes."
        elif action == "change_confidence_score":
            extra = {"change_confidence": _overall_change_confidence(changes), "change_summary": changes}
            status = "change_confidence_scored"
            rationale = "Calculated confidence scores from source count, severity, and explicit evidence."
        elif action == "duplicate_change_suppression":
            raw_for_suppression = explicit_changes or [*pricing_changes, *feature_diffs]
            extra = {
                "suppressed_duplicate_count": max(
                    0,
                    len(raw_for_suppression) - len(_dedupe_changes(raw_for_suppression)),
                )
            }
            status = "duplicates_suppressed"
            rationale = "Suppressed duplicate competitive changes by competitor, type, summary, and observed date."
        elif action == "evaluate_alert_thresholds":
            extra = {"alerts": alerts, "alert_thresholds": _thresholds(inputs)}
            status = "alert_thresholds_evaluated"
            rationale = "Applied severity thresholds to competitor changes and market signals."
        elif action == "positioning_recommendation":
            extra = {"positioning_recommendations": recommendations}
            status = "positioning_recommended"
            rationale = "Generated read-only competitive positioning recommendations."
        else:
            extra = {
                "competitor_snapshot": {
                    "profiles": profiles,
                    "pricing_changes": pricing_changes,
                    "feature_diffs": feature_diffs,
                    "win_loss_signals": win_loss_signals,
                    "alerts": alerts,
                    "positioning_recommendations": recommendations,
                },
                "change_summary": changes,
                "change_confidence": _overall_change_confidence(changes),
            }
            status = "snapshot_degraded" if degraded else "snapshot_ready"
            rationale = "Built a deterministic weekly competitive intelligence snapshot."

        return self._base_output(
            task,
            action=action,
            status=status,
            confidence=confidence,
            rationale=rationale,
            recommended_actions=recommendations or _default_recommendations(changes, alerts),
            approval_required=major_or_crisis,
            hitl_required=major_or_crisis,
            policy_context={"workflow_mode": mode, "workflow_id": "competitive_intel_monitoring"},
            escalation=escalation,
            source_refs=_source_refs(inputs, profiles, changes),
            degraded_reasons=degraded,
            extra=extra,
        )

    async def _guarded_competitive_write(
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
            blocked_reasons.append("Shadow/draft/internal Competitive Intel workflows are read-only.")
            trace.append("Competitive Intel write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.76,
                rationale=(
                    "Prepared competitive recommendation without publishing, launching, "
                    "or changing external systems."
                ),
                recommended_actions=[
                    {
                        "action": "review_competitive_recommendation",
                        "reason": "Review the competitive response before any active external write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                escalation=escalation,
                blocked_reasons=blocked_reasons,
                external_write_status="shadow_only",
                external_write_required=True,
                extra={"write_payload": _competitive_write_payload(action, inputs)},
            )

        write_safe, write_reason = _connector_write_safe(inputs)
        if not write_safe:
            blocked_reasons.append(write_reason)
        if not _has_approval(inputs):
            hitl_reasons.append(str(policy.get("reason") or "Competitive external action requires approval."))
        if policy.get("decision") in {"blocked", "missing_policy"}:
            blocked_reasons.append(str(policy.get("reason") or "Marketing policy blocks this competitive action."))
        escalation_needs_review = escalation.get("decision") not in {None, "", "no_escalation", "notify_owner"}
        if escalation_needs_review and not _has_escalation_approval(inputs):
            hitl_reasons.append("Competitive Intel action requires escalation review.")

        if blocked_reasons or hitl_reasons:
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale="Competitive Intel external action failed closed before vendor mutation.",
                recommended_actions=[
                    {
                        "action": "resolve_competitive_write_prerequisites",
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
                extra={"write_payload": _competitive_write_payload(action, inputs)},
            )

        result = _dict_or_none(inputs.get("external_write_result"))
        connector, tool = _connector_tool(action, inputs)
        idempotency_key = str(
            inputs.get("idempotency_key")
            or inputs.get("request_fingerprint")
            or f"competitive_intel:{task.correlation_id}:{task.step_id}:{action}"
        )
        if result is None:
            result = await self._safe_tool_call(
                connector,
                tool,
                _competitive_write_payload(action, inputs),
                trace,
                tool_calls,
                idempotency_key=idempotency_key,
            )

        write_status = _external_write_confirmation_status(result)
        write_confirmed = _is_confirmed_external_write(result)
        external_ref = _external_write_ref(result)
        blocked_reasons = (
            []
            if write_confirmed
            else ["Competitive Intel external write is unconfirmed and cannot be marked complete."]
        )
        trace.append(f"Competitive Intel write status={write_status}, confirmed={write_confirmed}")
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "write_unconfirmed",
            confidence=0.9 if write_confirmed else 0.0,
            rationale=(
                "Competitive Intel external write was confirmed by the connector."
                if write_confirmed
                else "Competitive Intel external write did not return confirmed vendor evidence."
            ),
            recommended_actions=[
                {
                    "action": (
                        "monitor_competitive_response"
                        if write_confirmed
                        else "verify_competitive_write_before_retry"
                    ),
                    "reason": (
                        "Track response, campaign, or battlecard impact."
                        if write_confirmed
                        else "Check vendor state using the idempotency key before retrying."
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
            extra={"write_payload": _competitive_write_payload(action, inputs)},
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
                "workflow_id": "competitive_intel_monitoring",
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
            "audit_ref": f"competitive_intel:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": COMPETITIVE_INTEL_AGENT_MATURITY,
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


def _task_inputs(task) -> dict[str, Any]:
    payload = task.task.inputs if hasattr(task.task, "inputs") else task.task.get("inputs", {})
    return payload if isinstance(payload, dict) else {}


def _task_action(task) -> str:
    return str(task.task.action if hasattr(task.task, "action") else task.task.get("action", "weekly_market_snapshot"))


def _normalize_action(action: str) -> str:
    normalized = _normalize(action)
    aliases = {
        "build_weekly_snapshot": "weekly_market_snapshot",
        "change_confidence": "change_confidence_score",
        "competitor_snapshot": "weekly_market_snapshot",
        "diff_capabilities": "feature_capability_diffing",
        "feature_diffing": "feature_capability_diffing",
        "next_best_action": "positioning_recommendation",
        "pricing_detection": "pricing_change_detection",
        "weekly_competitor_snapshot": "weekly_market_snapshot",
    }
    return aliases.get(normalized, normalized)


def _read_degraded_reasons(inputs: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if inputs.get("connector_read_ready") is False or inputs.get("connector_degraded") is True:
        reasons.append("Competitive intelligence connector is degraded or unavailable.")
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        relevant = [contract for contract in contracts if _is_competitive_contract(contract)]
        if relevant and not any(bool(contract.get("read_ready", contract.get("configured"))) for contract in relevant):
            reasons.append("Competitive intelligence source connector is not read-ready.")
        if any(contract.get("degraded") or contract.get("health_status") == "degraded" for contract in contracts):
            reasons.append("One or more competitive intelligence sources are degraded.")
    return _dedupe(reasons)


def _profiles(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_list(inputs.get("competitors") or inputs.get("profiles") or inputs.get("current_profiles"))


def _previous_profiles(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_list(inputs.get("previous_competitors") or inputs.get("previous_profiles"))


def _normalize_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or row.get("competitor") or row.get("company_name") or "").strip()
        domain = _domain(row)
        key = (domain or name).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        feature_values = _string_list(row.get("features") or row.get("capabilities"))
        features = sorted({_clean_feature(item) for item in feature_values})
        normalized.append(
            {
                "name": name or domain,
                "domain": domain,
                "segment": str(row.get("segment") or row.get("market_segment") or "unknown").strip().lower(),
                "pricing": _pricing(row),
                "features": [feature for feature in features if feature],
                "source": str(row.get("source") or row.get("connector_key") or "provided_input"),
                "source_url": row.get("source_url") or row.get("url"),
                "observed_at": str(row.get("observed_at") or row.get("updated_at") or ""),
            }
        )
    return normalized


def _profile_index(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_profile_key(row): row for row in profiles if _profile_key(row)}


def _pricing_changes(
    inputs: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, current_row in current.items():
        previous_row = previous.get(key)
        if not previous_row:
            continue
        old_price = _float((previous_row.get("pricing") or {}).get("amount"), 0.0)
        new_price = _float((current_row.get("pricing") or {}).get("amount"), 0.0)
        if old_price <= 0 or new_price <= 0 or old_price == new_price:
            continue
        delta = new_price - old_price
        percent = round((delta / old_price) * 100, 2)
        severity = "high" if abs(percent) >= _float(inputs.get("pricing_high_delta_pct"), 15.0) else "medium"
        changes.append(
            _change(
                competitor=str(current_row.get("name")),
                change_type="pricing",
                summary=f"Pricing changed from {old_price:g} to {new_price:g} ({percent:+.2f}%).",
                severity=severity,
                confidence=_change_confidence(severity=severity, source_count=2, explicit_confidence=None),
                source_refs=[_source_ref_from_profile(current_row), _source_ref_from_profile(previous_row)],
                observed_at=str(current_row.get("observed_at") or ""),
                value_delta={"old": old_price, "new": new_price, "delta": delta, "delta_pct": percent},
            )
        )
    for row in _explicit_changes(inputs):
        text = f"{row.get('change_type', '')} {row.get('summary', '')}".lower()
        if any(term in text for term in PRICING_TERMS):
            row["change_type"] = "pricing"
            changes.append(row)
    return _dedupe_changes(changes)


def _feature_diffs(
    _inputs: dict[str, Any],
    previous: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, current_row in current.items():
        previous_row = previous.get(key)
        if not previous_row:
            continue
        old_features = set(_string_list(previous_row.get("features")))
        new_features = set(_string_list(current_row.get("features")))
        added = sorted(new_features - old_features)
        removed = sorted(old_features - new_features)
        if not added and not removed:
            continue
        severity = "high" if len(added) + len(removed) >= 3 else "medium"
        changes.append(
            _change(
                competitor=str(current_row.get("name")),
                change_type="feature",
                summary=f"Feature changes: added {added or []}; removed {removed or []}.",
                severity=severity,
                confidence=_change_confidence(severity=severity, source_count=2, explicit_confidence=None),
                source_refs=[_source_ref_from_profile(current_row), _source_ref_from_profile(previous_row)],
                observed_at=str(current_row.get("observed_at") or ""),
                value_delta={"added": added, "removed": removed},
            )
        )
    return changes


def _win_loss_signals(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for note in _dict_list(inputs.get("win_loss_notes") or inputs.get("deal_notes")):
        text = str(note.get("text") or note.get("summary") or note.get("reason") or "").strip()
        normalized = text.lower()
        outcome = str(note.get("outcome") or "").lower()
        if not outcome:
            if any(term in normalized for term in WIN_TERMS):
                outcome = "win"
            elif any(term in normalized for term in LOSS_TERMS):
                outcome = "loss"
            else:
                outcome = "unknown"
        reason = _win_loss_reason(normalized)
        severity = "high" if outcome == "loss" else "medium"
        signals.append(
            _change(
                competitor=str(note.get("competitor") or note.get("competitor_name") or "unknown"),
                change_type=f"win_loss_{outcome}",
                summary=text or f"{outcome} signal from CRM notes.",
                severity=severity,
                confidence=_change_confidence(
                    severity=severity,
                    source_count=1,
                    explicit_confidence=note.get("confidence"),
                ),
                source_refs=[_source_ref("crm", note.get("source_id") or note.get("deal_id"), note.get("source_url"))],
                observed_at=str(note.get("observed_at") or note.get("created_at") or ""),
                value_delta={"outcome": outcome, "reason": reason},
            )
        )
    return signals


def _explicit_changes(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for row in _dict_list(inputs.get("changes") or inputs.get("events") or inputs.get("signals")):
        severity = _severity(row.get("severity"), default="medium")
        confidence = _change_confidence(
            severity=severity,
            source_count=max(1, len(_dict_list(row.get("source_refs")))),
            explicit_confidence=row.get("confidence"),
        )
        changes.append(
            _change(
                competitor=str(row.get("competitor") or row.get("name") or "unknown"),
                change_type=str(row.get("change_type") or row.get("type") or "market_signal"),
                summary=str(row.get("summary") or row.get("description") or row.get("title") or "Competitive signal."),
                severity=severity,
                confidence=confidence,
                source_refs=_dict_list(row.get("source_refs"))
                or [_source_ref(row.get("source"), row.get("source_id"), row.get("source_url"))],
                observed_at=str(row.get("observed_at") or row.get("created_at") or ""),
                value_delta=_dict_or_none(row.get("value_delta")) or {},
            )
        )
    return changes


def _alerts(changes: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    thresholds = _thresholds(inputs)
    alerts: list[dict[str, Any]] = []
    for change in changes:
        severity = _severity(change.get("severity"), default="medium")
        threshold = thresholds.get(severity, thresholds["medium"])
        confidence = _float(change.get("confidence"), 0.0)
        if confidence >= threshold:
            alerts.append(
                {
                    "competitor": change.get("competitor"),
                    "severity": severity,
                    "change_type": change.get("change_type"),
                    "confidence": confidence,
                    "threshold": threshold,
                    "summary": change.get("summary"),
                    "requires_escalation": severity == "critical" or _is_major_or_crisis(change),
                }
            )
    return alerts


def _positioning_recommendations(
    profiles: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    inputs: dict[str, Any],
) -> list[dict[str, Any]]:
    if alerts:
        top = alerts[0]
        return [
            {
                "action": (
                    "escalate_competitive_response"
                    if top.get("requires_escalation")
                    else "prepare_battlecard_update"
                ),
                "competitor": top.get("competitor"),
                "reason": str(top.get("summary") or "Competitive alert crossed threshold."),
                "mode": "read_only_recommendation",
            }
        ]
    if changes:
        change = changes[0]
        return [
            {
                "action": "prepare_positioning_update",
                "competitor": change.get("competitor"),
                "reason": str(change.get("summary") or "Competitive change detected."),
                "mode": "read_only_recommendation",
            }
        ]
    if profiles:
        return [
            {
                "action": "maintain_weekly_monitoring",
                "reason": "No material competitor change crossed alert thresholds.",
                "mode": "read_only_recommendation",
            }
        ]
    return [
        {
            "action": "connect_competitive_sources",
            "reason": str(inputs.get("missing_source_reason") or "No competitor profiles or signals were supplied."),
            "mode": "setup",
        }
    ]


def _default_recommendations(changes: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if alerts:
        return [{"action": "review_competitive_alerts", "reason": "One or more alerts crossed severity thresholds."}]
    if changes:
        return [
            {
                "action": "review_competitive_changes",
                "reason": "Competitive changes were detected below alert threshold.",
            }
        ]
    return [{"action": "continue_monitoring", "reason": "No material competitive change was detected."}]


def _snapshot_confidence(profiles: list[dict[str, Any]], changes: list[dict[str, Any]], degraded: list[str]) -> float:
    if not profiles and not changes:
        return 0.35
    base = 0.78
    if profiles:
        base += 0.08
    if changes:
        base += min(0.08, len(changes) * 0.02)
    if degraded:
        base -= 0.2
    return round(max(0.0, min(base, 0.94)), 3)


def _overall_change_confidence(changes: list[dict[str, Any]]) -> float:
    if not changes:
        return 0.0
    return round(sum(_float(change.get("confidence"), 0.0) for change in changes) / len(changes), 3)


def _change_confidence(*, severity: str, source_count: int, explicit_confidence: Any) -> float:
    explicit = _float(explicit_confidence, -1.0)
    if explicit >= 0:
        return round(max(0.0, min(explicit, 1.0)), 3)
    base = {"critical": 0.82, "high": 0.74, "medium": 0.62, "low": 0.48}.get(severity, 0.55)
    return round(min(0.96, base + max(0, source_count - 1) * 0.06), 3)


def _has_major_or_crisis_signal(changes: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> bool:
    return any(_is_major_or_crisis(change) for change in changes) or any(
        alert.get("requires_escalation") for alert in alerts
    )


def _is_major_or_crisis(change: dict[str, Any]) -> bool:
    text = f"{change.get('change_type', '')} {change.get('summary', '')}".lower()
    return _severity(change.get("severity"), default="medium") == "critical" or any(
        term in text for term in (*MAJOR_LAUNCH_TERMS, *CRISIS_TERMS)
    )


def _change(
    *,
    competitor: str,
    change_type: str,
    summary: str,
    severity: str,
    confidence: float,
    source_refs: list[dict[str, Any]],
    observed_at: str,
    value_delta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "competitor": competitor,
        "change_type": _normalize(change_type),
        "summary": summary,
        "severity": _severity(severity, default="medium"),
        "confidence": round(max(0.0, min(confidence, 1.0)), 3),
        "source_refs": [ref for ref in source_refs if ref],
        "observed_at": observed_at,
        "value_delta": value_delta,
    }


def _dedupe_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for change in changes:
        key = "|".join(
            [
                str(change.get("competitor") or "").lower(),
                str(change.get("change_type") or "").lower(),
                str(change.get("summary") or "").lower(),
                str(change.get("observed_at") or "")[:10],
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(change)
    return deduped


def _thresholds(inputs: dict[str, Any]) -> dict[str, float]:
    configured = _dict_or_none(inputs.get("alert_thresholds")) or {}
    thresholds = dict(DEFAULT_ALERT_THRESHOLDS)
    for key in ("critical", "high", "medium"):
        if key in configured:
            thresholds[key] = max(0.0, min(_float(configured[key], thresholds[key]), 1.0))
    return thresholds


def _source_refs(
    inputs: dict[str, Any],
    profiles: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _dict_list(inputs.get("source_refs"))
    for profile in profiles:
        refs.append(_source_ref_from_profile(profile))
    for change in changes:
        refs.extend(_dict_list(change.get("source_refs")))
    compact: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        ref_id = str(ref.get("ref_id") or ref.get("source_url") or ref.get("url") or ref.get("type") or "")
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        compact.append(ref)
    return compact[:20]


def _policy_context_for_write(action: str, inputs: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "workflow_id": "competitive_intel_monitoring",
        "workflow_mode": mode,
        "action": action,
        "external_write_required": True,
        "public_response": action in {"public_response", "publish_competitive_response"},
        "comparative_claim": action in {"comparative_claim", "publish_competitive_response"},
        "pricing_claim": action in {"pricing_claim"},
        "competitor_mention": True,
        "crisis_response": bool(inputs.get("crisis_response") or inputs.get("major_competitor_launch")),
        "budget_amount": _float(inputs.get("budget_amount") or inputs.get("budget"), 0.0),
    }


def _escalation_for_competitive_event(
    task,
    action: str,
    inputs: dict[str, Any],
    *,
    major_or_crisis: bool,
) -> dict[str, Any]:
    return evaluate_marketing_escalation(
        {
            "workflow_id": "competitive_intel_monitoring",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "event_type": "crisis_public_response" if major_or_crisis else "high_risk_copy",
            "crisis_response": major_or_crisis,
            "competitor_mention": True,
            "severity": "critical" if major_or_crisis else "medium",
            "reason": str(inputs.get("escalation_reason") or "Competitive signal requires owner routing."),
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
            "workflow_id": "competitive_intel_monitoring",
            "workflow_run_id": getattr(task, "workflow_run_id", None),
            "step_id": getattr(task, "step_id", None),
            "action": action,
            "public_response": action in {"public_response", "publish_competitive_response"},
            "comparative_claim": action in {"comparative_claim", "publish_competitive_response"},
            "pricing_claim": action == "pricing_claim",
            "crisis_response": bool(inputs.get("crisis_response") or inputs.get("major_competitor_launch")),
            "competitor_mention": True,
            "budget_threshold_exceeded": action == "launch_competitive_campaign",
            "severity": "high",
            "reason": "Competitive Intel external action requires owner routing.",
            "marketing_policy_decision": policy_result,
        }
    )


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "Competitive Intel connector is not write-safe."
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        for contract in contracts:
            if not _is_competitive_write_contract(contract):
                continue
            if contract.get("mock_or_test_double"):
                return False, "Test-double connector proof cannot satisfy active Competitive Intel writes."
            if bool(contract.get("write_safe", contract.get("write_ready"))):
                return True, ""
            status = str(contract.get("write_status") or contract.get("contract_state") or "unknown")
            return False, f"Competitive Intel connector contract is not write-safe ({status})."
        return False, "No Social/Ads/Brand connector contract is write-safe for Competitive Intel action."
    return False, "Competitive Intel connector write-readiness evidence is missing."


def _is_competitive_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"brand", "seo", "abm", "social", "ads"} or key in {
        "ahrefs",
        "brandwatch",
        "buffer",
        "google_ads",
        "linkedin_ads",
        "meta_ads",
        "twitter",
    }


def _is_competitive_write_contract(contract: dict[str, Any]) -> bool:
    category = str(contract.get("category") or "").strip().lower()
    key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
    return category in {"social", "ads", "brand"} or key in {
        "buffer",
        "brandwatch",
        "google_ads",
        "linkedin_ads",
        "meta_ads",
        "twitter",
    }


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
    if action in {"public_response", "publish_competitive_response"}:
        return explicit or "buffer", "publish_competitive_response"
    if action == "launch_competitive_campaign":
        return explicit or "linkedin_ads", "launch_competitive_campaign"
    return explicit or "brandwatch", "record_competitive_claim"


def _competitive_write_payload(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "competitor": inputs.get("competitor") or inputs.get("competitor_name"),
        "claim": inputs.get("claim") or inputs.get("message") or inputs.get("copy"),
        "campaign_name": inputs.get("campaign_name") or "Competitive response",
        "budget_amount": _float(inputs.get("budget_amount") or inputs.get("budget"), 0.0),
        "source_refs": _dict_list(inputs.get("source_refs")),
        "rollback_plan": inputs.get("rollback_plan") or "pause campaign or retract public response after review",
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
        reasons = [str(output["policy_result"].get("reason") or "Competitive Intel action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "cmo"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "Competitive Intel review required.",
        trigger_type="competitive_intel_review",
        decision_required=DecisionRequired(
            question="Review competitive recommendation or external action safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "Competitive Intel review",
            recommendation=output.get("recommended_actions", [{}])[0].get("action", "review"),
            agent_confidence=float(output.get("confidence") or 0.0),
            supporting_data={
                "policy_decision": (output.get("policy_result") or {}).get("decision"),
                "blocked_reasons": output.get("blocked_reasons") or [],
                "external_write_status": output.get("external_write_confirmation_status"),
                "alerts": (output.get("competitor_snapshot") or {}).get("alerts") or [],
            },
        ),
        assignee=HITLAssignee(role=str(role), notify_channels=["slack", "email"]),
    )


def _profile_key(row: dict[str, Any]) -> str:
    return str(row.get("domain") or row.get("name") or "").strip().lower()


def _source_ref_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return _source_ref(profile.get("source"), profile.get("domain") or profile.get("name"), profile.get("source_url"))


def _source_ref(source: Any, ref_id: Any, url: Any = None) -> dict[str, Any]:
    return {
        "type": str(source or "competitive_source"),
        "ref_id": str(ref_id or "provided_input"),
        "source_url": url,
    }


def _pricing(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("pricing")
    if isinstance(raw, dict):
        amount = _float(raw.get("amount") or raw.get("price"), 0.0)
        currency = str(raw.get("currency") or row.get("currency") or "USD")
        plan = str(raw.get("plan") or row.get("plan") or "default")
    else:
        amount = _float(row.get("price") or row.get("monthly_price") or row.get("annual_price"), 0.0)
        currency = str(row.get("currency") or "USD")
        plan = str(row.get("plan") or "default")
    return {"amount": amount, "currency": currency, "plan": plan}


def _domain(row: dict[str, Any]) -> str:
    return str(row.get("domain") or row.get("account_domain") or row.get("website") or "").strip().lower()


def _clean_feature(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def _win_loss_reason(text: str) -> str:
    if "price" in text or "pricing" in text or "cost" in text or "budget" in text:
        return "pricing"
    if "feature" in text or "integration" in text or "capability" in text:
        return "feature_gap"
    if "security" in text or "compliance" in text:
        return "security_compliance"
    if "support" in text or "service" in text:
        return "support"
    return "unspecified"


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return str(inputs.get("workflow_mode") or inputs.get("mode") or "shadow").strip().lower()


def _severity(value: Any, *, default: str) -> str:
    normalized = _normalize(str(value or default))
    return normalized if normalized in {"critical", "high", "medium", "low"} else default


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped
