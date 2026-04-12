"""User delegation endpoints.

A user on vacation can delegate their approvals to a colleague for a
bounded time window. Approval routing in core/approvals checks for an
active delegation for the intended assignee before creating or emailing
the HITL item.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.delegation import UserDelegation

logger = structlog.get_logger()
router = APIRouter(tags=["Organization"], dependencies=[require_tenant_admin])


class DelegationCreate(BaseModel):
    delegator_id: uuid.UUID
    delegate_id: uuid.UUID
    reason: str | None = Field(None, max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class DelegationOut(BaseModel):
    id: uuid.UUID
    delegator_id: uuid.UUID
    delegate_id: uuid.UUID
    reason: str | None
    starts_at: datetime
    ends_at: datetime | None
    revoked_at: datetime | None


@router.post("/delegations", response_model=DelegationOut, status_code=201)
async def create_delegation(
    body: DelegationCreate,
    tenant_id: str = Depends(get_current_tenant),
) -> DelegationOut:
    if body.delegator_id == body.delegate_id:
        raise HTTPException(400, "A user cannot delegate to themselves")
    if body.ends_at and body.starts_at and body.ends_at <= body.starts_at:
        raise HTTPException(400, "ends_at must be after starts_at")

    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        delegation = UserDelegation(
            tenant_id=tid,
            delegator_id=body.delegator_id,
            delegate_id=body.delegate_id,
            reason=body.reason,
            starts_at=body.starts_at or datetime.now(UTC),
            ends_at=body.ends_at,
        )
        session.add(delegation)
        await session.flush()
        logger.info(
            "delegation_created",
            tenant_id=tenant_id,
            delegator_id=str(delegation.delegator_id),
            delegate_id=str(delegation.delegate_id),
        )
        return _to_out(delegation)


@router.get("/delegations", response_model=list[DelegationOut])
async def list_delegations(
    active_only: bool = True,
    tenant_id: str = Depends(get_current_tenant),
) -> list[DelegationOut]:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        stmt = select(UserDelegation).where(UserDelegation.tenant_id == tid)
        if active_only:
            now = datetime.now(UTC)
            stmt = stmt.where(
                and_(
                    UserDelegation.revoked_at.is_(None),
                    UserDelegation.starts_at <= now,
                    or_(
                        UserDelegation.ends_at.is_(None),
                        UserDelegation.ends_at > now,
                    ),
                )
            )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [_to_out(d) for d in rows]


@router.delete("/delegations/{delegation_id}", status_code=204)
async def revoke_delegation(
    delegation_id: uuid.UUID,
    tenant_id: str = Depends(get_current_tenant),
) -> None:
    tid = uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(UserDelegation).where(
                UserDelegation.tenant_id == tid, UserDelegation.id == delegation_id
            )
        )
        delegation = result.scalar_one_or_none()
        if delegation is None:
            raise HTTPException(404, "Delegation not found")
        delegation.revoked_at = datetime.now(UTC)
        logger.info(
            "delegation_revoked",
            tenant_id=tenant_id,
            delegation_id=str(delegation_id),
        )


async def resolve_delegate(
    tenant_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    """Return the active delegate for user_id, or user_id itself if none."""
    now = datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(
            select(UserDelegation).where(
                UserDelegation.tenant_id == tenant_id,
                UserDelegation.delegator_id == user_id,
                UserDelegation.revoked_at.is_(None),
                UserDelegation.starts_at <= now,
                or_(
                    UserDelegation.ends_at.is_(None),
                    UserDelegation.ends_at > now,
                ),
            )
        )
        delegation = result.scalar_one_or_none()
        return delegation.delegate_id if delegation else user_id


def _to_out(d: UserDelegation) -> DelegationOut:
    return DelegationOut(
        id=d.id,
        delegator_id=d.delegator_id,
        delegate_id=d.delegate_id,
        reason=d.reason,
        starts_at=d.starts_at,
        ends_at=d.ends_at,
        revoked_at=d.revoked_at,
    )
