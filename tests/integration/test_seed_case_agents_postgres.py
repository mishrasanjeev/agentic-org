# SPDX-License-Identifier: Apache-2.0
"""The governed-case seed's case agents against real Postgres and the real grant path.

Replays the defect: after ``make seed`` the development tenant had no agent for either case role,
so the case authorizer refused every provider call (``grant_missing`` /
``case_agent_not_configured``) and every sample case ended ``failed``. After
``scripts.seed_governed_cases.prepare_case_agents`` the same authorizer - the real agent lookup,
purpose check, run-grant resolution, token pool and delegation from the root grant - allows exactly
the calls each role's registration covers. Grantex is a fake here that keeps its own records;
``make seed-cases`` runs this against the stack's Grantex service.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from scripts import seed_dev
from scripts import seed_governed_cases as seed
from scripts.seed_dev import SeedError

DB_URL = os.getenv("AGENTICORG_DB_URL", "")
TENANT_ID = seed_dev.seed_id("tenant")
PURPOSE = "aml.cdd.onboarding"

pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")

_CLEANUP = (
    "DELETE FROM approval_steps WHERE policy_id IN (SELECT id FROM approval_policies WHERE tenant_id = :tid)",
    "DELETE FROM approval_policies WHERE tenant_id = :tid",
    "DELETE FROM sso_configs WHERE tenant_id = :tid",
    "DELETE FROM agents WHERE tenant_id = :tid",
    "DELETE FROM users WHERE tenant_id = :tid",
    "DELETE FROM tenants WHERE id = :tid OR slug = :slug",
)


class _NotFoundError(Exception):
    status_code = 404


class _Grantex:
    """Grantex as the seed, the token pool and the enforcement check use it, keeping its own records."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}
        self.issued: dict[str, list[str]] = {}
        self.pending: dict[str, list[str]] = {}
        self.agents = SimpleNamespace(register=self._register, get=self._get, list=self._list)
        self.tokens = SimpleNamespace(exchange=self._exchange)
        self.grants = SimpleNamespace(delegate=self._delegate)

    def _register(self, *, name: str, scopes: list[str], description: str = "") -> Any:
        agent = SimpleNamespace(id=f"ag_{uuid.uuid4().hex[:12]}", name=name, scopes=list(scopes), did="")
        self.registered[agent.id] = agent
        return agent

    def _get(self, agent_id: str) -> Any:
        if agent_id not in self.registered:
            raise _NotFoundError(agent_id)
        return self.registered[agent_id]

    def _list(self) -> Any:
        return SimpleNamespace(agents=list(self.registered.values()))

    def authorize(self, params: Any) -> Any:
        assert set(params.scopes) <= set(self.registered[params.agent_id].scopes)
        code = f"code_{uuid.uuid4().hex[:8]}"
        self.pending[code] = list(params.scopes)
        return SimpleNamespace(code=code)

    def _exchange(self, params: Any) -> Any:
        token = f"root_{uuid.uuid4().hex[:8]}"
        self.issued[token] = self.pending.pop(params.code)
        return SimpleNamespace(grant_token=token)

    def _delegate(self, *, parent_grant_token: str, sub_agent_id: str, scopes: list[str], expires_in: str) -> dict:
        # Delegation narrows: the parent must hold every scope, the sub-agent must be registered for it.
        assert set(scopes) <= set(self.issued[parent_grant_token]), "the root grant does not cover the agent"
        assert set(scopes) <= set(self.registered[sub_agent_id].scopes)
        token = json.dumps({"agent": sub_agent_id, "scopes": scopes})
        self.issued[token] = list(scopes)
        expires = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
        return {"grantToken": token, "grantId": f"grnt_{uuid.uuid4().hex[:8]}", "expiresAt": expires}

    def enforce(self, *, grant_token: str, connector: str, tool: str, amount: float | None = None) -> Any:
        allowed = f"tool:{connector}:read:{tool}" in self.issued.get(grant_token, [])
        return SimpleNamespace(allowed=allowed, reason="" if allowed else "scope not granted", grant_id="")


@pytest.fixture(autouse=True)
def fresh_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenant sessions on connections of this test's own event loop."""
    import core.database as db_mod

    test_engine = create_async_engine(DB_URL, poolclass=NullPool)
    monkeypatch.setattr(db_mod, "async_session_factory", async_sessionmaker(test_engine, expire_on_commit=False))


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


