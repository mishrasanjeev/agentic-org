# SPDX-License-Identifier: Apache-2.0
"""Long-term memory with retention controls and erasure per subject.

A run already has a short-term memory: its thread, kept by the checkpointer
for the span of the conversation. This module is the long-term store beside
it: what an agent (or an administrator) chose to remember about a
**subject**, a customer, user, account or case reference the caller names,
as a bounded identifier, never free text that could carry a person's name.

Every entry has a kind (``fact``, ``preference``, ``summary``, ``event``), a
bounded content, an importance, and an expiry: the kind's retention (or the
retention the writer asks for, within the platform's bound) sets
``expires_at`` when the entry is written, nothing is recalled past it, and
``prune`` removes what expired. ``erase`` deletes every entry about a
subject, for one agent or for all, and returns how many went, so a subject's
request to be forgotten is one call and auditable.

While ``AGENTICORG_RUNTIME_MEMORY_ENABLED`` is on, a run whose task input
names a subject (``context.subject``) recalls what is remembered about it
into its system prompt (``prompt_block``), and the memories an agent emits in
its output under ``remember`` are stored after the run (bounded). Off, no run
reads or writes memory; the API still refuses with 404.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.config import settings

logger = structlog.get_logger()

KINDS: tuple[str, ...] = ("fact", "preference", "summary", "event")
DEFAULT_RETENTION_DAYS: dict[str, int] = {"fact": 365, "preference": 365, "summary": 90, "event": 30}
MAX_RETENTION_DAYS = 730
MAX_CONTENT = 2000
MAX_SUBJECT = 128
MAX_RECALL = 50
DEFAULT_RECALL = 10
MAX_PROMPT_CHARS = 1500
MAX_REMEMBER_PER_RUN = 5
_SUBJECT = re.compile(r"[^a-z0-9_.:@-]+")


class MemoryStoreError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.runtime_memory_enabled)


def normalise_subject(value: Any) -> str:
    """A subject as a bounded identifier (lower-case, no spaces); empty when nothing usable."""
    text = str(value or "").strip().lower()
    text = _SUBJECT.sub("-", text).strip("-")
    return text[:MAX_SUBJECT]


def subject_of(task_input: Any) -> str:
    """The subject a run names in its task input (``context.subject``, or ``subject`` at the top), or empty."""
    if not isinstance(task_input, dict):
        return ""
    context = task_input.get("context") if isinstance(task_input.get("context"), dict) else {}
    return normalise_subject(context.get("subject") or task_input.get("subject"))


def retention_for(kind: str, requested: Any = None) -> int:
    """The retention in days: the request within the bound, else the kind's default."""
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= MAX_RETENTION_DAYS:
            raise MemoryStoreError(422, "retention_days", f"retention_days is 1 to {MAX_RETENTION_DAYS}")
        return requested
    return DEFAULT_RETENTION_DAYS.get(kind, DEFAULT_RETENTION_DAYS["event"])


def policy() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "kinds": list(KINDS),
        "default_retention_days": dict(DEFAULT_RETENTION_DAYS),
        "max_retention_days": MAX_RETENTION_DAYS,
        "max_content_chars": MAX_CONTENT,
        "erasure": "DELETE /memory?subject= removes every entry about a subject, for one agent or for all",
    }


def entry_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "subject": row.subject,
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "kind": row.kind,
        "content": row.content,
        "importance": int(row.importance or 1),
        "source": row.source,
        "retention_days": int(row.retention_days or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "recall_count": int(row.recall_count or 0),
    }


