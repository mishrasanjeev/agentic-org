# SPDX-License-Identifier: Apache-2.0
"""Which workbenches and tabs a caller gets, and the counts behind each tab.

A caller holds a workbench through their platform role (the workbench's
default roles) or through an assignment an administrator made. Within a
workbench, a tab with named roles is shown only to those roles; a sensitive
tab is never shown to a role it does not name. The counts come from the
stores behind the tabs and are tenant-scoped reads.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select

from core.config import settings
from core.workbench.definitions import ADMIN, WORKBENCHES, Tab, Workbench

logger = structlog.get_logger()


def enabled() -> bool:
    return bool(getattr(settings, "workbench_v2_enabled", False))


def holds(workbench: Workbench, role: str, assigned: set[str]) -> bool:
    return role == ADMIN or workbench.name in assigned or role in workbench.default_roles


def tabs_for(workbench: Workbench, role: str) -> list[Tab]:
    """The tabs of a workbench a role may see: a tab with named roles only for them; admin sees all."""
    if role == ADMIN:
        return list(workbench.tabs)
    return [tab for tab in workbench.tabs if not tab.roles or role in tab.roles]


def workbenches_for(role: str, assigned: set[str] | None = None) -> list[dict[str, Any]]:
    """The caller's workbenches with the tabs they may see, in catalogue order."""
    assigned = assigned or set()
    out = []
    for workbench in WORKBENCHES.values():
        if not holds(workbench, role, assigned):
            continue
        tabs = tabs_for(workbench, role)
        if not tabs:
            continue
        out.append({**workbench.to_dict(tabs), "held_by": "assignment" if workbench.name in assigned else "role"})
    return out


def may_open(workbench_name: str, tab_key: str, role: str, assigned: set[str] | None = None) -> bool:
    workbench = WORKBENCHES.get(workbench_name)
    if workbench is None or not holds(workbench, role, assigned or set()):
        return False
    return any(tab.key == tab_key for tab in tabs_for(workbench, role))


async def _count(session: Any, model: Any, *conditions: Any) -> int:
    value = await session.scalar(select(func.count()).select_from(model).where(*conditions))
    return int(value or 0)


async def counts(tenant_id: uuid.UUID, sources: set[str]) -> dict[str, int | None]:
    """The number of items waiting behind each source; None when a source has no counter or cannot be read."""
    from core.database import get_tenant_session

    out: dict[str, int | None] = {}
    try:
        async with get_tenant_session(tenant_id) as session:
            if "approvals" in sources:
                from core.models.hitl import HITLQueue

                out["approvals"] = await _count(
                    session, HITLQueue, HITLQueue.tenant_id == tenant_id, HITLQueue.status == "pending"
                )
            if "documents" in sources:
                from core.models.idp_document import IdpDocument

                out["documents"] = await _count(
                    session, IdpDocument, IdpDocument.tenant_id == tenant_id, IdpDocument.status == "review"
                )
            if "drafts" in sources:
                from core.models.content_draft import ContentDraft

                out["drafts"] = await _count(
                    session,
                    ContentDraft,
                    ContentDraft.tenant_id == tenant_id,
                    ContentDraft.status == "pending_approval",
                )
            if "conversations" in sources:
                from core.models.conversation_session import ConversationSession

                out["conversations"] = await _count(
                    session,
                    ConversationSession,
                    ConversationSession.tenant_id == tenant_id,
                    ConversationSession.status.in_(("active", "escalated")),
                )
    except (RuntimeError, OSError) as exc:
        logger.warning("workbench_counts_unavailable", error_type=type(exc).__name__)
    for source in sources:
        out.setdefault(source, None)
    return out


async def summary(
    tenant_id: uuid.UUID, workbench_name: str, role: str, assigned: set[str] | None = None
) -> dict[str, Any] | None:
    """One workbench with the caller's tabs and the count behind each."""
    workbench = WORKBENCHES.get(workbench_name)
    if workbench is None or not holds(workbench, role, assigned or set()):
        return None
    tabs = tabs_for(workbench, role)
    found = await counts(tenant_id, {tab.source for tab in tabs})
    return {
        **workbench.to_dict(tabs),
        "held_by": "assignment" if workbench_name in (assigned or set()) else "role",
        "counts": {tab.key: found.get(tab.source) for tab in tabs},
        "waiting": sum(v for v in found.values() if isinstance(v, int)),
    }
