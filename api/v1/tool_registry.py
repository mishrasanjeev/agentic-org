# SPDX-License-Identifier: Apache-2.0
"""The tool registry: schema-validated tool registrations and a dry-run input check."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.database import get_tenant_session
from core.models.tool_registration import ToolRegistration
from core.tool_gateway import registry

logger = structlog.get_logger()
router = APIRouter()


class RegistrationIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=registry.MAX_NAME)
    description: str = Field("", max_length=500)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk: str = Field("read", max_length=16)
    timeout_seconds: int = Field(registry.DEFAULT_TIMEOUT_SECONDS, ge=1, le=registry.MAX_TIMEOUT_SECONDS)
    max_output_bytes: int = Field(registry.DEFAULT_MAX_OUTPUT_BYTES, ge=1_000, le=registry.MAX_OUTPUT_BYTES)
    untrusted_output: bool = True
    enabled: bool = True


class RegistrationPatch(BaseModel):
    model_config = {"extra": "forbid"}

    description: str | None = Field(None, max_length=500)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    risk: str | None = Field(None, max_length=16)
    timeout_seconds: int | None = Field(None, ge=1, le=registry.MAX_TIMEOUT_SECONDS)
    max_output_bytes: int | None = Field(None, ge=1_000, le=registry.MAX_OUTPUT_BYTES)
    untrusted_output: bool | None = None
    enabled: bool | None = None


class CheckIn(BaseModel):
    model_config = {"extra": "forbid"}

    params: dict[str, Any] = Field(default_factory=dict)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "tool_registry_disabled",
            "message": "The tool registry is off for this deployment (AGENTICORG_TOOL_REGISTRY_ENABLED).",
        },
    )


def _refused(exc: registry.RegistryError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _row_dict(row: ToolRegistration) -> dict[str, Any]:
    return {
        "id": str(row.id),
        **registry.registration_of(row).to_dict(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _row(session: Any, tid: uuid.UUID, name: str) -> ToolRegistration:
    row = (
        await session.execute(
            select(ToolRegistration).where(ToolRegistration.tenant_id == tid, ToolRegistration.name == name)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "No such registration"})
    return row


@router.get("/tools/registry", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="tools.registry.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="tools.registry.list",
)
async def list_registrations(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Every registered tool of the tenant with its schemas and envelope, and whether the registry is enforced."""
    if not registry.enabled():
        raise _off()
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        rows = list(
            (
                await session.execute(
                    select(ToolRegistration).where(ToolRegistration.tenant_id == tid).order_by(ToolRegistration.name)
                )
            )
            .scalars()
            .all()
        )
    return {
        "registrations": [_row_dict(r) for r in rows],
        "total": len(rows),
        "registration_required": registry.registration_required(),
        "risks": list(registry.RISKS),
    }


@router.post("/tools/registry", dependencies=[require_tenant_admin], status_code=201)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="tools.registry.sensitive.write",
    rate_limit="standard",
    idempotency="not-idempotent-create",
    audit_event="tools.registry.register",
)
async def register_tool(
    body: RegistrationIn,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Register a tool with its input schema (checked now, so a bad schema never reaches a call) and its envelope."""
    if not registry.enabled():
        raise _off()
    try:
        fields = registry.parse_fields(body.model_dump())
    except registry.RegistryError as exc:
        raise _refused(exc) from None
    tid = uuid.UUID(tenant_id)
    actor = _user_uuid_from_claims(user)
    async with get_tenant_session(tid) as session:
        existing = (
            await session.execute(
                select(ToolRegistration).where(
                    ToolRegistration.tenant_id == tid, ToolRegistration.name == fields["name"]
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(409, detail={"error": "exists", "message": f"{fields['name']} is already registered"})
        row = ToolRegistration(tenant_id=tid, created_by=actor, updated_by=actor, **fields)
        session.add(row)
        await session.flush()
        answer = _row_dict(row)
    registry.invalidate(tid)
    return answer


@router.put("/tools/registry/{name:path}", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="tools.registry.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-partial-update",
    audit_event="tools.registry.update",
)
async def update_registration(
    name: str,
    body: RegistrationPatch,
    tenant_id: str = Depends(get_current_tenant),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Change a registration's schemas, risk, envelope or enabled state."""
    if not registry.enabled():
        raise _off()
    try:
        key = registry.normalise_name(name)
        fields = registry.parse_fields(body.model_dump(exclude_unset=True), partial=True)
    except registry.RegistryError as exc:
        raise _refused(exc) from None
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await _row(session, tid, key)
        for field, value in fields.items():
            setattr(row, field, value)
        row.updated_by = _user_uuid_from_claims(user)
        await session.flush()
        answer = _row_dict(row)
    registry.invalidate(tid)
    return answer


@router.delete("/tools/registry/{name:path}", dependencies=[require_tenant_admin], status_code=204)
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="tools.registry.sensitive.write",
    rate_limit="standard",
    idempotency="idempotent-delete",
    audit_event="tools.registry.delete",
)
async def delete_registration(name: str, tenant_id: str = Depends(get_current_tenant)) -> None:
    """Remove a registration."""
    if not registry.enabled():
        raise _off()
    try:
        key = registry.normalise_name(name)
    except registry.RegistryError as exc:
        raise _refused(exc) from None
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        row = await _row(session, tid, key)
        await session.delete(row)
        await session.flush()
    registry.invalidate(tid)
    return None


@router.post("/tools/registry/check")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="tools.registry.sensitive.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="tools.registry.check",
)
async def check_inputs(
    body: CheckIn,
    name: str = Query(..., min_length=1, max_length=registry.MAX_NAME),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """A dry run of the gateway's input check for a tool: the registration it would use and what is wrong."""
    if not registry.enabled():
        raise _off()
    entries = await registry.load(tenant_id)
    connector, _, tool = str(name).partition(":") if ":" in str(name) else (None, "", str(name))
    check = registry.check_call(entries, connector or None, tool, body.params)
    return {
        "name": name,
        "registered": check.registration is not None,
        "registration": check.registration.to_dict() if check.registration else None,
        "errors": check.errors,
        "refused": check.refused,
    }
