"""Connector endpoints."""

from __future__ import annotations

import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.connector import Connector
from core.schemas.api import ConnectorCreate, ConnectorUpdate

router = APIRouter(dependencies=[require_tenant_admin])


def _connector_to_dict(conn: Connector) -> dict:
    return {
        "id": str(conn.id),
        "connector_id": str(conn.id),  # kept for backward compat
        "name": conn.name,
        "category": conn.category,
        "description": conn.description,
        "base_url": conn.base_url,
        "auth_type": conn.auth_type,
        "tool_functions": conn.tool_functions,
        "data_schema_ref": conn.data_schema_ref,
        "rate_limit_rpm": conn.rate_limit_rpm,
        "timeout_ms": conn.timeout_ms,
        "status": conn.status,
        "health_check_at": conn.health_check_at.isoformat() if conn.health_check_at else None,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
    }


# ── GET /connectors/registry ────────────────────────────────────────────────
@router.get("/connectors/registry")
async def list_registry_connectors(category: str | None = None):
    """Return available connectors from the code registry (not tenant-specific).

    This provides a catalogue of all connectors that can be registered,
    useful as a fallback when no tenant connectors exist yet.
    """
    import connectors  # noqa: F401, F811
    from connectors.registry import ConnectorRegistry

    if category:
        classes = ConnectorRegistry.by_category(category)
    else:
        classes = [
            ConnectorRegistry.get(name)
            for name in ConnectorRegistry.all_names()
            if ConnectorRegistry.get(name) is not None
        ]

    items = []
    for cls in classes:
        tools = []
        if hasattr(cls, "tools") and cls.tools:
            tools = [t if isinstance(t, str) else getattr(t, "name", str(t)) for t in cls.tools]
        items.append({
            "id": f"registry-{cls.name}",
            "connector_id": f"registry-{cls.name}",
            "name": cls.name,
            "category": cls.category,
            "description": getattr(cls, "description", "") or f"{cls.name} connector",
            "base_url": cls.base_url,
            "auth_type": cls.auth_type,
            "tool_functions": tools,
            "rate_limit_rpm": cls.rate_limit_rpm,
            "timeout_ms": cls.timeout_ms,
            "status": "available",
            "health_check_at": None,
            "created_at": None,
        })

    return {"items": items, "total": len(items)}


# ── GET /tools — Function-level tool names ─────────────────────────────────
@router.get("/tools")
async def list_tools(category: str | None = None):
    """Return all function-level tool names from the connector registry.

    Unlike ``GET /mcp/tools`` which returns agent-level MCP tool names
    (e.g., ``agenticorg_email_agent``), this returns the actual function-level
    tool names (e.g., ``send_email``, ``read_inbox``) that agents use.
    """
    try:
        from core.langgraph.tool_adapter import _build_tool_index

        tool_index = _build_tool_index()
        tools: list[str] = []
        tool_details: list[dict] = []
        for tool_name, (connector_name, description) in sorted(tool_index.items()):
            if category:
                # Filter by connector category if requested
                from connectors.registry import ConnectorRegistry

                cls = ConnectorRegistry.get(connector_name)
                if cls and cls.category != category:
                    continue
            tools.append(tool_name)
            tool_details.append({
                "name": tool_name,
                "connector": connector_name,
                "description": description or f"{tool_name} on {connector_name}",
            })
        return {
            "tools": tools,
            "details": tool_details,
            "total": len(tools),
        }
    except Exception:
        # Fallback: extract from _AGENT_TYPE_DEFAULT_TOOLS
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        all_tools: set[str] = set()
        for tools_list in _AGENT_TYPE_DEFAULT_TOOLS.values():
            all_tools.update(tools_list)
        sorted_tools = sorted(all_tools)
        return {
            "tools": sorted_tools,
            "details": [{"name": t, "connector": "unknown", "description": t} for t in sorted_tools],
            "total": len(sorted_tools),
        }


