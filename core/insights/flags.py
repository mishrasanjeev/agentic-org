# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Whether Insights is on for a tenant: the platform switch and the tenant's own opt-out.

Insights is on for a tenant when ``settings.insights_enabled``
(``AGENTICORG_INSIGHTS_ENABLED``, default off) is on and the tenant has not
turned the ``insights_dashboard`` feature flag off (the flag defaults on, so a
tenant without a row is in). While the switch is off no flag is read.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

INSIGHTS_FLAG_KEY = "insights_dashboard"


def _tenant_uuid(tenant_id: Any) -> uuid.UUID | None:
    if isinstance(tenant_id, uuid.UUID):
        return tenant_id
    try:
        return uuid.UUID(str(tenant_id or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


async def insights_enabled_for(tenant_id: Any) -> bool:
    """True when Insights is on for ``tenant_id``; False for a missing or malformed tenant id or a failed read."""
    if not settings.insights_enabled:
        return False
    tid = _tenant_uuid(tenant_id)
    if tid is None:
        return False
    try:
        from core.feature_flags import is_enabled

        return bool(await is_enabled(INSIGHTS_FLAG_KEY, tenant_id=tid, default=True))
    # enterprise-gate: broad-except-ok reason=insights-flag-read-failure-reads-as-off
    except Exception as exc:
        logger.warning("insights_flag_read_failed", error_type=type(exc).__name__)
        return False
