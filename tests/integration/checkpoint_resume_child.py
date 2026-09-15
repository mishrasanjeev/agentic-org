# SPDX-License-Identifier: Apache-2.0
"""Child process for ``test_hitl_checkpoint_restart_resume.py``.

Plays the part of a freshly started API or worker: a new interpreter with no
state from the process that paused the run. It reaches the checkpoint only
through the approval row (tenant-scoped session), opens its own checkpoint
store and resumes the run. Prints one JSON object on stdout.

    python tests/integration/checkpoint_resume_child.py <tenant_id> <hitl_id> <decision-json>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def _resume(tenant_id: str, hitl_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import core.database as db
    import core.langgraph.agent_graph as agent_graph
    from core.langgraph import checkpointer, runner
    from core.models.hitl import HITLQueue
    from core.test_doubles.scripted_model import ScriptedChatModel

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    db.engine = engine
    db.async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Resuming at the approval gate needs no model turn; any request fails loudly.
    model = ScriptedChatModel(steps=[])
    agent_graph.create_chat_model = lambda *_a, **_k: model  # type: ignore[assignment]

    try:
        tid = uuid.UUID(tenant_id)
        async with db.get_tenant_session(tid) as session:
            row = (
                await session.execute(
                    select(HITLQueue).where(HITLQueue.id == uuid.UUID(hitl_id), HITLQueue.tenant_id == tid)
                )
            ).scalar_one_or_none()
        if row is None or not row.checkpoint_thread_id:
            return {"outcome": "approval_not_found", "pid": os.getpid()}
        result = await runner.resume_agent(
            agent_id=str(row.agent_id),
            thread_id=row.checkpoint_thread_id,
            decision=decision,
            system_prompt="scripted",
            authorized_tools=[],
            confidence_floor=0.5,
            hitl_condition="total > 500000",
            tenant_id=tenant_id,
        )
        model.assert_consumed()
        return {"outcome": "resumed", "pid": os.getpid(), "result": result}
    finally:
        await checkpointer.close_checkpointer()
        await engine.dispose()


def main() -> None:
    tenant_id, hitl_id, decision = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    print(json.dumps(asyncio.run(_resume(tenant_id, hitl_id, decision), loop_factory=factory), default=str))


if __name__ == "__main__":
    main()
