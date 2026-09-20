# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — ``grants.enforce_closed`` mode resolution against real Postgres.

Uses the production ``get_tenant_session`` and ``feature_flags`` table, so the
flag keys, tenant-over-global precedence, rollout and the strict lookup are
exercised end to end. Requires ``AGENTICORG_DB_URL``; skipped otherwise.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from auth import grant_enforcement as ge
from auth.grant_enforcement import EnforcementMode, resolve_enforcement_mode
from core import feature_flags
from core.models.feature_flag import FeatureFlag

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.real_flag_store]


@pytest.fixture
def flag_rows(db_engine: AsyncEngine):
    """Insert feature-flag rows with a per-test engine; removes them afterwards."""
    import core.models  # noqa: F401 - registers all ORM models
    from core.models.base import BaseModel as ORMBase

    engine = create_async_engine(db_engine.url.render_as_string(hide_password=False), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    created: list[uuid.UUID] = []
    schema_ready: list[bool] = []

    async def _add(tenant_id: uuid.UUID | None, flag_key: str, enabled: bool = True, rollout: int = 100) -> None:
        if not schema_ready:
            async with engine.begin() as conn:
                await conn.run_sync(ORMBase.metadata.create_all, tables=[FeatureFlag.__table__])
            schema_ready.append(True)
        row_id = uuid.uuid4()
        async with session_factory() as session, session.begin():
            session.add(
                FeatureFlag(
                    id=row_id, tenant_id=tenant_id, flag_key=flag_key, enabled=enabled, rollout_percentage=rollout
                )
            )
        created.append(row_id)

    async def _cleanup() -> None:
        if created:
            async with session_factory() as session, session.begin():
                await session.execute(delete(FeatureFlag).where(FeatureFlag.id.in_(created)))
        await engine.dispose()

    _add.cleanup = _cleanup  # type: ignore[attr-defined]
    feature_flags.clear_cache()
    ge.clear_mode_cache()
    yield _add
    feature_flags.clear_cache()
    ge.clear_mode_cache()


async def test_modes_resolve_from_the_feature_flag_table(flag_rows, monkeypatch):
    try:
        monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
        warn_tenant, deny_tenant, plain_tenant, rolled_back = (uuid.uuid4() for _ in range(4))

        await flag_rows(warn_tenant, ge.FLAG_WARN)
        await flag_rows(deny_tenant, ge.FLAG_WARN)
        await flag_rows(deny_tenant, ge.FLAG_DENY)
        await flag_rows(rolled_back, ge.FLAG_DENY, enabled=False)
        await flag_rows(rolled_back, ge.FLAG_WARN)

        assert await resolve_enforcement_mode(warn_tenant) is EnforcementMode.WARN
        assert await resolve_enforcement_mode(deny_tenant) is EnforcementMode.DENY
        assert await resolve_enforcement_mode(plain_tenant) is EnforcementMode.OFF
        assert await resolve_enforcement_mode(rolled_back) is EnforcementMode.WARN
    finally:
        await flag_rows.cleanup()


async def test_a_zero_percent_rollout_does_not_enable_deny(flag_rows, monkeypatch):
    try:
        monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
        tenant = uuid.uuid4()
        await flag_rows(tenant, ge.FLAG_DENY, rollout=0)
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.OFF
    finally:
        await flag_rows.cleanup()


async def test_tenant_flags_never_weaken_a_deny_deployment_default(flag_rows, monkeypatch):
    try:
        monkeypatch.setattr(ge.settings, "grants_enforce_closed", "deny")
        tenant = uuid.uuid4()
        await flag_rows(tenant, ge.FLAG_DENY, enabled=False)
        await flag_rows(tenant, ge.FLAG_WARN)
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.DENY
    finally:
        await flag_rows.cleanup()


async def test_unreachable_flag_store_keeps_the_last_known_mode(flag_rows, monkeypatch):
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    tenant = uuid.uuid4()
    await flag_rows(tenant, ge.FLAG_WARN)
    assert await resolve_enforcement_mode(tenant) is EnforcementMode.WARN

    # Point the flag store at a closed port: the strict read must fail, and the
    # tenant must not drop to the ``off`` deployment default.
    from contextlib import asynccontextmanager

    broken = create_async_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none")
    broken_sessions = async_sessionmaker(broken, class_=AsyncSession)

    @asynccontextmanager
    async def _broken_session(*_args, **_kwargs):
        async with broken_sessions() as session:
            await session.connection()
            yield session

    feature_flags.clear_cache()
    monkeypatch.setattr(feature_flags, "get_tenant_session", _broken_session)
    try:
        with pytest.raises(feature_flags.FeatureFlagLookupError):
            await feature_flags.load_flag_rows_strict(ge.FLAG_WARN, tenant_id=tenant)
        # The remembered warn applies, not the deny used when nothing is known.
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.WARN
    finally:
        await broken.dispose()
        monkeypatch.undo()
        await flag_rows.cleanup()
