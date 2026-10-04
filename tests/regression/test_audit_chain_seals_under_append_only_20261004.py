# SPDX-License-Identifier: Apache-2.0
"""The sealing task links audit rows while ``audit_log`` stays append-only.

``audit_log`` refuses every UPDATE and DELETE through its trigger. Sealing a
row into the hash chain is an UPDATE, so the trigger function admits exactly
that transition: the four chain columns of an unsealed row filled, nothing
else changed (``core.governance.audit_chain.SEAL_TRIGGER_SQL``). These tests
run the real sealing against Postgres with that rule in force and then try
every other mutation.

The rule is installed under names of the test's own and dropped afterwards. A
database that already carries the production trigger is left alone: the two
triggers would both fire, so the tests skip there.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = [
    pytest.mark.skipif(not os.getenv("AGENTICORG_DB_URL"), reason="requires Postgres (AGENTICORG_DB_URL)"),
    # The fixture runs on the session loop; the tests share it.
    pytest.mark.asyncio(loop_scope="session"),
]

_TEST_FUNCTION = "chain_test_audit_log_reject_mutation"
_TEST_TRIGGER = "chain_test_audit_log_immutable"


@pytest.fixture
async def sealing_rule(monkeypatch):
    """Build the schema, put the sealing-aware append-only rule in force and
    route the app's sessions through a private NullPool engine."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import core.database as db
    import core.models  # noqa: F401 — registers every ORM model
    from core.governance.audit_chain import SEAL_TRIGGER_SQL
    from core.models.base import BaseModel as ORMBase

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)
        had_trigger = (
            await conn.execute(
                text("SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_immutable' AND NOT tgisinternal")
            )
        ).first() is not None
    if had_trigger:
        await engine.dispose()
        pytest.skip("the production trigger is installed; the test rule would fire beside it")
    async with engine.begin() as conn:
        # A run killed before teardown can leave the test trigger behind.
        await conn.execute(text(f"DROP TRIGGER IF EXISTS {_TEST_TRIGGER} ON audit_log"))
        await conn.execute(text(SEAL_TRIGGER_SQL.replace("audit_log_reject_mutation", _TEST_FUNCTION)))
        await conn.execute(
            text(
                f"CREATE TRIGGER {_TEST_TRIGGER} BEFORE UPDATE OR DELETE ON audit_log "
                f"FOR EACH ROW EXECUTE FUNCTION {_TEST_FUNCTION}()"
            )
        )
    monkeypatch.setattr(
        db, "async_session_factory", async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    )
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TRIGGER IF EXISTS {_TEST_TRIGGER} ON audit_log"))
            await conn.execute(text(f"DROP FUNCTION IF EXISTS {_TEST_FUNCTION}()"))
        await engine.dispose()


async def _seed(count: int) -> uuid.UUID:
    from core.database import async_session_factory
    from core.models.audit import AuditLog
    from core.models.tenant import Tenant

    tid = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(Tenant(id=tid, name=f"chain-{tid}", slug=f"chain-{tid}", settings={}))
        await session.flush()
        for n in range(count):
            session.add(
                AuditLog(
                    tenant_id=tid,
                    event_type="x",
                    actor_type="user",
                    actor_id="someone",
                    action=f"did-{n}",
                    outcome="success",
                    details={},
                )
            )
        await session.commit()
    return tid


async def _refused(tid: uuid.UUID, statement: str) -> bool:
    from sqlalchemy.exc import DBAPIError

    from core.database import get_tenant_session

    try:
        async with get_tenant_session(tid) as session:
            await session.execute(text(statement), {"tid": str(tid)})
    except DBAPIError as exc:
        return "append-only" in str(exc)
    return False


async def test_sealing_links_rows_and_the_chain_verifies(sealing_rule):
    from core.governance import audit_chain

    tid = await _seed(5)
    first = await audit_chain.seal(tid, batch=3)
    assert first.sealed == 3 and first.more is True and first.head.seq == 3
    second = await audit_chain.seal(tid, batch=3)
    assert second.sealed == 2 and second.head.seq == 5
    result = await audit_chain.verify(tid)
    assert result.status == "verified" and result.verified == 5 and result.unsealed == 0
    assert result.anchor is not None and (result.anchor.seq, result.anchor.hash) == (5, second.head.hash)
    current = await audit_chain.status(tid)
    assert current["head"]["seq"] == 5 and current["anchor"]["hash"] == second.head.hash


async def test_every_other_mutation_is_still_refused(sealing_rule):
    from core.governance import audit_chain

    tid = await _seed(4)
    await audit_chain.seal(tid, batch=2)
    where = "WHERE tenant_id = CAST(:tid AS uuid)"
    # A sealed row: no field, and no chain column, changes again.
    assert await _refused(tid, f"UPDATE audit_log SET action = 'edited' {where} AND chain_seq IS NOT NULL")
    assert await _refused(tid, f"UPDATE audit_log SET chain_hash = repeat('f', 64) {where} AND chain_seq IS NOT NULL")
    assert await _refused(tid, f"UPDATE audit_log SET chain_seq = NULL {where} AND chain_seq IS NOT NULL")
    # An unsealed row: no field changes, alone or beside the chain columns, and a part-filled seal is refused.
    assert await _refused(tid, f"UPDATE audit_log SET action = 'edited' {where} AND chain_seq IS NULL")
    assert await _refused(
        tid,
        "UPDATE audit_log SET action = 'edited', chain_seq = 99, chain_prev = repeat('0', 64), "
        f"chain_hash = repeat('f', 64), sealed_at = now() {where} AND chain_seq IS NULL",
    )
    assert await _refused(tid, f"UPDATE audit_log SET chain_seq = 99 {where} AND chain_seq IS NULL")
    assert await _refused(tid, f"DELETE FROM audit_log {where}")
    # Nothing above took effect: the chain still verifies and the rest still seals.
    assert (await audit_chain.verify(tid)).status == "verified"
    assert (await audit_chain.seal(tid)).head.seq == 4
