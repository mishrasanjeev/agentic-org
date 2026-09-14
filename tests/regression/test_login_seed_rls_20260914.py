"""Production incident 2026-09-14: admin login and new-org signup returned 500.

``agents`` / ``connectors`` / ``prompt_templates`` are FORCE ROW LEVEL
SECURITY (migration v6z16). Login, signup and Google signup seeded tenant
defaults on the credential session, which carries no
``agenticorg.tenant_id``:

* the login agent count always read 0 under RLS, so every admin login tried
  to seed;
* the connector INSERT violated ``connectors_tenant_isolation``;
* the flush rollback expired the session's User/Tenant objects, so the
  ``except`` handler's ``user.tenant_id`` raised PendingRollbackError → 500.

Replayed locally against a NOSUPERUSER NOBYPASSRLS role: admin login 500 on
both 783d07a0 and 784cbd03; after the fix admin login 200, signup 201 with
28 agents / 6 connectors / 40 templates seeded, zero-agent login re-seeds.
CI's Postgres user is a superuser (RLS bypassed), so these tests pin the
contract at source + helper level.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _src(fn) -> str:
    return inspect.getsource(fn)


class TestSeedingNeverRunsOnTheCredentialSession:
    @pytest.mark.parametrize("name", ["signup", "login", "google_login"])
    def test_route_seeds_through_isolated_tenant_helper(self, name: str) -> None:
        from api.v1 import auth

        src = _src(getattr(auth, name))
        assert "seed_tenant_defaults(session" not in src, name
        assert "_seed_tenant_defaults_isolated(" in src, name

    def test_login_no_longer_counts_agents_without_tenant_context(self) -> None:
        from api.v1 import auth

        src = _src(auth.login)
        assert "select(func.count()).select_from(Agent)" not in src

    def test_signup_seeds_after_commit(self) -> None:
        from api.v1 import auth

        src = _src(auth.signup)
        assert src.index("await session.commit()") < src.index("_seed_tenant_defaults_isolated(")

    def test_helper_uses_tenant_rls_session(self) -> None:
        from api.v1 import auth

        src = _src(auth._seed_tenant_defaults_isolated)
        assert "get_tenant_session(tenant_id)" in src
        assert "async_session_factory" not in src


def _tenant_session_factory(session):
    @asynccontextmanager
    async def factory(_tid, *_a, **_k):
        yield session

    return factory


class TestIsolatedSeedHelper:
    @pytest.mark.asyncio
    async def test_seed_failure_is_swallowed_and_logged(self) -> None:
        from api.v1 import auth

        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 0))
        boom = AsyncMock(side_effect=RuntimeError("new row violates row-level security policy"))
        with (
            patch.object(auth, "get_tenant_session", _tenant_session_factory(session)),
            patch.object(auth, "seed_tenant_defaults", boom),
            patch.object(auth.logger, "exception") as log_exc,
        ):
            await auth._seed_tenant_defaults_isolated(uuid.uuid4(), only_if_empty=True, trigger="login")
        boom.assert_awaited_once()
        log_exc.assert_called_once()

    @pytest.mark.asyncio
    async def test_only_if_empty_skips_seeded_tenant(self) -> None:
        from api.v1 import auth

        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 28))
        seeder = AsyncMock()
        with (
            patch.object(auth, "get_tenant_session", _tenant_session_factory(session)),
            patch.object(auth, "seed_tenant_defaults", seeder),
        ):
            await auth._seed_tenant_defaults_isolated(uuid.uuid4(), only_if_empty=True, trigger="login")
        seeder.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_signup_seeds_unconditionally(self) -> None:
        from api.v1 import auth

        session = MagicMock()
        session.execute = AsyncMock()
        seeder = AsyncMock()
        tid = uuid.uuid4()
        with (
            patch.object(auth, "get_tenant_session", _tenant_session_factory(session)),
            patch.object(auth, "seed_tenant_defaults", seeder),
        ):
            await auth._seed_tenant_defaults_isolated(tid, only_if_empty=False, trigger="signup")
        seeder.assert_awaited_once_with(session, tid)
        session.execute.assert_not_awaited()
