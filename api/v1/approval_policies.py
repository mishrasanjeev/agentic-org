"""Approval policy management endpoints.

Admins configure multi-step approval chains here. The /api/v1/approvals
decide endpoint consumes them via core.approvals.policy_engine.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import get_current_tenant
from core.database import async_session_factory
from core.models.approval_policy import ApprovalPolicy, ApprovalStep

logger = structlog.get_logger()
router = APIRouter(prefix="/approval-policies", tags=["Approvals"])


class StepIn(BaseModel):
    sequence: int = Field(..., ge=1)
    approver_role: str = Field(..., min_length=1, max_length=50)
    quorum_required: int = Field(1, ge=1)
    quorum_total: int = Field(1, ge=1)
    mode: str = Field("sequential", pattern="^(sequential|parallel)$")
    condition: str | None = Field(None, max_length=500)
    step_metadata: dict = Field(default_factory=dict)


class PolicyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    workflow_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    steps: list[StepIn] = Field(..., min_length=1)


class StepOut(BaseModel):
    id: uuid.UUID
    sequence: int
    approver_role: str
    quorum_required: int
    quorum_total: int
    mode: str
    condition: str | None
    step_metadata: dict


class PolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    workflow_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    steps: list[StepOut]


def _validate_steps(steps: list[StepIn]) -> None:
    for s in steps:
        if s.quorum_required > s.quorum_total:
            raise HTTPException(
                400, f"step {s.sequence}: quorum_required > quorum_total"
            )


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(
    body: PolicyIn,
    tenant_id: str = Depends(get_current_tenant),
) -> PolicyOut:
    _validate_steps(body.steps)
    tid = uuid.UUID(tenant_id)

    async with async_session_factory() as session:
        # Reject duplicate names per tenant
        result = await session.execute(
            select(ApprovalPolicy).where(
                ApprovalPolicy.tenant_id == tid, ApprovalPolicy.name == body.name
            )
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(409, f"Policy {body.name!r} already exists")

        policy = ApprovalPolicy(
            tenant_id=tid,
            name=body.name,
            description=body.description,
            workflow_id=body.workflow_id,
            agent_id=body.agent_id,
        )
        session.add(policy)
        await session.flush()

        for s in body.steps:
            step = ApprovalStep(
                policy_id=policy.id,
                sequence=s.sequence,
                approver_role=s.approver_role,
                quorum_required=s.quorum_required,
                quorum_total=s.quorum_total,
                mode=s.mode,
                condition=s.condition,
                step_metadata=s.step_metadata,
            )
            session.add(step)

        await session.commit()
        await session.refresh(policy)

        result = await session.execute(
            select(ApprovalStep)
            .where(ApprovalStep.policy_id == policy.id)
            .order_by(ApprovalStep.sequence)
        )
        steps = result.scalars().all()

    logger.info(
        "approval_policy_created",
        tenant_id=tenant_id,
        name=body.name,
        steps=len(body.steps),
    )
    return _to_out(policy, steps)


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    tenant_id: str = Depends(get_current_tenant),
) -> list[PolicyOut]:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(ApprovalPolicy).where(ApprovalPolicy.tenant_id == tid)
        )
        policies = result.scalars().all()

        out = []
        for p in policies:
            step_res = await session.execute(
                select(ApprovalStep)
                .where(ApprovalStep.policy_id == p.id)
                .order_by(ApprovalStep.sequence)
            )
            out.append(_to_out(p, step_res.scalars().all()))
        return out


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with async_session_factory() as session:
        result = await session.execute(
            select(ApprovalPolicy).where(
                ApprovalPolicy.tenant_id == tid, ApprovalPolicy.id == policy_id
            )
        )
        policy = result.scalar_one_or_none()
        if policy is None:
            raise HTTPException(404, "Policy not found")
        await session.delete(policy)
        await session.commit()
    logger.info(
        "approval_policy_deleted", tenant_id=tenant_id, policy_id=str(policy_id)
    )


def _to_out(policy: ApprovalPolicy, steps: list[ApprovalStep]) -> PolicyOut:
    return PolicyOut(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        workflow_id=policy.workflow_id,
        agent_id=policy.agent_id,
        steps=[
            StepOut(
                id=s.id,
                sequence=s.sequence,
                approver_role=s.approver_role,
                quorum_required=s.quorum_required,
                quorum_total=s.quorum_total,
                mode=s.mode,
                condition=s.condition,
                step_metadata=s.step_metadata,
            )
            for s in steps
        ],
    )
