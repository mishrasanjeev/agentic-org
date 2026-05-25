"""CMO workflow shadow-mode and promotion readiness projection.

This module projects stored marketing connector metadata into per-workflow
activation gates. It does not promote workflows by itself, call vendor APIs, or
trust demo/sample data as proof. A workflow can write to external marketing
systems only when its own row is explicitly active and every prerequisite still
passes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.marketing.approval_timeouts import build_workflow_approval_timeout_status
from core.marketing.connector_contracts import build_marketing_connector_contracts
from core.marketing.connector_retry_policy import summarize_degraded_modes
from core.marketing.decision_audit import (
    build_workflow_decision_audit_status,
    build_workflow_promotion_audit_package,
)
from core.marketing.escalation_matrix import build_workflow_escalation_status
from core.marketing.policy_manifest import build_workflow_marketing_policy_status

WORKFLOW_STATES = (
    "unavailable",
    "shadow",
    "promotion_blocked",
    "promotion_ready",
    "active",
    "degraded",
    "paused",
)

PROMOTABLE_MODES = {"shadow", "promotion_ready", "active", "paused"}
READ_ONLY_ACTIONS = {
    "account_deal_health_summary",
    "account_health_summary",
    "analyze",
    "analyze_pipeline_velocity",
    "change_confidence_score",
    "churn_risk_signal_extraction",
    "classify_crisis_severity",
    "classify_sentiment",
    "create_approval",
    "create_internal_approval",
    "draft",
    "aggregate_mentions",
    "aggregate_engagement",
    "aggregate_intent",
    "competitor_brand_grouping",
    "duplicate_change_suppression",
    "detect_crisis",
    "detect_negative_spike",
    "evaluate_alert_thresholds",
    "extract_churn_signals",
    "extract_win_loss_signals",
    "false_positive_suppression",
    "feature_capability_diffing",
    "funnel_conversion_analysis",
    "get_sentiment",
    "get_keyword_rankings",
    "group_mentions",
    "identify_content_gaps",
    "keyword_gap_analysis",
    "lead_scoring_refresh",
    "mention_aggregation",
    "monitor_mentions",
    "normalize_competitor_profiles",
    "optimize_content",
    "pipeline_velocity_analysis",
    "positioning_recommendation",
    "pricing_change_detection",
    "prioritize_technical_issues",
    "promote_to_sql",
    "query_target_accounts",
    "recommend",
    "recommend_next_best_action",
    "recommend_response_playbook",
    "recommend_segments",
    "refresh_lead_scores",
    "ranking_delta_computation",
    "recommendation_bundling",
    "score_accounts",
    "score_icp_fit",
    "score_intent_heat",
    "score_lead",
    "segment_recommendation",
    "seo_sprint_planning",
    "sentiment_trend",
    "simulate",
    "sql_promotion_criteria",
    "technical_issue_prioritization",
    "validate_account_csv",
    "weekly_benchmark",
    "weekly_market_snapshot",
}
EXTERNAL_WRITE_ACTIONS = {
    "bulk_crm_update",
    "change_segment_membership",
    "change_target_accounts",
    "comparative_claim",
    "apply_redirect",
    "create_abm_campaign",
    "create_campaign",
    "crisis_response",
    "launch_campaign",
    "launch_abm_campaign",
    "launch_competitive_campaign",
    "mutate_ad_budget",
    "pause_campaign",
    "pricing_claim",
    "promote_lifecycle_stage",
    "public_response",
    "publish",
    "publish_brand_response",
    "publish_competitive_response",
    "publish_landing_page",
    "publish_post",
    "publish_seo_change",
    "publish_to_wordpress",
    "reply_to_mention",
    "schedule_campaign_posts",
    "schedule_content_promotion",
    "schedule_post",
    "send",
    "send_email",
    "spend",
    "sync_target_accounts",
    "target_account_list_change",
    "update_canonical_tag",
    "update_ad_budget",
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


@dataclass(frozen=True)
class WorkflowActivationSpec:
    key: str
    name: str
    required_connector_categories: tuple[str, ...]
    required_mappings: tuple[str, ...]
    required_backfill_categories: tuple[str, ...]
    write_connector_categories: tuple[str, ...] = ()
    write_actions: tuple[str, ...] = ()
    optional_connector_categories: tuple[str, ...] = ()
    optional_mappings: tuple[str, ...] = ()
    optional_backfill_categories: tuple[str, ...] = ()
    production_available: bool = True
    unavailable_reason: str | None = None
    min_shadow_runs: int = 3
    min_shadow_success_rate: float = 0.8


CMO_WORKFLOW_SPECS: tuple[WorkflowActivationSpec, ...] = (
    WorkflowActivationSpec(
        key="weekly_marketing_report",
        name="Weekly Marketing Report",
        required_connector_categories=("CRM", "Ads", "Analytics", "Email"),
        required_mappings=(
            "lifecycle_stages",
            "opportunity_revenue",
            "campaign_ids",
            "utm_fields",
            "consent_unsubscribe",
            "fiscal_calendar",
            "currency",
            "timezone",
        ),
        required_backfill_categories=("CRM", "Ads", "Analytics", "Email"),
        optional_connector_categories=("SEO", "Brand", "Finance"),
        optional_backfill_categories=("SEO", "Brand", "Finance"),
    ),
    WorkflowActivationSpec(
        key="campaign_launch",
        name="Campaign Launch",
        required_connector_categories=("CRM", "Ads", "Analytics", "Email"),
        required_mappings=(
            "campaign_ids",
            "utm_fields",
            "consent_unsubscribe",
            "currency",
            "timezone",
        ),
        required_backfill_categories=("CRM", "Ads", "Analytics", "Email"),
        write_connector_categories=("Ads", "Email"),
        write_actions=("launch_campaign", "send_email"),
        optional_connector_categories=("CMS", "Social"),
        optional_mappings=("account_domains",),
        optional_backfill_categories=("CMS", "Social"),
    ),
    WorkflowActivationSpec(
        key="daily_spend_optimization",
        name="Daily Spend Optimization",
        required_connector_categories=("CRM", "Ads", "Analytics"),
        required_mappings=(
            "opportunity_revenue",
            "campaign_ids",
            "utm_fields",
            "currency",
            "timezone",
        ),
        required_backfill_categories=("CRM", "Ads", "Analytics"),
        write_connector_categories=("Ads",),
        write_actions=("mutate_ad_budget",),
        optional_connector_categories=("Finance",),
        optional_backfill_categories=("Finance",),
        min_shadow_runs=5,
        min_shadow_success_rate=0.85,
    ),
    WorkflowActivationSpec(
        key="content_pipeline",
        name="Content Pipeline",
        required_connector_categories=("CMS",),
        required_mappings=("campaign_ids", "utm_fields", "timezone"),
        required_backfill_categories=("CMS",),
        write_connector_categories=("CMS",),
        write_actions=("publish",),
        optional_connector_categories=("Analytics", "SEO", "Social"),
        optional_backfill_categories=("Analytics", "SEO", "Social"),
    ),
    WorkflowActivationSpec(
        key="lead_nurture",
        name="Lead Nurture",
        required_connector_categories=("CRM", "Email"),
        required_mappings=("lifecycle_stages", "consent_unsubscribe", "timezone"),
        required_backfill_categories=("CRM", "Email"),
        write_connector_categories=("CRM", "Email"),
        write_actions=("update_crm", "send_email"),
        optional_connector_categories=("ABM",),
        optional_mappings=("account_domains",),
        optional_backfill_categories=("ABM",),
    ),
    WorkflowActivationSpec(
        key="social_publishing",
        name="Social Publishing",
        required_connector_categories=("Social",),
        required_mappings=("campaign_ids", "utm_fields", "timezone"),
        required_backfill_categories=("Social",),
        write_connector_categories=("Social",),
        write_actions=("schedule_post", "publish_post", "reply_to_mention"),
        optional_connector_categories=("Analytics", "Brand"),
        optional_backfill_categories=("Analytics", "Brand"),
    ),
    WorkflowActivationSpec(
        key="abm_sprint",
        name="ABM Sprint",
        required_connector_categories=("CRM", "ABM"),
        required_mappings=("account_domains", "lifecycle_stages", "timezone"),
        required_backfill_categories=("CRM", "ABM"),
        write_connector_categories=("CRM", "Ads"),
        write_actions=("update_target_accounts", "launch_abm_campaign"),
    ),
    WorkflowActivationSpec(
        key="competitive_intel_monitoring",
        name="Competitive Intel Monitoring",
        required_connector_categories=("Brand", "SEO"),
        required_mappings=("timezone",),
        required_backfill_categories=("Brand", "SEO"),
        write_connector_categories=("Social", "Ads"),
        write_actions=("publish_competitive_response", "launch_competitive_campaign"),
        optional_connector_categories=("ABM", "CRM"),
        optional_backfill_categories=("ABM", "CRM"),
    ),
    WorkflowActivationSpec(
        key="brand_crisis_response",
        name="Brand Crisis Response",
        required_connector_categories=("Brand",),
        required_mappings=("timezone",),
        required_backfill_categories=("Brand",),
        write_connector_categories=("Social",),
        write_actions=("public_response", "crisis_response", "publish_brand_response"),
        optional_connector_categories=("Social",),
        optional_backfill_categories=("Social",),
    ),
    WorkflowActivationSpec(
        key="seo_sprint",
        name="SEO Sprint",
        required_connector_categories=("SEO", "Analytics", "CMS"),
        required_mappings=("campaign_ids", "utm_fields", "timezone"),
        required_backfill_categories=("SEO", "Analytics", "CMS"),
        write_connector_categories=("CMS",),
        write_actions=("update_page_metadata", "update_canonical_tag", "apply_redirect", "publish_seo_change"),
    ),
    WorkflowActivationSpec(
        key="crm_pipeline_intelligence",
        name="CRM Pipeline Intelligence",
        required_connector_categories=("CRM",),
        required_mappings=("lifecycle_stages", "opportunity_revenue", "timezone"),
        required_backfill_categories=("CRM",),
        write_connector_categories=("CRM",),
        write_actions=(
            "update_lead_scores",
            "update_lifecycle_stage",
            "change_segment_membership",
            "change_target_accounts",
            "bulk_crm_update",
        ),
        optional_connector_categories=("Analytics", "ABM"),
        optional_mappings=("account_domains",),
        optional_backfill_categories=("Analytics", "ABM"),
    ),
)


def build_cmo_workflow_activation(
    connector_setup: Iterable[dict[str, Any]],
    data_readiness: dict[str, Any],
    connector_configs: Iterable[Any],
    *,
    connector_contracts: Iterable[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build per-workflow activation gates for the CMO dashboard/API."""

    now = _ensure_aware(now) or datetime.now(UTC)
    setup_rows = list(connector_setup)
    config_rows = list(connector_configs)
    mapping_rows = list(data_readiness.get("field_mapping_status") or [])
    backfill_rows = list(data_readiness.get("backfill_status") or [])
    contract_rows = list(
        connector_contracts
        if connector_contracts is not None
        else build_marketing_connector_contracts(setup_rows, config_rows, now=now)
    )
    workflow_payloads = _workflow_payloads(config_rows)

    rows = [
        _build_workflow_row(
            spec,
            setup_rows,
            mapping_rows,
            backfill_rows,
            contract_rows,
            workflow_payloads.get(spec.key, {}),
            now,
        )
        for spec in CMO_WORKFLOW_SPECS
    ]
    return {
        "workflow_activation_status": rows,
        "workflow_activation_summary": summarize_cmo_workflow_activation(rows),
    }


