"""Feedback collector — stores user feedback on agent runs.

Supports thumbs up/down, corrections, and HITL rejections.
Stores in agent_feedback DB table when available, falls back to
in-memory storage.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# In-memory fallback when DB is not available
_in_memory_store: dict[str, list[dict[str, Any]]] = {}

VALID_FEEDBACK_TYPES = {
    "thumbs_up",
    "thumbs_down",
    "correction",
    "hitl_approve",
    "hitl_reject",
    "hitl_override",
    "hitl_vote",
}


async def submit_feedback(
    agent_id: str,
    run_id: str,
    feedback_type: str,
    text: str = "",
    corrected_output: dict[str, Any] | None = None,
    tenant_id: str = "",
    original_output: dict[str, Any] | None = None,
    source: str = "manual",
    source_event_id: str | None = None,
    actor_id: str | None = None,
    decision: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit feedback for an agent run.

    Args:
        agent_id: The agent UUID.
        run_id: The run/task UUID.
        feedback_type: One of thumbs_up, thumbs_down, correction, hitl_reject.
        text: Optional free-text feedback.
        corrected_output: Optional corrected output dict (for corrections).
        tenant_id: Tenant UUID for multi-tenant isolation.

    Returns:
        dict with feedback_id and status.
    """
    if feedback_type not in VALID_FEEDBACK_TYPES:
        return {
            "feedback_id": "",
            "status": "error",
            "message": f"Invalid feedback_type: {feedback_type}. Must be one of {VALID_FEEDBACK_TYPES}",
        }

    feedback_id = str(uuid.uuid4())
    entry: dict[str, Any] = {
        "feedback_id": feedback_id,
        "agent_id": agent_id,
        "run_id": run_id,
        "feedback_type": feedback_type,
        "text": text,
        "original_output": original_output,
        "corrected_output": corrected_output,
        "tenant_id": tenant_id,
        "source": source,
        "source_event_id": source_event_id,
        "actor_id": actor_id,
        "decision": decision,
        "context": context or {},
        "created_at": datetime.now(UTC).isoformat(),
    }

    # Try DB storage first
    stored_in_db = False
    try:
        from core.database import get_tenant_session

        if tenant_id:
            tid = uuid.UUID(tenant_id)
            async with get_tenant_session(tid) as session:
                from sqlalchemy import text as sql_text

                await session.execute(
                    sql_text(
                        "INSERT INTO agent_feedback "
                        "(id, agent_id, run_id, feedback_type, feedback_text, original_output, "
                        "corrected_output, tenant_id, source, source_event_id, actor_id, decision, "
                        "context, created_at) VALUES (:id, :agent_id, :run_id, :feedback_type, "
                        ":feedback_text, CAST(:original_output AS JSONB), "
                        "CAST(:corrected_output AS JSONB), :tenant_id, :source, :source_event_id, "
                        ":actor_id, :decision, CAST(:context AS JSONB), NOW()) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": feedback_id,
                        "agent_id": agent_id,
                        "run_id": run_id,
                        "feedback_type": feedback_type,
                        "feedback_text": text,
                        "original_output": json.dumps(original_output) if original_output else None,
                        "corrected_output": json.dumps(corrected_output) if corrected_output else None,
                        "tenant_id": tenant_id,
                        "source": source,
                        "source_event_id": source_event_id,
                        "actor_id": actor_id,
                        "decision": decision,
                        "context": json.dumps(context or {}),
                    },
                )
                stored_in_db = True
    # enterprise-gate: broad-except-ok reason=feedback-db-write-failure-records-memory-storage-in-response
    except Exception as exc:
        logger.debug("feedback_db_unavailable_using_memory", error=str(exc))

    if not stored_in_db:
        # Fallback to in-memory storage keyed by tenant_id:agent_id
        key = f"{tenant_id}:{agent_id}"
        if key not in _in_memory_store:
            _in_memory_store[key] = []
        _in_memory_store[key].append(entry)

    logger.info(
        "feedback_submitted",
        agent_id=agent_id,
        run_id=run_id,
        feedback_type=feedback_type,
        stored_in_db=stored_in_db,
    )

    return {
        "feedback_id": feedback_id,
        "status": "stored",
        "storage": "database" if stored_in_db else "memory",
    }


async def list_feedback(
    agent_id: str,
    tenant_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List feedback entries for an agent.

    Tries DB first, falls back to in-memory store.
    """
    # Try DB
    try:
        from core.database import get_tenant_session

        if tenant_id:
            tid = uuid.UUID(tenant_id)
            async with get_tenant_session(tid) as session:
                from sqlalchemy import text as sql_text

                result = await session.execute(
                    sql_text(
                        "SELECT id, agent_id, run_id, feedback_type, feedback_text, "
                        "corrected_output, tenant_id, created_at, original_output, source, "
                        "source_event_id, actor_id, decision, context "
                        "FROM agent_feedback "
                        "WHERE agent_id = :agent_id AND tenant_id = :tenant_id "
                        "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                    ),
                    {
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                rows = result.fetchall()
                return [
                    {
                        "feedback_id": str(r[0]),
                        "agent_id": str(r[1]),
                        "run_id": str(r[2]),
                        "feedback_type": r[3],
                        "text": r[4],
                        "corrected_output": r[5],
                        "tenant_id": str(r[6]),
                        "created_at": str(r[7]),
                        "original_output": r[8],
                        "source": r[9],
                        "source_event_id": r[10],
                        "actor_id": r[11],
                        "decision": r[12],
                        "context": r[13],
                    }
                    for r in rows
                ]
    # enterprise-gate: broad-except-ok reason=feedback-db-read-failure-degrades-to-memory-store
    except Exception:
        logger.debug("feedback_list_db_unavailable_using_memory")

    # Fallback: in-memory
    key = f"{tenant_id}:{agent_id}"
    entries = _in_memory_store.get(key, [])
    # Sort by created_at desc
    entries_sorted = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)
    return entries_sorted[offset : offset + limit]


def get_in_memory_store() -> dict[str, list[dict[str, Any]]]:
    """Expose in-memory store for testing."""
    return _in_memory_store


def clear_in_memory_store() -> None:
    """Clear in-memory store (for testing)."""
    _in_memory_store.clear()
