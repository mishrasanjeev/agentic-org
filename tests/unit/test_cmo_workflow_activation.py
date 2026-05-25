from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from core.marketing.connector_setup import build_marketing_connector_setup
from core.marketing.data_readiness import build_marketing_data_readiness
from core.marketing.workflow_activation import (
    build_cmo_workflow_activation,
    evaluate_cmo_workflow_external_write,
)

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _connector_config(
    connector_name: str,
    *,
    config: dict,
    health_status: str = "healthy",
    last_sync_at: datetime | None = None,
    sync_error: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        connector_name=connector_name,
        display_name=None,
        auth_type="oauth2",
        credentials_encrypted={"_encrypted": "enc"},
        config=config,
        status="configured",
        last_health_check=NOW,
        health_status=health_status,
        last_sync_at=last_sync_at or NOW - timedelta(hours=1),
        sync_error=sync_error,
    )


def _backfill(status: str = "completed") -> dict:
    return {
        "status": status,
        "requested_start": "2025-05-01",
        "requested_end": "2026-05-23",
        "records_discovered": 100,
        "records_imported": 98 if status in {"completed", "partial"} else 0,
        "records_skipped": 1,
        "records_failed": 1 if status == "partial" else 0,
        "last_run_at": "2026-05-23T10:00:00+00:00",
        "blocking_reason": "Vendor export failed" if status in {"failed", "blocked"} else None,
    }


def _contract(**overrides: object) -> dict:
    base = {
        "idempotency_supported": True,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 0,
            "remaining_attempts": 3,
            "idempotency_supported": True,
            "idempotency_key": "mkt-setup-001",
        },
    }
    base.update(overrides)
    return base


