from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import api.v1.kpis as kpis
from api.v1.kpis import _apply_cmo_production_data_policy
from core.marketing.connector_setup import (
    build_marketing_connector_setup,
    summarize_marketing_connector_setup,
)

NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _connector_config(
    connector_name: str,
    *,
    health_status: str = "healthy",
    last_sync_at: datetime | None = None,
    config: dict | None = None,
    sync_error: str | None = None,
    credentials: bool = True,
):
    return SimpleNamespace(
        connector_name=connector_name,
        display_name=None,
        auth_type="oauth2",
        credentials_encrypted={"_encrypted": "enc"} if credentials else {},
        config=config or {},
        status="configured",
        last_health_check=NOW,
        health_status=health_status,
        last_sync_at=last_sync_at if last_sync_at is not None else NOW - timedelta(hours=1),
        sync_error=sync_error,
    )


def _row(rows: list[dict], key: str) -> dict:
    return next(row for row in rows if row["key"] == key)


def test_missing_connector_creates_actionable_setup_state() -> None:
    rows = build_marketing_connector_setup([], now=NOW)

    hubspot = _row(rows, "hubspot")
    assert hubspot["configured_status"] == "unconfigured"
    assert hubspot["health_status"] == "missing"
    assert hubspot["data_coverage_status"] == "missing"
    assert hubspot["cta_state"] == "setup"


def test_healthy_connector_exposes_owner_account_scopes_and_no_action() -> None:
    rows = build_marketing_connector_setup(
        [
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
                },
            )
        ],
        now=NOW,
    )

    hubspot = _row(rows, "hubspot")
    assert hubspot["configured_status"] == "configured"
    assert hubspot["health_status"] == "healthy"
    assert hubspot["owner"] == "revops@example.com"
    assert hubspot["account_id"] == "portal-123"
    assert hubspot["missing_scopes"] == []
    assert hubspot["cta_state"] == "none"


def test_expired_auth_requires_reconnect() -> None:
    rows = build_marketing_connector_setup(
        [
            _connector_config(
                "ga4",
                health_status="unhealthy",
                sync_error="OAuth token expired",
                config={"property_id": "properties/123"},
            )
        ],
        now=NOW,
    )

    ga4 = _row(rows, "ga4")
    assert ga4["health_status"] == "expired_auth"
    assert ga4["data_coverage_status"] == "blocked"
    assert ga4["cta_state"] == "reconnect"


def test_insufficient_scope_lists_missing_scopes_and_cta() -> None:
    rows = build_marketing_connector_setup(
        [
            _connector_config(
                "google_ads",
                config={
                    "customer_id": "123-456-7890",
                    "granted_scopes": [],
                },
                sync_error="403 insufficient_scope",
            )
        ],
        now=NOW,
    )

    google_ads = _row(rows, "google_ads")
    assert google_ads["health_status"] == "insufficient_scope"
    assert google_ads["data_coverage_status"] == "blocked"
    assert google_ads["cta_state"] == "add_scope"
    assert google_ads["missing_scopes"] == [
        "https://www.googleapis.com/auth/adwords",
    ]


def test_stale_sync_is_not_fake_healthy() -> None:
    rows = build_marketing_connector_setup(
        [
            _connector_config(
                "ahrefs",
                last_sync_at=NOW - timedelta(days=4),
                config={"site_url": "agenticorg.ai"},
            )
        ],
        now=NOW,
    )

    ahrefs = _row(rows, "ahrefs")
    assert ahrefs["health_status"] == "stale"
    assert ahrefs["data_coverage_status"] == "stale"
    assert ahrefs["cta_state"] == "refresh"


def test_degraded_connector_requires_review() -> None:
    rows = build_marketing_connector_setup(
        [
            _connector_config(
                "brandwatch",
                health_status="degraded",
                sync_error="rate_limited",
                config={"project_id": "project-9"},
            )
        ],
        now=NOW,
    )

    brandwatch = _row(rows, "brandwatch")
    assert brandwatch["health_status"] == "degraded"
    assert brandwatch["data_coverage_status"] == "partial"
    assert brandwatch["cta_state"] == "review"


def test_summary_counts_setup_states() -> None:
    rows = build_marketing_connector_setup(
        [
            _connector_config(
                "hubspot",
                config={
                    "granted_scopes": [
                        "crm.objects.contacts.read",
                        "crm.objects.deals.read",
                        "automation",
                    ],
                },
            ),
            _connector_config(
                "ga4",
                health_status="unhealthy",
                sync_error="OAuth token expired",
            ),
        ],
        now=NOW,
    )

    summary = summarize_marketing_connector_setup(rows)
    assert summary["healthy"] == 1
    assert summary["auth_actions"] == 1
    assert summary["missing"] > 0
    assert summary["needs_action"] > 0
    assert summary["readiness"] == "setup_required"


def test_production_tenant_suppresses_demo_kpi_fallback() -> None:
    base = {
        "demo": True,
        "source": "computed",
        "agent_count": 0,
        "total_tasks_30d": 0,
    }

    result = _apply_cmo_production_data_policy(
        base,
        {"readiness": "setup_required"},
        None,
        strict_runtime=True,
    )

    assert result["demo"] is False
    assert result["demo_suppressed"] is True
    assert result["production_data_blocked"] is True
    assert result["data_coverage_status"] == "blocked_setup"
    assert result["source"] == "empty_real_tenant"


@pytest.mark.asyncio
async def test_cmo_kpi_response_includes_connector_setup_and_policy(monkeypatch) -> None:
    async def fake_base_response(tenant_id: str, role: str, company_id: str) -> dict:
        assert role == "cmo"
        return {
            "demo": True,
            "company_id": company_id,
            "agent_count": 0,
            "total_tasks_30d": 0,
            "success_rate": 0,
            "hitl_interventions": 0,
            "total_cost_usd": 0,
            "domain_breakdown": [],
            "source": "computed",
        }

    async def fake_connector_configs(tenant_id: str) -> list[dict]:
        return []

    async def fake_approval_timeout(tenant_id: str, company_id: str) -> dict:
        return {"pending": 0, "overdue": 0, "approval_timeout_decisions": []}

    monkeypatch.setattr(kpis, "_build_kpi_response", fake_base_response)
    monkeypatch.setattr(kpis, "_load_marketing_connector_configs", fake_connector_configs)
    monkeypatch.setattr(kpis, "_load_cmo_approval_timeout_risk", fake_approval_timeout)
    monkeypatch.setattr(kpis.settings, "env", "production")

    response = await kpis._build_cmo_kpi_response("tenant-1", "company-1")

    assert response["company_id"] == "company-1"
    assert response["demo"] is False
    assert response["production_data_blocked"] is True
    assert response["connector_setup_summary"]["missing"] > 0
    assert response["connector_setup"][0]["cta_state"] == "setup"
    assert response["field_mapping_summary"]["readiness"] == "blocked"
    assert response["backfill_summary"]["readiness"] == "blocked"
