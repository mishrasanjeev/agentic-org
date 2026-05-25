"""Account-Based Marketing agent implementation."""

from __future__ import annotations

import csv
import io
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

ABM_AGENT_MATURITY = "beta"
ABM_CONFIDENCE_FLOOR = 0.83

DEFAULT_SOURCE_WEIGHTS = {
    "bombora": 0.40,
    "g2": 0.25,
    "trustradius": 0.25,
    "crm": 0.10,
}
READ_ONLY_ACTIONS = {
    "account_scoring",
    "aggregate_engagement",
    "aggregate_intent",
    "csv_account_ingest_validation",
    "icp_fit_scoring",
    "intent_heat_scoring",
    "next_best_action",
    "query_target_accounts",
    "recommend_next_best_action",
    "score_accounts",
    "score_icp_fit",
    "score_intent_heat",
    "validate_account_csv",
}
WRITE_ACTIONS = {
    "create_abm_campaign",
    "launch_abm_campaign",
    "set_abm_budget",
    "sync_target_accounts",
    "target_account_list_change",
    "update_target_accounts",
}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}
TARGET_ACCOUNT_WRITE_ACTIONS = {"sync_target_accounts", "target_account_list_change", "update_target_accounts"}
BUDGET_WRITE_ACTIONS = {"create_abm_campaign", "launch_abm_campaign", "set_abm_budget"}


