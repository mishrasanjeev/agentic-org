# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL coverage for the encrypted per-case pseudonym map store (PRD F-5).

Runs the ``v6z24_case_pseudonym_maps`` migration (idempotent) against the
database in ``AGENTICORG_DB_URL`` and exercises ``DatabasePseudonymMapStore``
through ``core.database``: encryption at rest, persistence across sessions
(a restart), concurrent writers on one case, row-level security between
tenants, and failing closed on an unreadable map.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.pii.pseudonymiser import DatabasePseudonymMapStore, PseudonymisationError, PseudonymSession, open_session
from tests import pseudonymisation_case as case

_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
_SYNC_URL = _DB_URL.replace("postgresql+asyncpg", "postgresql")
_MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "v6_z24_case_pseudonym_maps.py"
_PROBE_ROLE = "pseudonym_rls_probe"

pytestmark = pytest.mark.skipif(not _DB_URL, reason="integration tests require AGENTICORG_DB_URL")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    import core.models  # noqa: F401 - registers every ORM model
    from core.models.base import BaseModel

    sync_engine = create_engine(_SYNC_URL)
    BaseModel.metadata.create_all(sync_engine)
    spec = importlib.util.spec_from_file_location("v6z24_case_pseudonym_maps", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with sync_engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
    yield sync_engine
    sync_engine.dispose()


@pytest.fixture(autouse=True)
def fresh_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never reuse an asyncpg connection across the per-test event loops."""
    import core.database as db_mod

    test_engine = create_async_engine(_DB_URL, poolclass=NullPool)
    monkeypatch.setattr(db_mod, "async_session_factory", async_sessionmaker(test_engine, expire_on_commit=False))


@pytest.fixture
def tenants(engine: Engine) -> tuple[str, str]:
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for tenant_id in (tenant_a, tenant_b):
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings, byok_kek_resource) "
                    "VALUES (:id, :name, :slug, 'enterprise', 'IN', '{}'::jsonb, '')"
                ),
                {"id": tenant_id, "name": f"tenant-{tenant_id}", "slug": f"tenant-{tenant_id}"},
            )
    return tenant_a, tenant_b


async def test_map_is_encrypted_at_rest_and_a_new_process_gets_the_same_tokens(
    engine: Engine, tenants: tuple[str, str]
) -> None:
    tenant_a, _ = tenants
    first = await open_session(tenant_a, case.CASE_ID, store=DatabasePseudonymMapStore())
    masked = await first.pseudonymise_value(case.task_input())

    with engine.connect() as conn:
        stored, entry_count = conn.execute(
            text("SELECT mapping_encrypted::text, entry_count FROM case_pseudonym_maps WHERE tenant_id = :t"),
            {"t": tenant_a},
        ).one()
    assert '"_encrypted"' in stored
    assert not [raw for raw in case.RAW_VALUES if raw in stored]
    assert entry_count == len(first._map or ())

    restarted = await open_session(tenant_a, case.CASE_ID, store=DatabasePseudonymMapStore(), require_existing=True)
    assert await restarted.pseudonymise_value(case.task_input()) == masked
    assert restarted.restore_value(masked) == case.task_input()


async def test_concurrent_writers_on_one_case_never_give_a_token_two_values(tenants: tuple[str, str]) -> None:
    tenant_a, _ = tenants
    values = [f"Placeholder Person {index:02d}" for index in range(12)]
    sessions = [PseudonymSession(tenant_a, "case-f5-concurrent", DatabasePseudonymMapStore()) for _ in values]

    await asyncio.gather(*(s.register_structured({"full_name": v}) for s, v in zip(sessions, values, strict=True)))

    final = await open_session(tenant_a, "case-f5-concurrent", store=DatabasePseudonymMapStore())
    assert final._map is not None
    assert sorted(final._map.by_value) == sorted(values)
    assert len(set(final._map.by_token)) == len(values)
    for session, value in zip(sessions, values, strict=True):
        token = session._map.by_value[value]  # type: ignore[union-attr]
        assert final._map.by_token[token] == value


async def test_row_level_security_hides_and_blocks_another_tenants_map(
    engine: Engine, tenants: tuple[str, str]
) -> None:
    tenant_a, tenant_b = tenants
    session = await open_session(tenant_a, "case-f5-rls", store=DatabasePseudonymMapStore())
    await session.register_structured({"full_name": case.APPLICANT_NAME})
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {_PROBE_ROLE}"))
        conn.execute(text(f"CREATE ROLE {_PROBE_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_PROBE_ROLE}"))
        conn.execute(text(f"GRANT SELECT, INSERT ON case_pseudonym_maps TO {_PROBE_ROLE}"))

    def visible(tenant_id: str | None) -> int:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {_PROBE_ROLE}"))
            if tenant_id is not None:
                conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": tenant_id})
            return conn.execute(
                text("SELECT count(*) FROM case_pseudonym_maps WHERE case_id = 'case-f5-rls'")
            ).scalar_one()

    try:
        assert visible(tenant_a) == 1
        assert visible(tenant_b) == 0
        assert visible(None) == 0
        with engine.connect() as conn, conn.begin() as transaction:
            conn.execute(text(f"SET LOCAL ROLE {_PROBE_ROLE}"))
            conn.execute(text("SELECT set_config('agenticorg.tenant_id', :t, true)"), {"t": tenant_b})
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO case_pseudonym_maps (id, tenant_id, case_id, mapping_encrypted, entry_count) "
                        "VALUES (:id, :t, 'case-f5-forged', '{}'::jsonb, 0)"
                    ),
                    {"id": str(uuid.uuid4()), "t": tenant_a},
                )
            transaction.rollback()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {_PROBE_ROLE}"))
            conn.execute(text(f"DROP ROLE {_PROBE_ROLE}"))


async def test_an_unreadable_stored_map_fails_closed(engine: Engine, tenants: tuple[str, str]) -> None:
    tenant_a, _ = tenants
    session = await open_session(tenant_a, "case-f5-damaged", store=DatabasePseudonymMapStore())
    await session.register_structured({"full_name": case.APPLICANT_NAME})
    with engine.begin() as conn:
        conn.execute(
            text(
                'UPDATE case_pseudonym_maps SET mapping_encrypted = \'{"_encrypted": "not-a-ciphertext"}\'::jsonb '
                "WHERE tenant_id = :t AND case_id = 'case-f5-damaged'"
            ),
            {"t": tenant_a},
        )

    with pytest.raises(PseudonymisationError) as excinfo:
        await open_session(tenant_a, "case-f5-damaged", store=DatabasePseudonymMapStore())
    assert excinfo.value.reason == "map_unreadable"
    with pytest.raises(PseudonymisationError):
        await session.pseudonymise_value({"full_name": case.DIRECTOR_NAME})


@pytest.mark.parametrize(("pool_size", "max_overflow", "same_case"), [(5, 5, False), (2, 0, False), (2, 0, True)])
async def test_concurrent_writers_do_not_exhaust_a_bounded_connection_pool(
    monkeypatch: pytest.MonkeyPatch,
    tenants: tuple[str, str],
    pool_size: int,
    max_overflow: int,
    same_case: bool,
) -> None:
    """Each writer holds one pooled connection at a time: the key is resolved before the row lock is taken."""
    import core.database as db_mod

    tenant_a, _ = tenants
    engine = create_async_engine(_DB_URL, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=10)
    monkeypatch.setattr(db_mod, "async_session_factory", async_sessionmaker(engine, expire_on_commit=False))
    writers = 10
    run = uuid.uuid4().hex[:8]
    sessions = [
        PseudonymSession(
            tenant_a, f"case-pool-{run}" if same_case else f"case-pool-{run}-{index}", DatabasePseudonymMapStore()
        )
        for index in range(writers)
    ]
    try:
        results = await asyncio.gather(
            *(s.register_structured({"full_name": f"Placeholder Person {i:02d}"}) for i, s in enumerate(sessions)),
            return_exceptions=True,
        )
    finally:
        await engine.dispose()
    assert [r for r in results if isinstance(r, BaseException)] == []
