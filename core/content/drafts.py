# SPDX-License-Identifier: Apache-2.0
"""The drafts queue: every draft is kept; a notice or circular waits for a second person to approve it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from core.content.services import ContentError, Run

logger = structlog.get_logger()

STATUSES = ("draft", "pending_approval", "approved", "rejected")
DECISIONS = {"approve": "approved", "reject": "rejected"}


def draft_dict(row: Any, *, with_output: bool = True) -> dict[str, Any]:
    out = {
        "id": str(row.id),
        "service": row.service,
        "kind": row.kind,
        "status": row.status,
        "title": row.title,
        "sources": list(row.sources or []),
        "guardrails": dict(row.guardrails or {}),
        "created_by": row.created_by,
        "decided_by": row.decided_by,
        "decision_notes": row.decision_notes,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if with_output:
        out["output"] = dict(row.output or {})
        out["input"] = dict(row.input or {})
    return out


async def record(
    tenant_id: uuid.UUID,
    *,
    user_id: str,
    run: Run,
    payload: dict[str, Any],
    kind: str,
    title: str,
    requires_approval: bool,
) -> dict[str, Any]:
    """Keep the draft; a draft that needs approval waits in the queue."""
    from core.database import get_tenant_session
    from core.models.content_draft import ContentDraft

    row = ContentDraft(
        tenant_id=tenant_id,
        service=run.service,
        kind=kind[:16],
        status="pending_approval" if requires_approval else "draft",
        title=title[:300],
        input=payload,
        output=run.output,
        sources=run.sources,
        guardrails=run.guardrails,
        created_by=str(user_id)[:128] if user_id else None,
    )
    async with get_tenant_session(tenant_id) as session:
        session.add(row)
        await session.flush()
        answer = draft_dict(row)
    logger.info("content_draft_recorded", service=run.service, kind=kind, status=answer["status"])
    return answer


async def list_drafts(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.content_draft import ContentDraft

    async with get_tenant_session(tenant_id) as session:
        query = select(ContentDraft).where(ContentDraft.tenant_id == tenant_id)
        if status:
            query = query.where(ContentDraft.status == status)
        rows = (await session.execute(query.order_by(ContentDraft.created_at.desc()).limit(limit))).scalars().all()
    return [draft_dict(row, with_output=False) for row in rows]


async def get_draft(tenant_id: uuid.UUID, draft_id: uuid.UUID) -> dict[str, Any] | None:
    from core.database import get_tenant_session
    from core.models.content_draft import ContentDraft

    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ContentDraft).where(ContentDraft.tenant_id == tenant_id, ContentDraft.id == draft_id)
            )
        ).scalar_one_or_none()
        return draft_dict(row) if row is not None else None


async def decide(
    tenant_id: uuid.UUID, draft_id: uuid.UUID, *, user_id: str, decision: str, notes: str = ""
) -> dict[str, Any]:
    """Approve or reject a waiting draft; the author may not decide their own (maker-checker)."""
    from core.database import get_tenant_session
    from core.models.content_draft import ContentDraft

    status = DECISIONS.get(decision)
    if status is None:
        raise ContentError(422, "decision_unknown", "decision must be approve or reject")
    async with get_tenant_session(tenant_id) as session:
        row = (
            await session.execute(
                select(ContentDraft)
                .where(ContentDraft.tenant_id == tenant_id, ContentDraft.id == draft_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContentError(404, "not_found", "No such draft")
        if row.status != "pending_approval":
            raise ContentError(409, "not_pending", f"The draft is {row.status}, not awaiting approval")
        if row.created_by and user_id and str(row.created_by) == str(user_id)[:128]:
            raise ContentError(409, "same_person", "A draft is approved by a second person, not its author")
        row.status = status
        row.decided_by = str(user_id)[:128] if user_id else None
        row.decision_notes = notes[:2000] or None
        row.decided_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        answer = draft_dict(row)
    logger.info("content_draft_decided", decision=status)
    return answer
