# SPDX-License-Identifier: Apache-2.0
"""Tenant-owned remote MCP catalogs shared by discovery, grants and execution."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import or_, select

from core.database import get_tenant_session
from core.models.agent import Agent
from core.models.connector import Connector
from core.models.connector_config import ConnectorConfig
from core.ownership import Caller, can_view_connector, connector_link_allowed
from core.remote_mcp_transport import MCP_SCHEMA, RemoteMCPError, call

CATALOG_KEY = "_remote_mcp_catalog"


def is_remote_name(name: str) -> bool:
    return name.startswith("mcp_")


def tool_ref(connector: str, tool: str) -> str:
    return f"{connector}__{tool}"


def review_tools(tools: list[dict], read_only_tools: list[str]) -> list[dict]:
    by_name = {tool["name"]: tool for tool in tools}
    if set(read_only_tools) - by_name.keys():
        raise RemoteMCPError("A selected read-only tool is absent from the discovered catalog")
    for name in read_only_tools:
        # Hints are insufficient: the operator must review them, and obvious writes stay writes.
        if not by_name[name]["read_only_hint"] or re.search(
            r"(?:^|_)(?:send|reply|create|update|delete|pay|execute|remove|post)(?:_|$)", name.lower()
        ):
            raise RemoteMCPError("A write or unclassified tool cannot be approved as read-only")
    return [{**tool, "permission": "read" if tool["name"] in read_only_tools else "write"} for tool in tools]


async def catalog(
    tenant_id: str, selected: list[str] | None, *, caller: Caller | None = None
) -> dict[str, list[dict[str, Any]]]:
    if selected == []:
        return {}
    if selected is not None and not any(
        value.removeprefix("registry-").startswith("mcp_") or value.count("-") == 4 for value in selected
    ):
        return {}
    tid = UUID(str(tenant_id))
    async with get_tenant_session(tid) as session:
        query = select(Connector).where(
            Connector.tenant_id == tid, Connector.data_schema_ref == MCP_SCHEMA, Connector.status == "active"
        )
        if selected is not None:
            names = [value.removeprefix("registry-") for value in selected]
            ids = []
            for name in names:
                try:
                    ids.append(UUID(name))
                except ValueError:
                    continue
            query = query.where(or_(Connector.name.in_(names), Connector.id.in_(ids)))
        rows = (await session.execute(query)).scalars().all()
        return {
            row.name: list(row.tool_functions or [])
            for row in rows
            if caller is None or can_view_connector(row, caller)
        }


async def credentials(tenant_id: str, connector: Connector, company_id: str | None = None) -> str:
    from core.crypto import decrypt_for_tenant

    tid = UUID(str(tenant_id))
    company = UUID(str(company_id)) if company_id else None
    async with get_tenant_session(tid, company) as session:
        query = select(ConnectorConfig).where(
            ConnectorConfig.tenant_id == tid,
            ConnectorConfig.connector_name == connector.name,
            ConnectorConfig.status == "configured",
        )
        row = None
        if company is not None:
            row = (await session.execute(query.where(ConnectorConfig.company_id == company))).scalar_one_or_none()
        if row is None:
            row = (await session.execute(query.where(ConnectorConfig.company_id.is_(None)))).scalar_one_or_none()
        encrypted = row.credentials_encrypted if row else None
    if not isinstance(encrypted, dict) or not isinstance(encrypted.get("_encrypted"), str):
        raise RemoteMCPError("MCP credentials are missing or disabled; reconnect this connector")
    raw = await asyncio.to_thread(decrypt_for_tenant, encrypted["_encrypted"])
    token = json.loads(raw).get("access_token")
    if not isinstance(token, str) or not token:
        raise RemoteMCPError("MCP bearer token is missing; reconnect this connector")
    return token


async def authorized_descriptor(tenant_id: str, agent_id: str, connector_name: str, tool_name: str):
    """Reload authority on every dispatch; disabling or unlinking applies across workers."""
    tid = UUID(str(tenant_id))
    aid = UUID(str(agent_id))
    async with get_tenant_session(tid) as session:
        agent = (
            await session.execute(select(Agent).where(Agent.tenant_id == tid, Agent.id == aid))
        ).scalar_one_or_none()
        connector = (
            await session.execute(
                select(Connector).where(
                    Connector.tenant_id == tid,
                    Connector.name == connector_name,
                    Connector.data_schema_ref == MCP_SCHEMA,
                    Connector.status == "active",
                )
            )
        ).scalar_one_or_none()
        if agent is None or connector is None or agent.status not in {"shadow", "active"}:
            raise RemoteMCPError("The agent or MCP connector is unavailable")
        selected = set(agent.connector_ids or [])
        if not selected.intersection({connector_name, str(connector.id), f"registry-{connector_name}"}):
            raise RemoteMCPError("This MCP connector is not linked to the agent")
        if not connector_link_allowed(connector, agent.visibility, agent.owner_user_id):
            raise RemoteMCPError("The MCP connector ownership does not permit this agent")
        if tool_ref(connector_name, tool_name) not in (agent.authorized_tools or []):
            raise RemoteMCPError("The MCP tool is not authorized for this agent")
        descriptor = next((t for t in connector.tool_functions if t.get("name") == tool_name), None)
        if descriptor is None:
            raise RemoteMCPError("The MCP tool is no longer in the discovered catalog")
        return connector, dict(descriptor), agent


async def execute(
    tenant_id: str | None,
    agent_id: str,
    connector_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_hash: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    from core.config import settings
    from core.governance.action_policy import ActionContext, evaluate_action
    from core.tool_gateway.audit_logger import AuditLogger
    from core.tool_gateway.rate_limiter import RateLimiter

    try:
        if not tenant_id or not agent_id:
            raise RemoteMCPError("MCP execution requires a persisted agent and tenant context")
        connector, descriptor, agent = await authorized_descriptor(tenant_id, agent_id, connector_name, tool_name)
        if company_id is not None and str(agent.company_id) != str(company_id):
            raise RemoteMCPError("MCP company scope does not match the agent")
        if expected_hash is not None and descriptor["schema_hash"] != expected_hash:
            raise RemoteMCPError("MCP catalog changed during this run; start a new run after reviewing the tools")
        decision = await evaluate_action(
            "remote_mcp_read" if descriptor.get("permission") == "read" else "remote_mcp_write",
            context=ActionContext(
                tenant_id=tenant_id, company_id=agent.company_id, domain=agent.domain, runtime_env=settings.env
            ),
        )
        if not decision.dispatch_allowed:
            return {
                "error": "action_contained",
                "message": "MCP write or unreviewed tools require action approval",
                "governance": decision.to_dict(),
            }
        token = await credentials(tenant_id, connector, str(agent.company_id) if agent.company_id else None)
        limiter = RateLimiter()
        try:
            await limiter.init()
            admitted = await limiter.check(tenant_id, connector_name)
        finally:
            await limiter.close()
        if not admitted.allowed:
            return {"error": "rate_limited", "retry_after_seconds": admitted.retry_after_seconds}
        result = await call(connector.base_url or "", token, connector_name, descriptor, arguments)
        await AuditLogger(lambda: get_tenant_session(UUID(tenant_id))).log(
            tenant_id=tenant_id,
            agent_id=agent_id,
            tool_name=tool_ref(connector_name, tool_name),
            action="mcp.tool.call",
            outcome="success",
            details={"schema_hash": descriptor["schema_hash"]},
        )
        return result
    except (RemoteMCPError, ValueError):
        return {
            "error": "remote_mcp_unavailable",
            "message": "MCP tool unavailable: review its link, permissions, schema and connection",
        }
    except RedisError:
        return {"error": "remote_mcp_unavailable", "message": "MCP rate limit service unavailable; retry later"}
