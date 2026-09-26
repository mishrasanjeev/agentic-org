# SPDX-License-Identifier: Apache-2.0
"""Buyer-safe operational readiness projection for a registered connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

HEALTH_FRESHNESS = timedelta(hours=24)
NO_AUTH_TYPES = frozenset({"none", "no_auth", "anonymous"})
DISABLED_STATUSES = frozenset({"disabled", "inactive", "archived", "deleted"})


def project_connector_readiness(
    *,
    registration_status: str,
    configuration_status: str | None,
    auth_type: str,
    has_credentials: bool,
    health_status: str | None,
    last_health_check: datetime | None,
    last_sync_at: datetime | None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Describe observed evidence without mistaking registration for a live integration."""
    now = observed_at or datetime.now(UTC)
    credential_state = "not_required" if auth_type.lower() in NO_AUTH_TYPES else (
        "configured" if has_credentials else "missing"
    )
    health = (health_status or "unknown").lower()
    if health in {"unhealthy", "error", "failed"}:
        health_state = "failed"
    elif health == "healthy" and last_health_check is not None and last_health_check.tzinfo is not None:
        age = now - last_health_check
        health_state = (
            "recent" if timedelta(0) <= age <= HEALTH_FRESHNESS else "stale"
        )
    else:
        health_state = "unverified"

    if registration_status.lower() in DISABLED_STATUSES or (configuration_status or "").lower() in DISABLED_STATUSES:
        state = "disabled"
    elif credential_state == "missing":
        state = "needs_credentials"
    elif health_state == "failed":
        state = "health_failed"
    elif health_state == "stale":
        state = "stale_health"
    elif health_state == "recent":
        state = "recent_health"
    else:
        state = "needs_health_check"

    return {
        "state": state,
        "credential_state": credential_state,
        "health_state": health_state,
        "last_health_check": last_health_check.isoformat() if last_health_check else None,
        "last_sync_at": last_sync_at.isoformat() if last_sync_at else None,
        "scope_verification": "unverified",
        "contract_verification": "unverified",
        "error_budget": "unmeasured",
    }
