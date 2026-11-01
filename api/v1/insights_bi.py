# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights BI access: a tenant administrator's read-only SQL logins for a BI tool.

``GET /insights/bi-access`` lists the tenant's logins and the connection
details (replica host, port 5432, database, ``sslmode=verify-ca``,
``channel_binding=require``, the views).
``POST /insights/bi-access`` creates a login and returns its password once,
with a connection string; the password is not stored anywhere and is in no
log or audit row; it answers 503 ``insights_bi_unavailable`` while the
database refuses to create logins (schema ``public`` writable by PUBLIC or the
BI group). ``DELETE /insights/bi-access/{role_name}`` revokes one.

Every route needs an active human administrator of the tenant
(``get_active_human_admin``: API keys and agent tokens are refused) and
answers 404 ``insights_bi_disabled`` unless BI access is on and configured
(``AGENTICORG_INSIGHTS_BI_ENABLED``, ``_HOST`` and ``_DATABASE``). The
isolation itself is in the database (migration ``v6z108_insights_bi``).
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from api.deps import ActiveHumanAdmin, get_active_human_admin
from api.route_metadata import route_meta
from core.database import get_tenant_session
from core.insights import bi_access
from core.insights.audit import audit_entry

logger = structlog.get_logger()

router = APIRouter(prefix="/insights/bi-access", tags=["Insights"])

RoleName = Annotated[str, Path(min_length=1, max_length=63)]
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def require_bi_admin(admin: ActiveHumanAdmin = Depends(get_active_human_admin)) -> ActiveHumanAdmin:
    """An active human administrator, while BI access is on and configured (404 otherwise)."""
    if not bi_access.bi_configured():
        raise HTTPException(404, "insights_bi_disabled")
    return admin


async def _require_configured() -> None:
    # Checked before the administrator lookup, so a disabled deployment does
    # not reveal the surface to anyone.
    if not bi_access.bi_configured():
        raise HTTPException(404, "insights_bi_disabled")


Admin = Annotated[ActiveHumanAdmin, Depends(require_bi_admin)]
_CONFIGURED = [Depends(_require_configured)]


@router.get("", dependencies=_CONFIGURED)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.bi.read",
    rate_limit="reporting-admin",
    idempotency="read-only",
    audit_event="insights.bi_access.read",
)
async def list_bi_access(admin: Admin) -> JSONResponse:
    """The tenant's BI logins (newest first) and how a BI tool connects."""
    async with get_tenant_session(admin.tenant_id) as session:
        logins = await bi_access.list_logins(session, admin.tenant_id)
    body: dict[str, Any] = {
        "logins": logins,
        "connection": bi_access.connection_details(),
        "max_logins": bi_access.max_logins(),
        "active_logins": sum(1 for login in logins if login["revoked_at"] is None),
    }
    return JSONResponse(body, headers=_NO_STORE)


@router.post("", status_code=201, dependencies=_CONFIGURED)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.bi.write",
    rate_limit="reporting-admin",
    idempotency="not-idempotent-create",
    audit_event="insights.bi_access.create",
)
async def create_bi_access(admin: Admin) -> JSONResponse:
    """Create a login. The password is in this response only; 409 at the per-tenant limit."""
    limit = bi_access.max_logins()
    async with get_tenant_session(admin.tenant_id) as session:
        try:
            created = await bi_access.create_login(session, admin.tenant_id, created_by=admin.user_id, limit=limit)
        except bi_access.BiLoginLimitError:
            raise HTTPException(409, detail={"error": "insights_bi_limit_reached", "max_logins": limit}) from None
        except bi_access.BiAccessUnavailableError:
            logger.warning("insights_bi_login_refused_public_schema_writable")
            raise HTTPException(503, "insights_bi_unavailable") from None
        session.add(
            audit_entry(
                admin.tenant_id,
                event_type="insights.bi_access.create",
                actor_id=str(admin.user_id),
                action="create",
                resource_type="insights_bi_login",
                resource_id=created.role_name,
                details={"role_name": created.role_name, "login_id": str(created.login_id), "max_logins": limit},
            )
        )
    logger.info("insights_bi_login_created")
    details = bi_access.connection_details()
    body = {
        "role_name": created.role_name,
        "password": created.password,
        "connection_string": bi_access.connection_string(created.role_name, created.password),
        "created_at": created.created_at.isoformat() if created.created_at else None,
        **details,
    }
    return JSONResponse(body, status_code=201, headers=_NO_STORE)


@router.delete("/{role_name}", dependencies=_CONFIGURED)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="insights.bi.write",
    rate_limit="reporting-admin",
    idempotency="idempotent-delete",
    audit_event="insights.bi_access.revoke",
)
async def revoke_bi_access(role_name: RoleName, admin: Admin) -> dict[str, Any]:
    """Revoke one of the tenant's logins (404 when it is not this tenant's)."""
    async with get_tenant_session(admin.tenant_id) as session:
        try:
            result = await bi_access.revoke_login(session, admin.tenant_id, role_name)
        except bi_access.BiLoginNotFoundError:
            raise HTTPException(404, "insights_bi_login_not_found") from None
        session.add(
            audit_entry(
                admin.tenant_id,
                event_type="insights.bi_access.revoke",
                actor_id=str(admin.user_id),
                action="revoke",
                resource_type="insights_bi_login",
                resource_id=role_name,
                details={"role_name": role_name, "result": result},
            )
        )
    logger.info("insights_bi_login_revoked", result=result)
    return {"role_name": role_name, "revoked": True, "result": result}
