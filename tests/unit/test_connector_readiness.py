# SPDX-License-Identifier: Apache-2.0
"""Connector registration is not evidence of a working provider integration."""

from datetime import UTC, datetime, timedelta

from core.connectors.readiness import project_connector_readiness

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def project(**overrides):
    values = {
        "registration_status": "active",
        "configuration_status": "configured",
        "auth_type": "oauth2",
        "has_credentials": True,
        "health_status": "healthy",
        "last_health_check": NOW - timedelta(minutes=5),
        "last_sync_at": None,
        "observed_at": NOW,
    }
    values.update(overrides)
    return project_connector_readiness(**values)


def test_recent_health_does_not_infer_scope_or_contract():
    result = project()
    assert result["state"] == "recent_health"
    assert result["scope_verification"] == "unverified"
    assert result["contract_verification"] == "unverified"
    assert result["error_budget"] == "unmeasured"


def test_missing_credentials_precedes_health():
    assert project(has_credentials=False)["state"] == "needs_credentials"
    assert project(auth_type="none", has_credentials=False)["state"] == "recent_health"


def test_stale_future_and_missing_health_fail_closed():
    assert project(last_health_check=NOW - timedelta(days=2))["state"] == "stale_health"
    assert project(last_health_check=NOW + timedelta(minutes=1))["state"] == "stale_health"
    assert project(last_health_check=None)["state"] == "needs_health_check"
    assert project(health_status="unknown")["state"] == "needs_health_check"


def test_failed_and_disabled_states():
    assert project(health_status="unhealthy")["state"] == "health_failed"
    assert project(configuration_status="disabled")["state"] == "disabled"
    assert project(registration_status="deleted")["state"] == "disabled"


def test_projection_exposes_only_safe_evidence_fields():
    result = project()
    assert set(result) == {
        "state", "credential_state", "health_state", "last_health_check",
        "last_sync_at", "scope_verification", "contract_verification", "error_budget",
    }
