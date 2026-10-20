# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Apply due OACP retention dispositions: the one narrow DELETE on an append-only OACP table.

OACP review manifests (``oacp_audit_review_manifest_records``) are append-only
(``v6z88_commerce_ledger``). Retention is the single transition out: a manifest
may be deleted when a stored retention disposition decision
(``oacp_retention_disposition_decision_records``) makes it due:

- the decision is ``approve_future_retention_review`` and its ``retain_until``
  has passed;
- the manifest is in the decision's tenant and merchant (and its seller and
  buyer agent when the decision names them), has the decision's retention
  class, is not ``legal_hold_candidate``, and its own ``retain_until`` has
  passed and is not later than the decision's;
- no ``approve_future_legal_hold_review`` or ``block_unsafe_disposition``
  decision exists for that tenant, merchant and retention class, and no
  ``reject_disposition`` or ``defer_until_recheck`` decision exists for the
  same source summary.

The database enforces this, not only this module: the task calls
``oacp_delete_due_manifest(disposition_id, manifest_id)`` (``DELETE_DUE_MANIFEST_SQL``,
SECURITY DEFINER, so the application role holds no DELETE grant on the table;
row-level security still binds it to the session's tenant). The function names
the disposition decision in a transaction-local setting
(``agenticorg.oacp_retention_disposition``) and the table's trigger
(``MANIFEST_RETENTION_GUARD_SQL``) re-checks every condition above for the row
before it lets the DELETE through; anything else is refused as before.
Each deletion is itself recorded on the commerce ledger
(``commerce.oacp.retention.manifest_deleted``, with the manifest id, the
disposition id and the retention facts) in the same transaction, and each
applied disposition once more (``commerce.oacp.retention.disposition_applied``).
The disposition decision rows and the ledger rows are never deleted.

The task (``core.tasks.commerce_ledger_tasks``) runs this for every tenant
behind ``AGENTICORG_OACP_RETENTION_APPLY_ENABLED`` (off by default). Nobody
approves a run: the stored decisions are the authority, and nothing waits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.commerce.ledger import record_commerce_event

logger = structlog.get_logger()

APPROVING_KIND = "approve_future_retention_review"
CLASS_HOLD_KINDS: tuple[str, ...] = ("approve_future_legal_hold_review", "block_unsafe_disposition")
SUMMARY_HOLD_KINDS: tuple[str, ...] = ("reject_disposition", "defer_until_recheck")
NEVER_DELETED_CLASS = "legal_hold_candidate"
DISPOSITION_SETTING = "agenticorg.oacp_retention_disposition"
DELETED_EVENT = "commerce.oacp.retention.manifest_deleted"
APPLIED_EVENT = "commerce.oacp.retention.disposition_applied"

# Installed by v6z88_commerce_ledger on oacp_audit_review_manifest_records; the two must stay the same text.
MANIFEST_RETENTION_GUARD_SQL = """CREATE OR REPLACE FUNCTION oacp_manifest_retention_guard() RETURNS trigger AS $$
DECLARE
    disposition_id text := current_setting('agenticorg.oacp_retention_disposition', true);
BEGIN
    IF TG_OP = 'DELETE'
       AND disposition_id IS NOT NULL AND disposition_id <> ''
       AND OLD.tenant_id = NULLIF(current_setting('agenticorg.tenant_id', true), '')
       AND OLD.retention_class <> 'legal_hold_candidate'
       AND OLD.retain_until <= now()
       AND EXISTS (
           SELECT 1 FROM oacp_retention_disposition_decision_records d
           WHERE d.disposition_decision_id = disposition_id
             AND d.decision_kind = 'approve_future_retention_review'
             AND d.tenant_id = OLD.tenant_id
             AND d.merchant_id = OLD.merchant_id
             AND (d.seller_agent_id IS NULL OR d.seller_agent_id = OLD.seller_agent_id)
             AND (d.buyer_agent_id IS NULL OR d.buyer_agent_id = OLD.buyer_agent_id)
             AND d.retention_class = OLD.retention_class
             AND d.retain_until <= now()
             AND OLD.retain_until <= d.retain_until
             AND NOT EXISTS (
                 SELECT 1 FROM oacp_retention_disposition_decision_records h
                 WHERE h.tenant_id = d.tenant_id
                   AND h.merchant_id = d.merchant_id
                   AND (
                       (h.decision_kind IN ('approve_future_legal_hold_review', 'block_unsafe_disposition')
                        AND h.retention_class = d.retention_class)
                       OR (h.decision_kind IN ('reject_disposition', 'defer_until_recheck')
                           AND h.source_summary_id = d.source_summary_id)
                   )
             )
       )
    THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
      '% is append-only — UPDATE/DELETE rejected', TG_TABLE_NAME
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

DELETE_DUE_MANIFEST_SQL = """CREATE OR REPLACE FUNCTION oacp_delete_due_manifest(
    p_disposition_id text, p_manifest_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    deleted integer;
BEGIN
    PERFORM set_config('agenticorg.oacp_retention_disposition', p_disposition_id, true);
    -- The definer may bypass row-level security: bind the tenant explicitly.
    DELETE FROM oacp_audit_review_manifest_records
    WHERE manifest_id = p_manifest_id
      AND tenant_id = NULLIF(current_setting('agenticorg.tenant_id', true), '');
    GET DIAGNOSTICS deleted = ROW_COUNT;
    PERFORM set_config('agenticorg.oacp_retention_disposition', '', true);
    RETURN deleted = 1;
END;
$$;"""


async def _held(session: Any, disposition: Any) -> bool:
    from sqlalchemy import and_, or_, select

    from core.models.oacp_retention_disposition_decision import OacpRetentionDispositionDecisionRecordRow as D

    hold = (
        await session.execute(
            select(D.disposition_decision_id)
            .where(
                D.tenant_id == disposition.tenant_id,
                D.merchant_id == disposition.merchant_id,
                or_(
                    and_(D.decision_kind.in_(CLASS_HOLD_KINDS), D.retention_class == disposition.retention_class),
                    and_(D.decision_kind.in_(SUMMARY_HOLD_KINDS), D.source_summary_id == disposition.source_summary_id),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return hold is not None


async def due_manifests(session: Any, disposition: Any, now: datetime) -> list[Any]:
    from sqlalchemy import select

    from core.models.oacp_audit_review_manifest import OacpAuditReviewManifestRecordRow as M

    query = select(M).where(
        M.tenant_id == disposition.tenant_id,
        M.merchant_id == disposition.merchant_id,
        M.retention_class == disposition.retention_class,
        M.retention_class != NEVER_DELETED_CLASS,
        M.retain_until <= now,
        M.retain_until <= disposition.retain_until,
    )
    if disposition.seller_agent_id is not None:
        query = query.where(M.seller_agent_id == disposition.seller_agent_id)
    if disposition.buyer_agent_id is not None:
        query = query.where(M.buyer_agent_id == disposition.buyer_agent_id)
    return list((await session.scalars(query.order_by(M.manifest_id))).all())


async def due_dispositions(session: Any, tenant_id: uuid.UUID, now: datetime) -> list[Any]:
    """The tenant's approving dispositions whose retain_until passed and that no hold blocks."""
    from sqlalchemy import select

    from core.models.oacp_retention_disposition_decision import OacpRetentionDispositionDecisionRecordRow as D

    rows = (
        await session.scalars(
            select(D)
            .where(D.tenant_id == str(tenant_id), D.decision_kind == APPROVING_KIND, D.retain_until <= now)
            .order_by(D.decided_at, D.disposition_decision_id)
        )
    ).all()
    return [row for row in rows if not await _held(session, row)]


async def apply_disposition(session: Any, disposition: Any, *, now: datetime) -> list[str]:
    """Delete the manifests ``disposition`` makes due, each ledgered, in the caller's transaction."""
    from sqlalchemy import text

    manifests = await due_manifests(session, disposition, now)
    if not manifests:
        return []
    deleted: list[str] = []
    for manifest in manifests:
        removed = (
            await session.execute(
                text("SELECT oacp_delete_due_manifest(:disposition, :manifest)"),
                {"disposition": disposition.disposition_decision_id, "manifest": manifest.manifest_id},
            )
        ).scalar()
        if removed is not True:
            continue
        retention = {
            "manifest_id": manifest.manifest_id,
            "bundle_id": manifest.bundle_id,
            "disposition_decision_id": disposition.disposition_decision_id,
            "retention_class": manifest.retention_class,
            "retain_until": manifest.retain_until,
        }
        await record_commerce_event(
            session,
            tenant_id=manifest.tenant_id,
            event=DELETED_EVENT,
            outcome="success",
            merchant_id=manifest.merchant_id,
            seller_agent_id=manifest.seller_agent_id,
            buyer_agent_id=manifest.buyer_agent_id,
            payload=retention,
            resource_id=manifest.manifest_id,
            actor_type="system",
            actor_id="oacp_retention_task",
            details={"retention": retention},
            now=now,
        )
        deleted.append(manifest.manifest_id)
    if not deleted:
        return []
    await record_commerce_event(
        session,
        tenant_id=disposition.tenant_id,
        event=APPLIED_EVENT,
        outcome="success",
        merchant_id=disposition.merchant_id,
        seller_agent_id=disposition.seller_agent_id,
        buyer_agent_id=disposition.buyer_agent_id,
        payload={"disposition_decision_id": disposition.disposition_decision_id, "manifest_ids": deleted},
        resource_id=disposition.disposition_decision_id,
        actor_type="system",
        actor_id="oacp_retention_task",
        details={"disposition_decision_id": disposition.disposition_decision_id, "deleted": len(deleted)},
        now=now,
    )
    return deleted


async def apply_due_dispositions(tenant_id: uuid.UUID, *, now: datetime | None = None) -> dict[str, Any]:
    """Apply every due disposition of one tenant, each in its own transaction; a failure is isolated."""
    from core.database import get_tenant_session

    moment = now or datetime.now(UTC)
    async with get_tenant_session(tenant_id) as session:
        disposition_ids = [row.disposition_decision_id for row in await due_dispositions(session, tenant_id, moment)]
    deleted = 0
    errors = 0
    for disposition_id in disposition_ids:
        try:
            async with get_tenant_session(tenant_id) as session:
                from core.models.oacp_retention_disposition_decision import (
                    OacpRetentionDispositionDecisionRecordRow,
                )

                disposition = await session.get(OacpRetentionDispositionDecisionRecordRow, disposition_id)
                if disposition is None or await _held(session, disposition):
                    continue
                deleted += len(await apply_disposition(session, disposition, now=moment))
        # enterprise-gate: broad-except-ok reason=one-dispositions-failure-is-logged-and-never-stops-the-sweep
        except Exception as exc:
            errors += 1
            logger.warning(
                "oacp_retention_disposition_failed",
                tenant_id=str(tenant_id),
                disposition_decision_id=disposition_id,
                error_type=type(exc).__name__,
            )
    return {"dispositions": len(disposition_ids), "deleted": deleted, "errors": errors}
