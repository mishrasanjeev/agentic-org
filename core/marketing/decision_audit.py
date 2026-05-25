"""Structured CMO decision audit packages.

CMO-6.3 keeps marketing governance evidence portable until a persistent audit
table is introduced. Callers receive deterministic, WORM-ready package objects
and canonical JSON serialization that can later be stored unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

DECISION_AUDIT_SCHEMA_VERSION = "2026-05-23.cmo-6.3"

CMO_DECISION_AUDIT_EVENT_TYPES = (
    "campaign_launch",
    "budget_change",
    "content_publish",
    "email_send",
    "landing_page_change",
    "target_account_list_change",
    "crisis_public_response",
    "high_risk_copy_pricing_legal_claim",
    "workflow_promotion",
    "approval_timeout",
    "policy_decision",
    "escalation_decision",
    "connector_degraded_failure_decision",
    "external_write_attempt",
    "external_write_confirmation",
    "external_write_rejection",
    "external_write_timeout",
    "external_write_idempotent_recovery",
    "external_write_retry_scheduled",
    "external_write_unconfirmed",
    "draft_created",
    "shadow_only",
    "override",
)

MAJOR_CUSTOMER_FACING_EVENT_TYPES = {
    "campaign_launch",
    "budget_change",
    "content_publish",
    "email_send",
    "landing_page_change",
    "target_account_list_change",
    "crisis_public_response",
    "high_risk_copy_pricing_legal_claim",
    "workflow_promotion",
    "external_write_attempt",
    "external_write_confirmation",
    "external_write_rejection",
    "external_write_timeout",
    "external_write_idempotent_recovery",
    "external_write_retry_scheduled",
    "external_write_unconfirmed",
}

DEFAULT_REQUIRED_AUDIT_EVIDENCE_CLASSES = (
    "actor_identity",
    "input_snapshot_hash",
    "policy_result",
    "source_refs",
    "workflow_context",
    "final_outcome",
)

SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "encrypted",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)

ACTION_EVENT_TYPE_MAP = {  # enterprise-gate: process-local-ok reason=static-action-event-routing-map
    "activate_campaign": "campaign_launch",
    "create_campaign": "campaign_launch",
    "create_linkedin_campaign": "campaign_launch",
    "launch_campaign": "campaign_launch",
    "setup_google_campaign": "campaign_launch",
    "setup_meta_campaign": "campaign_launch",
    "mutate_ad_budget": "budget_change",
    "pause_campaign": "budget_change",
    "spend": "budget_change",
    "update_ad_budget": "budget_change",
    "publish": "content_publish",
    "publish_to_wordpress": "content_publish",
    "schedule_content": "content_publish",
    "add_to_drip": "email_send",
    "send": "email_send",
    "send_email": "email_send",
    "send_to_segment": "email_send",
    "send_winner": "email_send",
    "start_nurture_sequence": "email_send",
    "create_abm_campaign": "campaign_launch",
    "launch_abm_campaign": "campaign_launch",
    "launch_competitive_campaign": "campaign_launch",
    "set_abm_budget": "budget_change",
    "landing_page_change": "landing_page_change",
    "apply_redirect": "landing_page_change",
    "publish_landing_page": "landing_page_change",
    "publish_seo_change": "landing_page_change",
    "submit_url_to_index": "landing_page_change",
    "update_canonical_tag": "landing_page_change",
    "update_landing_page": "landing_page_change",
    "update_page_metadata": "landing_page_change",
    "update_robots_txt": "landing_page_change",
    "update_sitemap": "landing_page_change",
    "query_target_accounts": "target_account_list_change",
    "sync_target_accounts": "target_account_list_change",
    "target_account_list_change": "target_account_list_change",
    "update_target_accounts": "target_account_list_change",
    "crisis_response": "crisis_public_response",
    "detect_crisis": "crisis_public_response",
    "publish_brand_response": "crisis_public_response",
    "publish_competitive_response": "crisis_public_response",
    "public_response": "crisis_public_response",
    "brand_claim": "high_risk_copy_pricing_legal_claim",
    "claims_review": "high_risk_copy_pricing_legal_claim",
    "comparative_claim": "high_risk_copy_pricing_legal_claim",
    "high_risk_copy": "high_risk_copy_pricing_legal_claim",
    "legal_claim": "high_risk_copy_pricing_legal_claim",
    "pricing_claim": "high_risk_copy_pricing_legal_claim",
}

EXTERNAL_WRITE_FINAL_EVENT_TYPES = {
    "write_confirmed": "external_write_confirmation",
    "idempotent_recovered": "external_write_idempotent_recovery",
    "rejected": "external_write_rejection",
    "timeout_unknown": "external_write_timeout",
    "retry_scheduled": "external_write_retry_scheduled",
    "write_unconfirmed": "external_write_unconfirmed",
    "accepted": "external_write_unconfirmed",
    "draft_created": "draft_created",
    "shadow_only": "shadow_only",
}


def build_cmo_decision_audit_package(
    context: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic, secret-redacted CMO decision audit package."""

    ctx = context if isinstance(context, Mapping) else {}
    timestamp = _timestamp(ctx, now)
    action = _normalize_key(ctx.get("action") or ctx.get("blocked_action"))
    event_type = _event_type(ctx, action)
    policy_result = _clean_ref(ctx.get("policy_result") or ctx.get("marketing_policy_decision"))
    escalation_result = _clean_ref(ctx.get("escalation_result") or ctx.get("escalation_decision"))
    approval_result = _clean_ref(ctx.get("approval_result") or ctx.get("approval_decision"))
    timeout_result = _clean_ref(ctx.get("timeout_result") or ctx.get("approval_timeout_decision"))
    external_write_result = _clean_ref(
        ctx.get("external_write_result") or ctx.get("external_write_decision")
    )
    input_snapshot = _input_snapshot(ctx, action, event_type)
    package: dict[str, Any] = {
        "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
        "audit_id": None,
        "audit_reference": None,
        "tenant_id": _string_or_none(ctx.get("tenant_id")),
        "company_id": _string_or_none(ctx.get("company_id")),
        "workflow_id": _string_or_none(ctx.get("workflow_id")),
        "workflow_run_id": _string_or_none(ctx.get("workflow_run_id")),
        "run_id": _string_or_none(ctx.get("run_id")),
        "step_id": _string_or_none(ctx.get("step_id")),
        "agent": _string_or_none(ctx.get("agent") or ctx.get("agent_type") or ctx.get("agent_id")),
        "action": action or None,
        "capability": _string_or_none(ctx.get("capability")),
        "event_type": event_type,
        "decision_type": _decision_type(ctx),
        "actor_type": _actor_type(ctx.get("actor_type")),
        "actor_id": _string_or_none(ctx.get("actor_id") or ctx.get("user_id") or ctx.get("agent_id")),
        "actor_role": _string_or_none(ctx.get("actor_role") or ctx.get("user_role")),
        "created_at": timestamp.isoformat(),
        "decided_at": timestamp.isoformat(),
        "input_snapshot_hash": _fingerprint(input_snapshot),
        "input_snapshot": input_snapshot,
        "source_refs": _source_refs(ctx),
        "connector_refs": _connector_refs(ctx),
        "policy_result_ref": _result_ref(policy_result, "policy"),
        "policy_result": policy_result,
        "escalation_result_ref": _result_ref(escalation_result, "escalation"),
        "escalation_result": escalation_result,
        "approval_result_ref": _result_ref(approval_result, "approval"),
        "approval_result": approval_result,
        "timeout_result_ref": _result_ref(timeout_result, "timeout"),
        "timeout_result": timeout_result,
        "external_write_result_ref": _result_ref(external_write_result, "external_write"),
        "external_write_result": external_write_result,
        "rationale": _string_or_none(ctx.get("rationale") or ctx.get("reason")) or "",
        "alternatives_considered": _string_list(ctx.get("alternatives_considered") or ctx.get("alternatives")),
        "risk_flags": _risk_flags(ctx),
        "confidence": _float_or_none(ctx.get("confidence")),
        "final_outcome": _string_or_none(ctx.get("final_outcome") or ctx.get("outcome")) or "unknown",
        "override": _override(ctx),
        "worm_ready": True,
        "immutable": True,
        "redaction": {
            "status": "applied",
            "redacted_markers": list(SECRET_KEY_MARKERS),
        },
        "serialization": {
            "format": "canonical_json",
            "sort_keys": True,
            "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
        },
    }
    package["audit_id"] = _audit_id(package)
    package["audit_reference"] = f"cmo_decision_audit:{package['audit_id']}"
    package["serialization"]["sha256"] = _fingerprint(
        {
            key: value
            for key, value in package.items()
            if key != "serialization"
        }
    )
    return package


