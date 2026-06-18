"""CMO marketing agent contract registry and output normalization.

CMO-9.1 keeps agent status and result shape explicit without pretending that
target or wrapper-level agents are production-ready. The helpers here are
deterministic and test-oriented: they do not call LLMs, vendors, or connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.marketing.policy_manifest import CUSTOMER_FACING_WRITE_ACTIONS

CMO_AGENT_CONTRACT_VERSION = "2026-05-24.cmo-9.1"

REQUIRED_AGENT_CONTRACT_KEYS = (
    "status",
    "confidence",
    "rationale",
    "recommended_actions",
    "source_refs",
    "policy_result",
    "approval_required",
    "hitl_required",
    "audit_ref",
    "degraded_reasons",
    "blocked_reasons",
    "external_write_confirmation_status",
    "external_writes_completed",
)


@dataclass(frozen=True)
class MarketingAgentContractSpec:
    agent_type: str
    maturity: str
    production_ready: bool
    actions: tuple[str, ...]
    surface: str
    blocker: str | None = None
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "maturity": self.maturity,
            "production_ready": self.production_ready,
            "actions": list(self.actions),
            "surface": self.surface,
            "blocker": self.blocker,
            "aliases": list(self.aliases),
            "contract_version": CMO_AGENT_CONTRACT_VERSION,
        }


MARKETING_AGENT_CONTRACT_SPECS: tuple[MarketingAgentContractSpec, ...] = (
    MarketingAgentContractSpec(
        agent_type="campaign_pilot",
        maturity="production",
        production_ready=True,
        surface="core.agents.marketing.campaign_pilot",
        actions=(
            "create",
            "setup",
            "manage_campaign",
            "create_plan",
            "launch_campaign",
            "mutate_ad_budget",
            "pause_campaign",
            "generate_weekly_report",
        ),
    ),
    MarketingAgentContractSpec(
        agent_type="content_factory",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.content_factory",
        actions=(
            "generate_content",
            "create_draft",
            "schedule_content",
            "publish",
            "publish_to_wordpress",
        ),
        blocker="Beta: requires approval, policy, audit, and write confirmation before production publishing.",
    ),
    MarketingAgentContractSpec(
        agent_type="email_marketing",
        maturity="beta",
        production_ready=False,
        surface="core.langgraph.agents.email_marketing",
        actions=(
            "segment_list",
            "send_email",
            "send_to_segment",
            "start_nurture_sequence",
        ),
        blocker="Beta LangGraph path: sends require approval and connector write confirmation.",
        aliases=("email_agent",),
    ),
    MarketingAgentContractSpec(
        agent_type="brand_monitor",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.brand_monitor",
        actions=(
            "aggregate_mentions",
            "brand_claim",
            "classify_crisis_severity",
            "classify_sentiment",
            "comparative_claim",
            "competitor_brand_grouping",
            "crisis_response",
            "detect_crisis",
            "detect_negative_spike",
            "false_positive_suppression",
            "get_sentiment",
            "group_mentions",
            "mention_aggregation",
            "monitor_mentions",
            "pricing_claim",
            "public_response",
            "publish_brand_response",
            "recommend_response_playbook",
            "sentiment_trend",
        ),
        blocker=(
            "Beta: first-class Brand Monitor logic exists, but production proof "
            "still requires real brand/social connector readiness, policy, "
            "approval, escalation, audit, confirmed-write, and pilot evidence."
        ),
    ),
    MarketingAgentContractSpec(
        agent_type="seo_strategist",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.seo_strategist",
        actions=(
            "apply_redirect",
            "content_optimization_recommendation",
            "get_keyword_rankings",
            "identify_content_gaps",
            "keyword_gap_analysis",
            "publish_seo_change",
            "ranking_delta_computation",
            "recommendation_bundling",
            "seo_sprint_planning",
            "submit_url_to_index",
            "technical_issue_prioritization",
            "update_canonical_tag",
            "update_page_metadata",
            "update_robots_txt",
            "update_sitemap",
        ),
        blocker=(
            "Beta: first-class SEO Strategist logic exists, but production proof "
            "still requires real SEO/Analytics/CMS connector readiness, policy, "
            "approval, audit, confirmed-write, and pilot evidence."
        ),
    ),
    MarketingAgentContractSpec(
        agent_type="crm_intelligence",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.crm_intelligence",
        actions=(
            "account_deal_health_summary",
            "account_health_summary",
            "analyze_pipeline_velocity",
            "bulk_crm_update",
            "change_segment_membership",
            "change_target_accounts",
            "churn_risk_signal_extraction",
            "extract_churn_signals",
            "funnel_conversion_analysis",
            "lead_scoring_refresh",
            "pipeline_velocity_analysis",
            "promote_lifecycle_stage",
            "promote_to_sql",
            "recommend_segments",
            "refresh_lead_scores",
            "score_lead",
            "segment_recommendation",
            "sql_promotion_criteria",
            "update_crm",
            "update_lead_scores",
            "update_lifecycle_stage",
            "update_segment_membership",
        ),
        blocker=(
            "Beta: first-class CRM Intelligence logic exists, but production "
            "proof still requires real CRM/intent connector readiness, policy, "
            "approval, audit, confirmed-write, and pilot evidence."
        ),
    ),
    MarketingAgentContractSpec(
        agent_type="social_media",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.social_media",
        actions=(
            "classify_reply_risk",
            "draft_social_post",
            "generate_content_calendar",
            "get_weekly_engagement",
            "optimize_posting_schedule",
            "publish_post",
            "reply_to_mention",
            "schedule_campaign_posts",
            "schedule_content_promotion",
            "schedule_post",
            "triage_engagement",
        ),
        blocker=(
            "Beta: first-class Social Media logic exists, but production proof still "
            "requires real connector/write, policy, approval, audit, and pilot evidence."
        ),
    ),
    MarketingAgentContractSpec(
        agent_type="abm",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.abm_agent",
        actions=(
            "aggregate_engagement",
            "aggregate_intent",
            "create_abm_campaign",
            "launch_abm_campaign",
            "query_target_accounts",
            "recommend_next_best_action",
            "score_accounts",
            "score_icp_fit",
            "score_intent_heat",
            "set_abm_budget",
            "sync_target_accounts",
            "target_account_list_change",
            "update_target_accounts",
            "validate_account_csv",
        ),
        blocker=(
            "Beta: first-class ABM logic exists, but production proof still "
            "requires real ABM/CRM connector-write, policy, approval, audit, "
            "confirmed-write, and pilot evidence."
        ),
        aliases=("abm_agent",),
    ),
    MarketingAgentContractSpec(
        agent_type="competitive_intel",
        maturity="beta",
        production_ready=False,
        surface="core.agents.marketing.competitive_intel",
        actions=(
            "change_confidence_score",
            "comparative_claim",
            "duplicate_change_suppression",
            "evaluate_alert_thresholds",
            "extract_win_loss_signals",
            "feature_capability_diffing",
            "launch_competitive_campaign",
            "normalize_competitor_profiles",
            "positioning_recommendation",
            "pricing_change_detection",
            "pricing_claim",
            "public_response",
            "publish_competitive_response",
            "weekly_benchmark",
            "weekly_market_snapshot",
        ),
        blocker=(
            "Beta: first-class Competitive Intel logic exists, but production "
            "proof still requires real competitive-source connector readiness, "
            "policy, approval, escalation, audit, confirmed-write, and pilot evidence."
        ),
    ),
)


def marketing_agent_contract_specs() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in MARKETING_AGENT_CONTRACT_SPECS]


def get_marketing_agent_contract_spec(agent_type: str) -> dict[str, Any]:
    normalized = _normalize_agent_type(agent_type)
    for spec in MARKETING_AGENT_CONTRACT_SPECS:
        if normalized == spec.agent_type or normalized in spec.aliases:
            return spec.to_dict()
    return {
        "agent_type": normalized,
        "maturity": "unknown",
        "production_ready": False,
        "actions": [],
        "surface": "unknown",
        "blocker": "Unknown CMO marketing agent surface.",
        "aliases": [],
        "contract_version": CMO_AGENT_CONTRACT_VERSION,
    }


def build_marketing_agent_contract_output(
    agent_type: str,
    action: str,
    *,
    result: Any | None = None,
    task_input: dict[str, Any] | None = None,
    policy_result: dict[str, Any] | None = None,
    audit_ref: str | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    workflow_mode: str = "shadow",
    connector_ready: bool = True,
    external_write_confirmation_status: str | None = None,
) -> dict[str, Any]:
    """Return a stable CMO agent contract output for test and readiness checks."""

    spec = get_marketing_agent_contract_spec(agent_type)
    canonical_agent_type = str(spec["agent_type"])
    output = _result_output(result)
    status = _result_status(result, output)
    confidence = _result_confidence(result, output)
    blocked_reasons = list(output.get("blocked_reasons") or [])
    degraded_reasons = list(output.get("degraded_reasons") or [])
    write_required = _external_write_required(action, output)
    write_status = (
        external_write_confirmation_status
        or str(output.get("external_write_confirmation_status") or "")
        or ("not_required" if not write_required else "write_unconfirmed")
    )
    policy = policy_result or output.get("policy_result") or _default_policy_result(
        action,
        write_required=write_required,
        workflow_mode=workflow_mode,
    )

    if spec["maturity"] in {"stub", "unavailable", "unknown"}:
        blocked_reasons.append(str(spec.get("blocker") or "Agent is not production-ready."))
        status = str(spec["maturity"])
        confidence = 0.0
        write_status = "not_applicable"

    if not connector_ready:
        degraded_reasons.append("Required connector is unavailable or degraded.")

    if write_required and write_status != "write_confirmed":
        blocked_reasons.append("External write is not confirmed and cannot be treated as complete.")

    final_audit_ref = audit_ref or str(output.get("audit_ref") or "")
    if spec["maturity"] not in {"stub", "unavailable", "unknown"} and not final_audit_ref:
        blocked_reasons.append("Decision audit evidence is missing.")

    normalized = {
        "agent_type": canonical_agent_type,
        "action": action,
        "maturity": spec["maturity"],
        "production_ready": bool(spec["production_ready"]),
        "status": status,
        "confidence": confidence,
        "rationale": _rationale(result, output, spec),
        "recommended_actions": _recommended_actions(output),
        "source_refs": source_refs if source_refs is not None else _source_refs(result, output),
        "policy_result": policy,
        "approval_required": _approval_required(result, output, policy),
        "hitl_required": _hitl_required(result, output),
        "audit_ref": final_audit_ref or None,
        "degraded_reasons": degraded_reasons,
        "blocked_reasons": _dedupe(blocked_reasons),
        "external_write_confirmation_status": write_status,
        "external_writes_completed": write_required and write_status == "write_confirmed",
        "shadow_read_only": workflow_mode in {"shadow", "draft", "internal", "internal_only"},
        "task_input": task_input or {},
        "contract_version": CMO_AGENT_CONTRACT_VERSION,
    }
    return normalized


def contract_has_required_shape(contract_output: dict[str, Any]) -> bool:
    return all(key in contract_output for key in REQUIRED_AGENT_CONTRACT_KEYS)


def _normalize_agent_type(agent_type: str) -> str:
    return str(agent_type or "").strip().lower()


def _result_output(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        maybe_output = result.get("output")
        return maybe_output if isinstance(maybe_output, dict) else result
    maybe_output = getattr(result, "output", None)
    return maybe_output if isinstance(maybe_output, dict) else {}


def _result_status(result: Any | None, output: dict[str, Any]) -> str:
    return str(getattr(result, "status", None) or output.get("status") or "unknown")


def _result_confidence(result: Any | None, output: dict[str, Any]) -> float:
    raw = getattr(result, "confidence", None)
    if raw is None:
        raw = output.get("confidence", 0.0)
    try:
        return max(0.0, min(float(raw), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _rationale(result: Any | None, output: dict[str, Any], spec: dict[str, Any]) -> str:
    direct = output.get("rationale") or output.get("explanation")
    if direct:
        return str(direct)
    trace = getattr(result, "reasoning_trace", None) or []
    if trace:
        return str(trace[-1])
    return str(spec.get("blocker") or "CMO agent contract output.")


def _recommended_actions(output: dict[str, Any]) -> list[Any]:
    for key in ("recommended_actions", "actions_taken", "recommendations"):
        value = output.get(key)
        if isinstance(value, list):
            return value
    return []


def _source_refs(result: Any | None, output: dict[str, Any]) -> list[dict[str, Any]]:
    refs = output.get("source_refs")
    if isinstance(refs, list):
        return [ref for ref in refs if isinstance(ref, dict)]
    tool_calls = getattr(result, "tool_calls", None) or []
    return [
        {"kind": "tool_call", "tool_name": call.tool_name, "status": call.status}
        for call in tool_calls
    ]


def _default_policy_result(
    action: str,
    *,
    write_required: bool,
    workflow_mode: str,
) -> dict[str, Any]:
    if workflow_mode in {"shadow", "draft", "internal", "internal_only"} and write_required:
        return {
            "decision": "read_only_only",
            "reason": "Shadow/draft/internal CMO workflow cannot execute external writes.",
        }
    if write_required:
        return {
            "decision": "requires_approval",
            "reason": "Customer-facing marketing write requires approval.",
        }
    return {
        "decision": "allowed",
        "reason": f"Action {action} is read-only or recommendation-only.",
    }


def _approval_required(
    result: Any | None,
    output: dict[str, Any],
    policy: dict[str, Any],
) -> bool:
    decision = str(policy.get("decision") or "")
    return (
        bool(output.get("approval_required"))
        or _hitl_required(result, output)
        or decision in {"requires_approval", "requires_escalation", "read_only_only"}
    )


def _hitl_required(result: Any | None, output: dict[str, Any]) -> bool:
    return bool(output.get("hitl_required")) or getattr(result, "status", None) == "hitl_triggered"


def _external_write_required(action: str, output: dict[str, Any]) -> bool:
    if output.get("external_write_confirmation_status") == "not_required":
        return False
    if output.get("external_write_required") is True:
        return True
    if action in CUSTOMER_FACING_WRITE_ACTIONS:
        return True
    return bool(output.get("actions_taken")) and any(
        str(item.get("action") or "") in {"paused", "scaled_up", "published", "sent"}
        for item in output.get("actions_taken", [])
        if isinstance(item, dict)
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
