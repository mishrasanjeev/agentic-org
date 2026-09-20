# SPDX-License-Identifier: Apache-2.0
"""The integration fixtures can run repeatedly against one database.

A test session mints fresh tenant and user ids, so the rows it seeds must not
collide with rows an earlier session left behind. `tenants.slug` is globally
unique, which is where a fixed value — or one truncated to the first few
characters of the id — bites.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parents[2]
_TRUNCATION = re.compile(r"slug[^\n]*\[:\s*\d+\s*\]|\[:\s*\d+\s*\][^\n]*slug")


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
    # Two ids differing only in their last character: a truncating slug would
    # give both the same value.
    first = "00000000-0000-0000-0000-000000000001"
    second = "00000000-0000-0000-0000-000000000002"
    assert tenant_slug(first) != tenant_slug(second)
    assert uuid.UUID(TEST_TENANT_ID).hex in tenant_slug(TEST_TENANT_ID)
    assert tenant_slug(first, "iso-a").startswith("iso-a-")


def test_tenant_slug_tolerates_an_identifier_that_is_not_a_uuid() -> None:
    assert tenant_slug("Tenant One!") == "test-tenant-tenant-one"
    assert tenant_slug("") == "test-tenant-unidentified"


def test_no_test_seeds_a_tenant_slug_from_a_truncated_identifier() -> None:
    """A slug built from `id[:8]` collides between ids sharing a prefix."""
    offenders = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == Path(__file__).relative_to(REPO_ROOT).as_posix():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _TRUNCATION.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert not offenders, "a tenant slug must come from the whole id:\n  " + "\n  ".join(offenders)


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


@pytest.mark.usefixtures("client")
async def test_client_fixture_seeds_a_run_specific_slug(seed_engine: AsyncEngine) -> None:
    async with seed_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT slug, name FROM tenants WHERE id = CAST(:id AS uuid)"),
                {"id": TEST_TENANT_ID},
            )
        ).one()
    assert row.slug == tenant_slug(TEST_TENANT_ID)
    # The name is derived from the id too, so a reused database stays readable.
    assert uuid.UUID(TEST_TENANT_ID).hex in row.name
