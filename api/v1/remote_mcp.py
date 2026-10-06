# SPDX-License-Identifier: Apache-2.0
"""Authenticated remote MCP registration and catalog management."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, require_scope
from api.route_metadata import route_meta
from core.crypto import encrypt_for_tenant
from core.database import get_tenant_session
from core.models.connector import Connector
from core.models.connector_config import ConnectorConfig
from core.ownership import (
    caller_from_request,
    can_mutate_connector,
    can_view_connector,
    require_connector_mutable,
    resolve_new_connector_owner,
)
from core.remote_mcp import credentials, review_tools, tool_ref
from core.remote_mcp_transport import MCP_SCHEMA, RemoteMCPError, call, discover

router = APIRouter(prefix="/connectors/mcp", tags=["Remote MCP"])


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^mcp_[a-z][a-z0-9_]{0,22}$")
    url: str = Field(min_length=10, max_length=500)
    access_token: SecretStr

    @field_validator("name")
    @classmethod
    def unambiguous_name(cls, value: str) -> str:
        if "__" in value:
            raise ValueError("Connection names cannot contain double underscores")
        return value


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: SecretStr | None = None


class Permissions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_only_tools: list[str] = Field(max_length=100)
    schema_hashes: dict[str, str]


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    arguments: dict = Field(default_factory=dict)


def public(connector: Connector) -> dict:
    return {
        "id": str(connector.id),
        "name": connector.name,
        "base_url": connector.base_url,
        "status": connector.status,
        "transport": "streamable_http",
        "auth_type": "bearer",
        "tools": [{**tool, "ref": tool_ref(connector.name, tool["name"])} for tool in connector.tool_functions],
        "health_check_at": connector.health_check_at.isoformat() if connector.health_check_at else None,
    }


async def owned(conn_id: UUID, request: Request, tenant_id: str) -> Connector:
    tid = UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        connector = (
            await session.execute(
                select(Connector).where(
                    Connector.tenant_id == tid,
                    Connector.id == conn_id,
                    Connector.data_schema_ref == MCP_SCHEMA,
                    Connector.status == "active",
                )
            )
        ).scalar_one_or_none()
        require_connector_mutable(connector, caller_from_request(request))
        return connector


@router.get("", dependencies=[require_scope("connectors.read")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.read",
    rate_limit="default",
    idempotency="read-only",
    audit_event="connectors.mcp.list",
)
async def list_remote(request: Request, tenant_id: str = Depends(get_current_tenant)):
    tid = UUID(tenant_id)
    caller = caller_from_request(request)
    async with get_tenant_session(tid) as session:
        rows = (
            (
                await session.execute(
                    select(Connector).where(
                        Connector.tenant_id == tid,
                        Connector.data_schema_ref == MCP_SCHEMA,
                        Connector.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "items": [
                {**public(row), "can_manage": can_mutate_connector(row, caller)}
                for row in rows
                if can_view_connector(row, caller)
            ]
        }


@router.post("", status_code=201, dependencies=[require_scope("connectors.personal.write")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.create",
    rate_limit="connector-test",
    idempotency="tenant-unique-connector-name",
    audit_event="connectors.mcp.create",
)
async def register(body: Registration, request: Request, tenant_id: str = Depends(get_current_tenant)):
    owner = resolve_new_connector_owner(caller_from_request(request))
    tid = UUID(tenant_id)
    token = body.access_token.get_secret_value()
    try:
        tools = await discover(body.url, token, body.name)
        if not tools:
            raise RemoteMCPError("The MCP server has no tools")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    encrypted = await encrypt_for_tenant(json.dumps({"access_token": token}), tid)
    now = datetime.now(UTC)
    try:
        async with get_tenant_session(tid) as session:
            connector = Connector(
                tenant_id=tid,
                name=body.name,
                category="mcp",
                base_url=body.url,
                auth_type="bearer",
                auth_config={},
                data_schema_ref=MCP_SCHEMA,
                tool_functions=tools,
                status="active",
                owner_user_id=owner,
                health_check_at=now,
            )
            session.add(connector)
            session.add(
                ConnectorConfig(
                    tenant_id=tid,
                    connector_name=body.name,
                    auth_type="bearer",
                    credentials_encrypted={"_encrypted": encrypted},
                    config={"base_url": body.url},
                    status="configured",
                    health_status="healthy",
                    last_health_check=now,
                )
            )
            await session.flush()
            return public(connector)
    except IntegrityError:
        raise HTTPException(409, "This connector name already exists. Refresh or choose a new name.") from None


@router.post("/{conn_id}/refresh", dependencies=[require_scope("connectors.personal.write")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.test",
    rate_limit="connector-test",
    idempotency="replace-discovered-catalog",
    audit_event="connectors.mcp.refresh",
)
async def refresh(conn_id: UUID, body: Refresh, request: Request, tenant_id: str = Depends(get_current_tenant)):
    connector = await owned(conn_id, request, tenant_id)
    try:
        token = body.access_token.get_secret_value() if body.access_token else await credentials(tenant_id, connector)
        tools = await discover(connector.base_url or "", token, connector.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    tid = UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        current = (
            await session.execute(
                select(Connector)
                .where(
                    Connector.tenant_id == tid,
                    Connector.id == conn_id,
                    Connector.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        require_connector_mutable(current, caller_from_request(request))
        old = {tool["name"]: tool for tool in current.tool_functions}
        approved = [
            tool["name"]
            for tool in tools
            if old.get(tool["name"], {}).get("schema_hash") == tool["schema_hash"]
            and old[tool["name"]].get("permission") == "read"
        ]
        current.tool_functions = review_tools(tools, approved)
        current.health_check_at = datetime.now(UTC)
        config = (
            await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                    ConnectorConfig.company_id.is_(None),
                )
            )
        ).scalar_one()
        if body.access_token:
            config.credentials_encrypted = {
                "_encrypted": await encrypt_for_tenant(json.dumps({"access_token": token}), tid)
            }
        config.last_health_check = current.health_check_at
        config.health_status = "healthy"
        config.status = "configured"
        return public(current)


@router.put("/{conn_id}/permissions", dependencies=[require_scope("connectors.personal.write")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.personal.write",
    rate_limit="admin-mutating",
    idempotency="replace-reviewed-read-tool-set",
    audit_event="connectors.mcp.permissions",
)
async def permissions(conn_id: UUID, body: Permissions, request: Request, tenant_id: str = Depends(get_current_tenant)):
    tid = UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        connector = (
            await session.execute(
                select(Connector)
                .where(
                    Connector.tenant_id == tid,
                    Connector.id == conn_id,
                    Connector.data_schema_ref == MCP_SCHEMA,
                    Connector.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        require_connector_mutable(connector, caller_from_request(request))
        if {tool["name"]: tool["schema_hash"] for tool in connector.tool_functions} != body.schema_hashes:
            raise HTTPException(409, "The catalog changed. Reload it before reviewing permissions.")
        try:
            connector.tool_functions = review_tools(connector.tool_functions, body.read_only_tools)
        except RemoteMCPError as exc:
            raise HTTPException(422, str(exc)) from None
        return public(connector)


@router.post("/{conn_id}/probe", dependencies=[require_scope("connectors.personal.write")])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="connectors.test",
    rate_limit="connector-test",
    idempotency="explicit-read-tool-probe",
    audit_event="connectors.mcp.probe",
)
async def probe(conn_id: UUID, body: Probe, request: Request, tenant_id: str = Depends(get_current_tenant)):
    connector = await owned(conn_id, request, tenant_id)
    descriptor = next((tool for tool in connector.tool_functions if tool["name"] == body.tool), None)
    if descriptor is None or descriptor.get("permission") != "read":
        raise HTTPException(422, "Only a reviewed read-only tool can be probed")
    from core.governance.guardrails.hooks import guard_action
    from core.governance.guardrails.schema import GuardrailBlocked
    from core.governance.operator_override import check

    override = await check(tenant_id, connector=connector.name, tool=body.tool, throttle_unit="tool")
    if override.blocked:
        raise HTTPException(423, "This connector is paused by an operator")
    try:
        await guard_action(connector.name, body.tool, body.arguments, tenant_id=tenant_id)
    except GuardrailBlocked:
        raise HTTPException(403, "The tool arguments were blocked by a guardrail") from None
    try:
        token = await credentials(tenant_id, connector)
        result = await call(connector.base_url or "", token, connector.name, descriptor, body.arguments)
        return {"tested": True, "result": result}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
