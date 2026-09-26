# SPDX-License-Identifier: Apache-2.0
"""Regression tests: DSAR erasure against the append-only audit log (2026-09-26).

Every ``POST /dsar/erase`` in production failed with a 500. ``erase_subject``
rewrote ``actor_id`` on the subject's ``audit_log`` rows, and the
``audit_log_immutable`` trigger rejects every UPDATE and DELETE on that table.
The CI schema is built with ``create_all``, which has no trigger, so the
Postgres replay in ``test_secrets_dsar_webhooks_audit_20260913`` passed.

Pinned here, against Postgres with the production rule in force (the
``audit_log_immutable`` trigger, or an identical one under test-only names):

1. Erasure completes: the ``users`` row is anonymised, feedback is
   pseudonymised, and the audit rows are left unchanged and reported as
   retained, with a count and the basis.
2. A database error inside the request's work is persisted as ``failed``
   instead of aborting the transaction and surfacing as an unrecorded 500.
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

# The rule ``core/database.py`` installs in production (``audit_log_immutable``
# calling ``audit_log_reject_mutation()``), under names of the test's own, so the
# fixture never replaces or drops a production object that is already there.
_TEST_FUNCTION = "dsar_test_audit_log_reject_mutation"
_TEST_TRIGGER = "dsar_test_audit_log_immutable"
_REJECT_MUTATION_FN = f"""
CREATE OR REPLACE FUNCTION {_TEST_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      'audit_log is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""
_IMMUTABLE_TRIGGER = f"""
CREATE TRIGGER {_TEST_TRIGGER}
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION {_TEST_FUNCTION}();
"""


@pytest.fixture
async def immutable_audit_log(monkeypatch):
    """Build the schema, put the audit-log rule in force and route the app's
    sessions through a private engine.

    The private NullPool engine keeps these tests off the shared, loop-guarded
    engine, whose cross-loop trips tests/conftest.py holds to a baseline.
    ``get_tenant_session`` and the tests resolve ``async_session_factory`` from
    ``core.database`` at call time, so they follow the patch. When the
    production trigger is absent, the fixture adds the same rule under its own
    names and drops exactly those afterwards, so the shared CI database is left
    as it was found.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import core.database as db
    import core.models  # noqa: F401 — registers every ORM model
    from core.models.base import BaseModel as ORMBase

    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"], poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(ORMBase.metadata.create_all)
        had_trigger = (
            await conn.execute(
                text("SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_immutable' AND NOT tgisinternal")
            )
        ).first() is not None
        if not had_trigger:
            # A run killed before teardown can leave the test trigger behind.
            await conn.execute(text(f"DROP TRIGGER IF EXISTS {_TEST_TRIGGER} ON audit_log"))
            await conn.execute(text(_REJECT_MUTATION_FN))
            await conn.execute(text(_IMMUTABLE_TRIGGER))
    monkeypatch.setattr(
        db, "async_session_factory", async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    )
    try:
        yield
    finally:
        if not had_trigger:
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP TRIGGER IF EXISTS {_TEST_TRIGGER} ON audit_log"))
                await conn.execute(text(f"DROP FUNCTION IF EXISTS {_TEST_FUNCTION}()"))
        await engine.dispose()


async def _seed_subject(subject: str) -> tuple[uuid.UUID, uuid.UUID]:
    from core.database import async_session_factory
    from core.models.audit import AuditLog
    from core.models.tenant import Tenant
    from core.models.user import User

    tid, other_tid = uuid.uuid4(), uuid.uuid4()
    async with async_session_factory() as session:
        for t in (tid, other_tid):
            session.add(Tenant(id=t, name=f"dsar-{t}", slug=f"dsar-{t}", settings={}))
        await session.flush()
        session.add(User(id=uuid.uuid4(), tenant_id=tid, email=subject, name="Subject", role="analyst"))
        session.add(User(id=uuid.uuid4(), tenant_id=other_tid, email=subject, name="Other", role="analyst"))
        for t, count in ((tid, 2), (other_tid, 1)):
            for n in range(count):
                session.add(
                    AuditLog(
                        tenant_id=t,
                        event_type="x",
                        actor_type="user",
                        actor_id=subject,
                        action=f"did-{n}",
                        outcome="success",
                        details={},
                    )
                )
        await session.commit()
    return tid, other_tid


async def test_the_fixture_trigger_rejects_audit_updates(immutable_audit_log):
    """The proof below only means something if the trigger is really active."""
    from sqlalchemy.exc import DBAPIError

    from core.database import async_session_factory

    subject = f"subject-{uuid.uuid4().hex[:8]}@example.com"
    tid, _ = await _seed_subject(subject)
    async with async_session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text("UPDATE audit_log SET actor_id = 'x' WHERE tenant_id = :tid"), {"tid": str(tid)})
        await session.rollback()


async def test_erase_completes_and_keeps_audit_rows_unchanged(immutable_audit_log):
    from sqlalchemy import select

    from audit.dsar import AUDIT_LOG_RETENTION_BASIS, DSARHandler
    from core.database import async_session_factory, get_tenant_session
    from core.models.audit import AuditLog
    from core.models.dsar import DSARRequestRecord
    from core.models.user import User

    subject = f"subject-{uuid.uuid4().hex[:8]}@example.com"
    tid, other_tid = await _seed_subject(subject)

    handler = DSARHandler()
    async with get_tenant_session(tid) as session:
        record = await handler.submit(
            session, tenant_id=tid, request_type="erase", subject_email=subject, requested_by="admin@example.com"
        )
        record = await handler.process(session, record)
        assert record.status == "completed", record.error
        assert record.result["users_anonymised"] == 1
        assert record.result["agent_feedback_pseudonymised"] == 0
        assert record.result["audit_log_retained"] == 2
        assert record.result["audit_log_retention_basis"] == AUDIT_LOG_RETENTION_BASIS
        assert "audit_log_pseudonymised" not in record.result
        record_id = record.id

    async with async_session_factory() as session:
        stored = (
            await session.execute(select(DSARRequestRecord).where(DSARRequestRecord.id == record_id))
        ).scalar_one()
        assert stored.status == "completed" and stored.completed_at is not None
        assert stored.result["audit_log_retained"] == 2

        erased = (await session.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        assert erased.email != subject and erased.email.startswith("erased:")
        assert erased.name is None and erased.password_hash is None
        assert erased.status == "inactive" and erased.sessions_invalid_before is not None

        kept = (await session.execute(select(AuditLog).where(AuditLog.tenant_id == tid))).scalars().all()
        assert sorted(a.action for a in kept) == ["did-0", "did-1"]
        assert {a.actor_id for a in kept} == {subject}

        untouched = (await session.execute(select(User).where(User.tenant_id == other_tid))).scalar_one()
        assert untouched.email == subject and untouched.name == "Other"


async def test_a_database_error_is_persisted_as_failed(immutable_audit_log, monkeypatch):
    from sqlalchemy import exc as sa_exc
    from sqlalchemy import select

    from audit.dsar import DSARHandler
    from core.database import async_session_factory, get_tenant_session
    from core.models.dsar import DSARRequestRecord

    async def failing_erase(self, session, *, tenant_id, subject_email):
        await session.execute(text("SELECT 1 / 0"))
        raise AssertionError("the division above must raise")

    monkeypatch.setattr(DSARHandler, "erase_subject", failing_erase)
    subject = f"subject-{uuid.uuid4().hex[:8]}@example.com"
    tid, _ = await _seed_subject(subject)

    handler = DSARHandler()
    async with get_tenant_session(tid) as session:
        record = await handler.submit(
            session, tenant_id=tid, request_type="erase", subject_email=subject, requested_by="admin@example.com"
        )
        record = await handler.process(session, record)
        assert record.status == "failed"
        # The recorded error is the database error, not the guard below it.
        assert issubclass(getattr(sa_exc, record.error), sa_exc.DBAPIError)
        record_id, record_error = record.id, record.error

    async with async_session_factory() as session:
        stored = (
            await session.execute(select(DSARRequestRecord).where(DSARRequestRecord.id == record_id))
        ).scalar_one()
        assert stored.status == "failed" and stored.error == record_error
        assert stored.completed_at is not None
