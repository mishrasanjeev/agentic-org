"""Schema registry endpoints."""

from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.schema_registry import SchemaRegistry
from core.schemas.api import PaginatedResponse, SchemaCreate

router = APIRouter()


def _schema_to_dict(s: SchemaRegistry) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "version": s.version,
        "description": s.description,
        "json_schema": s.json_schema,
        "is_default": s.is_default,
        "created_by": str(s.created_by) if s.created_by else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# ── GET /schemas ─────────────────────────────────────────────────────────────
@router.get("/schemas", response_model=PaginatedResponse)
async def list_schemas(
    page: int = 1,
    per_page: int = 20,
    tenant_id: str = Depends(get_current_tenant),
):
    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        count_q = (
            select(func.count()).select_from(SchemaRegistry).where(SchemaRegistry.tenant_id == tid)
        )
        total = (await session.execute(count_q)).scalar() or 0

        query = (
            select(SchemaRegistry)
            .where(SchemaRegistry.tenant_id == tid)
            .order_by(SchemaRegistry.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await session.execute(query)
        schemas = result.scalars().all()

    pages = max(1, (total + per_page - 1) // per_page)
    return PaginatedResponse(
        items=[_schema_to_dict(s) for s in schemas],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ── GET /schemas/{name} ─────────────────────────────────────────────────────
#
# Codex 2026-04-22 release-signoff review: the Schemas UI was reading
# hardcoded SCHEMA_DEFINITIONS from a local TS file instead of the
# persisted backend schemas. That meant custom schemas created via
# POST/PUT never appeared in the editor. This route lets the UI
# resolve the latest version for a given schema name in one call.
@router.get("/schemas/{name}")
async def get_schema_by_name(
    name: str,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(SchemaRegistry)
            .where(
                SchemaRegistry.tenant_id == tid,
                SchemaRegistry.name == name,
            )
            .order_by(SchemaRegistry.created_at.desc())
            .limit(1)
        )
        schema_row = result.scalar_one_or_none()

    if not schema_row:
        raise HTTPException(404, f"Schema {name!r} not found")
    return _schema_to_dict(schema_row)


# ── POST /schemas ────────────────────────────────────────────────────────────
@router.post("/schemas", status_code=201)
async def create_schema(
    body: SchemaCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        schema_row = SchemaRegistry(
            tenant_id=tid,
            name=body.name,
            version=body.version,
            description=body.description,
            json_schema=body.json_schema,
            is_default=body.is_default,
        )
        session.add(schema_row)
        await session.flush()

    return _schema_to_dict(schema_row)


# ── PUT /schemas/{name} ─────────────────────────────────────────────────────
@router.put("/schemas/{name}")
async def upsert_schema(
    name: str,
    body: SchemaCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Try to find existing schema by name + version for this tenant
        result = await session.execute(
            select(SchemaRegistry).where(
                SchemaRegistry.tenant_id == tid,
                SchemaRegistry.name == name,
                SchemaRegistry.version == body.version,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update in place
            existing.description = body.description
            existing.json_schema = body.json_schema
            existing.is_default = body.is_default
            schema_row = existing
            created = False
        else:
            # Insert new
            schema_row = SchemaRegistry(
                tenant_id=tid,
                name=name,
                version=body.version,
                description=body.description,
                json_schema=body.json_schema,
                is_default=body.is_default,
            )
            session.add(schema_row)
            await session.flush()
            created = True

    resp = _schema_to_dict(schema_row)
    resp["created"] = created
    return resp