def serialize_cmo_decision_audit_package(package: Mapping[str, Any]) -> str:
    """Return canonical WORM-ready JSON for a decision audit package."""

    return _canonical_json(_redact(package))


def build_policy_decision_audit(
    decision: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_decision = _without_audit(decision)
    return build_cmo_decision_audit_package(
        {
            **(dict(context) if isinstance(context, Mapping) else {}),
            "event_type": "policy_decision",
            "decision_type": clean_decision.get("decision"),
            "workflow_id": clean_decision.get("affected_workflow"),
            "action": clean_decision.get("affected_action"),
            "policy_result": clean_decision,
            "source_refs": _policy_source_refs(clean_decision),
            "rationale": clean_decision.get("reason"),
            "final_outcome": clean_decision.get("decision"),
            "actor_type": "system",
        },
        now=now,
    )


def build_escalation_decision_audit(
    decision: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_decision = _without_audit(decision)
    return build_cmo_decision_audit_package(
        {
            **(dict(context) if isinstance(context, Mapping) else {}),
            "event_type": "escalation_decision",
            "decision_type": clean_decision.get("decision"),
            "workflow_id": clean_decision.get("workflow_id"),
            "workflow_run_id": clean_decision.get("workflow_run_id"),
            "run_id": clean_decision.get("run_id"),
            "step_id": clean_decision.get("step_id"),
            "action": clean_decision.get("action"),
            "escalation_result": clean_decision,
            "rationale": clean_decision.get("reason"),
            "risk_flags": [clean_decision.get("severity")],
            "final_outcome": clean_decision.get("decision"),
            "actor_type": "system",
        },
        now=now,
    )


def build_approval_timeout_decision_audit(
    decision: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_decision = _without_audit(decision)
    audit_evidence = clean_decision.get("audit_evidence")
    audit_evidence = audit_evidence if isinstance(audit_evidence, Mapping) else {}
    return build_cmo_decision_audit_package(
        {
            "event_type": "approval_timeout",
            "decision_type": clean_decision.get("outcome"),
            "workflow_id": clean_decision.get("workflow_id") or audit_evidence.get("workflow_id"),
            "workflow_run_id": clean_decision.get("workflow_run_id") or audit_evidence.get("workflow_run_id"),
            "run_id": clean_decision.get("run_id") or audit_evidence.get("run_id"),
            "step_id": clean_decision.get("step_id") or audit_evidence.get("step_id"),
            "action": clean_decision.get("blocked_action") or audit_evidence.get("blocked_action"),
            "approval_result": clean_decision,
            "timeout_result": audit_evidence or clean_decision,
            "policy_result": clean_decision.get("marketing_policy_decision"),
            "escalation_result": clean_decision.get("escalation_decision"),
            "rationale": clean_decision.get("safe_fallback_message"),
            "final_outcome": clean_decision.get("outcome"),
            "actor_type": "system",
        },
        now=now,
    )


def build_external_write_decision_audit(
    event: Mapping[str, Any],
    *,
    event_type: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_event = _without_audit(event)
    final_state = _normalize_key(clean_event.get("outcome") or clean_event.get("final_state"))
    resolved_event_type = event_type or EXTERNAL_WRITE_FINAL_EVENT_TYPES.get(final_state, "external_write_attempt")
    return build_cmo_decision_audit_package(
        {
            **clean_event,
            "event_type": resolved_event_type,
            "decision_type": clean_event.get("outcome") or clean_event.get("final_state") or "attempted",
            "external_write_result": clean_event,
            "action": clean_event.get("action"),
            "connector_key": clean_event.get("connector_key"),
            "rationale": clean_event.get("reason"),
            "final_outcome": clean_event.get("outcome") or clean_event.get("final_state") or "attempted",
            "actor_type": clean_event.get("actor_type") or "agent",
        },
        now=now,
    )


def build_connector_degraded_decision_audit(
    degraded_mode: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_mode = _without_audit(degraded_mode)
    return build_cmo_decision_audit_package(
        {
            "event_type": "connector_degraded_failure_decision",
            "decision_type": clean_mode.get("status"),
            "connector_key": next(iter(clean_mode.get("affected_connectors") or []), None),
            "source_refs": clean_mode.get("affected_connectors"),
            "risk_flags": [clean_mode.get("failure_class"), clean_mode.get("status")],
            "confidence": 1.0 - float(clean_mode.get("confidence_impact") or 0.0),
            "rationale": clean_mode.get("reason"),
            "final_outcome": clean_mode.get("status"),
            "escalation_result": clean_mode.get("escalation_decision"),
            "actor_type": "system",
        },
        now=now,
    )


def build_workflow_promotion_audit_package(
    workflow_row: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    row = _without_audit(workflow_row)
    return build_cmo_decision_audit_package(
        {
            "event_type": "workflow_promotion",
            "decision_type": row.get("state"),
            "workflow_id": row.get("workflow_key"),
            "action": "workflow_promotion",
            "policy_result": row.get("marketing_policy"),
            "escalation_result": row.get("escalation_matrix"),
            "approval_result": row.get("approval_timeout_policy"),
            "rationale": "; ".join(str(item) for item in row.get("blocked_reasons") or [])
            or "Workflow activation gate evaluated.",
            "risk_flags": row.get("degraded_reasons") or row.get("blocked_reasons") or [],
            "final_outcome": row.get("state"),
            "actor_type": "system",
        },
        now=now,
    )


def build_workflow_decision_audit_status(
    workflow_key: str,
    actions: Iterable[Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else {}
    required_actions = _unique(_normalize_key(action) for action in actions)
    required_event_types = _unique(
        action_event_type(action, payload)
        for action in required_actions
    )
    if not required_actions:
        status = "not_required"
        next_action = "none"
    elif _audit_disabled(payload):
        status = "missing_audit_evidence"
        next_action = "configure_decision_audit_package"
    else:
        status = "ready"
        next_action = "none"
    return {
        "workflow_key": _normalize_key(workflow_key),
        "status": status,
        "required_actions": required_actions,
        "required_event_types": required_event_types,
        "required_evidence_classes": list(DEFAULT_REQUIRED_AUDIT_EVIDENCE_CLASSES),
        "worm_ready": status == "ready",
        "missing_audit_actions": required_actions if status == "missing_audit_evidence" else [],
        "next_action_cta": next_action,
        "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
    }


def has_decision_audit_evidence_for_step(step: Mapping[str, Any] | None) -> bool:
    if not isinstance(step, Mapping) or _audit_disabled(step):
        return False
    evidence_keys = (
        "audit_evidence",
        "audit_package",
        "audit_reference",
        "audit_refs",
        "decision_audit",
        "decision_audit_package",
        "decision_audit_ref",
        "decision_audit_required",
        "audit_package_required",
        "required_audit_evidence",
    )
    return any(_truthy(step.get(key)) for key in evidence_keys)


def build_marketing_decision_audit_projection(
    sources: Iterable[Any] | None = None,
) -> dict[str, Any]:
    disabled = False
    for source in sources or []:
        config = _config_dict(source)
        if _audit_disabled(config):
            disabled = True
            break
    summary = {
        "status": "missing_audit_evidence" if disabled else "ready",
        "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
        "event_type_count": len(CMO_DECISION_AUDIT_EVENT_TYPES),
        "major_event_types": sorted(MAJOR_CUSTOMER_FACING_EVENT_TYPES),
        "required_evidence_classes": list(DEFAULT_REQUIRED_AUDIT_EVIDENCE_CLASSES),
        "secret_redaction": "applied",
        "worm_ready": not disabled,
        "next_action_cta": "configure_decision_audit_package" if disabled else "none",
    }
    return {
        "marketing_decision_audit": {
            "schema_version": DECISION_AUDIT_SCHEMA_VERSION,
            "event_types": list(CMO_DECISION_AUDIT_EVENT_TYPES),
            "required_evidence_classes": list(DEFAULT_REQUIRED_AUDIT_EVIDENCE_CLASSES),
            "serialization": {
                "format": "canonical_json",
                "sort_keys": True,
                "worm_ready": not disabled,
            },
        },
        "marketing_decision_audit_summary": summary,
    }


def action_event_type(action: Any, context: Mapping[str, Any] | None = None) -> str:
    ctx = context if isinstance(context, Mapping) else {}
    explicit = _normalize_key(ctx.get("event_type") or ctx.get("audit_event_type"))
    if explicit in CMO_DECISION_AUDIT_EVENT_TYPES:
        return explicit
    normalized_action = _normalize_key(action)
    if _truthy(ctx.get("crisis_response")) or _truthy(ctx.get("public_response")):
        return "crisis_public_response"
    if _truthy(ctx.get("pricing_claim")) or _truthy(ctx.get("legal_claim")) or _truthy(ctx.get("high_risk_copy")):
        return "high_risk_copy_pricing_legal_claim"
    return ACTION_EVENT_TYPE_MAP.get(normalized_action, "policy_decision")


def _event_type(ctx: Mapping[str, Any], action: str) -> str:
    explicit = _normalize_key(ctx.get("event_type") or ctx.get("audit_event_type"))
    if explicit in CMO_DECISION_AUDIT_EVENT_TYPES:
        return explicit
    return action_event_type(action, ctx)


def _decision_type(ctx: Mapping[str, Any]) -> str:
    return _normalize_key(
        ctx.get("decision_type")
        or ctx.get("decision")
        or ctx.get("final_state")
        or ctx.get("status")
        or ctx.get("outcome")
        or "decision"
    )


def _input_snapshot(ctx: Mapping[str, Any], action: str, event_type: str) -> dict[str, Any]:
    for key in ("input_snapshot", "inputs", "request", "external_write_request", "write_request"):
        value = ctx.get(key)
        if isinstance(value, Mapping):
            return _redact(value)
    return _redact(
        {
            "event_type": event_type,
            "action": action,
            "workflow_id": ctx.get("workflow_id"),
            "workflow_run_id": ctx.get("workflow_run_id") or ctx.get("run_id"),
            "step_id": ctx.get("step_id"),
            "source_refs": ctx.get("source_refs"),
            "connector_key": ctx.get("connector_key"),
        }
    )


def _source_refs(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = ctx.get("source_refs") or ctx.get("source_references") or ctx.get("sources")
    refs: list[dict[str, Any]] = []
    for item in _list_from_value(raw):
        if isinstance(item, Mapping):
            refs.append(_redact(dict(item)))
        else:
            refs.append({"ref": str(item)})
    return refs


def _connector_refs(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = ctx.get("connector_refs") or ctx.get("connector_references")
    refs: list[dict[str, Any]] = []
    for item in _list_from_value(raw):
        if isinstance(item, Mapping):
            refs.append(_redact(dict(item)))
        else:
            refs.append({"connector_key": str(item)})
    connector_key = _string_or_none(ctx.get("connector_key"))
    if connector_key and not any(ref.get("connector_key") == connector_key for ref in refs):
        refs.append(
            {
                "connector_key": connector_key,
                "external_object_id": _string_or_none(ctx.get("external_object_id")),
                "source_url": _string_or_none(ctx.get("source_url")),
            }
        )
    return refs


def _policy_source_refs(decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for rule in decision.get("matched_rules") or []:
        if isinstance(rule, Mapping):
            refs.append(
                {
                    "type": "marketing_policy_rule",
                    "policy_id": decision.get("policy_id"),
                    "version": decision.get("version"),
                    "rule_id": rule.get("rule_id"),
                }
            )
    return refs


def _result_ref(result: Mapping[str, Any] | None, prefix: str) -> str | None:
    if not isinstance(result, Mapping) or not result:
        return None
    explicit = _string_or_none(
        result.get("audit_reference")
        or result.get("decision_audit_ref")
        or result.get("audit_id")
    )
    if explicit:
        return explicit
    return f"{prefix}:{_fingerprint(_without_audit(result))[:20]}"


def _override(ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    override = ctx.get("override")
    if isinstance(override, Mapping):
        source = override
    elif any(ctx.get(key) for key in ("override_reason", "replacement_action", "overridden_by")):
        source = ctx
    else:
        return None
    return {
        "overridden": True,
        "actor_type": _actor_type(source.get("actor_type") or source.get("override_actor_type") or "user"),
        "actor_id": _string_or_none(source.get("actor_id") or source.get("overridden_by")),
        "actor_role": _string_or_none(source.get("actor_role") or source.get("override_actor_role")),
        "reason": _string_or_none(source.get("reason") or source.get("override_reason")),
        "replacement_action": _string_or_none(source.get("replacement_action")),
        "replacement_action_ref": _string_or_none(source.get("replacement_action_ref")),
    }


def _audit_id(package: Mapping[str, Any]) -> str:
    explicit = _string_or_none(package.get("audit_id"))
    if explicit:
        return explicit
    source = {
        key: value
        for key, value in package.items()
        if key not in {"audit_id", "audit_reference", "serialization"}
    }
    return f"cmo_audit_{_fingerprint(source)[:24]}"


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(_redact(value), sort_keys=True, separators=(",", ":"), default=str)


def _without_audit(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_audit(item)
            for key, item in value.items()
            if key
            not in {
                "decision_audit",
                "decision_audit_package",
                "audit_package",
                "serialized_audit_package",
            }
        }
    if isinstance(value, list):
        return [_without_audit(item) for item in value]
    return value


def _clean_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return _redact(_without_audit(value))


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _looks_secret_key(text_key):
                result[text_key] = "[REDACTED]"
            else:
                result[text_key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _looks_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in SECRET_KEY_MARKERS)


def _timestamp(ctx: Mapping[str, Any], now: datetime | None) -> datetime:
    for key in ("decided_at", "timestamp", "created_at", "attempted_at", "confirmed_at"):
        parsed = _parse_datetime(ctx.get(key))
        if parsed is not None:
            return parsed
    return _ensure_aware(now) or datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware(parsed)


def _ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _actor_type(value: Any) -> str:
    normalized = _normalize_key(value)
    return normalized if normalized in {"agent", "user", "system"} else "system"


def _risk_flags(ctx: Mapping[str, Any]) -> list[str]:
    flags = _string_list(ctx.get("risk_flags") or ctx.get("risks"))
    for field in (
        "failure_class",
        "severity",
        "final_state",
        "status",
        "policy_missing",
    ):
        value = _string_or_none(ctx.get(field))
        if value:
            flags.append(value)
    return _unique(flags)


def _list_from_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _list_from_value(value) if str(item).strip()]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _config_dict(config: Any | None) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    value = getattr(config, "config", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _audit_disabled(source: Mapping[str, Any]) -> bool:
    return any(
        _truthy(source.get(key))
        for key in (
            "decision_audit_disabled",
            "marketing_decision_audit_disabled",
            "cmo_decision_audit_disabled",
            "audit_package_disabled",
        )
    )


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
