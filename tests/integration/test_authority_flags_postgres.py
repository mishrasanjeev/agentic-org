# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — authority flags against real Postgres.

Through the production ``get_tenant_session`` and ``feature_flags`` table:
a disabled tenant row cannot hide an operator's global deny, the operator
script sets and clears rows, and an unreachable store fails closed. Requires
``AGENTICORG_DB_URL``; skipped otherwise.
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
def pg(db_engine: AsyncEngine, monkeypatch):
    """Per-test engine on the integration database; removes the rows it created."""
    import core.models  # noqa: F401 - registers all ORM models
    from core.models.base import BaseModel as ORMBase

    url = db_engine.url.render_as_string(hide_password=False)
    engine = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    created: list[uuid.UUID] = []

    class _Pg:
        db_url = url

        async def ensure_schema(self) -> None:
            async with engine.begin() as conn:
                await conn.run_sync(ORMBase.metadata.create_all, tables=[FeatureFlag.__table__])

        async def add(self, tenant_id: uuid.UUID | None, flag_key: str, enabled: bool = True) -> None:
            await self.ensure_schema()
            row_id = uuid.uuid4()
            async with sessions() as session, session.begin():
                session.add(
                    FeatureFlag(
                        id=row_id, tenant_id=tenant_id, flag_key=flag_key, enabled=enabled, rollout_percentage=100
                    )
                )
            created.append(row_id)

        async def cleanup(self, *keys: str) -> None:
            async with sessions() as session, session.begin():
                if created:
                    await session.execute(delete(FeatureFlag).where(FeatureFlag.id.in_(created)))
                for key in keys:
                    await session.execute(delete(FeatureFlag).where(FeatureFlag.flag_key == key))
            await engine.dispose()

    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    feature_flags.clear_cache()
    ge.clear_mode_cache()
    yield _Pg()
    feature_flags.clear_cache()
    ge.clear_mode_cache()


async def test_global_deny_with_a_disabled_tenant_row_resolves_to_deny_on_postgres(pg):
    tenant = uuid.uuid4()
    try:
        await pg.add(None, ge.FLAG_DENY, enabled=True)
        await pg.add(tenant, ge.FLAG_DENY, enabled=False)
        await pg.add(tenant, ge.FLAG_WARN, enabled=False)
        # The lenient evaluator still lets the tenant row win (unchanged) ...
        assert await feature_flags.is_enabled(ge.FLAG_DENY, tenant_id=tenant) is False
        # ... but the authority path reads both rows and takes the strictest.
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.DENY
    finally:
        await pg.cleanup()


async def test_operator_script_sets_and_clears_an_authority_flag(pg):
    from scripts import authority_flags

    tenant = uuid.uuid4()
    await pg.ensure_schema()
    try:
        parser = authority_flags.build_parser()
        args = parser.parse_args(["set", ge.FLAG_WARN, "--tenant", str(tenant)])
        assert await authority_flags.run(args, pg.db_url) == 0
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.WARN

        feature_flags.clear_cache()
        args = parser.parse_args(["clear", ge.FLAG_WARN, "--tenant", str(tenant)])
        assert await authority_flags.run(args, pg.db_url) == 0
        assert await resolve_enforcement_mode(tenant) is EnforcementMode.OFF

        refused = parser.parse_args(["set", "new_workflow_builder", "--tenant", str(tenant)])
        assert await authority_flags.run(refused, pg.db_url) == 2
    finally:
        await pg.cleanup(ge.FLAG_WARN)


async def test_unreachable_flag_store_with_no_known_mode_resolves_to_deny(pg, monkeypatch):
    from contextlib import asynccontextmanager

    broken = create_async_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:9/none")
    broken_sessions = async_sessionmaker(broken, class_=AsyncSession)

    @asynccontextmanager
    async def _broken_session(*_args, **_kwargs):
        async with broken_sessions() as session:
            await session.connection()
            yield session

    monkeypatch.setattr(feature_flags, "get_tenant_session", _broken_session)
    try:
        assert await resolve_enforcement_mode(uuid.uuid4()) is EnforcementMode.DENY
    finally:
        await broken.dispose()
        await pg.cleanup()
