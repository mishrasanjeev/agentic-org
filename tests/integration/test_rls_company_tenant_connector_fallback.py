# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""BUG-01 reopened: a company agent can use a tenant-global connector, under real RLS.

Connector precedence for a company-bound agent (bug sheet 2026-09-21, BUG-01) is the exact
company binding, then the tenant-global binding, never another company's. ``connector_configs``
RLS (``v6z6``) shows a session only the rows of its exact company context, so the tenant-global
fallback must be read in a tenant-scoped session. Production refused a company agent's run with
``missing_connector_config`` because the fallback ran inside the company session.

Runs against PostgreSQL connected as the NOBYPASSRLS application role, like
``test_rls_nobypass_role.py``; skipped unless ``AGENTICORG_RLS_TEST_DB_URL`` is set.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

RLS_DB_URL = os.getenv("AGENTICORG_RLS_TEST_DB_URL")

pytestmark = pytest.mark.skipif(
    not RLS_DB_URL,
    reason="set AGENTICORG_RLS_TEST_DB_URL to a NOBYPASSRLS application-role DSN",
)

_PASSWORD = "Rls-Proof-Passw0rd!"  # noqa: S105 - test fixture credential


class _Env:
    engine: Any = None
    factory: Any = None
    tenant_id: uuid.UUID
    company_a: uuid.UUID
    company_b: uuid.UUID


@pytest_asyncio.fixture(scope="module")
async def env() -> AsyncGenerator[_Env, None]:
    import core.database as db_mod

    state = _Env()
    state.engine = create_async_engine(RLS_DB_URL, echo=False, poolclass=NullPool)
    async with state.engine.connect() as conn:
        posture = await db_mod.get_db_role_posture(conn)
    if not posture.rls_enforced:
        await state.engine.dispose()
        pytest.fail(f"role {posture.role!r} can bypass RLS; this proof requires a NOBYPASSRLS role")
    state.factory = async_sessionmaker(state.engine, class_=AsyncSession, expire_on_commit=False)
    original = (db_mod.engine, db_mod.async_session_factory)
    db_mod.engine, db_mod.async_session_factory = state.engine, state.factory
    try:
        yield state
    finally:
        db_mod.engine, db_mod.async_session_factory = original
        await state.engine.dispose()


@pytest.fixture(autouse=True)
def _app_role_sessions(env: _Env):
    from core.database import session_factory_scope

    with session_factory_scope(lambda: env.factory):
        yield


@pytest_asyncio.fixture(scope="module")
async def seeded(env: _Env) -> _Env:
    """One tenant with companies A and B, a tenant-global connector and a company-B-only connector."""
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        signup = await client.post(
            "/api/v1/auth/signup",
            json={
                "org_name": f"BUG-01 RLS {uuid.uuid4().hex[:6]}",
                "admin_email": f"bug01-{uuid.uuid4().hex[:8]}@example.com",
                "admin_name": "Admin BUG-01",
                "password": _PASSWORD,
            },
        )
        assert signup.status_code in (200, 201), signup.text
        body = signup.json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        env.tenant_id = uuid.UUID(body["user"]["tenant_id"])

        companies = []
        for label, pan in (("A", "AAACA1234A"), ("B", "AAACB1234B")):  # synthetic fixture PANs
            resp = await client.post(
                "/api/v1/companies", headers=headers, json={"name": f"Company {label}", "pan": pan}
            )
            assert resp.status_code in (200, 201), resp.text
            companies.append(uuid.UUID(resp.json()["id"]))
        env.company_a, env.company_b = companies

        for name, company in (("bug01_shared", None), ("bug01_b_only", env.company_b)):
            payload: dict[str, Any] = {
                "name": name,
                "category": "ops",
                "auth_type": "api_key",
                "auth_config": {"api_key": f"synthetic-{name}-key"},
            }
            if company is not None:
                payload["company_id"] = str(company)
            resp = await client.post("/api/v1/connectors", headers=headers, json=payload)
            assert resp.status_code in (200, 201), resp.text

    # Mark both configs healthy, each in its own exact scope (writes stay exact-scope under RLS).
    from core.database import get_tenant_session
    from core.models.connector_config import ConnectorConfig

    for name, company in (("bug01_shared", None), ("bug01_b_only", env.company_b)):
        async with get_tenant_session(env.tenant_id, company) as session:
            result = await session.execute(
                update(ConnectorConfig).where(ConnectorConfig.connector_name == name).values(health_status="healthy")
            )
            assert result.rowcount == 1, name
    return env


async def _readiness(env: _Env, connector: str, company: uuid.UUID | None) -> None:
    from api.v1.agents import _assert_connectors_ready_for_activation
    from core.database import get_tenant_session

    async with get_tenant_session(env.tenant_id, company) as session:
        await _assert_connectors_ready_for_activation(session, env.tenant_id, [connector], company)


@pytest.mark.asyncio
async def test_a_company_session_cannot_see_the_tenant_global_row(seeded: _Env) -> None:
    """The reason for the fix: v6z6 RLS hides tenant-global rows from a company session."""
    from core.database import get_tenant_session
    from core.models.connector_config import ConnectorConfig

    query = select(ConnectorConfig).where(
        ConnectorConfig.connector_name == "bug01_shared", ConnectorConfig.company_id.is_(None)
    )
    async with get_tenant_session(seeded.tenant_id, seeded.company_a) as session:
        assert (await session.execute(query)).scalar_one_or_none() is None
    async with get_tenant_session(seeded.tenant_id) as session:
        assert (await session.execute(query)).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_company_agent_dispatch_readiness_accepts_the_tenant_global_connector(seeded: _Env) -> None:
    await _readiness(seeded, "bug01_shared", seeded.company_a)


@pytest.mark.asyncio
async def test_company_agent_resolves_the_tenant_global_connector_credentials(seeded: _Env) -> None:
    from api.v1.agents import _resolve_connector_configs

    config, names = await _resolve_connector_configs(
        tenant_id=str(seeded.tenant_id), connector_ids=["bug01_shared"], company_id=str(seeded.company_a)
    )
    assert names == ["bug01_shared"]
    assert config["api_key"] == "synthetic-bug01_shared-key"


@pytest.mark.asyncio
async def test_company_agent_never_gets_another_companys_connector(seeded: _Env) -> None:
    from api.v1.agents import _resolve_connector_configs

    with pytest.raises(HTTPException) as refused:
        await _readiness(seeded, "bug01_b_only", seeded.company_a)
    assert "missing_connector_config" in str(refused.value.detail)
    config, names = await _resolve_connector_configs(
        tenant_id=str(seeded.tenant_id), connector_ids=["bug01_b_only"], company_id=str(seeded.company_a)
    )
    assert names == [] and "api_key" not in config


@pytest.mark.asyncio
async def test_tenant_level_agent_never_gets_a_company_connector(seeded: _Env) -> None:
    with pytest.raises(HTTPException) as refused:
        await _readiness(seeded, "bug01_b_only", None)
    assert "missing_connector_config" in str(refused.value.detail)


@pytest.mark.asyncio
async def test_the_owning_company_still_gets_its_own_connector(seeded: _Env) -> None:
    await _readiness(seeded, "bug01_b_only", seeded.company_b)


@pytest.mark.asyncio
async def test_role_is_not_a_superuser(seeded: _Env) -> None:
    async with seeded.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"))
        ).scalar() is False
