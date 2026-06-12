"""Machine-checkable CMO marketing policy manifest.

CMO-6.1 makes marketing autonomy policy explicit. The default manifest is
conservative for real companies: customer-facing writes require approval,
crisis/legal/comparative claims escalate, destructive actions are blocked, and
shadow mode remains read-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from core.marketing.decision_audit import build_policy_decision_audit
from core.marketing.escalation_matrix import evaluate_marketing_escalation

POLICY_DECISIONS = (
    "allowed",
    "blocked",
    "requires_approval",
    "requires_escalation",
    "read_only_only",
    "missing_policy",
)

ACTIVE_MODES = {"active", "prod", "production"}
SHADOW_MODES = {"shadow", "draft", "internal", "internal_only", "recommendation", "simulation"}

READ_ONLY_ACTIONS = {
    "account_deal_health_summary",
    "account_health_summary",
    "aggregate_engagement",
    "aggregate_intent",
    "aggregate_weekly_metrics",
    "analyze",
    "analyze_pipeline_velocity",
    "churn_risk_signal_extraction",
    "compile_weekly_digest",
    "create_approval",
    "create_draft",
    "create_internal_approval",
    "create_plan",
    "detect_crisis",
    "detect_negative_spike",
    "draft",
    "draft_social_post",
    "extract_churn_signals",
    "funnel_conversion_analysis",
    "generate_content_calendar",
    "generate",
    "generate_campaign_assets",
    "generate_content",
    "generate_weekly_calendar",
    "generate_weekly_report",
    "get_keyword_rankings",
    "get_sentiment",
    "lead_scoring_refresh",
    "mention_aggregation",
    "get_weekly_metrics",
    "get_weekly_engagement",
    "identify_content_gaps",
    "keyword_gap_analysis",
    "monitor_mentions",
    "optimize_content",
    "pipeline_velocity_analysis",
    "promote_to_sql",
    "recommend_response_playbook",
    "recommend_segments",
    "optimize_posting_schedule",
    "prioritize_technical_issues",
    "query_target_accounts",
    "recommend",
    "recommend_next_best_action",
    "refresh_lead_scores",
    "ranking_delta_computation",
    "recommendation_bundling",
    "score_accounts",
    "score_icp_fit",
    "score_intent_heat",
    "segment_recommendation",
    "seo_sprint_planning",
    "score_lead",
    "segment_list",
    "simulate",
    "sql_promotion_criteria",
    "validate_account_csv",
    "triage_engagement",
    "classify_reply_risk",
    "change_confidence_score",
    "duplicate_change_suppression",
    "evaluate_alert_thresholds",
    "extract_win_loss_signals",
    "feature_capability_diffing",
    "technical_issue_prioritization",
    "weekly_benchmark",
    "weekly_market_snapshot",
    "normalize_competitor_profiles",
    "positioning_recommendation",
    "pricing_change_detection",
}

CUSTOMER_FACING_WRITE_ACTIONS = {
    "activate_campaign",
    "add_to_drip",
    "apply_redirect",
    "bulk_crm_update",
    "change_segment_membership",
    "change_target_accounts",
    "create_abm_campaign",
    "create_campaign",
    "create_linkedin_campaign",
    "launch_abm_campaign",
    "launch_campaign",
    "launch_competitive_campaign",
    "manage_campaign",
    "mutate_ad_budget",
    "pause_campaign",
    "promote_lifecycle_stage",
    "publish",
    "publish_brand_response",
    "publish_competitive_response",
    "publish_landing_page",
    "publish_post",
    "publish_seo_change",
    "publish_to_wordpress",
    "public_response",
    "reply_to_mention",
    "schedule_campaign_posts",
    "schedule_content",
    "schedule_content_promotion",
    "schedule_post",
    "send",
    "send_email",
    "send_to_segment",
    "send_winner",
    "setup",
    "setup_google_campaign",
    "setup_meta_campaign",
    "spend",
    "start_nurture_sequence",
    "sync_target_accounts",
    "target_account_list_change",
    "submit_url_to_index",
    "update_ad_budget",
    "update_canonical_tag",
    "update_crm",
    "update_landing_page",
    "update_lead_scores",
    "update_lifecycle_stage",
    "update_page_metadata",
    "update_robots_txt",
    "update_segment_membership",
    "update_sitemap",
    "update_target_accounts",
    "write_external",
}

BUDGET_ACTIONS = {
    "activate_campaign",
    "create_abm_campaign",
    "create_campaign",
    "create_linkedin_campaign",
    "launch_abm_campaign",
    "launch_campaign",
    "launch_competitive_campaign",
    "mutate_ad_budget",
    "set_abm_budget",
    "setup_google_campaign",
    "setup_meta_campaign",
    "spend",
    "update_ad_budget",
}

CRISIS_ACTIONS = {
    "crisis_response",
    "detect_crisis",
    "publish_brand_response",
    "public_response",
}

HIGH_RISK_CLAIM_ACTIONS = {
    "brand_claim",
    "claims_review",
    "comparative_claim",
    "compliance_claim",
    "high_risk_copy",
    "legal_claim",
    "pricing_claim",
    "regulated_claim",
    "sensitive_claim",
}

TARGET_ACCOUNT_ACTIONS = {
    "change_target_accounts",
    "query_target_accounts",
    "sync_target_accounts",
    "target_account_list_change",
    "update_target_accounts",
}

DISALLOWED_ACTIONS = {
    "bulk_delete_audience",
    "bulk_unsubscribe",
    "delete_audience",
    "delete_campaign",
    "delete_contact_list",
    "delete_crm_records",
    "disable_tracking",
    "purge_list",
    "remove_all_budget_caps",
    "write_external_without_confirmation",
}

DEFAULT_REQUIRED_AUDIT = {
    "approval": ("approval_record", "policy_decision", "workflow_run", "actor"),
    "escalation": (
        "approval_record",
        "escalation_record",
        "policy_decision",
        "workflow_run",
        "actor",
    ),
    "blocked": ("policy_decision", "workflow_run", "actor"),
    "allowed": ("policy_decision", "workflow_run", "actor"),
}


@dataclass(frozen=True)
class MarketingPolicyRule:
    rule_id: str
    description: str
    decision: str
    actions: tuple[str, ...] = ()
    required_approver_role: str | None = None
    required_escalation_role: str | None = None
    required_audit_evidence: tuple[str, ...] = ()
    next_action_cta: str = "none"
    reason: str = ""
    threshold: int | float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "decision": self.decision,
            "actions": list(self.actions),
            "required_approver_role": self.required_approver_role,
            "required_escalation_role": self.required_escalation_role,
            "required_audit_evidence": list(self.required_audit_evidence),
            "next_action_cta": self.next_action_cta,
            "reason": self.reason,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class MarketingPolicyManifest:
    policy_id: str
    version: str
    rules: tuple[MarketingPolicyRule, ...]
    allowed_autonomous_actions: tuple[str, ...]
    disallowed_autonomous_actions: tuple[str, ...]
    budget_thresholds: dict[str, int]
    audience_size_threshold: int
    target_account_change_threshold: int
    high_risk_copy_categories: tuple[str, ...]
    region_legal_constraints: dict[str, tuple[str, ...]]
    required_approval_roles: tuple[str, ...]
    required_audit_evidence_classes: tuple[str, ...]
    shadow_mode_allowances: tuple[str, ...]
    active_mode_allowances: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "rules": [rule.to_dict() for rule in self.rules],
            "allowed_autonomous_actions": list(self.allowed_autonomous_actions),
            "disallowed_autonomous_actions": list(self.disallowed_autonomous_actions),
            "budget_thresholds": dict(self.budget_thresholds),
            "audience_size_threshold": self.audience_size_threshold,
            "target_account_change_threshold": self.target_account_change_threshold,
            "high_risk_copy_categories": list(self.high_risk_copy_categories),
            "region_legal_constraints": {
                key: list(values)
                for key, values in self.region_legal_constraints.items()
            },
            "required_approval_roles": list(self.required_approval_roles),
            "required_audit_evidence_classes": list(self.required_audit_evidence_classes),
            "shadow_mode_allowances": list(self.shadow_mode_allowances),
            "active_mode_allowances": list(self.active_mode_allowances),
        }


DEFAULT_MARKETING_POLICY_MANIFEST = MarketingPolicyManifest(
    policy_id="cmo_default_marketing_policy",
    version="2026-05-23.cmo-6.1",
    rules=(
        MarketingPolicyRule(
            rule_id="cmo_forbidden_destructive_actions",
            description="Destructive or irreversible external marketing actions are blocked by default.",
            decision="blocked",
            actions=tuple(sorted(DISALLOWED_ACTIONS)),
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["blocked"],
            next_action_cta="remove_destructive_action",
            reason="Destructive or irreversible marketing action is disallowed by policy.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_crisis_public_response_escalation",
            description="Crisis and public-response actions require executive escalation.",
            decision="requires_escalation",
            actions=tuple(sorted(CRISIS_ACTIONS)),
            required_escalation_role="ceo",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["escalation"],
            next_action_cta="escalate_crisis_response_to_exec",
            reason="Crisis or public response requires executive escalation.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_high_risk_claims_review",
            description="Pricing, legal, compliance, comparative, and sensitive claims require review.",
            decision="requires_escalation",
            required_escalation_role="legal",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["escalation"],
            next_action_cta="request_legal_or_compliance_review",
            reason="High-risk marketing copy or claim requires legal/compliance review.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_budget_threshold_approval",
            description="Budget increases above channel threshold require approval.",
            decision="requires_approval",
            actions=tuple(sorted(BUDGET_ACTIONS)),
            required_approver_role="cmo",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["approval"],
            next_action_cta="request_budget_approval",
            reason="Budget change exceeds the configured approval threshold.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_audience_threshold_approval",
            description="Large audience or list-size changes require approval.",
            decision="requires_approval",
            required_approver_role="cmo",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["approval"],
            next_action_cta="request_audience_approval",
            reason="Audience/list size exceeds the configured approval threshold.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_target_account_threshold_approval",
            description="Target account list changes above threshold require approval.",
            decision="requires_approval",
            actions=tuple(sorted(TARGET_ACCOUNT_ACTIONS)),
            required_approver_role="revops_lead",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["approval"],
            next_action_cta="request_target_account_approval",
            reason="Target account list change exceeds the configured threshold.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_publish_send_launch_approval",
            description="Customer-facing publish, send, launch, spend, and update actions require approval.",
            decision="requires_approval",
            actions=tuple(sorted(CUSTOMER_FACING_WRITE_ACTIONS)),
            required_approver_role="cmo",
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["approval"],
            next_action_cta="request_marketing_approval",
            reason="Customer-facing marketing write requires approval.",
        ),
        MarketingPolicyRule(
            rule_id="cmo_read_only_autonomy",
            description="Read-only analysis, recommendation, drafting, and simulation can run autonomously.",
            decision="allowed",
            actions=tuple(sorted(READ_ONLY_ACTIONS)),
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["allowed"],
            next_action_cta="none",
            reason="Read-only marketing action is allowed by policy.",
        ),
    ),
    allowed_autonomous_actions=tuple(sorted(READ_ONLY_ACTIONS)),
    disallowed_autonomous_actions=tuple(sorted(DISALLOWED_ACTIONS)),
    budget_thresholds={
        "default": 500,
        "google_ads": 1000,
        "meta_ads": 1000,
        "linkedin_ads": 1500,
        "email": 250,
        "cms": 0,
        "social": 250,
    },
    audience_size_threshold=50000,
    target_account_change_threshold=25,
    high_risk_copy_categories=(
        "pricing",
        "legal",
        "compliance",
        "comparative",
        "competitor",
        "regulated_industry",
        "sensitive_claim",
    ),
    region_legal_constraints={
        "eu": ("privacy", "gdpr", "regulated_claims"),
        "in": ("privacy", "regulated_claims"),
        "us": ("pricing", "regulated_claims"),
    },
    required_approval_roles=("cmo", "content_lead", "legal", "revops_lead", "ceo"),
    required_audit_evidence_classes=(
        "approval_record",
        "audit_reference",
        "connector_confirmation",
        "escalation_record",
        "external_object_id",
        "policy_decision",
        "workflow_run",
    ),
    shadow_mode_allowances=("recommend", "draft", "simulate", "create_internal_approval"),
    active_mode_allowances=("approved_external_write", "confirmed_external_write"),
)


def default_marketing_policy_manifest() -> dict[str, Any]:
    """Return a serializable copy of the default marketing policy manifest."""

    return DEFAULT_MARKETING_POLICY_MANIFEST.to_dict()


def load_marketing_policy_manifest(
    source: Mapping[str, Any] | None = None,
    *,
    use_default: bool = True,
) -> dict[str, Any] | None:
    """Load a manifest from workflow/config source or return the default.

    Passing an explicit disabled flag or an empty explicit manifest returns
    ``None`` so callers can test fail-closed missing-policy behavior.
    """

    if _manifest_disabled(source):
        return None
    candidate = _manifest_candidate(source)
    if candidate is None:
        return default_marketing_policy_manifest() if use_default else None
    if not candidate:
        return None
    manifest = _normalize_manifest(candidate)
    if not manifest.get("policy_id") or not manifest.get("version"):
        return None
    return manifest


def evaluate_marketing_policy(
    context: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
    *,
    use_default: bool = True,
) -> dict[str, Any]:
    """Evaluate a marketing workflow/action context against the manifest."""

    ctx = _normalize_context(context)
    manifest_source = manifest if manifest is not None else ctx
    loaded = load_marketing_policy_manifest(manifest_source, use_default=use_default)
    if loaded is None:
        return _missing_policy_decision(ctx)

    policy_id = str(loaded.get("policy_id"))
    version = str(loaded.get("version"))
    action = ctx["action"]
    mode = ctx["workflow_mode"]

    if mode in SHADOW_MODES and (ctx["external_write_required"] or action in CUSTOMER_FACING_WRITE_ACTIONS):
        return _decision(
            policy_id=policy_id,
            version=version,
            matched_rules=[_rule_dict(loaded, "cmo_shadow_read_only")],
            decision="read_only_only",
            reason="Shadow, draft, and internal marketing workflows cannot execute external writes.",
            required_approver_role=None,
            required_escalation_role=None,
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["blocked"],
            affected_workflow=ctx["workflow_id"],
            affected_action=action,
            next_action_cta="convert_to_recommendation_or_draft",
        )

    blocked_rule = _manifest_rule(loaded, "cmo_forbidden_destructive_actions")
    if action in _string_set(loaded.get("disallowed_autonomous_actions")) or action in DISALLOWED_ACTIONS:
        return _decision_from_rule(loaded, blocked_rule, ctx)

    crisis_rule = _manifest_rule(loaded, "cmo_crisis_public_response_escalation")
    if action in CRISIS_ACTIONS or ctx["crisis_response"] or ctx["public_response"]:
        return _decision_from_rule(loaded, crisis_rule, ctx)

    high_risk_rule = _manifest_rule(loaded, "cmo_high_risk_claims_review")
    if _has_high_risk_claim(ctx):
        return _decision_from_rule(loaded, high_risk_rule, ctx)

    region_rule = high_risk_rule
    if _matches_region_constraint(ctx, loaded):
        return _decision_from_rule(
            loaded,
            {
                **region_rule,
                "rule_id": "cmo_region_legal_constraints",
                "reason": "Regional legal constraints require legal/compliance review.",
                "next_action_cta": "request_regional_legal_review",
            },
            ctx,
        )

    budget_rule = _manifest_rule(loaded, "cmo_budget_threshold_approval")
    if _budget_change_requires_approval(ctx, loaded):
        return _decision_from_rule(loaded, budget_rule, ctx)

    audience_rule = _manifest_rule(loaded, "cmo_audience_threshold_approval")
    if _audience_requires_approval(ctx, loaded):
        return _decision_from_rule(loaded, audience_rule, ctx)

    target_rule = _manifest_rule(loaded, "cmo_target_account_threshold_approval")
    if _target_accounts_require_approval(ctx, loaded):
        return _decision_from_rule(loaded, target_rule, ctx)

    write_rule = _manifest_rule(loaded, "cmo_publish_send_launch_approval")
    if action in CUSTOMER_FACING_WRITE_ACTIONS or ctx["customer_facing"] or ctx["external_write_required"]:
        return _decision_from_rule(loaded, write_rule, ctx)

    read_only_rule = _manifest_rule(loaded, "cmo_read_only_autonomy")
    allowed_actions = _string_set(loaded.get("allowed_autonomous_actions")) | READ_ONLY_ACTIONS
    if action in allowed_actions:
        return _decision_from_rule(loaded, read_only_rule, ctx)

    if mode in ACTIVE_MODES:
        return _decision(
            policy_id=policy_id,
            version=version,
            matched_rules=[],
            decision="missing_policy",
            reason="No marketing policy rule covers this active workflow action.",
            required_approver_role=None,
            required_escalation_role=None,
            required_audit_evidence=DEFAULT_REQUIRED_AUDIT["blocked"],
            affected_workflow=ctx["workflow_id"],
            affected_action=action,
            next_action_cta="add_marketing_policy_rule",
        )

    return _decision(
        policy_id=policy_id,
        version=version,
        matched_rules=[read_only_rule],
        decision="read_only_only",
        reason="Non-production action may continue only as read-only or shadow output.",
        required_approver_role=None,
        required_escalation_role=None,
        required_audit_evidence=DEFAULT_REQUIRED_AUDIT["allowed"],
        affected_workflow=ctx["workflow_id"],
        affected_action=action,
        next_action_cta="label_as_read_only_recommendation",
    )


def build_workflow_marketing_policy_status(
    workflow_key: str,
    actions: Iterable[Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project policy coverage for one CMO workflow activation row."""

    payload = payload if isinstance(payload, Mapping) else {}
    required_actions = _unique(_normalize_key(action) for action in actions)
    evaluations = [
        evaluate_marketing_policy(
            {
                **dict(payload),
                "workflow_id": workflow_key,
                "workflow_mode": "active",
                "action": action,
                "external_write_required": True,
                "customer_facing": True,
            },
            use_default=True,
        )
        for action in required_actions
    ]

    if not required_actions:
        status = "not_required"
        next_action = "none"
    elif any(item["decision"] == "missing_policy" for item in evaluations):
        status = "missing_policy"
        next_action = "configure_marketing_policy_manifest"
    elif any(item["decision"] in {"blocked", "read_only_only"} for item in evaluations):
        status = "blocked"
        next_action = "revise_workflow_or_policy"
    else:
        status = "ready"
        next_action = "none"

    return {
        "workflow_key": _normalize_key(workflow_key),
        "status": status,
        "required_actions": required_actions,
        "covered_actions": [
            item["affected_action"]
            for item in evaluations
            if item["decision"] in {"allowed", "requires_approval", "requires_escalation"}
        ],
        "missing_policy_actions": [
            item["affected_action"]
            for item in evaluations
            if item["decision"] == "missing_policy"
        ],
        "blocked_actions": [
            item["affected_action"]
            for item in evaluations
            if item["decision"] in {"blocked", "read_only_only"}
        ],
        "evaluations": evaluations,
        "next_action_cta": next_action,
    }


