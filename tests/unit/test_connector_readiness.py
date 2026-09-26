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


def test_replacing_credentials_clears_the_last_health_check():
    """A check proves the credentials it ran against. Any writer that replaces a
    connector's credentials must clear last_health_check, or the list shows
    "Recently checked" for credentials no check has ever used."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "api" / "v1" / "connectors.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    writers = 0
    blocks = [
        block
        for node in ast.walk(tree)
        for block in (getattr(node, "body", None), getattr(node, "orelse", None), getattr(node, "finalbody", None))
        if isinstance(block, list)
    ]
    for body in blocks:
        assigned: dict[str, set[str]] = {}
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        assigned.setdefault(target.value.id, set()).add(target.attr)
        for owner, attrs in assigned.items():
            if "credentials_encrypted" in attrs:
                writers += 1
                assert "last_health_check" in attrs, (
                    f"{owner}.credentials_encrypted is replaced without clearing last_health_check"
                )
    assert writers >= 3


def test_healthy_status_without_a_check_is_not_recent():
    """Setup paths mark a connector healthy without running a check; with the
    check time cleared, readiness must still ask for a health check."""
    result = project_connector_readiness(
        registration_status="active",
        configuration_status="configured",
        auth_type="api_key",
        has_credentials=True,
        health_status="healthy",
        last_health_check=None,
        last_sync_at=None,
        observed_at=NOW,
    )
    assert result["health_state"] == "unverified"
    assert result["state"] == "needs_health_check"


def test_a_naive_check_time_is_unverified_not_an_error():
    result = project_connector_readiness(
        registration_status="active",
        configuration_status="configured",
        auth_type="api_key",
        has_credentials=True,
        health_status="healthy",
        last_health_check=NOW.replace(tzinfo=None) - timedelta(minutes=5),
        last_sync_at=None,
        observed_at=NOW,
    )
    assert result["health_state"] == "unverified"


def test_a_token_refresh_does_not_revive_a_failed_check_time():
    """A refresh proves the grant, not that a failed health check is fixed."""
    from types import SimpleNamespace

    from core.tasks.token_refresh import _store_refreshed_credentials

    failed_at = NOW - timedelta(hours=1)
    config = SimpleNamespace(
        health_status="unhealthy", last_health_check=failed_at, credentials_encrypted={"_encrypted": "old"}
    )
    _store_refreshed_credentials(config, "new")

    assert config.credentials_encrypted == {"_encrypted": "new"}
    assert config.health_status == "healthy"
    assert config.last_health_check is None
    readiness = project_connector_readiness(
        registration_status="active",
        configuration_status="configured",
        auth_type="oauth2",
        has_credentials=True,
        health_status=config.health_status,
        last_health_check=config.last_health_check,
        last_sync_at=None,
        observed_at=NOW,
    )
    assert readiness["state"] == "needs_health_check"


def test_a_token_refresh_keeps_a_passing_check_time():
    from types import SimpleNamespace

    from core.tasks.token_refresh import _store_refreshed_credentials

    checked_at = NOW - timedelta(hours=1)
    config = SimpleNamespace(health_status="healthy", last_health_check=checked_at, credentials_encrypted={})
    _store_refreshed_credentials(config, "new")

    assert config.last_health_check == checked_at
    assert config.health_status == "healthy"

