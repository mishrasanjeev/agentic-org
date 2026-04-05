"""Feedback collector — stores user feedback on agent runs.

Supports thumbs up/down, corrections, and HITL rejections.
Stores in agent_feedback DB table when available, falls back to
in-memory storage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# In-memory fallback when DB is not available
_in_memory_store: dict[str, list[dict[str, Any]]] = {}

VALID_FEEDBACK_TYPES = {"thumbs_up", "thumbs_down", "correction", "hitl_reject"}


async def submit_feedback(
    agent_id: str,
    run_id: str,
    feedback_type: str,
    text: str = "",
    corrected_output: dict[str, Any] | None = None,
    tenant_id: str = "",
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
        "corrected_output": corrected_output,
        "tenant_id": tenant_id,
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
                        "(id, agent_id, run_id, feedback_type, text, corrected_output, tenant_id, created_at) "
                        "VALUES (:id, :agent_id, :run_id, :feedback_type, :text, :corrected_output, :tenant_id, NOW())"
                    ),
                    {
                        "id": feedback_id,
                        "agent_id": agent_id,
                        "run_id": run_id,
                        "feedback_type": feedback_type,
                        "text": text,
                        "corrected_output": str(corrected_output) if corrected_output else None,
                        "tenant_id": tenant_id,
                    },
                )
                stored_in_db = True
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
                        "SELECT id, agent_id, run_id, feedback_type, text, "
                        "corrected_output, tenant_id, created_at "
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
                    }
                    for r in rows
                ]
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
