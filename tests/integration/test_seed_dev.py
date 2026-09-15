# SPDX-License-Identifier: Apache-2.0
"""Development seed against real Postgres: contents, idempotency and conflicts."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from scripts import seed_dev

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
ENV = {"AGENTICORG_ENV": "test"}
TENANT_ID = seed_dev.seed_id("tenant")

pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")

_CLEANUP = (
    "DELETE FROM approval_steps WHERE policy_id IN (SELECT id FROM approval_policies WHERE tenant_id = :tid)",
    "DELETE FROM approval_policies WHERE tenant_id = :tid",
    "DELETE FROM sso_configs WHERE tenant_id = :tid",
    "DELETE FROM agents WHERE tenant_id = :tid",
    "DELETE FROM users WHERE tenant_id = :tid",
    "DELETE FROM tenants WHERE id = :tid OR slug = :slug",
)


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    queries = {
        "tenants": "SELECT count(*) FROM tenants WHERE slug = :slug",
        "users": "SELECT count(*) FROM users WHERE tenant_id = :tid",
        "agents": "SELECT count(*) FROM agents WHERE tenant_id = :tid",
        "sso": "SELECT count(*) FROM sso_configs WHERE tenant_id = :tid",
        "policies": "SELECT count(*) FROM approval_policies WHERE tenant_id = :tid",
        "steps": "SELECT count(*) FROM approval_steps s JOIN approval_policies p ON p.id = s.policy_id "
        "WHERE p.tenant_id = :tid",
    }
    async with engine.connect() as conn:
        params = {"tid": TENANT_ID, "slug": seed_dev.TENANT_SLUG}
        return {name: (await conn.execute(text(sql), params)).scalar_one() for name, sql in queries.items()}


@pytest.fixture()
async def engine(_setup_schema: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(DB_URL, poolclass=NullPool)

    async def cleanup() -> None:
        async with engine.begin() as conn:
            for statement in _CLEANUP:
                await conn.execute(text(statement), {"tid": TENANT_ID, "slug": seed_dev.TENANT_SLUG})

    await cleanup()
    try:
        yield engine
    finally:
        await cleanup()
        await engine.dispose()


async def test_seed_creates_tenant_users_agents_sso_and_four_eyes_policy(engine: AsyncEngine) -> None:
    summary = await seed_dev.seed(DB_URL, ENV)
    assert summary["tenant_id"] == str(TENANT_ID)
    assert await _counts(engine) == {"tenants": 1, "users": 2, "agents": 2, "sso": 1, "policies": 1, "steps": 2}

    async with engine.connect() as conn:
        users = (
            await conn.execute(
                text("SELECT email, role, status, password_hash FROM users WHERE tenant_id = :tid ORDER BY email"),
                {"tid": TENANT_ID},
            )
        ).all()
        assert [(u.email, u.role, u.status, u.password_hash) for u in users] == [
            ("approver.a@example.com", "domain_lead", "active", None),
            ("approver.b@example.com", "domain_lead", "active", None),
        ]
        tenant = {"tid": TENANT_ID}
        agent_sql = text("SELECT status, authorized_tools FROM agents WHERE tenant_id = :tid")
        agents = (await conn.execute(agent_sql, tenant)).all()
        assert all(a.status == "shadow" and a.authorized_tools == [] for a in agents)
        sso = (await conn.execute(text("SELECT enabled, config FROM sso_configs WHERE tenant_id = :tid"), tenant)).one()
        assert sso.enabled is False
        assert sso.config["client_id"] == "agenticorg-dev-public"
        assert "client_secret" not in sso.config and "client_secret_enc" not in sso.config
        steps = (
            await conn.execute(
                text(
                    "SELECT s.sequence, s.approver_role FROM approval_steps s JOIN approval_policies p "
                    "ON p.id = s.policy_id WHERE p.tenant_id = :tid ORDER BY s.sequence"
                ),
                {"tid": TENANT_ID},
            )
        ).all()
        assert [(s.sequence, s.approver_role) for s in steps] == [(1, "domain_lead"), (2, "domain_lead")]
        metadata = (
            await conn.execute(
                text(
                    "SELECT s.step_metadata FROM approval_steps s JOIN approval_policies p "
                    "ON p.id = s.policy_id WHERE p.tenant_id = :tid"
                ),
                {"tid": TENANT_ID},
            )
        ).scalars().all()
        assert metadata == [{}, {}], "the seed must not claim a distinct-approver rule nothing enforces"


async def test_seed_is_idempotent_and_restores_seeded_fields(engine: AsyncEngine) -> None:
    await seed_dev.seed(DB_URL, ENV)
    before = await _counts(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET role = 'admin' WHERE id = :uid"), {"uid": seed_dev.seed_id("user:dev-approver-b")}
        )
        await conn.execute(text("UPDATE agents SET status = 'active' WHERE tenant_id = :tid"), {"tid": TENANT_ID})

    await seed_dev.seed(DB_URL, ENV)

    assert await _counts(engine) == before
    async with engine.connect() as conn:
        user_b = {"uid": seed_dev.seed_id("user:dev-approver-b")}
        role = (await conn.execute(text("SELECT role FROM users WHERE id = :uid"), user_b)).scalar_one()
        statuses = (
            await conn.execute(text("SELECT DISTINCT status FROM agents WHERE tenant_id = :tid"), {"tid": TENANT_ID})
        ).scalars().all()
    assert role == "domain_lead"
    assert statuses == ["shadow"]


async def test_optional_password_is_stored_hashed(engine: AsyncEngine) -> None:
    await seed_dev.seed(DB_URL, {**ENV, "AGENTICORG_SEED_PASSWORD": "a-local-only-passphrase"})
    async with engine.connect() as conn:
        hashes = (
            await conn.execute(text("SELECT password_hash FROM users WHERE tenant_id = :tid"), {"tid": TENANT_ID})
        ).scalars().all()
    assert len(hashes) == 2
    assert all(h and h.startswith("$2") and "a-local-only-passphrase" not in h for h in hashes)


async def test_conflicting_existing_tenant_fails_without_writing(engine: AsyncEngine) -> None:
    squatter = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                "VALUES (:id, 'Someone else', :slug, 'free', 'EU', '{}'::jsonb)"
            ),
            {"id": squatter, "slug": seed_dev.TENANT_SLUG},
        )

    with pytest.raises(seed_dev.SeedError, match="did not create"):
        await seed_dev.seed(DB_URL, ENV)

    counts = await _counts(engine)
    assert counts["users"] == counts["agents"] == counts["sso"] == counts["policies"] == 0
    async with engine.connect() as conn:
        name = (await conn.execute(text("SELECT name FROM tenants WHERE id = :id"), {"id": squatter})).scalar_one()
    assert name == "Someone else"


async def test_seed_removes_the_retired_policy_it_created_earlier(engine: AsyncEngine) -> None:
    await seed_dev.seed(DB_URL, ENV)
    retired_id = seed_dev.seed_id(seed_dev.RETIRED_POLICY_KEY)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO approval_policies (id, tenant_id, name, description, is_active, created_at, updated_at) "
                "VALUES (:id, :tid, 'four-eyes-dev', 'retired', TRUE, now(), now())"
            ),
            {"id": retired_id, "tid": TENANT_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO approval_steps (id, policy_id, sequence, approver_role, quorum_required, quorum_total, "
                "mode, step_metadata) VALUES (:id, :pid, 1, 'underwriter', 1, 1, 'sequential', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "pid": retired_id},
        )

    await seed_dev.seed(DB_URL, ENV)

    counts = await _counts(engine)
    assert (counts["policies"], counts["steps"]) == (1, 2)