@pytest.fixture()
def grantex(monkeypatch: pytest.MonkeyPatch) -> _Grantex:
    """One fake Grantex behind every client the grant path builds, and no Redis."""
    from auth.token_pool import token_pool
    from core.config import external_keys

    client = _Grantex()
    monkeypatch.setattr("core.langgraph.grantex_auth.get_grantex_client", lambda: client)
    monkeypatch.setattr(external_keys, "grantex_root_grant_token", "")
    monkeypatch.setattr(token_pool, "lazy_redis", False)
    monkeypatch.setattr(token_pool, "redis", None)
    token_pool._local_grants.clear()
    return client


def _register(client: _Grantex, calls: list[str]) -> Any:
    def register(*, name: str, agent_type: str, domain: str, authorized_tools: list[str]) -> dict[str, Any]:
        calls.append(agent_type)
        scopes = [f"tool:mock:read:{tool}" for tool in authorized_tools]
        agent = client.agents.register(name=f"{name} ({agent_type})", scopes=scopes)
        return {"grantex_agent_id": agent.id, "grantex_did": "", "grantex_scopes": scopes}

    return register


async def _authorize(role: str, tool: str) -> Any:
    from core.cases.grant_authorizer import case_authorizer

    authorizer = case_authorizer(str(TENANT_ID), "case_seed_agents", role, PURPOSE)
    return await authorizer.authorize(connector="mock", tool=tool)


async def test_the_seeded_case_agents_satisfy_the_real_case_authorizer(engine: AsyncEngine, grantex: _Grantex) -> None:
    from core.cases.grant_authorizer import _active_agent

    await seed_dev.seed(DB_URL, {"AGENTICORG_ENV": "test"})

    # The defect: `make seed` alone leaves no case agent, so every provider call is refused.
    before = await _authorize("business_underwriter", "resolve_business")
    assert (before.allowed, before.reason, before.sub_reason) == (False, "grant_missing", "case_agent_not_configured")

    calls: list[str] = []
    agents = await seed.prepare_case_agents(TENANT_ID, grantex=grantex, register=_register(grantex, calls))

    for role in seed.case_agent_roles():
        found = await _active_agent(str(TENANT_ID), role.agent_type)
        assert found is not None, f"no single active shared {role.agent_type} agent"
        agent_id, config = found
        assert agent_id == agents[role.agent_type]
        assert config["case_purposes"] == [PURPOSE]
        assert config["grantex_agent_id"] in grantex.registered

    # Each role may make exactly the calls its own registration covers.
    assert (await _authorize("business_underwriter", "resolve_business")).allowed
    assert (await _authorize("business_underwriter", "web_presence")).allowed
    assert (await _authorize("screening_disposition", "screen_person")).allowed
    refused = await _authorize("screening_disposition", "resolve_business")
    assert refused.allowed is False


async def test_running_the_seed_again_reuses_the_registrations_and_keeps_one_agent_per_role(
    engine: AsyncEngine, grantex: _Grantex
) -> None:
    await seed_dev.seed(DB_URL, {"AGENTICORG_ENV": "test"})
    calls: list[str] = []
    first = await seed.prepare_case_agents(TENANT_ID, grantex=grantex, register=_register(grantex, calls))
    second = await seed.prepare_case_agents(TENANT_ID, grantex=grantex, register=_register(grantex, calls))
    assert first == second
    assert calls == ["business_underwriter", "screening_disposition"]

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT agent_type, count(*) FROM agents WHERE tenant_id = :tid AND status = 'active' "
                    "AND agent_type IN ('business_underwriter', 'screening_disposition') GROUP BY agent_type"
                ),
                {"tid": TENANT_ID},
            )
        ).all()
    assert dict(rows) == {"business_underwriter": 1, "screening_disposition": 1}


async def test_an_agent_the_seed_did_not_create_stops_it_before_anything_is_registered(
    engine: AsyncEngine, grantex: _Grantex
) -> None:
    from core.models.agent import Agent

    await seed_dev.seed(DB_URL, {"AGENTICORG_ENV": "test"})
    # An operator's own active, shared underwriter: with the seed's as well the runtime would find two.
    async with AsyncSession(engine) as session, session.begin():
        session.add(
            Agent(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                name="Underwriter",
                employee_name="Underwriter",
                agent_type="business_underwriter",
                domain="backoffice",
                system_prompt_ref="inline://case-test",
                hitl_condition="always",
                authorized_tools=[],
                status="active",
                visibility="tenant",
                config={},
            )
        )

    calls: list[str] = []
    with pytest.raises(SeedError, match="did not create"):
        await seed.prepare_case_agents(TENANT_ID, grantex=grantex, register=_register(grantex, calls))
    assert calls == []
    async with engine.connect() as conn:
        seeded = (
            await conn.execute(
                text("SELECT count(*) FROM agents WHERE id = :id"),
                {"id": seed_dev.seed_id("agent:case:business_underwriter")},
            )
        ).scalar_one()
    assert seeded == 0