def _base_configs() -> list[SimpleNamespace]:
    updated_at = "2026-05-01T00:00:00+00:00"
    hubspot_mapping = {
        "lifecycle_stages": {
            "source_field": "lifecyclestage",
            "stage_map": {"mql": "marketingqualifiedlead", "sql": "salesqualifiedlead"},
            "updated_at": updated_at,
        },
        "opportunity_revenue": {
            "amount_field": "amount",
            "close_date_field": "closedate",
            "currency_field": "deal_currency_code",
            "updated_at": updated_at,
        },
        "account_domains": {
            "domain_field": "website",
            "updated_at": updated_at,
        },
        "consent_unsubscribe": {
            "consent_field": "marketing_opt_in",
            "unsubscribe_field": "unsubscribed",
            "updated_at": updated_at,
        },
        "fiscal_calendar": {
            "fiscal_year_start_month": 4,
            "updated_at": updated_at,
        },
        "currency": {
            "currency": "USD",
            "updated_at": updated_at,
        },
        "timezone": {
            "timezone": "Asia/Kolkata",
            "updated_at": updated_at,
        },
    }
    campaign_mapping = {
        "campaign_ids": {
            "campaign_id_field": "campaign_id",
            "updated_at": updated_at,
        },
        "utm_fields": {
            "source": "utm_source",
            "medium": "utm_medium",
            "campaign": "utm_campaign",
            "updated_at": updated_at,
        },
    }
    mailchimp_mapping = {
        **campaign_mapping,
        "consent_unsubscribe": {
            "consent_field": "marketing_permission",
            "unsubscribe_field": "unsubscribe_status",
            "updated_at": updated_at,
        },
    }
    return [
        _connector_config(
            "hubspot",
            config={
                "owner": "revops@example.com",
                "account_id": "portal-123",
                "granted_scopes": [
                    "crm.objects.contacts.read",
                    "crm.objects.deals.read",
                    "automation",
                ],
                "data_coverage_status": "ready",
                "marketing_field_mapping": hubspot_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
        _connector_config(
            "google_ads",
            config={
                "owner": "growth@example.com",
                "customer_id": "123-456-7890",
                "granted_scopes": ["https://www.googleapis.com/auth/adwords"],
                "marketing_field_mapping": campaign_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
        _connector_config(
            "ga4",
            config={
                "owner": "analytics@example.com",
                "property_id": "properties/123",
                "granted_scopes": ["https://www.googleapis.com/auth/analytics.readonly"],
                "marketing_field_mapping": campaign_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": {
                    "retry_budget": {
                        "max_attempts": 3,
                        "attempts_used": 0,
                        "remaining_attempts": 3,
                    },
                },
            },
        ),
        _connector_config(
            "mailchimp",
            config={
                "owner": "email@example.com",
                "audience_id": "aud-1",
                "marketing_field_mapping": mailchimp_mapping,
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        ),
    ]


def _workflow_config(
    *,
    mode: str = "shadow",
    promoted: bool = False,
    approval_owner: str | None = "cmo@example.com",
    policy_owner: str | None = "legal@example.com",
    quality_status: str = "passed",
    sample_count: int = 5,
    success_rate: float = 0.95,
    accepted_partial_backfills: list[str] | None = None,
) -> dict:
    payload = {
        "mode": mode,
        "promoted": promoted,
        "approval_owner": approval_owner,
        "policy_owner": policy_owner,
        "shadow_quality": {
            "status": quality_status,
            "sample_count": sample_count,
            "success_rate": success_rate,
            "last_run_at": "2026-05-23T09:00:00+00:00",
        },
    }
    if accepted_partial_backfills:
        payload["accepted_partial_backfills"] = accepted_partial_backfills
    return payload


def _with_workflow(
    configs: list[SimpleNamespace],
    workflow_key: str,
    payload: dict,
) -> list[SimpleNamespace]:
    configs = deepcopy(configs)
    workflows = configs[0].config.setdefault("marketing_workflows", {})
    workflows[workflow_key] = payload
    return configs


def _activation(configs: list[SimpleNamespace]) -> dict:
    setup = build_marketing_connector_setup(configs, now=NOW)
    readiness = build_marketing_data_readiness(setup, configs, now=NOW)
    return build_cmo_workflow_activation(setup, readiness, configs, now=NOW)


def _workflow(activation: dict, key: str) -> dict:
    return next(row for row in activation["workflow_activation_status"] if row["workflow_key"] == key)


def test_workflows_default_to_shadow_or_blocked_never_active() -> None:
    activation = _activation(_base_configs())

    campaign = _workflow(activation, "campaign_launch")
    assert campaign["state"] == "promotion_blocked"
    assert campaign["external_writes_allowed"] is False
    assert all(row["state"] != "active" for row in activation["workflow_activation_status"])

    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(quality_status="not_measured", sample_count=0, success_rate=0),
    )
    shadow = _workflow(_activation(configs), "campaign_launch")
    assert shadow["state"] == "shadow"
    assert shadow["next_action_cta"] == "run_shadow_quality"


def test_promotion_blocked_when_required_connector_is_missing_or_unhealthy() -> None:
    configs = [config for config in _base_configs() if config.connector_name != "google_ads"]
    configs = _with_workflow(configs, "campaign_launch", _workflow_config())

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "fix_required_connector"
    assert any("Ads connector" in reason for reason in campaign["blocked_reasons"])


def test_promotion_blocked_when_required_mapping_is_invalid() -> None:
    configs = _base_configs()
    configs[0].config["marketing_field_mapping"]["currency"] = {
        "currency": "ZZZ",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }
    configs = _with_workflow(configs, "campaign_launch", _workflow_config())

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "fix_required_mapping"
    assert any("Currency" in reason and "invalid" in reason for reason in campaign["blocked_reasons"])


def test_promotion_blocked_when_required_backfill_failed_or_blocked() -> None:
    configs = _base_configs()
    configs[1].config["marketing_backfill"] = _backfill("failed")
    configs = _with_workflow(configs, "campaign_launch", _workflow_config())

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "complete_backfill"
    assert any("Ads backfill" in reason for reason in campaign["blocked_reasons"])


def test_promotion_blocked_when_approval_or_policy_owner_missing() -> None:
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(approval_owner=None, policy_owner=None),
    )

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "configure_policy_owner"
    assert "Approval owner is not configured." in campaign["blocked_reasons"]
    assert "Policy owner is not configured." in campaign["blocked_reasons"]


def test_promotion_blocked_when_approval_timeout_policy_is_missing() -> None:
    payload = _workflow_config()
    payload["approval_timeout_policy_disabled"] = True
    configs = _with_workflow(_base_configs(), "campaign_launch", payload)

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_blocked"
    assert campaign["next_action_cta"] == "configure_approval_timeout_policy"
    assert campaign["approval_timeout_policy"]["status"] == "missing_policy"
    assert any("Approval timeout policy" in reason for reason in campaign["blocked_reasons"])


def test_promotion_ready_when_prerequisites_pass_but_workflow_not_promoted() -> None:
    configs = _with_workflow(_base_configs(), "campaign_launch", _workflow_config())

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "promotion_ready"
    assert campaign["next_action_cta"] == "promote_workflow"
    assert campaign["external_writes_allowed"] is False
    assert campaign["approval_timeout_policy"]["status"] == "ready"


def test_active_only_when_explicitly_promoted() -> None:
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(mode="active", promoted=True),
    )

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "active"
    assert campaign["external_writes_allowed"] is True


def test_shadow_and_ready_modes_prevent_external_write_actions() -> None:
    configs = _with_workflow(_base_configs(), "campaign_launch", _workflow_config())
    rows = _activation(configs)["workflow_activation_status"]

    publish = evaluate_cmo_workflow_external_write(rows, "campaign_launch", "publish")
    draft = evaluate_cmo_workflow_external_write(rows, "campaign_launch", "draft")

    assert publish["allowed"] is False
    assert publish["state"] == "promotion_ready"
    assert "promoted to active" in publish["reason"]
    assert draft["allowed"] is True


def test_active_workflow_can_attempt_external_write_subject_to_downstream_confirmation() -> None:
    configs = _with_workflow(
        _base_configs(),
        "campaign_launch",
        _workflow_config(mode="active", promoted=True),
    )
    rows = _activation(configs)["workflow_activation_status"]

    decision = evaluate_cmo_workflow_external_write(rows, "campaign_launch", "publish")

    assert decision["allowed"] is True
    assert decision["state"] == "active"
    assert "downstream connector confirmation" in decision["reason"]


def test_per_workflow_promotion_does_not_activate_unrelated_workflows() -> None:
    configs = _base_configs()
    configs = _with_workflow(
        configs,
        "campaign_launch",
        _workflow_config(mode="active", promoted=True),
    )
    configs = _with_workflow(configs, "lead_nurture", _workflow_config())

    activation = _activation(configs)

    assert _workflow(activation, "campaign_launch")["state"] == "active"
    assert _workflow(activation, "lead_nurture")["state"] == "promotion_ready"
    assert _workflow(activation, "daily_spend_optimization")["state"] != "active"


def test_degraded_when_optional_connector_or_data_is_stale_or_partial() -> None:
    configs = _base_configs()
    configs.append(
        _connector_config(
            "brandwatch",
            config={
                "owner": "brand@example.com",
                "project_id": "brand-1",
                "granted_scopes": ["read_mentions", "read_queries"],
                "marketing_backfill": _backfill("partial"),
            },
            health_status="healthy",
            last_sync_at=NOW - timedelta(days=7),
        )
    )
    configs = _with_workflow(configs, "weekly_marketing_report", _workflow_config())

    weekly = _workflow(_activation(configs), "weekly_marketing_report")

    assert weekly["state"] == "degraded"
    assert weekly["next_action_cta"] == "review_degraded_dependency"
    assert any("Brand" in reason for reason in weekly["degraded_reasons"])
    assert weekly["external_writes_allowed"] is False


def test_accepted_partial_required_backfill_degrades_instead_of_promoting_active() -> None:
    configs = _base_configs()
    configs[1].config["marketing_backfill"] = _backfill("partial")
    configs = _with_workflow(
        configs,
        "campaign_launch",
        _workflow_config(accepted_partial_backfills=["google_ads"]),
    )

    campaign = _workflow(_activation(configs), "campaign_launch")

    assert campaign["state"] == "degraded"
    assert any("accepted partial backfill" in reason for reason in campaign["degraded_reasons"])


def test_beta_brand_workflow_is_blocked_not_unavailable_without_required_connectors() -> None:
    activation = _activation(_base_configs())

    assert _workflow(activation, "social_publishing")["state"] == "promotion_blocked"
    assert _workflow(activation, "abm_sprint")["state"] == "promotion_blocked"
    assert _workflow(activation, "brand_crisis_response")["state"] == "promotion_blocked"
    assert _workflow(activation, "seo_sprint")["state"] == "promotion_blocked"
    assert "Required Brand connector" in " ".join(
        _workflow(activation, "brand_crisis_response")["blocked_reasons"]
    )
    assert "Required SEO connector" in " ".join(_workflow(activation, "seo_sprint")["blocked_reasons"])


def test_social_publishing_is_promotable_only_after_social_connector_and_gates_pass() -> None:
    updated_at = "2026-05-01T00:00:00+00:00"
    configs = _base_configs()
    configs.append(
        _connector_config(
            "buffer",
            config={
                "owner": "social@example.com",
                "account_id": "profile-123",
                "granted_scopes": ["profile:read", "updates:create"],
                "marketing_field_mapping": {
                    "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": updated_at},
                    "utm_fields": {
                        "source": "utm_source",
                        "medium": "utm_medium",
                        "campaign": "utm_campaign",
                        "updated_at": updated_at,
                    },
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs = _with_workflow(
        configs,
        "social_publishing",
        _workflow_config(mode="active", promoted=True),
    )

    social = _workflow(_activation(configs), "social_publishing")

    assert social["state"] == "active"
    assert social["external_writes_allowed"] is True
    assert social["write_connector_categories"] == ["Social"]


def test_abm_sprint_is_promotable_only_after_abm_connector_and_gates_pass() -> None:
    updated_at = "2026-05-01T00:00:00+00:00"
    configs = _base_configs()
    configs.append(
        _connector_config(
            "bombora",
            config={
                "owner": "revops@example.com",
                "account_id": "company-123",
                "marketing_field_mapping": {
                    "account_domains": {"domain_field": "domain", "updated_at": updated_at},
                    "lifecycle_stages": {
                        "source_field": "lifecycle_stage",
                        "stage_map": {"mql": "MQL", "sql": "SQL"},
                        "updated_at": updated_at,
                    },
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs = _with_workflow(
        configs,
        "abm_sprint",
        _workflow_config(mode="active", promoted=True),
    )

    abm = _workflow(_activation(configs), "abm_sprint")

    assert abm["state"] == "active"
    assert abm["external_writes_allowed"] is True
    assert abm["write_connector_categories"] == ["CRM", "Ads"]


def test_brand_crisis_response_is_promotable_only_after_brand_social_and_gates_pass() -> None:
    updated_at = "2026-05-01T00:00:00+00:00"
    configs = _base_configs()
    configs.append(
        _connector_config(
            "brandwatch",
            config={
                "owner": "brand@example.com",
                "project_id": "brand-project-123",
                "granted_scopes": ["read_mentions", "read_queries"],
                "marketing_field_mapping": {
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs.append(
        _connector_config(
            "buffer",
            config={
                "owner": "social@example.com",
                "account_id": "profile-123",
                "granted_scopes": ["profile:read", "updates:create"],
                "marketing_field_mapping": {
                    "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": updated_at},
                    "utm_fields": {
                        "source": "utm_source",
                        "medium": "utm_medium",
                        "campaign": "utm_campaign",
                        "updated_at": updated_at,
                    },
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs = _with_workflow(
        configs,
        "brand_crisis_response",
        _workflow_config(mode="active", promoted=True),
    )

    brand = _workflow(_activation(configs), "brand_crisis_response")

    assert brand["state"] == "active"
    assert brand["external_writes_allowed"] is True
    assert brand["write_connector_categories"] == ["Social"]


def test_seo_sprint_is_promotable_only_after_seo_analytics_cms_and_gates_pass() -> None:
    updated_at = "2026-05-01T00:00:00+00:00"
    configs = _base_configs()
    configs.append(
        _connector_config(
            "ahrefs",
            config={
                "owner": "seo@example.com",
                "project_id": "seo-project-123",
                "granted_scopes": ["read_keywords", "read_site_audit"],
                "marketing_field_mapping": {
                    "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": updated_at},
                    "utm_fields": {
                        "source": "utm_source",
                        "medium": "utm_medium",
                        "campaign": "utm_campaign",
                        "updated_at": updated_at,
                    },
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs.append(
        _connector_config(
            "wordpress",
            config={
                "owner": "web@example.com",
                "site_id": "site-123",
                "granted_scopes": ["posts:read", "posts:write"],
                "marketing_field_mapping": {
                    "campaign_ids": {"campaign_id_field": "campaign_id", "updated_at": updated_at},
                    "utm_fields": {
                        "source": "utm_source",
                        "medium": "utm_medium",
                        "campaign": "utm_campaign",
                        "updated_at": updated_at,
                    },
                    "timezone": {"timezone": "Asia/Kolkata", "updated_at": updated_at},
                },
                "marketing_backfill": _backfill(),
                "marketing_connector_contract": _contract(),
            },
        )
    )
    configs = _with_workflow(
        configs,
        "seo_sprint",
        _workflow_config(mode="active", promoted=True),
    )

    seo = _workflow(_activation(configs), "seo_sprint")

    assert seo["state"] == "active"
    assert seo["external_writes_allowed"] is True
    assert seo["write_connector_categories"] == ["CMS"]
    assert "update_page_metadata" in seo["write_actions"]
