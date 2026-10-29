# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The tenant's Grantex service-tenant enrolment, as Sell everywhere asks for and shows it.

A merchant turning on Sell everywhere enrols its tenant (with the merchant and its catalog
source) with Grantex (``core.commerce.c6z_runtime_vertical.enrol_service_tenant``), so Grantex
issues that tenant's artifacts without an operator adding it to an environment list. The last
answer is kept in the tenant's catalogue row (``tenants.settings["grantex_service_enrolment"]``,
written with ``jsonb_set`` so no other settings key is touched) and shown on the page:

* ``active``: Grantex holds the enrolment (``merchant_enrolled`` says whether it names this merchant).
* ``unavailable``: the Grantex in use has no enrolment route yet (404 or 405); the merchant waits.
* ``not_configured``: this deployment has no Grantex settings; the merchant waits.
* ``revoked``: the platform administrator revoked the tenant; only they can re-enrol it.
* ``refused`` / ``failed``: Grantex said no, or could not be reached; retry.

No enrolment call runs inside a database transaction, and none waits or retries.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text

from core.commerce.c6z_runtime_vertical import (
    ENROLMENT_ACTIVE,
    ENROLMENT_FAILED,
    ENROLMENT_NOT_CONFIGURED,
    ENROLMENT_REFUSED,
    ENROLMENT_REVOKED,
    ENROLMENT_UNAVAILABLE,
    C6ZRuntimeValidationError,
    enrol_service_tenant,
)
from core.database import get_tenant_session
from core.models.tenant import Tenant

logger = structlog.get_logger()

SETTINGS_KEY = "grantex_service_enrolment"

_MESSAGES: dict[str, str] = {
    ENROLMENT_ACTIVE: "Enrolled with Grantex.",
    ENROLMENT_UNAVAILABLE: "Waiting: Grantex enrolment is not available on this deployment yet.",
    ENROLMENT_NOT_CONFIGURED: "Waiting: this deployment is not connected to Grantex yet.",
    ENROLMENT_REVOKED: "Grantex enrolment was revoked. Contact the platform administrator.",
    ENROLMENT_REFUSED: "Grantex refused the enrolment. Retry, or contact the platform administrator.",
    ENROLMENT_FAILED: "Grantex could not be reached. Retry in a minute.",
}
WAITING_STATUSES = frozenset({ENROLMENT_UNAVAILABLE, ENROLMENT_NOT_CONFIGURED})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def read_enrolment(tenant_id: str) -> dict[str, Any] | None:
    """The last recorded enrolment answer for the tenant, or None."""
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        settings_value = (
            await session.execute(select(Tenant.settings).where(Tenant.id == uuid.UUID(tenant_id)))
        ).scalar()
    state = (settings_value or {}).get(SETTINGS_KEY) if isinstance(settings_value, dict) else None
    return state if isinstance(state, dict) else None


def enrolled_for(state: dict[str, Any] | None, merchant_id: str, source_system: str | None) -> bool:
    """Whether the recorded enrolment is active and names this merchant (and source, when given)."""
    if not state or state.get("status") != ENROLMENT_ACTIVE:
        return False
    if merchant_id not in (state.get("merchant_ids") or []):
        return False
    return source_system is None or source_system in (state.get("source_systems") or [])


def enrolment_view(state: dict[str, Any] | None, merchant_id: str) -> dict[str, Any]:
    """What the Sell everywhere page shows (no credential, no Grantex error body)."""
    if not state:
        return {"status": "not_started", "message": "Not enrolled yet.", "merchant_enrolled": False}
    status = str(state.get("status") or ENROLMENT_FAILED)
    return {
        "status": status,
        "message": _MESSAGES.get(status, _MESSAGES[ENROLMENT_FAILED]),
        "waiting": status in WAITING_STATUSES,
        "merchant_enrolled": enrolled_for(state, merchant_id, None),
        "source_systems": list(state.get("source_systems") or []),
        "enrolled_at": state.get("enrolled_at"),
        "checked_at": state.get("checked_at"),
        "reason": state.get("reason"),
    }


async def ensure_enrolment(tenant_id: str, merchant_id: str, source_system: str) -> dict[str, Any]:
    """Enrol unless the recorded enrolment already names this merchant and source; returns the state."""
    current = await read_enrolment(tenant_id)
    if enrolled_for(current, merchant_id, source_system):
        assert current is not None
        return current
    try:
        answer = await enrol_service_tenant(tenant_id, merchant_id, source_system)
    except C6ZRuntimeValidationError as exc:
        answer = {"status": ENROLMENT_FAILED, "reason": "enrolment_request_invalid"}
        logger.warning("grantex_enrolment_request_invalid", error=type(exc).__name__)
    state: dict[str, Any] = {"status": answer.get("status") or ENROLMENT_FAILED, "checked_at": _now_iso()}
    if state["status"] == ENROLMENT_ACTIVE:
        state.update(
            {
                "merchant_ids": sorted(set(answer.get("merchant_ids") or []) | {merchant_id}),
                "source_systems": sorted(set(answer.get("source_systems") or []) | {source_system}),
                "enrolled_at": answer.get("enrolled_at") or _now_iso(),
            }
        )
    else:
        state["reason"] = str(answer.get("reason") or "")[:120] or None
        if current and current.get("status") == ENROLMENT_ACTIVE and state["status"] in (
            ENROLMENT_FAILED,
            ENROLMENT_UNAVAILABLE,
            ENROLMENT_NOT_CONFIGURED,
        ):
            # A transient failure to add a merchant does not erase an enrolment Grantex holds.
            state = {**current, "checked_at": state["checked_at"], "last_attempt": state}
    await _write_state(tenant_id, state)
    logger.info("grantex_enrolment_checked", status=state["status"])
    return state


async def _write_state(tenant_id: str, state: dict[str, Any]) -> None:
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        await session.execute(
            text(
                "UPDATE tenants SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), "
                "ARRAY[CAST(:key AS text)], CAST(:value AS jsonb), true) WHERE id = :tenant_id"
            ),
            {"key": SETTINGS_KEY, "value": json.dumps(state), "tenant_id": uuid.UUID(tenant_id)},
        )
