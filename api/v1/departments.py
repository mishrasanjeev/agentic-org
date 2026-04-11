"""Department + cost-center management endpoints.

Lets a tenant admin build out their org hierarchy:
  - POST /departments
  - GET  /departments
  - POST /departments/{id}/cost-centers
  - GET  /cost-centers

All endpoints are tenant-scoped via RLS (agenticorg.tenant_id GUC).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import get_tenant_session
from core.models.organization import CostCenter, Department

logger = structlog.get_logger()
router = APIRouter(tags=["Organization"])


# ── Schemas ─────────────────────────────────────────────────────────


class DepartmentCreate(BaseModel):
    company_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    code: str | None = Field(None, max_length=50)
    parent_id: uuid.UUID | None = None
    manager_user_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    code: str | None
    parent_id: uuid.UUID | None
    manager_user_id: uuid.UUID | None


class CostCenterCreate(BaseModel):
    company_id: uuid.UUID
    department_id: uuid.UUID | None = None
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    budget_limit: Decimal | None = None
    fiscal_year: int | None = None


class CostCenterOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    department_id: uuid.UUID | None
    code: str
    name: str
    budget_limit: Decimal | None
    fiscal_year: int | None


# ── Department endpoints ───────────────────────────────────────────


@router.post("/departments", response_model=DepartmentOut, status_code=201)
async def create_department(
    body: DepartmentCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> DepartmentOut:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        dept = Department(
            tenant_id=tid,
            company_id=body.company_id,
            name=body.name,
            code=body.code,
            parent_id=body.parent_id,
            manager_user_id=body.manager_user_id,
        )
        session.add(dept)
        await session.flush()
        logger.info(
            "department_created",
            tenant_id=tenant_id,
            department_id=str(dept.id),
            name=dept.name,
        )
        return DepartmentOut(
            id=dept.id,
            company_id=dept.company_id,
            name=dept.name,
            code=dept.code,
            parent_id=dept.parent_id,
            manager_user_id=dept.manager_user_id,
        )


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    company_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[DepartmentOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        stmt = select(Department).where(Department.tenant_id == tid)
        if company_id is not None:
            stmt = stmt.where(Department.company_id == company_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            DepartmentOut(
                id=d.id,
                company_id=d.company_id,
                name=d.name,
                code=d.code,
                parent_id=d.parent_id,
                manager_user_id=d.manager_user_id,
            )
            for d in rows
        ]


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(
    department_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Department).where(
                Department.tenant_id == tid, Department.id == department_id
            )
        )
        dept = result.scalar_one_or_none()
        if dept is None:
            raise HTTPException(404, "Department not found")
        await session.delete(dept)
        logger.info(
            "department_deleted", tenant_id=tenant_id, department_id=str(department_id)
        )


# ── Cost center endpoints ──────────────────────────────────────────


@router.post("/cost-centers", response_model=CostCenterOut, status_code=201)
async def create_cost_center(
    body: CostCenterCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> CostCenterOut:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        cc = CostCenter(
            tenant_id=tid,
            company_id=body.company_id,
            department_id=body.department_id,
            code=body.code,
            name=body.name,
            budget_limit=body.budget_limit,
            fiscal_year=body.fiscal_year,
        )
        session.add(cc)
        await session.flush()
        logger.info(
            "cost_center_created",
            tenant_id=tenant_id,
            cost_center_id=str(cc.id),
            code=cc.code,
        )
        return CostCenterOut(
            id=cc.id,
            company_id=cc.company_id,
            department_id=cc.department_id,
            code=cc.code,
            name=cc.name,
            budget_limit=cc.budget_limit,
            fiscal_year=cc.fiscal_year,
        )


@router.get("/cost-centers", response_model=list[CostCenterOut])
async def list_cost_centers(
    company_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_current_tenant),
) -> list[CostCenterOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        stmt = select(CostCenter).where(CostCenter.tenant_id == tid)
        if company_id is not None:
            stmt = stmt.where(CostCenter.company_id == company_id)
        if department_id is not None:
            stmt = stmt.where(CostCenter.department_id == department_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            CostCenterOut(
                id=c.id,
                company_id=c.company_id,
                department_id=c.department_id,
                code=c.code,
                name=c.name,
                budget_limit=c.budget_limit,
                fiscal_year=c.fiscal_year,
            )
            for c in rows
        ]