def build_marketing_policy_projection(
    sources: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build a KPI/API projection for the active marketing policy manifest."""

    manifest = None
    for source in sources or []:
        config = _config_dict(source)
        if _manifest_disabled(config):
            return {
                "marketing_policy_manifest": None,
                "marketing_policy_summary": summarize_marketing_policy_manifest(
                    {"marketing_policy_manifest_disabled": True}
                ),
            }
        candidate = load_marketing_policy_manifest(config, use_default=False)
        if candidate is not None:
            manifest = candidate
            break
    if manifest is None:
        manifest = default_marketing_policy_manifest()
    return {
        "marketing_policy_manifest": manifest,
        "marketing_policy_summary": summarize_marketing_policy_manifest(manifest),
    }


def summarize_marketing_policy_manifest(manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    loaded = load_marketing_policy_manifest(manifest, use_default=True)
    if loaded is None:
        return {
            "status": "missing_policy",
            "policy_id": None,
            "version": None,
            "rule_count": 0,
            "next_action_cta": "configure_marketing_policy_manifest",
        }
    rules = list(loaded.get("rules") or [])
    decisions = dict.fromkeys(POLICY_DECISIONS, 0)
    for rule in rules:
        decision = str(rule.get("decision") or "")
        if decision in decisions:
            decisions[decision] += 1
    return {
        "status": "ready",
        "policy_id": loaded.get("policy_id"),
        "version": loaded.get("version"),
        "rule_count": len(rules),
        "allowed_autonomous_actions": len(loaded.get("allowed_autonomous_actions") or []),
        "disallowed_autonomous_actions": len(loaded.get("disallowed_autonomous_actions") or []),
        "next_action_cta": "none",
        **decisions,
    }


def marketing_policy_allows_external_write(
    decision: Mapping[str, Any] | None,
    *,
    approval_satisfied: bool = False,
) -> bool:
    """Return true only when policy permits the active external write."""

    if not isinstance(decision, Mapping):
        return False
    outcome = str(decision.get("decision") or "")
    if outcome == "allowed":
        return True
    if outcome in {"requires_approval", "requires_escalation"}:
        return approval_satisfied
    return False


def _decision_from_rule(
    manifest: Mapping[str, Any],
    rule: Mapping[str, Any],
    ctx: Mapping[str, Any],
) -> dict[str, Any]:
    return _decision(
        policy_id=str(manifest.get("policy_id")),
        version=str(manifest.get("version")),
        matched_rules=[dict(rule)],
        decision=str(rule.get("decision") or "missing_policy"),
        reason=str(rule.get("reason") or rule.get("description") or ""),
        required_approver_role=_string_or_none(rule.get("required_approver_role")),
        required_escalation_role=_string_or_none(rule.get("required_escalation_role")),
        required_audit_evidence=_string_list(rule.get("required_audit_evidence")),
        affected_workflow=str(ctx.get("workflow_id") or ""),
        affected_action=str(ctx.get("action") or ""),
        next_action_cta=str(rule.get("next_action_cta") or "none"),
    )


def _decision(
    *,
    policy_id: str | None,
    version: str | None,
    matched_rules: list[Mapping[str, Any]],
    decision: str,
    reason: str,
    required_approver_role: str | None,
    required_escalation_role: str | None,
    required_audit_evidence: Iterable[str],
    affected_workflow: str,
    affected_action: str,
    next_action_cta: str,
) -> dict[str, Any]:
    normalized_decision = decision if decision in POLICY_DECISIONS else "missing_policy"
    matched = [dict(rule) for rule in matched_rules]
    escalation_decision = _escalation_for_policy_decision(
        normalized_decision,
        affected_workflow,
        affected_action,
        matched,
        required_escalation_role,
        required_approver_role,
        reason,
    )
    result = {
        "policy_id": policy_id,
        "version": version,
        "matched_rules": matched,
        "decision": normalized_decision,
        "reason": reason,
        "required_approver_role": required_approver_role,
        "required_escalation_role": required_escalation_role,
        "required_audit_evidence": list(required_audit_evidence),
        "affected_workflow": affected_workflow,
        "affected_action": affected_action,
        "next_action_cta": next_action_cta,
        "escalation_decision": escalation_decision,
        "escalation_evidence": escalation_decision.get("evidence") if escalation_decision else None,
    }
    audit_package = build_policy_decision_audit(result)
    result["decision_audit"] = audit_package
    result["decision_audit_ref"] = audit_package["audit_reference"]
    result["audit_reference"] = audit_package["audit_reference"]
    return result


def _escalation_for_policy_decision(
    decision: str,
    workflow_id: str,
    action: str,
    matched_rules: list[Mapping[str, Any]],
    required_escalation_role: str | None,
    required_approver_role: str | None,
    reason: str,
) -> dict[str, Any] | None:
    trigger_type = _policy_escalation_trigger(
        decision,
        action,
        matched_rules,
        required_escalation_role,
        required_approver_role,
    )
    if trigger_type is None:
        return None
    return evaluate_marketing_escalation(
        {
            "trigger_type": trigger_type,
            "workflow_id": workflow_id,
            "action": action,
            "reason": reason,
        }
    )


def _policy_escalation_trigger(
    decision: str,
    action: str,
    matched_rules: list[Mapping[str, Any]],
    required_escalation_role: str | None,
    required_approver_role: str | None,
) -> str | None:
    rule_ids = {_normalize_key(rule.get("rule_id")) for rule in matched_rules}
    if decision == "missing_policy":
        return "missing_policy"
    if "cmo_crisis_public_response_escalation" in rule_ids:
        return "crisis_public_response"
    if "cmo_high_risk_claims_review" in rule_ids or "cmo_region_legal_constraints" in rule_ids:
        if _normalize_key(required_escalation_role) in {"legal", "compliance"}:
            return "pricing_or_legal_claim"
        return "high_risk_copy"
    if "cmo_budget_threshold_approval" in rule_ids:
        return "budget_threshold_exceeded"
    if "cmo_target_account_threshold_approval" in rule_ids:
        return "target_account_change"
    if decision == "requires_escalation":
        if _normalize_key(required_escalation_role) in {"legal", "compliance"}:
            return "pricing_or_legal_claim"
        return "high_risk_copy"
    if decision == "requires_approval" and required_approver_role:
        return "approval_timeout"
    if action in DISALLOWED_ACTIONS:
        return "missing_policy"
    return None


def _missing_policy_decision(ctx: Mapping[str, Any]) -> dict[str, Any]:
    read_only_shadow = (
        str(ctx.get("workflow_mode") or "") in SHADOW_MODES
        and str(ctx.get("action") or "") in READ_ONLY_ACTIONS
        and not bool(ctx.get("external_write_required"))
    )
    return _decision(
        policy_id=None,
        version=None,
        matched_rules=[],
        decision="read_only_only" if read_only_shadow else "missing_policy",
        reason=(
            "Marketing policy manifest is missing; this can continue only as a "
            "read-only shadow recommendation."
            if read_only_shadow
            else "Marketing policy manifest is missing for this active/customer-facing action."
        ),
        required_approver_role=None,
        required_escalation_role=None,
        required_audit_evidence=DEFAULT_REQUIRED_AUDIT["blocked"],
        affected_workflow=str(ctx.get("workflow_id") or ""),
        affected_action=str(ctx.get("action") or ""),
        next_action_cta=(
            "label_as_read_only_recommendation"
            if read_only_shadow
            else "configure_marketing_policy_manifest"
        ),
    )


def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    context = context if isinstance(context, Mapping) else {}
    action = _normalize_key(
        context.get("action")
        or context.get("approval_action")
        or context.get("blocked_action")
        or "unknown_action"
    )
    mode = _normalize_key(
        context.get("workflow_mode")
        or context.get("mode")
        or context.get("configured_mode")
        or "active"
    )
    return {
        **dict(context),
        "action": action,
        "workflow_mode": mode,
        "workflow_id": _normalize_key(
            context.get("workflow_id")
            or context.get("workflow_key")
            or context.get("workflow")
        ),
        "channel": _normalize_key(context.get("channel") or context.get("connector_key") or "default"),
        "external_write_required": _truthy(
            context.get("external_write_required") or context.get("requires_external_write")
        ),
        "customer_facing": _truthy(context.get("customer_facing") or context.get("external")),
        "crisis_response": _truthy(context.get("crisis_response")),
        "public_response": _truthy(context.get("public_response")),
    }


def _normalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(manifest)
    normalized["rules"] = [
        dict(rule)
        for rule in normalized.get("rules", [])
        if isinstance(rule, Mapping)
    ]
    normalized.setdefault("allowed_autonomous_actions", list(READ_ONLY_ACTIONS))
    normalized.setdefault("disallowed_autonomous_actions", list(DISALLOWED_ACTIONS))
    normalized.setdefault("budget_thresholds", {"default": 500})
    normalized.setdefault("audience_size_threshold", 50000)
    normalized.setdefault("target_account_change_threshold", 25)
    normalized.setdefault("high_risk_copy_categories", [])
    normalized.setdefault("region_legal_constraints", {})
    return normalized


def _manifest_candidate(source: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(source, Mapping):
        return None
    for key in (
        "marketing_policy_manifest",
        "cmo_marketing_policy_manifest",
        "marketing_policy",
        "policy_manifest",
    ):
        value = source.get(key)
        if isinstance(value, Mapping):
            return value
    if source.get("policy_id") and source.get("version") and isinstance(source.get("rules"), list):
        return source
    return None


def _manifest_disabled(source: Mapping[str, Any] | None) -> bool:
    if not isinstance(source, Mapping):
        return False
    return any(
        _truthy(source.get(key))
        for key in (
            "marketing_policy_manifest_disabled",
            "cmo_marketing_policy_manifest_disabled",
            "policy_manifest_disabled",
        )
    )


def _manifest_rule(manifest: Mapping[str, Any], rule_id: str) -> dict[str, Any]:
    for rule in manifest.get("rules") or []:
        if isinstance(rule, Mapping) and str(rule.get("rule_id") or "") == rule_id:
            return dict(rule)
    return _rule_dict(manifest, rule_id)


def _rule_dict(manifest: Mapping[str, Any], rule_id: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "description": f"Implicit rule in {manifest.get('policy_id') or 'missing policy'}.",
        "decision": "missing_policy",
        "required_audit_evidence": list(DEFAULT_REQUIRED_AUDIT["blocked"]),
        "next_action_cta": "configure_marketing_policy_manifest",
        "reason": "Policy rule is missing.",
    }


def _has_high_risk_claim(ctx: Mapping[str, Any]) -> bool:
    if str(ctx.get("action") or "") in HIGH_RISK_CLAIM_ACTIONS:
        return True
    return any(
        _truthy(ctx.get(field))
        for field in (
            "brand_claim",
            "claims_review",
            "claims_review_required",
            "comparative_claim",
            "competitor_mention",
            "compliance_claim",
            "compliance_review_required",
            "high_risk",
            "high_risk_copy",
            "legal_claim",
            "legal_review_required",
            "pricing_claim",
            "regulated_claim",
            "sensitive_claim",
        )
    ) or bool(_string_set(ctx.get("high_risk_copy_categories")))


def _matches_region_constraint(ctx: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    region = _normalize_key(ctx.get("region") or ctx.get("country") or ctx.get("market"))
    if not region:
        return False
    constraints = manifest.get("region_legal_constraints")
    if not isinstance(constraints, Mapping):
        return False
    region_constraints = _string_set(constraints.get(region))
    if not region_constraints:
        return False
    if ctx.get("legal_claim") or ctx.get("regulated_claim") or ctx.get("compliance_claim"):
        return True
    categories = _string_set(ctx.get("high_risk_copy_categories"))
    return bool(categories & region_constraints)


def _budget_change_requires_approval(ctx: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    action = str(ctx.get("action") or "")
    budget_value = _number(
        ctx.get("budget_delta")
        or ctx.get("budget_increase")
        or ctx.get("spend_delta")
        or ctx.get("budget_amount")
        or ctx.get("daily_budget")
    )
    if budget_value <= 0 and action not in BUDGET_ACTIONS:
        return False
    thresholds = manifest.get("budget_thresholds")
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    channel = str(ctx.get("channel") or "default")
    threshold = _number(thresholds.get(channel) or thresholds.get("default") or 0)
    return budget_value > threshold


def _audience_requires_approval(ctx: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    size = _number(
        ctx.get("audience_size")
        or ctx.get("list_size")
        or ctx.get("recipient_count")
        or ctx.get("estimated_recipients")
    )
    threshold = _number(manifest.get("audience_size_threshold") or 0)
    return bool(size and threshold and size > threshold)


def _target_accounts_require_approval(ctx: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    action = str(ctx.get("action") or "")
    delta = _number(
        ctx.get("target_account_delta")
        or ctx.get("target_account_change_count")
        or ctx.get("account_list_change_count")
    )
    threshold = _number(manifest.get("target_account_change_threshold") or 0)
    return action in TARGET_ACCOUNT_ACTIONS and bool(delta and threshold and delta > threshold)


def _config_dict(config: Any | None) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    value = getattr(config, "config", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {_normalize_key(part) for part in value.split(",") if _normalize_key(part)}
    if isinstance(value, Iterable):
        return {_normalize_key(item) for item in value if _normalize_key(item)}
    return set()


def _string_list(value: Any) -> list[str]:
    return sorted(_string_set(value))


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