def summarize_cmo_workflow_activation(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    counts = dict.fromkeys(WORKFLOW_STATES, 0)
    for row in items:
        state = str(row.get("state") or "")
        if state in counts:
            counts[state] += 1

    external_writes_allowed = sum(1 for row in items if row.get("external_writes_allowed"))
    return {
        "total": len(items),
        **counts,
        "external_writes_allowed": external_writes_allowed,
        "needs_action": sum(1 for row in items if row.get("next_action_cta") != "none"),
        "readiness": (
            "blocked"
            if counts["promotion_blocked"] or counts["unavailable"]
            else "degraded"
            if counts["shadow"] or counts["degraded"] or counts["paused"]
            else "ready"
        ),
    }


def evaluate_cmo_workflow_external_write(
    workflow_rows: Iterable[dict[str, Any]],
    workflow_key: str,
    action: str,
) -> dict[str, Any]:
    """Return an allow/deny decision for a workflow action.

    Unknown non-read-only actions are treated as external writes. This is a
    fail-closed policy for marketing actions that could publish, send, spend,
    update CRM, or mutate vendor systems.
    """

    normalized_action = _normalize_key(action)
    row = _workflow_row_by_key(workflow_rows, workflow_key)
    if row is None:
        return {
            "allowed": False,
            "workflow_key": workflow_key,
            "action": normalized_action,
            "state": "unavailable",
            "reason": "Workflow is not known to the CMO activation gate.",
            "next_action_cta": "implement_workflow",
        }

    state = str(row.get("state") or "unavailable")
    if normalized_action in READ_ONLY_ACTIONS:
        return {
            "allowed": True,
            "workflow_key": row.get("workflow_key"),
            "action": normalized_action,
            "state": state,
            "reason": "Read-only shadow action is allowed.",
            "next_action_cta": "none",
        }

    is_write = normalized_action in EXTERNAL_WRITE_ACTIONS or normalized_action not in READ_ONLY_ACTIONS
    if is_write and state != "active":
        return {
            "allowed": False,
            "workflow_key": row.get("workflow_key"),
            "action": normalized_action,
            "state": state,
            "reason": (
                "External marketing writes require this workflow to be "
                "explicitly promoted to active mode."
            ),
            "next_action_cta": row.get("next_action_cta") or "promote_workflow",
        }

    return {
        "allowed": True,
        "workflow_key": row.get("workflow_key"),
        "action": normalized_action,
        "state": state,
        "reason": (
            "Workflow is active; downstream connector confirmation and audit "
            "still apply before marking an external write complete."
        ),
        "next_action_cta": "none",
    }


def _build_workflow_row(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    mapping_rows: list[dict[str, Any]],
    backfill_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, Any]],
    payload: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    mode = _workflow_mode(payload)
    approval_owner = _string_or_none(payload.get("approval_owner"))
    policy_owner = _string_or_none(payload.get("policy_owner"))
    accepted_partials = _accepted_partial_backfills(payload)
    approval_timeout_policy = build_workflow_approval_timeout_status(
        spec.key,
        spec.write_actions,
        payload,
    )
    marketing_policy = build_workflow_marketing_policy_status(
        spec.key,
        spec.write_actions,
        payload,
    )
    escalation_matrix = build_workflow_escalation_status(
        spec.key,
        spec.write_actions,
        payload,
    )
    decision_audit = build_workflow_decision_audit_status(
        spec.key,
        spec.write_actions,
        payload,
    )

    blockers: list[str] = []
    degraded: list[str] = []

    if not spec.production_available:
        reason = spec.unavailable_reason or "Workflow is not production-available yet."
        blockers.append(reason)
        state = "unavailable"
        next_action_cta = "implement_first_class_agent"
    else:
        blockers.extend(_required_connector_blockers(spec, setup_rows, contract_rows))
        blockers.extend(_required_write_contract_blockers(spec, contract_rows))
        blockers.extend(_required_mapping_blockers(spec, mapping_rows))
        blockers.extend(_required_backfill_blockers(spec, setup_rows, backfill_rows, accepted_partials))
        blockers.extend(_policy_blockers(approval_owner, policy_owner))
        blockers.extend(_marketing_policy_blockers(marketing_policy))
        blockers.extend(_approval_timeout_policy_blockers(approval_timeout_policy))
        blockers.extend(_escalation_matrix_blockers(escalation_matrix))
        blockers.extend(_decision_audit_blockers(decision_audit))
        degraded.extend(_required_connector_degraded(spec, setup_rows, contract_rows))
        degraded.extend(_optional_connector_degraded(spec, setup_rows, contract_rows))
        degraded.extend(_optional_mapping_degraded(spec, mapping_rows))
        degraded.extend(_optional_backfill_degraded(spec, setup_rows, backfill_rows))
        degraded.extend(_accepted_partial_degraded(spec, setup_rows, backfill_rows, accepted_partials))

        shadow_quality = _shadow_quality(payload, spec, now)
        if blockers:
            state = "promotion_blocked"
            next_action_cta = _blocked_cta(blockers)
        elif shadow_quality["status"] != "passed":
            blockers.append(str(shadow_quality["blocking_reason"]))
            state = "shadow"
            next_action_cta = "run_shadow_quality"
        elif mode == "paused":
            state = "paused"
            next_action_cta = "resume_workflow"
        elif degraded:
            state = "degraded"
            next_action_cta = "review_degraded_dependency"
        elif _is_promoted(payload, mode):
            state = "active"
            next_action_cta = "none"
        else:
            state = "promotion_ready"
            next_action_cta = "promote_workflow"

    if not spec.production_available:
        shadow_quality = _shadow_quality(payload, spec, now)

    row = {
        "workflow_key": spec.key,
        "name": spec.name,
        "state": state,
        "configured_mode": mode,
        "required_connectors": list(spec.required_connector_categories),
        "optional_connectors": list(spec.optional_connector_categories),
        "required_mappings": list(spec.required_mappings),
        "optional_mappings": list(spec.optional_mappings),
        "required_backfill_categories": list(spec.required_backfill_categories),
        "optional_backfill_categories": list(spec.optional_backfill_categories),
        "write_connector_categories": list(spec.write_connector_categories),
        "write_actions": list(spec.write_actions),
        "approval_owner": approval_owner,
        "policy_owner": policy_owner,
        "marketing_policy": marketing_policy,
        "approval_timeout_policy": approval_timeout_policy,
        "escalation_matrix": escalation_matrix,
        "decision_audit": decision_audit,
        "shadow_quality": shadow_quality,
        "blocked_reasons": blockers,
        "degraded_reasons": degraded,
        "degraded_mode": _workflow_degraded_mode(spec, contract_rows, degraded),
        "next_action_cta": next_action_cta,
        "external_writes_allowed": state == "active",
        "read_only_actions": sorted(READ_ONLY_ACTIONS),
        "blocked_write_actions": sorted(EXTERNAL_WRITE_ACTIONS),
        "evaluated_at": now.isoformat(),
    }
    audit_package = build_workflow_promotion_audit_package(row, now=now)
    row["workflow_promotion_audit"] = audit_package
    row["workflow_promotion_audit_ref"] = audit_package["audit_reference"]
    return row