# ── GET /connectors ─────────────────────────────────────────────────────────
@router.get("/connectors")
async def list_connectors(
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
):
    from sqlalchemy import func

    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        query = select(Connector).where(Connector.tenant_id == tid)
        count_query = select(func.count()).select_from(Connector).where(Connector.tenant_id == tid)
        if category:
            query = query.where(Connector.category == category)
            count_query = count_query.where(Connector.category == category)
        total = (await session.execute(count_query)).scalar() or 0
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        connectors = result.scalars().all()
    return {
        "items": [_connector_to_dict(c) for c in connectors],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── POST /connectors ────────────────────────────────────────────────────────
@router.post("/connectors", status_code=201)
async def register_connector(
    body: ConnectorCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)

    # Separate secret material from non-secret config.
    # auth_config on the Connector model is deprecated for secrets —
    # new writes go to connector_configs.credentials_encrypted via
    # tenant-aware encryption.
    secret_fields = body.auth_config or {}
    non_secret_config = {
        "base_url": body.base_url,
        "auth_type": body.auth_type,
        "data_schema_ref": body.data_schema_ref,
        "rate_limit_rpm": body.rate_limit_rpm,
    }

    async with get_tenant_session(tid) as session:
        connector = Connector(
            tenant_id=tid,
            name=body.name,
            category=body.category,
            base_url=body.base_url,
            auth_type=body.auth_type,
            auth_config={},  # no longer store secrets here
            secret_ref=body.secret_ref,
            tool_functions=body.tool_functions,
            data_schema_ref=body.data_schema_ref,
            rate_limit_rpm=body.rate_limit_rpm,
            status="active",
        )
        session.add(connector)
        try:
            await session.flush()
        except IntegrityError:
            raise HTTPException(409, f"Connector '{body.name}' already exists") from None

        # Store secrets in the encrypted connector_configs table
        if secret_fields:
            from core.crypto import encrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            encrypted_creds = await encrypt_for_tenant(
                __import__("json").dumps(secret_fields), tid
            )
            cc = ConnectorConfig(
                tenant_id=tid,
                connector_name=body.name,
                display_name=body.name,
                auth_type=body.auth_type or "api_key",
                credentials_encrypted={"_encrypted": encrypted_creds},
                config=non_secret_config,
                status="configured",
            )
            session.add(cc)
            await session.flush()

    return _connector_to_dict(connector)


# ── GET /connectors/{conn_id} ────────────────────────────────────────────────
@router.get("/connectors/{conn_id}")
async def get_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")
    return _connector_to_dict(connector)


# ── PUT /connectors/{conn_id} ──────────────────────────────────────────────
@router.put("/connectors/{conn_id}")
async def update_connector(
    conn_id: UUID,
    body: ConnectorUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")

        # Prevent blind setattr on secret-bearing or internal fields.
        # auth_config is deprecated for new writes — secrets go via
        # connector_configs.credentials_encrypted.
        _blocked_fields = {"id", "tenant_id", "auth_config", "secret_ref"}
        for field, value in body.model_dump(exclude_none=True).items():
            if field in _blocked_fields:
                continue
            setattr(connector, field, value)

        # If auth_config was provided in the update, store it encrypted
        new_secrets = body.model_dump(exclude_none=True).get("auth_config")
        if new_secrets:
            from core.crypto import encrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            encrypted = await encrypt_for_tenant(
                __import__("json").dumps(new_secrets), tid
            )
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc:
                cc.credentials_encrypted = {"_encrypted": encrypted}
            else:
                new_cc = ConnectorConfig(
                    tenant_id=tid,
                    connector_name=connector.name,
                    display_name=connector.name,
                    auth_type=connector.auth_type or "api_key",
                    credentials_encrypted={"_encrypted": encrypted},
                    config={},
                    status="configured",
                )
                session.add(new_cc)

        await session.commit()
        await session.refresh(connector)

    return _connector_to_dict(connector)


# ── GET /connectors/{conn_id}/health ─────────────────────────────────────────
@router.get("/connectors/{conn_id}/health")
async def connector_health(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    return {
        "connector_id": str(connector.id),
        "name": connector.name,
        "status": connector.status,
        "health_check_at": connector.health_check_at.isoformat()
        if connector.health_check_at
        else None,
        "healthy": connector.status == "active",
    }