@AgentRegistry.register
class ABMAgent(BaseAgent):
    agent_type = "abm"
    domain = "marketing"
    confidence_floor = ABM_CONFIDENCE_FLOOR
    prompt_file = "abm_agent.prompt.txt"

    async def execute(self, task):
        """Run deterministic ABM scoring, validation, planning, or guarded writes."""

        start = time.monotonic()
        trace: list[str] = []
        tool_calls: list[ToolCallRecord] = []
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        try:
            inputs = _task_inputs(task)
            action = _normalize_action(_task_action(task))
            mode = _workflow_mode(inputs)
            trace.append(f"ABM action={action}, mode={mode}")

            if action in {"score_accounts", "account_scoring", "query_target_accounts"}:
                output = self._score_accounts(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in {"score_icp_fit", "icp_fit_scoring"}:
                output = self._score_icp_fit(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in {"score_intent_heat", "intent_heat_scoring", "aggregate_intent"}:
                output = self._score_intent_heat(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action == "aggregate_engagement":
                output = self._aggregate_engagement(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in {"recommend_next_best_action", "next_best_action"}:
                output = self._recommend_next_best_action(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in {"validate_account_csv", "csv_account_ingest_validation"}:
                output = self._validate_account_csv(task, inputs, trace)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)
            if action in WRITE_ACTIONS:
                output = await self._guarded_abm_write(task, action, inputs, mode, trace, tool_calls)
                return self._complete_or_hitl(task, msg_id, output, trace, tool_calls, start)

            output = self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale=f"Unsupported ABM action '{action}'.",
                recommended_actions=[
                    {
                        "action": "use_supported_abm_action",
                        "reason": "The ABM agent only executes declared deterministic actions.",
                    }
                ],
                blocked_reasons=[f"Unsupported ABM action '{action}'."],
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
                error={"code": "ABM_ACTION_UNSUPPORTED", "message": output["blocked_reasons"][0]},
                start=start,
            )
        # enterprise-gate: broad-except-ok reason=agent-execution-boundary-returns-failed-result
        except Exception as exc:
            logger.error("abm_agent_error", agent=self.agent_id, error=str(exc))
            trace.append(f"Error: {exc}")
            return self._make_result(
                task,
                msg_id,
                "failed",
                {},
                0.0,
                trace,
                tool_calls,
                error={"code": "ABM_AGENT_ERR", "message": str(exc)},
                start=start,
            )

    def _score_accounts(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        accounts = _account_rows(inputs)
        weights = _normalized_weights(inputs.get("source_weights") or inputs.get("weights"))
        high_threshold = _float(inputs.get("high_intent_threshold"), 75.0)
        rows = [_score_account(account, weights, inputs) for account in accounts]
        rows.sort(key=lambda row: (-row["overall_score"], row["domain"]))
        alerts = _high_intent_alerts(rows, high_threshold)
        trace.append(f"Scored {len(rows)} ABM accounts; alerts={len(alerts)}")
        return self._base_output(
            task,
            action="score_accounts",
            status="accounts_scored" if rows else "degraded",
            confidence=0.87 if rows else 0.5,
            rationale=(
                f"Scored {len(rows)} accounts using ICP fit, intent heat, and engagement inputs."
                if rows
                else "No structured account rows were supplied for ABM scoring."
            ),
            recommended_actions=_scoring_recommendations(rows, alerts),
            source_refs=_source_refs_for_accounts(rows, weights),
            degraded_reasons=[] if rows else ["No account rows supplied."],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={
                "account_scores": rows,
                "scoring_summary": _scoring_summary(rows, weights),
                "intent_alerts": alerts,
                "source_weights": weights,
            },
        )

    def _score_icp_fit(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        accounts = _account_rows(inputs)
        rows = []
        for account in accounts:
            base = _account_identity(account)
            base["icp_fit_score"] = _icp_fit_score(account, inputs)
            base["icp_fit_reasons"] = _icp_fit_reasons(account, inputs)
            rows.append(base)
        rows.sort(key=lambda row: (-row["icp_fit_score"], row["domain"]))
        trace.append(f"Computed ICP fit for {len(rows)} accounts")
        return self._base_output(
            task,
            action="score_icp_fit",
            status="icp_fit_scored" if rows else "degraded",
            confidence=0.86 if rows else 0.5,
            rationale="Computed ICP fit against configured industry, size, revenue, region, and tier rules.",
            recommended_actions=[
                {
                    "action": "prioritize_high_fit_accounts",
                    "reason": "Use high-fit accounts for ABM tiering before budget or outreach changes.",
                }
            ],
            source_refs=[{"kind": "icp_config", "fields": sorted(_icp_fields(inputs))}],
            degraded_reasons=[] if rows else ["No account rows supplied."],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={"account_scores": rows, "scoring_summary": {"account_count": len(rows)}},
        )

    def _score_intent_heat(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        accounts = _account_rows(inputs)
        weights = _normalized_weights(inputs.get("source_weights") or inputs.get("weights"))
        rows = []
        for account in accounts:
            base = _account_identity(account)
            source_scores = _source_scores(account)
            base["intent_heat_score"] = _intent_heat_score(source_scores, weights)
            base["source_scores"] = source_scores
            rows.append(base)
        rows.sort(key=lambda row: (-row["intent_heat_score"], row["domain"]))
        trace.append(f"Computed intent heat for {len(rows)} accounts")
        return self._base_output(
            task,
            action="score_intent_heat",
            status="intent_heat_scored" if rows else "degraded",
            confidence=0.85 if rows else 0.5,
            rationale="Aggregated Bombora, G2, TrustRadius, and CRM-style signals into intent heat scores.",
            recommended_actions=_intent_recommendations(rows),
            source_refs=_source_refs_for_accounts(rows, weights),
            degraded_reasons=[] if rows else ["No intent account rows supplied."],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={
                "account_scores": rows,
                "scoring_summary": {"account_count": len(rows), "source_weights": weights},
                "source_weights": weights,
            },
        )

    def _aggregate_engagement(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        accounts = _account_rows(inputs)
        rows = []
        for account in accounts:
            base = _account_identity(account)
            base["engagement_score"] = _engagement_score(account)
            base["engagement_inputs"] = {
                "website_visits": _float(account.get("website_visits"), 0.0),
                "email_clicks": _float(account.get("email_clicks"), 0.0),
                "form_submissions": _float(account.get("form_submissions"), 0.0),
                "meetings": _float(account.get("meetings"), 0.0),
            }
            rows.append(base)
        rows.sort(key=lambda row: (-row["engagement_score"], row["domain"]))
        trace.append(f"Aggregated engagement for {len(rows)} accounts")
        return self._base_output(
            task,
            action="aggregate_engagement",
            status="engagement_aggregated" if rows else "degraded",
            confidence=0.82 if rows else 0.5,
            rationale="Aggregated website, email, form, and meeting engagement signals by account.",
            recommended_actions=[
                {
                    "action": "route_engaged_accounts_to_sdr",
                    "reason": "High engagement accounts should be reviewed before outbound actions.",
                }
            ],
            source_refs=[{"kind": "crm_engagement", "count": len(rows)}],
            degraded_reasons=[] if rows else ["No engagement account rows supplied."],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={"account_scores": rows, "scoring_summary": {"account_count": len(rows)}},
        )

    def _recommend_next_best_action(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        accounts = _account_rows(inputs)
        weights = _normalized_weights(inputs.get("source_weights") or inputs.get("weights"))
        scored = [_score_account(account, weights, inputs) for account in accounts]
        scored.sort(key=lambda row: (-row["overall_score"], row["domain"]))
        recommendations = [_next_best_action(row) for row in scored]
        trace.append(f"Recommended next-best actions for {len(recommendations)} accounts")
        return self._base_output(
            task,
            action="recommend_next_best_action",
            status="next_best_actions_recommended" if recommendations else "degraded",
            confidence=0.84 if recommendations else 0.5,
            rationale="Generated read-only ABM next-best-action recommendations from score bands.",
            recommended_actions=recommendations,
            source_refs=_source_refs_for_accounts(scored, weights),
            degraded_reasons=[] if recommendations else ["No account rows supplied for next-best action planning."],
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={"account_scores": scored, "scoring_summary": _scoring_summary(scored, weights)},
        )

    def _validate_account_csv(
        self,
        task,
        inputs: dict[str, Any],
        trace: list[str],
    ) -> dict[str, Any]:
        csv_text = str(inputs.get("csv_content") or inputs.get("account_csv") or "")
        valid_accounts, invalid_rows, missing_headers = _validate_account_csv(csv_text)
        blocked = []
        if missing_headers:
            blocked.append(f"CSV is missing required fields: {', '.join(missing_headers)}.")
        if invalid_rows:
            blocked.append(f"CSV has {len(invalid_rows)} malformed account row(s).")
        trace.append(f"Validated ABM CSV: valid={len(valid_accounts)}, invalid={len(invalid_rows)}")
        return self._base_output(
            task,
            action="validate_account_csv",
            status="csv_validated" if not blocked else "blocked",
            confidence=0.88 if not blocked else 0.0,
            rationale=(
                f"Validated {len(valid_accounts)} target account rows."
                if not blocked
                else "CSV account ingest validation failed closed."
            ),
            recommended_actions=[
                {
                    "action": "import_target_accounts" if not blocked else "fix_csv_account_rows",
                    "reason": "Rows are ready for review." if not blocked else "Correct required fields before ingest.",
                }
            ],
            source_refs=[{"kind": "csv_account_ingest", "valid_rows": len(valid_accounts)}],
            blocked_reasons=blocked,
            policy_context={"workflow_mode": _workflow_mode(inputs), "workflow_id": "abm_sprint"},
            extra={
                "valid_accounts": valid_accounts,
                "invalid_rows": invalid_rows,
                "missing_headers": missing_headers,
                "scoring_summary": {"valid_accounts": len(valid_accounts), "invalid_rows": len(invalid_rows)},
            },
        )

    async def _guarded_abm_write(
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
        escalation = _escalation_for_context(task, action, inputs, policy)
        blocked_reasons: list[str] = []
        hitl_reasons: list[str] = []

        if mode in SHADOW_MODES:
            blocked_reasons.append("Shadow/draft/internal ABM workflows are read-only.")
            trace.append("ABM write converted to shadow-only recommendation")
            return self._base_output(
                task,
                action=action,
                status="shadow_only",
                confidence=0.78,
                rationale="Prepared ABM recommendation without mutating target lists, budgets, or campaigns.",
                recommended_actions=[
                    {
                        "action": "create_internal_approval",
                        "reason": "Review the ABM change before any active external write is attempted.",
                    }
                ],
                approval_required=True,
                hitl_required=True,
                policy_result=policy,
                blocked_reasons=blocked_reasons,
                escalation=escalation,
                external_write_status="not_required",
                external_write_required=False,
                extra={"write_payload": _abm_write_payload(action, inputs)},
            )

        connector_safe, connector_reason = _connector_write_safe(inputs)
        if not connector_safe:
            blocked_reasons.append(connector_reason)
        if policy.get("decision") in {"requires_approval", "requires_escalation"} and not _has_approval(inputs):
            hitl_reasons.append(str(policy.get("reason") or "ABM external action requires approval."))
        if policy.get("decision") == "blocked":
            blocked_reasons.append(str(policy.get("reason") or "Marketing policy blocks this ABM action."))
        escalation_needs_review = escalation.get("decision") not in {
            None,
            "",
            "no_escalation",
            "notify_owner",
        }
        if escalation_needs_review and not _has_escalation_approval(inputs):
            hitl_reasons.append("ABM action requires escalation review.")

        if blocked_reasons or hitl_reasons:
            return self._base_output(
                task,
                action=action,
                status="blocked",
                confidence=0.0,
                rationale="ABM external action failed closed before vendor mutation.",
                recommended_actions=[
                    {
                        "action": "resolve_abm_write_prerequisites",
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
                extra={"write_payload": _abm_write_payload(action, inputs)},
            )

        result = _dict_or_none(inputs.get("external_write_result"))
        connector, tool = _connector_tool(action, inputs)
        idempotency_key = str(
            inputs.get("idempotency_key")
            or inputs.get("request_fingerprint")
            or f"abm:{task.correlation_id}:{task.step_id}:{action}"
        )
        if result is None:
            result = await self._safe_tool_call(
                connector,
                tool,
                _abm_write_payload(action, inputs),
                trace,
                tool_calls,
                idempotency_key=idempotency_key,
            )

        write_status = _external_write_confirmation_status(result)
        write_confirmed = _is_confirmed_external_write(result)
        external_ref = _external_write_ref(result)
        if not write_confirmed:
            blocked_reasons.append("ABM external write is unconfirmed and cannot be marked complete.")
        trace.append(f"ABM write status={write_status}, confirmed={write_confirmed}")
        return self._base_output(
            task,
            action=action,
            status="write_confirmed" if write_confirmed else "write_unconfirmed",
            confidence=0.9 if write_confirmed else 0.0,
            rationale=(
                "ABM external write was confirmed by the connector."
                if write_confirmed
                else "ABM external write did not return confirmed vendor evidence."
            ),
            recommended_actions=[
                {
                    "action": "monitor_abm_change" if write_confirmed else "verify_abm_write_before_retry",
                    "reason": (
                        "Track target-account and campaign performance."
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
            extra={
                "write_payload": _abm_write_payload(action, inputs),
                "account_scores": [
                    _score_account(row, _normalized_weights(inputs.get("source_weights")), inputs)
                    for row in _account_rows(inputs)
                ],
            },
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
                "workflow_id": "abm_sprint",
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
            "audit_ref": f"abm:{task.correlation_id}:{task.step_id}:{action}",
            "degraded_reasons": _dedupe(degraded_reasons or []),
            "blocked_reasons": _dedupe(blocked_reasons or []),
            "external_write_confirmation_status": external_write_status,
            "external_writes_completed": external_write_status == "write_confirmed",
            "external_write_required": external_write_required,
            "external_write_ref": external_write_ref,
            "production_status": ABM_AGENT_MATURITY,
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
    return str(task.task.action if hasattr(task.task, "action") else task.task.get("action", "score_accounts"))


def _normalize_action(action: str) -> str:
    normalized = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "account_score": "score_accounts",
        "account_scoring": "score_accounts",
        "csv_account_ingest_validation": "validate_account_csv",
        "icp_fit_scoring": "score_icp_fit",
        "intent_heat_scoring": "score_intent_heat",
        "next_best_action": "recommend_next_best_action",
        "query_target_accounts": "score_accounts",
        "sync_target_account_list": "sync_target_accounts",
    }
    return aliases.get(normalized, normalized)


def _workflow_mode(inputs: dict[str, Any]) -> str:
    return str(inputs.get("workflow_mode") or inputs.get("mode") or "shadow").strip().lower()


def _account_rows(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inputs.get("accounts") or inputs.get("target_accounts") or inputs.get("account_rows") or []
    if isinstance(raw, dict):
        return [dict(raw)]
    if not isinstance(raw, list | tuple):
        return []
    return [dict(row) for row in raw if isinstance(row, dict)]


def _normalized_weights(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return dict(DEFAULT_SOURCE_WEIGHTS)
    weights = {}
    for key in DEFAULT_SOURCE_WEIGHTS:
        weights[key] = max(_float(value.get(key), DEFAULT_SOURCE_WEIGHTS[key]), 0.0)
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_SOURCE_WEIGHTS)
    return {key: round(score / total, 4) for key, score in weights.items()}


def _score_account(
    account: dict[str, Any],
    weights: dict[str, float],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    identity = _account_identity(account)
    source_scores = _source_scores(account)
    intent = _intent_heat_score(source_scores, weights)
    icp = _icp_fit_score(account, inputs)
    engagement = _engagement_score(account)
    overall = round((intent * 0.45) + (icp * 0.40) + (engagement * 0.15), 2)
    row = {
        **identity,
        "overall_score": overall,
        "icp_fit_score": icp,
        "intent_heat_score": intent,
        "engagement_score": engagement,
        "score_band": _score_band(overall),
        "source_scores": source_scores,
        "recommended_action": _recommended_action_for_score(overall, intent, icp),
    }
    return row


def _account_identity(account: dict[str, Any]) -> dict[str, Any]:
    domain = str(account.get("domain") or account.get("account_domain") or "").strip().lower()
    name = str(account.get("company_name") or account.get("company") or account.get("name") or domain or "unknown")
    return {
        "account_name": name,
        "domain": domain,
        "industry": str(account.get("industry") or ""),
        "tier": str(account.get("tier") or account.get("account_tier") or ""),
    }


def _source_scores(account: dict[str, Any]) -> dict[str, float]:
    signals = account.get("signals") if isinstance(account.get("signals"), dict) else {}
    return {
        "bombora": _bounded_score(account.get("bombora") or signals.get("bombora")),
        "g2": _bounded_score(account.get("g2") or signals.get("g2")),
        "trustradius": _bounded_score(account.get("trustradius") or signals.get("trustradius")),
        "crm": _bounded_score(account.get("crm") or signals.get("crm") or account.get("crm_score")),
    }


def _intent_heat_score(source_scores: dict[str, float], weights: dict[str, float]) -> float:
    return round(sum(source_scores[key] * weights.get(key, 0.0) for key in DEFAULT_SOURCE_WEIGHTS), 2)


def _icp_fit_score(account: dict[str, Any], inputs: dict[str, Any]) -> float:
    rules = _icp_rules(inputs)
    score = 0.0
    if _domain(account):
        score += 10
    if not rules["target_industries"] or _normalize(account.get("industry")) in rules["target_industries"]:
        score += 25
    employees = _float(account.get("employee_count") or account.get("employees"), 0.0)
    if _in_range(employees, rules["employee_min"], rules["employee_max"]):
        score += 20
    revenue = _float(account.get("annual_revenue") or account.get("revenue"), 0.0)
    if _in_range(revenue, rules["revenue_min"], rules["revenue_max"]):
        score += 20
    if not rules["regions"] or _normalize(account.get("region")) in rules["regions"]:
        score += 15
    if not rules["tiers"] or _normalize(account.get("tier") or account.get("account_tier")) in rules["tiers"]:
        score += 10
    return round(min(score, 100.0), 2)


def _icp_fit_reasons(account: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    rules = _icp_rules(inputs)
    reasons = []
    if _domain(account):
        reasons.append("account_domain_present")
    if not rules["target_industries"] or _normalize(account.get("industry")) in rules["target_industries"]:
        reasons.append("industry_matches_icp")
    employees = _float(account.get("employee_count") or account.get("employees"), 0.0)
    if _in_range(employees, rules["employee_min"], rules["employee_max"]):
        reasons.append("employee_count_matches_icp")
    revenue = _float(account.get("annual_revenue") or account.get("revenue"), 0.0)
    if _in_range(revenue, rules["revenue_min"], rules["revenue_max"]):
        reasons.append("revenue_matches_icp")
    if not rules["regions"] or _normalize(account.get("region")) in rules["regions"]:
        reasons.append("region_matches_icp")
    if not rules["tiers"] or _normalize(account.get("tier") or account.get("account_tier")) in rules["tiers"]:
        reasons.append("tier_matches_icp")
    return reasons


def _icp_rules(inputs: dict[str, Any]) -> dict[str, Any]:
    raw = inputs.get("icp") if isinstance(inputs.get("icp"), dict) else inputs
    return {
        "target_industries": _normalized_set(raw.get("target_industries") or raw.get("industries")),
        "employee_min": _float(raw.get("employee_min") or raw.get("min_employees"), 0.0),
        "employee_max": _float(raw.get("employee_max") or raw.get("max_employees"), 0.0),
        "revenue_min": _float(raw.get("revenue_min") or raw.get("min_revenue"), 0.0),
        "revenue_max": _float(raw.get("revenue_max") or raw.get("max_revenue"), 0.0),
        "regions": _normalized_set(raw.get("regions") or raw.get("target_regions")),
        "tiers": _normalized_set(raw.get("tiers") or raw.get("target_tiers")),
    }


def _icp_fields(inputs: dict[str, Any]) -> set[str]:
    rules = _icp_rules(inputs)
    fields = set()
    for key, value in rules.items():
        if isinstance(value, set):
            if value:
                fields.add(key)
        elif value:
            fields.add(key)
    return fields


def _engagement_score(account: dict[str, Any]) -> float:
    direct = account.get("engagement_score")
    if direct not in {None, ""}:
        return _bounded_score(direct)
    visits = _float(account.get("website_visits"), 0.0)
    clicks = _float(account.get("email_clicks"), 0.0)
    forms = _float(account.get("form_submissions"), 0.0)
    meetings = _float(account.get("meetings"), 0.0)
    score = min((visits * 0.5) + (clicks * 2.0) + (forms * 12.0) + (meetings * 18.0), 100.0)
    return round(score, 2)


def _next_best_action(row: dict[str, Any]) -> dict[str, Any]:
    action = row.get("recommended_action") or _recommended_action_for_score(
        _float(row.get("overall_score"), 0.0),
        _float(row.get("intent_heat_score"), 0.0),
        _float(row.get("icp_fit_score"), 0.0),
    )
    reasons = []
    if _float(row.get("intent_heat_score"), 0.0) >= 80:
        reasons.append("high intent heat")
    if _float(row.get("icp_fit_score"), 0.0) >= 80:
        reasons.append("strong ICP fit")
    if _float(row.get("engagement_score"), 0.0) >= 70:
        reasons.append("high engagement")
    return {
        "action": action,
        "account_domain": row.get("domain"),
        "account_name": row.get("account_name"),
        "score": row.get("overall_score"),
        "reason": ", ".join(reasons) or "score band review",
    }


def _recommended_action_for_score(overall: float, intent: float, icp: float) -> str:
    if intent >= 80 and icp >= 70:
        return "create_sales_alert"
    if overall >= 75:
        return "prioritize_1_to_1_outreach"
    if intent >= 70:
        return "route_to_sdr_research"
    if icp >= 75:
        return "add_to_nurture_segment"
    return "monitor_intent"


def _score_band(score: float) -> str:
    if score >= 80:
        return "hot"
    if score >= 65:
        return "warm"
    if score >= 45:
        return "nurture"
    return "monitor"


def _high_intent_alerts(rows: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    return [
        {
            "account_domain": row["domain"],
            "account_name": row["account_name"],
            "intent_heat_score": row["intent_heat_score"],
            "recommended_action": row["recommended_action"],
            "reason": f"Intent heat score {row['intent_heat_score']} exceeds threshold {threshold}.",
        }
        for row in rows
        if row["intent_heat_score"] >= threshold
    ]


def _scoring_recommendations(
    rows: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return [{"action": "connect_abm_and_crm_sources", "reason": "No account rows were supplied."}]
    recommendations = []
    if alerts:
        recommendations.append(
            {
                "action": "review_high_intent_alerts",
                "reason": f"{len(alerts)} account(s) crossed the high-intent threshold.",
            }
        )
    recommendations.append(
        {
            "action": "review_abm_score_rankings",
            "reason": "Approve any target-list or outreach changes before execution.",
        }
    )
    return recommendations


def _intent_recommendations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return [{"action": "connect_intent_sources", "reason": "No intent rows were supplied."}]
    return [_next_best_action(row) for row in rows[:5]]


def _scoring_summary(rows: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    if not rows:
        return {"account_count": 0, "source_weights": weights}
    return {
        "account_count": len(rows),
        "hot_accounts": sum(1 for row in rows if row["score_band"] == "hot"),
        "warm_accounts": sum(1 for row in rows if row["score_band"] == "warm"),
        "average_overall_score": round(sum(row["overall_score"] for row in rows) / len(rows), 2),
        "source_weights": weights,
    }


def _source_refs_for_accounts(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    domains = [row["domain"] for row in rows if row.get("domain")]
    return [
        {"kind": "abm_account_scores", "count": len(rows), "domains": domains[:10]},
        {"kind": "intent_source_weights", "weights": weights},
    ]


def _validate_account_csv(csv_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if not csv_text.strip():
        return [], [{"row_number": 0, "reason": "CSV content is empty."}], ["company_name", "domain"]
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except csv.Error as exc:
        return [], [{"row_number": 0, "reason": str(exc)}], ["company_name", "domain"]
    headers = {_normalize(field) for field in (reader.fieldnames or []) if field}
    has_company = bool({"company_name", "company", "name"} & headers)
    missing = []
    if not has_company:
        missing.append("company_name")
    if "domain" not in headers and "account_domain" not in headers:
        missing.append("domain")
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    if missing:
        return valid, invalid, missing
    for index, row in enumerate(reader, start=2):
        company = _csv_value(row, "company_name", "company", "name")
        domain = _csv_value(row, "domain", "account_domain")
        if not company or not domain or "." not in domain:
            invalid.append(
                {
                    "row_number": index,
                    "company_name": company,
                    "domain": domain,
                    "reason": "Required company name/domain is missing or malformed.",
                }
            )
            continue
        valid.append(
            {
                "company_name": company,
                "domain": domain.lower(),
                "industry": _csv_value(row, "industry"),
                "tier": _csv_value(row, "tier"),
            }
        )
    return valid, invalid, []


def _policy_context_for_write(action: str, inputs: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "workflow_id": "abm_sprint",
        "workflow_mode": mode,
        "action": action,
        "external_write_required": True,
        "customer_facing": action in BUDGET_WRITE_ACTIONS,
        "target_account_delta": _target_account_delta(inputs),
        "target_account_change": action in TARGET_ACCOUNT_WRITE_ACTIONS,
        "budget_amount": _float(
            inputs.get("budget_amount")
            or inputs.get("budget")
            or inputs.get("monthly_budget")
            or inputs.get("daily_budget"),
            0.0,
        ),
        "budget_delta": _float(inputs.get("budget_delta") or inputs.get("budget_increase"), 0.0),
        "channel": str(inputs.get("channel") or "abm"),
    }


def _escalation_for_context(
    task,
    action: str,
    inputs: dict[str, Any],
    policy_result: dict[str, Any],
) -> dict[str, Any]:
    context = {
        "workflow_id": "abm_sprint",
        "workflow_run_id": getattr(task, "workflow_run_id", None),
        "step_id": getattr(task, "step_id", None),
        "event_type": (
            "target_account_change"
            if action in TARGET_ACCOUNT_WRITE_ACTIONS
            else "budget_threshold_exceeded"
        ),
        "target_account_change": action in TARGET_ACCOUNT_WRITE_ACTIONS,
        "budget_threshold_exceeded": action in BUDGET_WRITE_ACTIONS,
        "severity": "high",
        "reason": "ABM external action requires owner routing.",
        "marketing_policy_decision": policy_result,
        "target_account_delta": _target_account_delta(inputs),
    }
    return evaluate_marketing_escalation(context)


def _connector_write_safe(inputs: dict[str, Any]) -> tuple[bool, str]:
    if inputs.get("connector_write_ready") is True or inputs.get("write_ready") is True:
        return True, ""
    if inputs.get("connector_write_ready") is False or inputs.get("write_ready") is False:
        return False, "ABM connector is not write-safe."
    contracts = _dict_list(inputs.get("connector_contracts"))
    direct = _dict_or_none(inputs.get("connector_contract"))
    if direct:
        contracts.insert(0, direct)
    if contracts:
        for contract in contracts:
            category = str(contract.get("category") or "").strip().lower()
            key = str(contract.get("connector_key") or contract.get("key") or "").strip().lower()
            known_keys = {"6sense", "bombora", "g2", "trustradius", "hubspot", "salesforce"}
            if category not in {"abm", "crm"} and key not in known_keys:
                continue
            if contract.get("mock_or_test_double"):
                return False, "Test-double connector proof cannot satisfy active ABM writes."
            if bool(contract.get("write_safe", contract.get("write_ready"))):
                return True, ""
            status = str(contract.get("write_status") or contract.get("contract_state") or "unknown")
            return False, f"ABM connector contract is not write-safe ({status})."
        return False, "No ABM/CRM connector contract is write-safe."
    return False, "ABM connector write-readiness evidence is missing."


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
    if action in TARGET_ACCOUNT_WRITE_ACTIONS:
        return explicit or "hubspot", "update_target_accounts"
    return explicit or "linkedin_ads", "create_abm_campaign"


def _abm_write_payload(action: str, inputs: dict[str, Any]) -> dict[str, Any]:
    accounts = _account_rows(inputs)
    return {
        "action": action,
        "accounts": [_account_identity(row) for row in accounts],
        "budget_amount": _float(
            inputs.get("budget_amount")
            or inputs.get("budget")
            or inputs.get("monthly_budget")
            or inputs.get("daily_budget"),
            0.0,
        ),
        "campaign_name": inputs.get("campaign_name") or inputs.get("name") or "ABM campaign",
        "target_account_delta": _target_account_delta(inputs),
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
        reasons = [str(output["policy_result"].get("reason") or "ABM action requires review.")]
    escalation = output.get("escalation_result") or {}
    role = escalation.get("owner_role") or escalation.get("primary_owner_role") or "revops_lead"
    return HITLRequest(
        hitl_id=f"hitl_{uuid.uuid4().hex[:12]}",
        trigger_condition="; ".join(str(reason) for reason in reasons) or "ABM review required.",
        trigger_type="abm_review",
        decision_required=DecisionRequired(
            question="Review ABM recommendation or external action safeguard.",
            options=[
                DecisionOption(id="approve", label="Approve", action="proceed"),
                DecisionOption(id="request_changes", label="Request changes", action="defer"),
                DecisionOption(id="escalate", label="Escalate", action="defer"),
                DecisionOption(id="reject", label="Reject", action="reject"),
            ],
        ),
        context=HITLContext(
            summary=output.get("rationale") or "ABM review",
            recommendation=output.get("recommended_actions", [{}])[0].get("action", "review"),
            agent_confidence=float(output.get("confidence") or 0.0),
            supporting_data={
                "policy_decision": (output.get("policy_result") or {}).get("decision"),
                "blocked_reasons": output.get("blocked_reasons") or [],
                "external_write_status": output.get("external_write_confirmation_status"),
                "target_account_delta": (output.get("write_payload") or {}).get("target_account_delta"),
            },
        ),
        assignee=HITLAssignee(role=str(role), notify_channels=["slack", "email"]),
    )


def _target_account_delta(inputs: dict[str, Any]) -> int:
    direct = _int(
        inputs.get("target_account_delta")
        or inputs.get("target_account_change_count")
        or inputs.get("account_list_change_count"),
        0,
    )
    if direct:
        return direct
    return len(_account_rows(inputs)) if inputs.get("accounts_are_new") else 0


def _domain(account: dict[str, Any]) -> str:
    return str(account.get("domain") or account.get("account_domain") or "").strip().lower()


def _in_range(value: float, minimum: float, maximum: float) -> bool:
    if value <= 0:
        return minimum <= 0 and maximum <= 0
    if minimum and value < minimum:
        return False
    if maximum and value > maximum:
        return False
    return True


def _csv_value(row: dict[str, Any], *keys: str) -> str:
    normalized = {_normalize(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(_normalize(key))
        if value is not None:
            return str(value).strip()
    return ""


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalized_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {_normalize(part) for part in value.split(",") if _normalize(part)}
    if isinstance(value, list | tuple | set):
        return {_normalize(item) for item in value if _normalize(item)}
    return set()


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _bounded_score(value: Any) -> float:
    return round(max(0.0, min(_float(value, 0.0), 100.0)), 2)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
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