def _required_connector_blockers(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    for category in spec.required_connector_categories:
        rows = _configured_rows_for_category(setup_rows, category)
        healthy = [row for row in rows if _health(row) == "healthy"]
        contracts = _contracts_for_category(contract_rows, category)
        degraded_allowed = [
            row
            for row in contracts
            if row.get("read_status") == "degraded"
            and isinstance(row.get("degraded_mode"), dict)
            and row["degraded_mode"].get("allowed")
        ]
        if not rows:
            blockers.append(f"Required {category} connector is not configured.")
        elif not healthy:
            if degraded_allowed:
                continue
            states = ", ".join(sorted({_health(row) for row in rows}))
            blockers.append(f"Required {category} connector is not healthy ({states}).")
        else:
            read_ready = [row for row in contracts if row.get("read_ready")]
            if not read_ready:
                if degraded_allowed:
                    continue
                states = ", ".join(
                    sorted(
                        {
                            str(row.get("read_status") or row.get("contract_state") or "unknown")
                            for row in _contracts_for_category(contract_rows, category)
                        }
                    )
                )
                blockers.append(f"Required {category} connector contract is not read-ready ({states or 'missing'}).")
    return blockers


def _required_write_contract_blockers(
    spec: WorkflowActivationSpec,
    contract_rows: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    for category in spec.write_connector_categories:
        rows = _contracts_for_category(contract_rows, category)
        write_ready = [row for row in rows if row.get("write_safe", row.get("write_ready"))]
        if write_ready:
            continue
        if not rows:
            blockers.append(f"Required {category} connector contract is missing for external writes.")
            continue
        details = []
        for row in rows:
            status = str(row.get("write_status") or row.get("contract_state") or "unknown")
            failure_class = str(row.get("failure_class") or "")
            missing = row.get("missing_write_scopes") or []
            if missing:
                status = f"{status}: missing {', '.join(str(scope) for scope in missing)}"
            if failure_class:
                status = f"{status}: {failure_class}"
            details.append(f"{row.get('connector_key')}={status}")
        actions = ", ".join(spec.write_actions) or "external writes"
        blockers.append(
            f"Required {category} connector is not write-ready for "
            f"{actions} ({'; '.join(details)})."
        )
    return blockers


def _required_connector_degraded(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for category in spec.required_connector_categories:
        if not _configured_rows_for_category(setup_rows, category):
            continue
        for contract in _contracts_for_category(contract_rows, category):
            degraded_mode = contract.get("degraded_mode") if isinstance(contract.get("degraded_mode"), dict) else {}
            if contract.get("read_status") == "degraded" and degraded_mode.get("allowed"):
                reason = (
                    degraded_mode.get("reason")
                    or contract.get("degraded_mode_reason")
                    or contract.get("failure_class")
                    or "degraded connector contract"
                )
                impact = degraded_mode.get("confidence_impact")
                impact_text = f"; confidence impact {impact}" if impact not in (None, "", 0, 0.0) else ""
                reasons.append(
                    f"Required {category} connector {contract.get('connector_key')} "
                    f"is degraded ({reason}{impact_text})."
                )
    return reasons


def _optional_connector_degraded(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    contract_rows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    for category in spec.optional_connector_categories:
        rows = _configured_rows_for_category(setup_rows, category)
        for row in rows:
            health = _health(row)
            if health in {"stale", "degraded"}:
                reasons.append(f"Optional {category} connector {row.get('name')} is {health}.")
        for contract in _contracts_for_category(contract_rows, category):
            if contract.get("read_status") == "degraded":
                reasons.append(
                    f"Optional {category} connector contract {contract.get('connector_key')} is degraded."
                )
    return reasons


def _workflow_degraded_mode(
    spec: WorkflowActivationSpec,
    contract_rows: list[dict[str, Any]],
    degraded_reasons: list[str],
) -> dict[str, Any]:
    relevant_categories = set(spec.required_connector_categories).union(spec.optional_connector_categories)
    relevant_rows: list[dict[str, Any]] = []
    for row in contract_rows:
        if str(row.get("category") or "").strip() not in relevant_categories:
            continue
        degraded_mode = row.get("degraded_mode")
        if isinstance(degraded_mode, dict):
            affected_workflows = set(degraded_mode.get("affected_workflows") or [])
            affected_workflows.add(spec.key)
            relevant_rows.append(
                {
                    **row,
                    "degraded_mode": {
                        **degraded_mode,
                        "affected_workflows": sorted(affected_workflows),
                    },
                }
            )
        else:
            relevant_rows.append(row)
    summary = summarize_degraded_modes(relevant_rows)
    return {
        **summary,
        "active": bool(degraded_reasons) or bool(summary.get("active")),
        "affected_workflows": sorted(
            set(summary.get("affected_workflows") or [])
            | ({spec.key} if degraded_reasons else set())
        ),
    }


def _required_mapping_blockers(
    spec: WorkflowActivationSpec,
    mapping_rows: list[dict[str, Any]],
) -> list[str]:
    by_key = {str(row.get("key") or ""): row for row in mapping_rows}
    blockers: list[str] = []
    for key in spec.required_mappings:
        row = by_key.get(key)
        status = str((row or {}).get("status") or "unmapped")
        if status != "valid":
            name = str((row or {}).get("name") or key)
            blockers.append(f"Required mapping {name} is {status.replace('_', ' ')}.")
    return blockers


def _optional_mapping_degraded(
    spec: WorkflowActivationSpec,
    mapping_rows: list[dict[str, Any]],
) -> list[str]:
    by_key = {str(row.get("key") or ""): row for row in mapping_rows}
    reasons: list[str] = []
    for key in spec.optional_mappings:
        row = by_key.get(key)
        status = str((row or {}).get("status") or "unmapped")
        if status in {"partially_mapped", "stale", "invalid", "blocked"}:
            name = str((row or {}).get("name") or key)
            reasons.append(f"Optional mapping {name} is {status.replace('_', ' ')}.")
    return reasons


def _required_backfill_blockers(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    backfill_rows: list[dict[str, Any]],
    accepted_partials: set[str],
) -> list[str]:
    blockers: list[str] = []
    backfills_by_key = {
        str(row.get("source_connector_key") or "").strip().lower(): row
        for row in backfill_rows
    }
    for category in spec.required_backfill_categories:
        sources = [
            row
            for row in _configured_rows_for_category(setup_rows, category)
            if _health(row) == "healthy"
        ]
        if not sources:
            continue

        statuses = []
        for source in sources:
            key = str(source.get("key") or "").strip().lower()
            status = str((backfills_by_key.get(key) or {}).get("status") or "not_started")
            statuses.append((key, status))
        if any(status == "completed" for _key, status in statuses):
            continue
        if any(status == "partial" and _partial_accepted(key, category, accepted_partials) for key, status in statuses):
            continue
        status_text = ", ".join(f"{key}:{status}" for key, status in statuses)
        blockers.append(f"Required {category} backfill is not complete ({status_text}).")
    return blockers


def _accepted_partial_degraded(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    backfill_rows: list[dict[str, Any]],
    accepted_partials: set[str],
) -> list[str]:
    reasons: list[str] = []
    backfills_by_key = {
        str(row.get("source_connector_key") or "").strip().lower(): row
        for row in backfill_rows
    }
    for category in spec.required_backfill_categories:
        for source in _configured_rows_for_category(setup_rows, category):
            key = str(source.get("key") or "").strip().lower()
            status = str((backfills_by_key.get(key) or {}).get("status") or "")
            if status == "partial" and _partial_accepted(key, category, accepted_partials):
                reasons.append(
                    f"Required {category} source {source.get('name')} has accepted partial backfill."
                )
    return reasons


def _optional_backfill_degraded(
    spec: WorkflowActivationSpec,
    setup_rows: list[dict[str, Any]],
    backfill_rows: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    backfills_by_key = {
        str(row.get("source_connector_key") or "").strip().lower(): row
        for row in backfill_rows
    }
    for category in spec.optional_backfill_categories:
        for source in _configured_rows_for_category(setup_rows, category):
            key = str(source.get("key") or "").strip().lower()
            row = backfills_by_key.get(key)
            status = str((row or {}).get("status") or "")
            if status and status != "completed":
                reasons.append(f"Optional {category} source {source.get('name')} backfill is {status}.")
    return reasons


def _policy_blockers(approval_owner: str | None, policy_owner: str | None) -> list[str]:
    blockers: list[str] = []
    if not approval_owner:
        blockers.append("Approval owner is not configured.")
    if not policy_owner:
        blockers.append("Policy owner is not configured.")
    return blockers


def _approval_timeout_policy_blockers(timeout_policy: dict[str, Any]) -> list[str]:
    if timeout_policy.get("status") != "missing_policy":
        return []
    missing = timeout_policy.get("missing_policy_actions") or []
    action_text = ", ".join(str(action) for action in missing) or "approval-sensitive action"
    return [f"Approval timeout policy is missing for {action_text}."]


def _escalation_matrix_blockers(escalation_matrix: dict[str, Any]) -> list[str]:
    if escalation_matrix.get("status") != "missing_route":
        return []
    missing = escalation_matrix.get("missing_route_triggers") or []
    trigger_text = ", ".join(str(trigger) for trigger in missing) or "escalation-sensitive action"
    return [f"Escalation route is missing for {trigger_text}."]


def _decision_audit_blockers(decision_audit: dict[str, Any]) -> list[str]:
    if decision_audit.get("status") != "missing_audit_evidence":
        return []
    missing = decision_audit.get("missing_audit_actions") or []
    action_text = ", ".join(str(action) for action in missing) or "customer-facing action"
    return [f"Decision audit evidence is missing for {action_text}."]


def _marketing_policy_blockers(marketing_policy: dict[str, Any]) -> list[str]:
    status = str(marketing_policy.get("status") or "")
    if status == "missing_policy":
        missing = marketing_policy.get("missing_policy_actions") or []
        action_text = ", ".join(str(action) for action in missing) or "external marketing action"
        return [f"Marketing policy manifest is missing for {action_text}."]
    if status == "blocked":
        blocked = marketing_policy.get("blocked_actions") or []
        action_text = ", ".join(str(action) for action in blocked) or "external marketing action"
        return [f"Marketing policy manifest blocks {action_text}."]
    return []


def _shadow_quality(
    payload: dict[str, Any],
    spec: WorkflowActivationSpec,
    now: datetime,
) -> dict[str, Any]:
    raw = payload.get("shadow_quality") or payload.get("shadow_run_quality") or {}
    raw = raw if isinstance(raw, dict) else {}
    status = str(raw.get("status") or "").strip().lower()
    sample_count = _int_or_zero(raw.get("sample_count") or raw.get("runs"))
    success_rate = _float_or_zero(raw.get("success_rate") or raw.get("pass_rate"))
    last_run_at = _string_or_none(raw.get("last_run_at"))

    if status == "passed" and sample_count >= spec.min_shadow_runs and success_rate >= spec.min_shadow_success_rate:
        normalized = "passed"
        blocking_reason = None
        next_action_cta = "none"
    else:
        normalized = status if status in {"failed", "running", "not_measured"} else "not_measured"
        if normalized == "failed":
            blocking_reason = "Recent shadow-run quality gate failed."
        elif normalized == "running":
            blocking_reason = "Recent shadow-run quality gate is still running."
        elif sample_count < spec.min_shadow_runs:
            blocking_reason = (
                "Recent shadow-run quality gate has not collected enough runs "
                f"({sample_count}/{spec.min_shadow_runs})."
            )
        else:
            blocking_reason = (
                "Recent shadow-run success rate is below the workflow threshold "
                f"({success_rate:.2f}/{spec.min_shadow_success_rate:.2f})."
            )
        next_action_cta = "run_shadow_quality"

    return {
        "status": normalized,
        "sample_count": sample_count,
        "success_rate": success_rate,
        "required_sample_count": spec.min_shadow_runs,
        "required_success_rate": spec.min_shadow_success_rate,
        "last_run_at": last_run_at,
        "blocking_reason": blocking_reason,
        "next_action_cta": next_action_cta,
        "evaluated_at": now.isoformat(),
    }


def _workflow_payloads(connector_configs: Iterable[Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for config in connector_configs:
        root = _config_dict(config)
        for key in (
            "marketing_workflows",
            "cmo_workflows",
            "workflow_activation",
            "cmo_workflow_activation",
        ):
            value = root.get(key)
            if isinstance(value, dict):
                source = value.get("workflows") if isinstance(value.get("workflows"), dict) else value
                for workflow_key, workflow_payload in source.items():
                    normalized_key = _normalize_key(workflow_key)
                    if isinstance(workflow_payload, dict) and normalized_key:
                        payloads[normalized_key] = {
                            **payloads.get(normalized_key, {}),
                            **workflow_payload,
                        }
    return payloads


def _workflow_mode(payload: dict[str, Any]) -> str:
    raw = _normalize_key(payload.get("mode") or payload.get("configured_mode") or "shadow")
    return raw if raw in PROMOTABLE_MODES else "shadow"


def _is_promoted(payload: dict[str, Any], mode: str) -> bool:
    return bool(
        mode == "active"
        or payload.get("promoted") is True
        or _string_or_none(payload.get("promoted_at"))
        or _string_or_none(payload.get("promoted_by"))
    )


def _accepted_partial_backfills(payload: dict[str, Any]) -> set[str]:
    raw = (
        payload.get("accepted_partial_backfills")
        or payload.get("accepted_partial_backfill_sources")
        or []
    )
    categories = payload.get("accepted_partial_backfill_categories") or []
    values: set[str] = set()
    for item in _list_from_value(raw):
        values.add(_normalize_key(item))
    for item in _list_from_value(categories):
        values.add(str(item).strip())
    return {item for item in values if item}


def _partial_accepted(key: str, category: str, accepted_partials: set[str]) -> bool:
    return (
        "all" in accepted_partials
        or key in accepted_partials
        or category in accepted_partials
        or _normalize_key(category) in accepted_partials
    )


def _blocked_cta(blockers: list[str]) -> str:
    text = " ".join(blockers).lower()
    if "escalation route" in text:
        return "configure_escalation_matrix"
    if "marketing policy manifest" in text:
        return "configure_marketing_policy_manifest"
    if "timeout" in text:
        return "configure_approval_timeout_policy"
    if "audit" in text:
        return "configure_decision_audit_package"
    if "connector" in text:
        return "fix_required_connector"
    if "mapping" in text:
        return "fix_required_mapping"
    if "backfill" in text:
        return "complete_backfill"
    if "owner" in text or "policy" in text:
        return "configure_policy_owner"
    return "resolve_promotion_blocker"


def _configured_rows_for_category(
    setup_rows: list[dict[str, Any]],
    category: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in setup_rows
        if str(row.get("category") or "").strip() == category
        and str(row.get("configured_status") or "").strip() != "unconfigured"
    ]


def _health(row: dict[str, Any]) -> str:
    return str(row.get("health_status") or "missing").strip().lower()


def _contracts_for_category(
    contract_rows: list[dict[str, Any]],
    category: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in contract_rows
        if str(row.get("category") or "").strip() == category
        and str(row.get("configured_status") or "") != "unconfigured"
    ]


def _workflow_row_by_key(
    workflow_rows: Iterable[dict[str, Any]],
    workflow_key: str,
) -> dict[str, Any] | None:
    normalized = _normalize_key(workflow_key)
    for row in workflow_rows:
        if _normalize_key(row.get("workflow_key")) == normalized:
            return row
    return None


def _config_dict(config: Any | None) -> dict[str, Any]:
    value = getattr(config, "config", None)
    return value if isinstance(value, dict) else {}


def _list_from_value(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
