"""Workflow A/B variant selection — deterministic hash bucketing.

Reuses the same hash strategy as core.feature_flags so a given user
always lands on the same variant across requests. Selection steps:

  1. Load all active variants for the workflow.
  2. Compute weight-normalized buckets: variant A = 0..70, variant B = 70..100.
  3. Hash (workflow_id, subject_id) into 0..99 and look up the bucket.

Winner selection (not implemented here — it's a batch job) compares
success_count / run_count across variants after a sufficient sample
and promotes the best one by setting the control's weight to 0.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select, update

from core.database import async_session_factory, get_tenant_session
from core.models.workflow_variant import WorkflowVariant

logger = structlog.get_logger()


def _variant_session(tenant_id: uuid.UUID | str | None, op: str):
    """Session for ``workflow_variants`` (FORCE-RLS).

    With a tenant id the exact RLS context is bound. Without one, a raw
    session is used for callers that predate the tenant argument: under a
    non-BYPASSRLS role it sees no rows / updates nothing, so it is logged
    loudly rather than silently. Callers should always pass ``tenant_id``.
    """
    if tenant_id is not None:
        return get_tenant_session(uuid.UUID(str(tenant_id)))
    logger.warning("workflow_ab_no_tenant_context", op=op)
    return async_session_factory()


@dataclass
class VariantPick:
    variant_id: uuid.UUID
    variant_name: str
    definition: dict


def _bucket(workflow_id: uuid.UUID, subject_id: str) -> int:
    h = hashlib.sha256(f"{workflow_id}:{subject_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") % 100


async def pick_variant(
    workflow_id: uuid.UUID,
    subject_id: str,
    tenant_id: uuid.UUID | str | None = None,
) -> VariantPick | None:
    """Return the variant a subject lands on, or None if no active variants.

    ``tenant_id`` binds the RLS context for ``workflow_variants``; when given,
    the query is additionally pinned to that tenant.
    """
    async with _variant_session(tenant_id, "pick_variant") as session:
        stmt = select(WorkflowVariant).where(
            WorkflowVariant.workflow_id == workflow_id,
            WorkflowVariant.is_active.is_(True),
        )
        if tenant_id is not None:
            stmt = stmt.where(WorkflowVariant.tenant_id == uuid.UUID(str(tenant_id)))
        result = await session.execute(stmt)
        variants = result.scalars().all()

    if not variants:
        return None

    total_weight = sum(v.weight for v in variants)
    if total_weight <= 0:
        return None

    bucket = _bucket(workflow_id, subject_id)
    # Normalize bucket to [0, total_weight)
    position = bucket * total_weight // 100

    running = 0
    for v in sorted(variants, key=lambda x: x.variant_name):
        running += v.weight
        if position < running:
            logger.debug(
                "variant_picked",
                workflow_id=str(workflow_id),
                variant=v.variant_name,
                bucket=bucket,
            )
            return VariantPick(
                variant_id=v.id,
                variant_name=v.variant_name,
                definition=v.definition,
            )

    # Fallback — should be unreachable
    return VariantPick(
        variant_id=variants[-1].id,
        variant_name=variants[-1].variant_name,
        definition=variants[-1].definition,
    )


async def record_outcome(
    variant_id: uuid.UUID,
    success: bool,
    tenant_id: uuid.UUID | str | None = None,
) -> None:
    """Increment run/success/failure counters after a run completes.

    ``tenant_id`` binds the RLS context for ``workflow_variants`` (the UPDATE
    matches zero rows without it under a non-BYPASSRLS role).
    """
    # Atomic SQL increment: concurrent runs must not lose counts to a
    # read-modify-write race.
    counter = WorkflowVariant.success_count if success else WorkflowVariant.failure_count
    values = {
        "run_count": WorkflowVariant.run_count + 1,
        counter.key: counter + 1,
    }
    stmt = update(WorkflowVariant).where(WorkflowVariant.id == variant_id).values(**values)
    if tenant_id is not None:
        stmt = stmt.where(WorkflowVariant.tenant_id == uuid.UUID(str(tenant_id)))
    async with _variant_session(tenant_id, "record_outcome") as session:
        await session.execute(stmt)
        await session.commit()
