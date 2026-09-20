# SPDX-License-Identifier: Apache-2.0
"""The integration fixtures can run repeatedly against one database.

A test session mints fresh tenant and user ids, so the rows it seeds must not
collide with rows an earlier session left behind. `tenants.slug` is globally
unique, which is where a fixed value bites (FINDINGS A-41).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import (
    DB_URL,
    TEST_TENANT_ID,
    TEST_USER_ID,
    TEST_USER_SUB,
    seed_tenant_and_admin,
    tenant_slug,
)


@pytest_asyncio.fixture
async def seed_engine(_setup_schema: None):
    """A per-test engine: the session-scoped one belongs to another loop."""
    if not DB_URL:
        pytest.skip("integration tests require AGENTICORG_DB_URL")
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_tenant_slug_is_unique_per_tenant_id() -> None:
    first = "00000000-0000-0000-0000-000000000001"
    second = "00000000-0000-0000-0000-000000000002"
    assert tenant_slug(first) != tenant_slug(second)
    assert uuid.UUID(TEST_TENANT_ID).hex in tenant_slug(TEST_TENANT_ID)


async def test_seeding_twice_with_different_ids_succeeds(seed_engine: AsyncEngine) -> None:
    """A later session's seed must not collide with this one's."""
    later_tenant_id = str(uuid.uuid4())
    later_user_id = str(uuid.uuid4())
    async with seed_engine.begin() as conn:
        await seed_tenant_and_admin(conn, TEST_TENANT_ID, TEST_USER_ID, TEST_USER_SUB)
        await seed_tenant_and_admin(conn, later_tenant_id, later_user_id, TEST_USER_SUB)

    try:
        async with seed_engine.connect() as conn:
            slugs = (
                await conn.execute(
                    text("SELECT slug FROM tenants WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": [TEST_TENANT_ID, later_tenant_id]},
                )
            ).scalars()
            assert set(slugs) == {tenant_slug(TEST_TENANT_ID), tenant_slug(later_tenant_id)}
    finally:
        async with seed_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE tenant_id = CAST(:id AS uuid)"),
                {"id": later_tenant_id},
            )
            await conn.execute(
                text("DELETE FROM tenants WHERE id = CAST(:id AS uuid)"),
                {"id": later_tenant_id},
            )


async def test_seeding_the_same_session_twice_is_idempotent(seed_engine: AsyncEngine) -> None:
    """The fixture runs once per session, but a re-run must not raise."""
    async with seed_engine.begin() as conn:
        await seed_tenant_and_admin(conn, TEST_TENANT_ID, TEST_USER_ID, TEST_USER_SUB)
        await seed_tenant_and_admin(conn, TEST_TENANT_ID, TEST_USER_ID, TEST_USER_SUB)

    async with seed_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM tenants WHERE id = CAST(:id AS uuid)"),
            {"id": TEST_TENANT_ID},
        )
    assert count == 1


@pytest.mark.usefixtures("client")
async def test_client_fixture_seeds_a_run_specific_slug(seed_engine: AsyncEngine) -> None:
    async with seed_engine.connect() as conn:
        slug = await conn.scalar(
            text("SELECT slug FROM tenants WHERE id = CAST(:id AS uuid)"),
            {"id": TEST_TENANT_ID},
        )
    assert slug == tenant_slug(TEST_TENANT_ID)
