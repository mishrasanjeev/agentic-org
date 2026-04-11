"""Workflow A/B variant CRUD endpoints."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import async_session_factory
from core.models.workflow_variant import WorkflowVariant

logger = structlog.get_logger()
router = APIRouter(prefix="/workflows/{workflow_id}/variants", tags=["Workflows"])


class VariantIn(BaseModel):
    variant_name: str = Field(..., min_length=1, max_length=100)
    weight: int = Field(50, ge=0, le=100)
    definition: dict
    is_active: bool = True


class VariantOut(BaseModel):
    id: uuid.UUID
    variant_name: str
    weight: int
    is_active: bool
    run_count: int
    success_count: int
    failure_count: int
    success_rate: float


def _to_out(v: WorkflowVariant) -> VariantOut:
    rate = 0.0
    if v.run_count > 0:
        rate = round(v.success_count / v.run_count, 4)
    return VariantOut(
        id=v.id,
        variant_name=v.variant_name,
        weight=v.weight,
        is_active=v.is_active,
        run_count=v.run_count,
        success_count=v.success_count,
        failure_count=v.failure_count,
        success_rate=rate,
    )


@router.get("", response_model=list[VariantOut])
async def list_variants(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> list[VariantOut]:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(WorkflowVariant).where(
                WorkflowVariant.tenant_id == tid,
                WorkflowVariant.workflow_id == workflow_id,
            )
        )
        return [_to_out(v) for v in result.scalars().all()]


@router.post("", response_model=VariantOut, status_code=201)
async def upsert_variant(
    workflow_id: uuid.UUID,
    body: VariantIn,
    tenant_id: str = Depends(get_current_tenant),
) -> VariantOut:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(WorkflowVariant).where(
                WorkflowVariant.tenant_id == tid,
                WorkflowVariant.workflow_id == workflow_id,
                WorkflowVariant.variant_name == body.variant_name,
            )
        )
        variant = result.scalar_one_or_none()
        if variant is None:
            variant = WorkflowVariant(
                tenant_id=tid,
                workflow_id=workflow_id,
                variant_name=body.variant_name,
                weight=body.weight,
                definition=body.definition,
                is_active=body.is_active,
            )
            session.add(variant)
        else:
            variant.weight = body.weight
            variant.definition = body.definition
            variant.is_active = body.is_active
        await session.commit()
        await session.refresh(variant)
    logger.info(
        "workflow_variant_upserted",
        tenant_id=tenant_id,
        workflow_id=str(workflow_id),
        variant=body.variant_name,
        weight=body.weight,
    )
    return _to_out(variant)


@router.delete("/{variant_name}", status_code=204)
async def delete_variant(
    workflow_id: uuid.UUID,
    variant_name: str,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(WorkflowVariant).where(
                WorkflowVariant.tenant_id == tid,
                WorkflowVariant.workflow_id == workflow_id,
                WorkflowVariant.variant_name == variant_name,
            )
        )
        variant = result.scalar_one_or_none()
        if variant is None:
            raise HTTPException(404, "Variant not found")
        await session.delete(variant)
        await session.commit()