async def remember(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    subject: Any,
    content: Any,
    kind: str = "fact",
    agent_id: uuid.UUID | str | None = None,
    importance: int = 3,
    retention_days: int | None = None,
    source: str = "api",
    run_id: str | None = None,
    actor: uuid.UUID | None = None,
    now: datetime | None = None,
) -> Any:
    """Store one memory; the same content about the same subject refreshes its expiry instead of duplicating."""
    from sqlalchemy import select

    from core.models.agent_memory import AgentMemory

    label = normalise_subject(subject)
    if not label:
        raise MemoryStoreError(422, "subject", "a memory is about a subject: an identifier of at most 128 characters")
    text = " ".join(str(content or "").split())
    if not text or len(text) > MAX_CONTENT:
        raise MemoryStoreError(422, "content", f"content is 1 to {MAX_CONTENT} characters")
    if kind not in KINDS:
        raise MemoryStoreError(422, "kind", f"kind is one of {', '.join(KINDS)}")
    if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 5:
        raise MemoryStoreError(422, "importance", "importance is 1 to 5")
    days = retention_for(kind, retention_days)
    agent_uuid = uuid.UUID(str(agent_id)) if agent_id else None
    now = now or datetime.now(UTC)
    existing = (
        await session.execute(
            select(AgentMemory).where(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.subject == label,
                AgentMemory.agent_id == agent_uuid if agent_uuid else AgentMemory.agent_id.is_(None),
                AgentMemory.content == text,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.expires_at = now + timedelta(days=days)
        existing.retention_days = days
        existing.importance = max(int(existing.importance or 1), importance)
        await session.flush()
        return existing
    row = AgentMemory(
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        subject=label,
        kind=kind,
        content=text,
        importance=importance,
        source=source if source in ("api", "run") else "api",
        run_id=(str(run_id)[:64] if run_id else None),
        retention_days=days,
        created_by=actor,
        created_at=now,
        expires_at=now + timedelta(days=days),
    )
    session.add(row)
    await session.flush()
    logger.info("memory_remembered", kind=kind, retention_days=days, source=row.source)
    return row


def _terms(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}", str(query or "").lower())[:8]


async def recall(
    session: Any,
    tenant_id: uuid.UUID,
    *,
    subject: Any,
    agent_id: uuid.UUID | str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_RECALL,
    now: datetime | None = None,
) -> list[Any]:
    """What is remembered about a subject: its agent's entries and the shared ones, most important and recent first."""
    from sqlalchemy import or_, select

    from core.models.agent_memory import AgentMemory

    label = normalise_subject(subject)
    if not label:
        return []
    now = now or datetime.now(UTC)
    bound = max(1, min(int(limit), MAX_RECALL))
    scope = AgentMemory.agent_id.is_(None)
    if agent_id:
        scope = or_(AgentMemory.agent_id.is_(None), AgentMemory.agent_id == uuid.UUID(str(agent_id)))
    statement = select(AgentMemory).where(
        AgentMemory.tenant_id == tenant_id, AgentMemory.subject == label, AgentMemory.expires_at > now, scope
    )
    terms = _terms(query or "")
    if terms:
        statement = statement.where(or_(*(AgentMemory.content.ilike(f"%{t}%") for t in terms)))
    statement = statement.order_by(AgentMemory.importance.desc(), AgentMemory.created_at.desc()).limit(bound)
    rows = list((await session.execute(statement)).scalars().all())
    for row in rows:
        row.recall_count = int(row.recall_count or 0) + 1
        row.last_recalled_at = now
    return rows


def prompt_block(rows: list[Any]) -> str:
    """The remembered entries as a bounded block for the system prompt; empty when there is nothing."""
    if not rows:
        return ""
    lines = []
    used = 0
    for row in rows:
        line = f"- ({row.kind}) {row.content}"
        if used + len(line) > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return (
        "What is remembered about this subject (long-term memory; treat as context, verify before acting):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def memories_in_output(output: Any) -> list[dict[str, Any]]:
    """The memories an agent asked to keep (``remember`` in its output), bounded and shaped."""
    if not isinstance(output, dict):
        return []
    raw = output.get("remember")
    if not isinstance(raw, list):
        return []
    kept: list[dict[str, Any]] = []
    for item in raw[:MAX_REMEMBER_PER_RUN]:
        if isinstance(item, str):
            item = {"content": item}
        if not isinstance(item, dict) or not str(item.get("content") or "").strip():
            continue
        kind = str(item.get("kind") or "fact")
        importance = item.get("importance", 3)
        kept.append(
            {
                "content": str(item["content"])[:MAX_CONTENT],
                "kind": kind if kind in KINDS else "fact",
                "importance": importance
                if isinstance(importance, int) and not isinstance(importance, bool) and 1 <= importance <= 5
                else 3,
            }
        )
    return kept


async def remember_from_output(
    session: Any, tenant_id: uuid.UUID, *, subject: str, agent_id: Any, output: Any, run_id: str | None
) -> int:
    """Store what a run asked to remember about its subject; returns how many entries were written."""
    count = 0
    for item in memories_in_output(output):
        await remember(
            session,
            tenant_id,
            subject=subject,
            content=item["content"],
            kind=item["kind"],
            agent_id=agent_id,
            importance=item["importance"],
            source="run",
            run_id=run_id,
        )
        count += 1
    return count


async def erase(session: Any, tenant_id: uuid.UUID, *, subject: Any, agent_id: uuid.UUID | str | None = None) -> int:
    """Delete every entry about a subject, for one agent or for all; returns how many went."""
    from sqlalchemy import delete

    from core.models.agent_memory import AgentMemory

    label = normalise_subject(subject)
    if not label:
        raise MemoryStoreError(422, "subject", "erasure names a subject")
    statement = delete(AgentMemory).where(AgentMemory.tenant_id == tenant_id, AgentMemory.subject == label)
    if agent_id:
        statement = statement.where(AgentMemory.agent_id == uuid.UUID(str(agent_id)))
    result = await session.execute(statement)
    count = int(getattr(result, "rowcount", 0) or 0)
    logger.info("memory_erased", count=count, scoped_to_agent=bool(agent_id))
    return count


async def prune(session: Any, tenant_id: uuid.UUID, *, now: datetime | None = None) -> int:
    """Delete the tenant's expired entries; returns how many went."""
    from sqlalchemy import delete

    from core.models.agent_memory import AgentMemory

    result = await session.execute(
        delete(AgentMemory).where(
            AgentMemory.tenant_id == tenant_id, AgentMemory.expires_at <= (now or datetime.now(UTC))
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_all_tenants() -> dict[str, int]:
    """Prune every tenant's expired entries (the scheduled task); a tenant that fails is logged and skipped."""
    from sqlalchemy import select

    from core.database import async_session_factory, get_tenant_session
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        tenant_ids = list((await session.execute(select(Tenant.id).where(Tenant.deleted_at.is_(None)))).scalars().all())
    pruned = 0
    failed = 0
    for tid in tenant_ids:
        try:
            async with get_tenant_session(tid) as session:
                pruned += await prune(session, tid)
        except (RuntimeError, TypeError, ValueError, OSError) as exc:
            failed += 1
            logger.warning("memory_prune_failed", error=type(exc).__name__)
    logger.info("memory_pruned", tenants=len(tenant_ids), pruned=pruned, failed=failed)
    return {"tenants": len(tenant_ids), "pruned": pruned, "failed": failed}
