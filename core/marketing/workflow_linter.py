"""Marketing workflow linter for CMO production-readiness gates.

CMO-5.1 validates marketing workflow definitions before execution. The linter
does not implement missing agents or vendor adapters; it reports whether a
workflow is safe to treat as production, target/demo/shadow, or out of scope.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.marketing.approval_timeouts import (
    has_timeout_policy_for_step,
    requires_approval_timeout_policy,
)
from core.marketing.decision_audit import (
    action_event_type,
    has_decision_audit_evidence_for_step,
)
from core.marketing.escalation_matrix import (
    escalation_triggers_for_action,
    missing_escalation_triggers_for_step,
)
from core.marketing.policy_manifest import evaluate_marketing_policy
from core.marketing.workflow_activation import EXTERNAL_WRITE_ACTIONS

SEVERITIES = ("error", "warning", "info")

PRODUCTION_MODES = {"active", "prod", "production"}
SHADOW_MODES = {"shadow"}
NON_PRODUCTION_MODES = {
    "demo",
    "draft",
    "internal",
    "internal_only",
    "recommendation",
    "simulation",
    "target",
}
READINESS_MODE_FIELDS = (
    "workflow_mode",
    "mode",
    "deployment_mode",
    "readiness",
    "capability_state",
    "maturity",
    "status",
)
MARKETING_WRITE_ACTION_HINTS = (
    "activate",
    "add_to_drip",
    "launch",
    "mutate",
    "publish",
    "schedule",
    "send",
    "setup",
    "spend",
    "start_nurture",
    "update_crm",
)

MARKETING_AGENT_STATES: dict[str, str] = {  # enterprise-gate: process-local-ok reason=static-marketing-agent-state-map
    "campaign_pilot": "production",
    "content_factory": "beta",
    "email_agent": "beta",
    "email_marketing": "beta",
    "brand_monitor": "beta",
    "seo_strategist": "beta",
    "crm_intelligence": "beta",
    "social_media": "beta",
    "abm": "beta",
    "abm_agent": "beta",
    "competitive_intel": "beta",
}

MARKETING_AGENT_ACTIONS: dict[str, set[str] | None] = {
    "campaign_pilot": {
        "activate_campaign",
        "aggregate_weekly_metrics",
        "compile_weekly_digest",
        "create",
        "create_campaign",
        "create_linkedin_campaign",
        "create_plan",
        "generate_weekly_report",
        "get_weekly_metrics",
        "launch_campaign",
        "manage_campaign",
        "mutate_ad_budget",
        "pause_campaign",
        "setup",
        "setup_google_campaign",
        "setup_meta_campaign",
        "update_ad_budget",
    },
    "content_factory": {
        "create_draft",
        "generate",
        "generate_campaign_assets",
        "generate_content",
        "generate_weekly_calendar",
        "publish",
        "publish_to_wordpress",
        "schedule_content",
    },
    "email_marketing": {
        "add_to_drip",
        "check_winner",
        "create_ab_test",
        "segment_list",
        "send_email",
        "send_to_segment",
        "send_winner",
        "start_nurture_sequence",
    },
    "email_agent": {
        "draft_email",
        "read_email",
        "search_email",
        "send_email",
    },
    "brand_monitor": {
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
    },
    "seo_strategist": {
        "apply_redirect",
        "analyze_keyword_gaps",
        "bundle_recommendations",
        "compute_ranking_deltas",
        "content_optimization_recommendation",
        "get_keyword_rankings",
        "identify_content_gaps",
        "keyword_gap_analysis",
        "optimize_content",
        "plan_seo_sprint",
        "prioritize_technical_issues",
        "publish_landing_page",
        "publish_seo_change",
        "publish_to_wordpress",
        "ranking_delta_computation",
        "recommendation_bundling",
        "seo_sprint_planning",
        "submit_url_to_index",
        "technical_issue_prioritization",
        "update_canonical_tag",
        "update_landing_page",
        "update_page_metadata",
        "update_robots_txt",
        "update_sitemap",
    },
    "crm_intelligence": {
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
    },
    "social_media": {
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
    },
    "abm": {
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
    },
    "abm_agent": {
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
    },
    "competitive_intel": {
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
    },
}

ACTION_CONNECTOR_REQUIREMENTS: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("campaign_pilot", "activate_campaign"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("campaign_pilot", "create_campaign"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("campaign_pilot", "create_linkedin_campaign"): {
        "categories": (),
        "keys": ("linkedin_ads",),
    },
    ("campaign_pilot", "launch_campaign"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("campaign_pilot", "mutate_ad_budget"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("campaign_pilot", "pause_campaign"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("campaign_pilot", "setup_google_campaign"): {
        "categories": (),
        "keys": ("google_ads",),
    },
    ("campaign_pilot", "setup_meta_campaign"): {
        "categories": (),
        "keys": ("meta_ads",),
    },
    ("campaign_pilot", "update_ad_budget"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("content_factory", "publish"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("content_factory", "publish_to_wordpress"): {
        "categories": (),
        "keys": ("wordpress",),
    },
    ("content_factory", "schedule_content"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("email_marketing", "add_to_drip"): {
        "categories": ("Email",),
        "keys": (),
    },
    ("email_marketing", "send_email"): {
        "categories": ("Email",),
        "keys": (),
    },
    ("email_marketing", "send_to_segment"): {
        "categories": ("Email",),
        "keys": (),
    },
    ("email_marketing", "send_winner"): {
        "categories": ("Email",),
        "keys": (),
    },
    ("email_marketing", "start_nurture_sequence"): {
        "categories": ("Email",),
        "keys": (),
    },
    ("crm_intelligence", "score_lead"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "analyze_pipeline_velocity"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "pipeline_velocity_analysis"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "funnel_conversion_analysis"): {
        "categories": ("CRM", "Analytics"),
        "keys": (),
    },
    ("crm_intelligence", "lead_scoring_refresh"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "refresh_lead_scores"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "extract_churn_signals"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "churn_risk_signal_extraction"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "recommend_segments"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "segment_recommendation"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "sql_promotion_criteria"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "promote_to_sql"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "account_health_summary"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "account_deal_health_summary"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "update_lead_scores"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "update_lifecycle_stage"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "promote_lifecycle_stage"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "change_segment_membership"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "update_segment_membership"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "change_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("crm_intelligence", "bulk_crm_update"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("crm_intelligence", "update_crm"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("social_media", "schedule_campaign_posts"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("social_media", "schedule_content_promotion"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("social_media", "schedule_post"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("social_media", "publish_post"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("social_media", "reply_to_mention"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("abm", "query_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm", "score_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm", "aggregate_intent"): {
        "categories": ("ABM",),
        "keys": (),
    },
    ("abm", "aggregate_engagement"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("abm", "sync_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm", "target_account_list_change"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm", "update_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm", "launch_abm_campaign"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("abm", "create_abm_campaign"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("abm", "set_abm_budget"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("abm_agent", "query_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm_agent", "score_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm_agent", "aggregate_intent"): {
        "categories": ("ABM",),
        "keys": (),
    },
    ("abm_agent", "aggregate_engagement"): {
        "categories": ("CRM",),
        "keys": (),
    },
    ("abm_agent", "sync_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm_agent", "target_account_list_change"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm_agent", "update_target_accounts"): {
        "categories": ("CRM", "ABM"),
        "keys": (),
    },
    ("abm_agent", "launch_abm_campaign"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("abm_agent", "create_abm_campaign"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("abm_agent", "set_abm_budget"): {
        "categories": ("Ads", "ABM"),
        "keys": (),
    },
    ("competitive_intel", "weekly_market_snapshot"): {
        "categories": ("Brand", "SEO"),
        "keys": (),
    },
    ("competitive_intel", "weekly_benchmark"): {
        "categories": ("Brand", "SEO"),
        "keys": (),
    },
    ("competitive_intel", "pricing_change_detection"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("competitive_intel", "feature_capability_diffing"): {
        "categories": ("Brand", "SEO"),
        "keys": (),
    },
    ("competitive_intel", "publish_competitive_response"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("competitive_intel", "public_response"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("competitive_intel", "launch_competitive_campaign"): {
        "categories": ("Ads",),
        "keys": (),
    },
    ("brand_monitor", "aggregate_mentions"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "detect_crisis"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "get_sentiment"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "mention_aggregation"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "monitor_mentions"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "sentiment_trend"): {
        "categories": ("Brand",),
        "keys": (),
    },
    ("brand_monitor", "crisis_response"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("brand_monitor", "public_response"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("brand_monitor", "publish_brand_response"): {
        "categories": ("Social",),
        "keys": (),
    },
    ("seo_strategist", "analyze_keyword_gaps"): {
        "categories": ("SEO",),
        "keys": (),
    },
    ("seo_strategist", "get_keyword_rankings"): {
        "categories": ("SEO",),
        "keys": (),
    },
    ("seo_strategist", "identify_content_gaps"): {
        "categories": ("SEO", "Analytics"),
        "keys": (),
    },
    ("seo_strategist", "keyword_gap_analysis"): {
        "categories": ("SEO",),
        "keys": (),
    },
    ("seo_strategist", "ranking_delta_computation"): {
        "categories": ("SEO",),
        "keys": (),
    },
    ("seo_strategist", "technical_issue_prioritization"): {
        "categories": ("SEO", "CMS"),
        "keys": (),
    },
    ("seo_strategist", "content_optimization_recommendation"): {
        "categories": ("SEO", "CMS"),
        "keys": (),
    },
    ("seo_strategist", "seo_sprint_planning"): {
        "categories": ("SEO", "Analytics", "CMS"),
        "keys": (),
    },
    ("seo_strategist", "apply_redirect"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "publish_landing_page"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "publish_seo_change"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "publish_to_wordpress"): {
        "categories": (),
        "keys": ("wordpress",),
    },
    ("seo_strategist", "submit_url_to_index"): {
        "categories": (),
        "keys": ("google_search_console",),
    },
    ("seo_strategist", "update_canonical_tag"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "update_landing_page"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "update_page_metadata"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "update_robots_txt"): {
        "categories": ("CMS",),
        "keys": (),
    },
    ("seo_strategist", "update_sitemap"): {
        "categories": ("CMS",),
        "keys": (),
    },
}


@dataclass(frozen=True)
class MarketingWorkflowLintFinding:
    workflow_file: str | None
    workflow_id: str
    workflow_name: str
    step_id: str | None
    step_name: str | None
    severity: str
    code: str
    message: str
    suggested_fix: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "workflow_file": self.workflow_file,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "step_id": self.step_id,
            "step_name": self.step_name,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
        }


@dataclass(frozen=True)
class MarketingWorkflowLintResult:
    workflow_file: str | None
    workflow_id: str
    workflow_name: str
    in_scope: bool
    findings: tuple[MarketingWorkflowLintFinding, ...]

    @property
    def has_errors(self) -> bool:
        return any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[MarketingWorkflowLintFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    @property
    def warnings(self) -> tuple[MarketingWorkflowLintFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_file": self.workflow_file,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "in_scope": self.in_scope,
            "has_errors": self.has_errors,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def lint_marketing_workflow_file(
    path: str | Path,
    *,
    connector_contracts: Iterable[dict[str, Any]] | None = None,
    marketing_policy_manifest: dict[str, Any] | None = None,
    marketing_escalation_matrix: dict[str, Any] | None = None,
) -> MarketingWorkflowLintResult:
    """Load and lint one workflow YAML file."""

    workflow_path = Path(path)
    with workflow_path.open(encoding="utf-8") as handle:
        definition = yaml.safe_load(handle)
    return lint_marketing_workflow(
        definition,
        workflow_file=str(workflow_path),
        connector_contracts=connector_contracts,
        marketing_policy_manifest=marketing_policy_manifest,
        marketing_escalation_matrix=marketing_escalation_matrix,
    )


def lint_marketing_workflow_paths(
    paths: Iterable[str | Path],
    *,
    connector_contracts: Iterable[dict[str, Any]] | None = None,
    marketing_policy_manifest: dict[str, Any] | None = None,
    marketing_escalation_matrix: dict[str, Any] | None = None,
) -> list[MarketingWorkflowLintResult]:
    """Lint a list of workflow YAML files."""

    return [
        lint_marketing_workflow_file(
            path,
            connector_contracts=connector_contracts,
            marketing_policy_manifest=marketing_policy_manifest,
            marketing_escalation_matrix=marketing_escalation_matrix,
        )
        for path in paths
    ]


def lint_marketing_workflow(
    definition: dict[str, Any] | str,
    *,
    workflow_file: str | None = None,
    connector_contracts: Iterable[dict[str, Any]] | None = None,
    marketing_policy_manifest: dict[str, Any] | None = None,
    marketing_escalation_matrix: dict[str, Any] | None = None,
) -> MarketingWorkflowLintResult:
    """Lint a parsed workflow definition or YAML string."""

    if isinstance(definition, str):
        definition = yaml.safe_load(definition)
    if not isinstance(definition, dict):
        definition = {}

    workflow_id = _workflow_id(definition, workflow_file)
    workflow_name = _string_or_none(definition.get("name")) or workflow_id
    workflow_mode = _workflow_mode(definition)
    contracts = tuple(connector_contracts or ())
    findings: list[MarketingWorkflowLintFinding] = []

    steps = list(_iter_steps(definition.get("steps") or []))
    in_scope = _is_marketing_workflow(definition, steps)
    if not in_scope:
        findings.append(
            _finding(
                workflow_file,
                workflow_id,
                workflow_name,
                None,
                None,
                "info",
                "marketing_workflow_out_of_scope",
                "Workflow is not a marketing workflow; marketing lint rules were not applied.",
                "Run the relevant domain linter for this workflow.",
            )
        )
        return MarketingWorkflowLintResult(
            workflow_file=workflow_file,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            in_scope=False,
            findings=tuple(findings),
        )

    for step, parent_id in steps:
        if step.get("type", "agent") != "agent":
            continue
        findings.extend(
            _lint_agent_step(
                step,
                parent_id=parent_id,
                workflow_file=workflow_file,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                workflow_mode=workflow_mode,
                connector_contracts=contracts,
                marketing_policy_manifest=marketing_policy_manifest,
                marketing_escalation_matrix=marketing_escalation_matrix,
            )
        )

    return MarketingWorkflowLintResult(
        workflow_file=workflow_file,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        in_scope=True,
        findings=tuple(findings),
    )


def _lint_agent_step(
    step: dict[str, Any],
    *,
    parent_id: str | None,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    workflow_mode: str,
    connector_contracts: tuple[dict[str, Any], ...],
    marketing_policy_manifest: dict[str, Any] | None,
    marketing_escalation_matrix: dict[str, Any] | None,
) -> list[MarketingWorkflowLintFinding]:
    step_id = _string_or_none(step.get("id")) or "unknown_step"
    step_name = _string_or_none(step.get("name")) or step_id
    agent_type = _normalize_key(step.get("agent_type") or step.get("agent"))
    action = _normalize_key(step.get("action") or "process")
    step_mode = _workflow_mode(step, default=workflow_mode)
    findings: list[MarketingWorkflowLintFinding] = []

    if not agent_type:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_agent_type_missing",
                "Marketing agent step is missing agent_type.",
                "Set agent_type to a known marketing agent or convert this step to a non-agent step.",
                parent_id=parent_id,
            )
        )
        return findings

    agent_state = MARKETING_AGENT_STATES.get(agent_type)
    if agent_state is None:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_agent_type_unknown",
                f"Unknown marketing agent_type '{agent_type}'.",
                "Use a registered marketing agent type or implement and register this agent before referencing it.",
                parent_id=parent_id,
            )
        )
        return findings

    allowed_actions = MARKETING_AGENT_ACTIONS.get(agent_type)
    if allowed_actions is not None and action not in allowed_actions:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_action_unknown",
                f"Action '{action}' is not declared for marketing agent '{agent_type}'.",
                "Use a declared action or add explicit action metadata and tests for this agent.",
                parent_id=parent_id,
            )
        )

    if agent_state in {"stub", "unavailable"}:
        severity = "error" if _is_production_mode(step_mode) else "warning"
        code = (
            "marketing_agent_unavailable_for_production"
            if agent_state == "unavailable"
            else "marketing_agent_stub_for_production"
        )
        non_prod_code = (
            "marketing_agent_unavailable_target_only"
            if agent_state == "unavailable"
            else "marketing_agent_stub_target_only"
        )
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                severity,
                code if severity == "error" else non_prod_code,
                (
                    f"Agent '{agent_type}' is {agent_state}; this cannot be treated "
                    f"as production/active CMO behavior."
                ),
                (
                    "Implement a first-class production marketing agent or mark the "
                    "workflow explicitly as target, demo, or shadow."
                ),
                parent_id=parent_id,
            )
        )
    elif agent_state == "beta" and _is_production_mode(step_mode):
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "warning",
                "marketing_agent_beta_in_production",
                f"Agent '{agent_type}' is beta; production workflows should treat this as degraded.",
                "Keep the workflow shadow/degraded or add production evidence before claiming full readiness.",
                parent_id=parent_id,
            )
        )

    required_keys, required_categories = _connector_requirements(agent_type, action, step)
    findings.extend(
        _lint_declared_connector_readiness(
            required_keys,
            required_categories,
            connector_contracts,
            workflow_file=workflow_file,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            step_id=step_id,
            step_name=step_name,
            step_mode=step_mode,
            parent_id=parent_id,
        )
    )

    if _is_external_write_step(agent_type, action, step):
        findings.extend(
            _lint_marketing_policy(
                action,
                step,
                workflow_file=workflow_file,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                step_id=step_id,
                step_name=step_name,
                step_mode=step_mode,
                parent_id=parent_id,
                marketing_policy_manifest=marketing_policy_manifest,
                external_write_required=True,
            )
        )
        findings.extend(
            _lint_external_write_step(
                required_keys,
                required_categories,
                connector_contracts,
                step,
                workflow_file=workflow_file,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                step_id=step_id,
                step_name=step_name,
                step_mode=step_mode,
                parent_id=parent_id,
            )
        )
    else:
        findings.extend(
            _lint_marketing_policy(
                action,
                step,
                workflow_file=workflow_file,
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                step_id=step_id,
                step_name=step_name,
                step_mode=step_mode,
                parent_id=parent_id,
                marketing_policy_manifest=marketing_policy_manifest,
                external_write_required=False,
            )
        )

    findings.extend(
        _lint_approval_timeout_policy(
            action,
            step,
            workflow_file=workflow_file,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            step_id=step_id,
            step_name=step_name,
            step_mode=step_mode,
            parent_id=parent_id,
        )
    )
    findings.extend(
        _lint_escalation_route(
            action,
            step,
            workflow_file=workflow_file,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            step_id=step_id,
            step_name=step_name,
            step_mode=step_mode,
            parent_id=parent_id,
            marketing_escalation_matrix=marketing_escalation_matrix,
            external_write_required=_is_external_write_step(agent_type, action, step),
        )
    )
    findings.extend(
        _lint_decision_audit_evidence(
            action,
            step,
            workflow_file=workflow_file,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            step_id=step_id,
            step_name=step_name,
            step_mode=step_mode,
            parent_id=parent_id,
            external_write_required=_is_external_write_step(agent_type, action, step),
        )
    )

    return findings


def _lint_marketing_policy(
    action: str,
    step: dict[str, Any],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
    marketing_policy_manifest: dict[str, Any] | None,
    external_write_required: bool,
) -> list[MarketingWorkflowLintFinding]:
    manifest = marketing_policy_manifest
    if manifest is None:
        for key in ("marketing_policy_manifest", "cmo_marketing_policy_manifest", "policy_manifest"):
            value = step.get(key)
            if isinstance(value, dict):
                manifest = value
                break
    context = {
        **step,
        "workflow_id": workflow_id,
        "workflow_mode": step_mode,
        "action": action,
        "external_write_required": external_write_required,
        "customer_facing": step.get("customer_facing", external_write_required),
    }
    decision = evaluate_marketing_policy(context, manifest=manifest, use_default=manifest is None)
    outcome = str(decision.get("decision") or "")

    if _is_production_mode(step_mode):
        if outcome == "missing_policy":
            return [
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_policy_missing",
                    "Production marketing step has no matching marketing policy manifest rule.",
                    "Add a CMO marketing policy manifest rule or keep this workflow out of production.",
                    parent_id=parent_id,
                )
            ]
        if outcome == "blocked":
            return [
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_policy_blocked",
                    str(decision.get("reason") or "Marketing policy blocks this production step."),
                    "Remove the blocked action or change the policy only after governance review.",
                    parent_id=parent_id,
                )
            ]
        if outcome == "read_only_only" and external_write_required:
            return [
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_policy_read_only_only",
                    "Marketing policy permits this step only as read-only/shadow behavior.",
                    "Replace the external write with a recommendation/draft or add a governed active policy.",
                    parent_id=parent_id,
                )
            ]

    if outcome == "read_only_only" and _is_shadow_mode(step_mode):
        return [
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "warning",
                "marketing_policy_shadow_read_only",
                "Marketing policy allows this only as read-only/shadow output.",
                "Keep the step labeled as a recommendation, draft, simulation, or internal approval.",
                parent_id=parent_id,
            )
        ]
    return []


def _lint_declared_connector_readiness(
    required_keys: tuple[str, ...],
    required_categories: tuple[str, ...],
    connector_contracts: tuple[dict[str, Any], ...],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
) -> list[MarketingWorkflowLintFinding]:
    if not _is_production_mode(step_mode):
        return []
    if not required_keys and not required_categories:
        return []
    if not connector_contracts:
        return [
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_connector_contracts_missing",
                "Production marketing step declares connector requirements but no connector contracts were supplied.",
                "Pass CMO connector contract rows into the linter or keep this workflow out of production.",
                parent_id=parent_id,
            )
        ]
    findings: list[MarketingWorkflowLintFinding] = []
    for key in required_keys:
        row = _contract_by_key(connector_contracts, key)
        if row is not None and _contract_is_degraded_only(row):
            findings.append(
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_connector_degraded_only",
                    f"Required connector '{key}' is only available in degraded mode for this production step.",
                    (
                        "Restore fresh/complete connector data or keep this workflow "
                        "shadow/degraded with visible confidence impact."
                    ),
                    parent_id=parent_id,
                )
            )
        elif row is None or not row.get("read_ready"):
            findings.append(
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_connector_readiness_missing",
                    f"Required connector '{key}' is not read-ready for this production step.",
                    "Configure the connector, scopes, auth, health, and freshness before production lint can pass.",
                    parent_id=parent_id,
                )
            )
    for category in required_categories:
        rows = _contracts_by_category(connector_contracts, category)
        degraded_only = (
            rows
            and not any(row.get("read_ready") for row in rows)
            and any(_contract_is_degraded_only(row) for row in rows)
        )
        if degraded_only:
            findings.append(
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_connector_degraded_only",
                    (
                        f"Required connector category '{category}' is only available "
                        "in degraded mode for this production step."
                    ),
                    (
                        "Restore fresh/complete connector data or keep this workflow "
                        "shadow/degraded with visible confidence impact."
                    ),
                    parent_id=parent_id,
                )
            )
        elif not rows or not any(row.get("read_ready") for row in rows):
            findings.append(
                _step_finding(
                    workflow_file,
                    workflow_id,
                    workflow_name,
                    step_id,
                    step_name,
                    "error",
                    "marketing_connector_category_readiness_missing",
                    f"Required connector category '{category}' has no read-ready connector contract.",
                    "Connect at least one healthy connector in this category before production lint can pass.",
                    parent_id=parent_id,
                )
            )
    return findings


def _lint_external_write_step(
    required_keys: tuple[str, ...],
    required_categories: tuple[str, ...],
    connector_contracts: tuple[dict[str, Any], ...],
    step: dict[str, Any],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
) -> list[MarketingWorkflowLintFinding]:
    if _is_shadow_mode(step_mode):
        return [
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_shadow_external_write",
                "Shadow marketing workflows must stay read-only and cannot execute external-write steps.",
                "Replace this with a recommendation, simulation, draft, or internal approval record.",
                parent_id=parent_id,
            )
        ]

    if not _is_production_mode(step_mode):
        return [
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "warning",
                "marketing_external_write_target_only",
                "External-write step is present only in target/demo/internal mode.",
                "Keep this workflow out of production until connector write readiness and confirmation metadata pass.",
                parent_id=parent_id,
            )
        ]

    findings: list[MarketingWorkflowLintFinding] = []
    if not required_keys and not required_categories:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_connector_missing",
                "Production external-write step does not declare a connector key or category.",
                "Declare connector_key, connector_category, or action metadata that maps the write to a connector.",
                parent_id=parent_id,
            )
        )

    if not _has_write_confirmation_metadata(step):
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_confirmation_metadata_missing",
                "Production external-write step lacks CMO-5.3 write-confirmation metadata.",
                "Set external_write_confirmation_required and expected confirmation fields for the write.",
                parent_id=parent_id,
            )
        )

    if not _has_idempotency_metadata(step):
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_idempotency_metadata_missing",
                "Production external-write step lacks idempotency metadata.",
                "Add idempotency_key, idempotency_key_template, or request_fingerprint for safe retry reasoning.",
                parent_id=parent_id,
            )
        )

    if not connector_contracts:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_contracts_missing",
                "Production external-write step cannot prove connector write readiness without connector contracts.",
                "Pass CMO connector contract rows into the linter before promoting this workflow.",
                parent_id=parent_id,
            )
        )
        return findings

    write_ready_rows = _matching_write_contracts(required_keys, required_categories, connector_contracts)
    if not write_ready_rows:
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_connector_not_ready",
                "No matching connector contract is write-ready for this production external-write step.",
                "Configure write scopes, auth, health, freshness, and idempotency support for the required connector.",
                parent_id=parent_id,
            )
        )
        return findings

    if not any(row.get("idempotency_key_supported") for row in write_ready_rows):
        findings.append(
            _step_finding(
                workflow_file,
                workflow_id,
                workflow_name,
                step_id,
                step_name,
                "error",
                "marketing_external_write_contract_idempotency_missing",
                "Matching write-ready connector contract does not prove idempotency-key support.",
                "Harden the connector contract retry metadata before this active write can pass lint.",
                parent_id=parent_id,
            )
        )

    return findings


def _lint_approval_timeout_policy(
    action: str,
    step: dict[str, Any],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
) -> list[MarketingWorkflowLintFinding]:
    if not _is_production_mode(step_mode):
        return []
    if not requires_approval_timeout_policy(action, step):
        return []
    if has_timeout_policy_for_step(step):
        return []
    return [
        _step_finding(
            workflow_file,
            workflow_id,
            workflow_name,
            step_id,
            step_name,
            "error",
            "marketing_approval_timeout_policy_missing",
            "Production approval-sensitive marketing step has no approval timeout policy.",
            (
                "Attach a CMO approval timeout policy or use a supported "
                "approval-sensitive action with a default timeout policy."
            ),
            parent_id=parent_id,
        )
    ]


def _lint_escalation_route(
    action: str,
    step: dict[str, Any],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
    marketing_escalation_matrix: dict[str, Any] | None,
    external_write_required: bool,
) -> list[MarketingWorkflowLintFinding]:
    if not _is_production_mode(step_mode):
        return []
    triggers = escalation_triggers_for_action(
        action,
        {**step, "workflow_id": workflow_id},
        external_write_required=external_write_required,
    )
    if not triggers:
        return []
    missing = missing_escalation_triggers_for_step(
        {**step, "workflow_id": workflow_id},
        action=action,
        matrix=marketing_escalation_matrix,
        external_write_required=external_write_required,
    )
    if not missing:
        return []
    return [
        _step_finding(
            workflow_file,
            workflow_id,
            workflow_name,
            step_id,
            step_name,
            "error",
            "marketing_escalation_route_missing",
            (
                "Production escalation-sensitive marketing step has no CMO "
                f"escalation route for {', '.join(missing)}."
            ),
            "Configure the CMO escalation matrix route or keep this workflow out of production.",
            parent_id=parent_id,
        )
    ]


def _lint_decision_audit_evidence(
    action: str,
    step: dict[str, Any],
    *,
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    step_mode: str,
    parent_id: str | None,
    external_write_required: bool,
) -> list[MarketingWorkflowLintFinding]:
    if not _is_production_mode(step_mode):
        return []
    event_type = action_event_type(action, step)
    major_customer_action = external_write_required or event_type not in {
        "policy_decision",
        "shadow_only",
    }
    if not major_customer_action:
        return []
    if has_decision_audit_evidence_for_step(step):
        return []
    return [
        _step_finding(
            workflow_file,
            workflow_id,
            workflow_name,
            step_id,
            step_name,
            "error",
            "marketing_decision_audit_evidence_missing",
            (
                "Production customer-facing marketing step lacks CMO-6.3 "
                "decision-audit evidence metadata."
            ),
            (
                "Set decision_audit_required/audit_package_required or attach "
                "a decision audit package/ref before production promotion."
            ),
            parent_id=parent_id,
        )
    ]


def _is_marketing_workflow(
    definition: dict[str, Any],
    steps: list[tuple[dict[str, Any], str | None]],
) -> bool:
    if _normalize_key(definition.get("domain")) == "marketing":
        return True
    return any(
        _normalize_key(step.get("agent_type") or step.get("agent")) in MARKETING_AGENT_STATES
        for step, _parent_id in steps
    )


def _iter_steps(
    steps: Iterable[Any],
    *,
    parent_id: str | None = None,
) -> Iterable[tuple[dict[str, Any], str | None]]:
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        yield raw_step, parent_id
        step_id = _string_or_none(raw_step.get("id")) or parent_id
        for child_key in ("sub_steps", "steps"):
            child_steps = raw_step.get(child_key)
            if isinstance(child_steps, list):
                yield from _iter_steps(child_steps, parent_id=step_id)


def _connector_requirements(
    agent_type: str,
    action: str,
    step: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    metadata = ACTION_CONNECTOR_REQUIREMENTS.get((agent_type, action), {})
    keys = set(_string_list(metadata.get("keys")))
    categories = set(_string_list(metadata.get("categories")))
    keys.update(
        _string_list(
            step.get("connector_key")
            or step.get("connector")
            or step.get("required_connector_key")
            or step.get("required_connector_keys")
            or step.get("required_connectors")
            or step.get("connectors")
        )
    )
    categories.update(
        _string_list(
            step.get("connector_category")
            or step.get("required_connector_category")
            or step.get("connector_categories")
            or step.get("required_connector_categories")
        )
    )
    return tuple(sorted(keys)), tuple(sorted(categories))


def _is_external_write_step(agent_type: str, action: str, step: dict[str, Any]) -> bool:
    if step.get("external_write_required") is False or step.get("requires_external_write") is False:
        return False
    if step.get("external_write_required") is True or step.get("requires_external_write") is True:
        return True
    if action in EXTERNAL_WRITE_ACTIONS:
        return True
    if (agent_type, action) in ACTION_CONNECTOR_REQUIREMENTS and _action_looks_write(action):
        return True
    return _action_looks_write(action)


def _action_looks_write(action: str) -> bool:
    return any(hint in action for hint in MARKETING_WRITE_ACTION_HINTS)


def _has_write_confirmation_metadata(step: dict[str, Any]) -> bool:
    if step.get("external_write_confirmation_required") is True:
        return True
    if step.get("requires_write_confirmation") is True:
        return True
    if step.get("write_confirmation_required") is True:
        return True
    if step.get("expected_external_object_id_field"):
        return True
    fields = step.get("expected_confirmation_fields")
    return isinstance(fields, list) and bool(fields)


def _has_idempotency_metadata(step: dict[str, Any]) -> bool:
    return any(
        step.get(field)
        for field in (
            "idempotency_key",
            "idempotency_key_template",
            "request_fingerprint",
            "request_fingerprint_template",
        )
    )


def _matching_write_contracts(
    required_keys: tuple[str, ...],
    required_categories: tuple[str, ...],
    connector_contracts: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in required_keys:
        row = _contract_by_key(connector_contracts, key)
        if row is not None and _contract_write_safe(row):
            rows.append(row)
    for category in required_categories:
        rows.extend(row for row in _contracts_by_category(connector_contracts, category) if _contract_write_safe(row))
    return rows


def _contract_is_degraded_only(row: dict[str, Any]) -> bool:
    degraded_mode = row.get("degraded_mode") if isinstance(row.get("degraded_mode"), dict) else {}
    return (
        not row.get("read_ready")
        and (
            row.get("read_status") == "degraded"
            or degraded_mode.get("status") == "degraded"
            or bool(degraded_mode.get("allowed"))
        )
    )


def _contract_write_safe(row: dict[str, Any]) -> bool:
    if "write_safe" in row:
        return bool(row.get("write_safe"))
    return bool(row.get("write_ready"))


def _contract_by_key(
    connector_contracts: tuple[dict[str, Any], ...],
    key: str,
) -> dict[str, Any] | None:
    normalized_key = _normalize_key(key)
    for row in connector_contracts:
        if _normalize_key(row.get("connector_key") or row.get("key")) == normalized_key:
            return row
    return None


def _contracts_by_category(
    connector_contracts: tuple[dict[str, Any], ...],
    category: str,
) -> list[dict[str, Any]]:
    normalized_category = _normalize_key(category)
    return [
        row
        for row in connector_contracts
        if _normalize_key(row.get("category")) == normalized_category
    ]


def _workflow_id(definition: dict[str, Any], workflow_file: str | None) -> str:
    explicit = definition.get("id") or definition.get("workflow_id") or definition.get("key")
    if explicit:
        return _normalize_key(explicit)
    if workflow_file:
        return Path(workflow_file).stem
    return _normalize_key(definition.get("name")) or "unknown_workflow"


def _workflow_mode(definition: dict[str, Any], *, default: str = "production") -> str:
    for field in READINESS_MODE_FIELDS:
        value = _normalize_key(definition.get(field))
        if value:
            return value
    if definition.get("production_ready") is False:
        return "target"
    return default


def _is_production_mode(mode: str) -> bool:
    return _normalize_key(mode) in PRODUCTION_MODES


def _is_shadow_mode(mode: str) -> bool:
    return _normalize_key(mode) in SHADOW_MODES


def _step_finding(
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str,
    step_name: str,
    severity: str,
    code: str,
    message: str,
    suggested_fix: str,
    *,
    parent_id: str | None,
) -> MarketingWorkflowLintFinding:
    if parent_id:
        message = f"{message} Parent step: {parent_id}."
    return _finding(
        workflow_file,
        workflow_id,
        workflow_name,
        step_id,
        step_name,
        severity,
        code,
        message,
        suggested_fix,
    )


def _finding(
    workflow_file: str | None,
    workflow_id: str,
    workflow_name: str,
    step_id: str | None,
    step_name: str | None,
    severity: str,
    code: str,
    message: str,
    suggested_fix: str,
) -> MarketingWorkflowLintFinding:
    if severity not in SEVERITIES:
        raise ValueError(f"Invalid lint severity: {severity}")
    return MarketingWorkflowLintFinding(
        workflow_file=workflow_file,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        step_id=step_id,
        step_name=step_name,
        severity=severity,
        code=code,
        message=message,
        suggested_fix=suggested_fix,
    )


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_normalize_key(value),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(_normalize_key(item) for item in value if _normalize_key(item))
    return ()


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
