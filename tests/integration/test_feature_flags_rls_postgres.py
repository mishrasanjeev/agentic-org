# SPDX-License-Identifier: Apache-2.0
"""Global feature-flag rows for a database role that row-level security binds.

``feature_flags`` is FORCE ROW LEVEL SECURITY. A global row has ``tenant_id``
NULL, so the tenant policy alone never shows it; the other Postgres tests of
global rows connect as the container's superuser, which bypasses row-level
security, and pass either way. This module migrates a database of its own to
head with the real Alembic chain, then reads and writes it as a
``NOSUPERUSER NOBYPASSRLS`` role holding the table privileges the application
role has, through the production ``get_tenant_session``, ``core.feature_flags``
and ``scripts/authority_flags.py``.

Requires ``AGENTICORG_DB_URL`` (a role that may create databases and roles);
skipped otherwise.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core import feature_flags
from core.models.feature_flag import FeatureFlag

_ROOT = Path(__file__).resolve().parents[2]
_DB_URL = os.getenv("AGENTICORG_DB_URL", "")
_NIL_TENANT = uuid.UUID(int=0)
_DENY_FLAG = "approvals.unevaluable_condition.deny"


def _pg_available() -> bool:
    if not _DB_URL:
        return False
    try:
        engine = create_engine(_sync(_DB_URL), connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg", "postgresql")


pytestmark = [
    pytest.mark.skipif(not _pg_available(), reason="Postgres is not reachable (AGENTICORG_DB_URL)"),
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.real_flag_store,
]


class _Db:
    """A database migrated to head, and a role bound by its row-level security."""

    def __init__(self, url: str, role: str) -> None:
        self.url = url
        self.role = role
        self.owner = create_engine(_sync(url), poolclass=NullPool)

    def add(self, tenant_id: uuid.UUID | None, flag_key: str, *, enabled: bool = True) -> None:
        """Seed a row as the owner (a privileged role), the way an operator writes global rows."""
        with self.owner.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO feature_flags (id, tenant_id, flag_key, enabled, rollout_percentage) "
                    "VALUES (:id, :tenant_id, :flag_key, :enabled, 100)"
                ),
                {"id": uuid.uuid4(), "tenant_id": tenant_id, "flag_key": flag_key, "enabled": enabled},
            )

    def rows(self, flag_key: str) -> list[tuple[Any, ...]]:
        with self.owner.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    text(
                        "SELECT tenant_id, enabled, rollout_percentage FROM feature_flags "
                        "WHERE flag_key = :flag_key ORDER BY tenant_id NULLS FIRST"
                    ),
                    {"flag_key": flag_key},
                )
            ]

    def reset(self) -> None:
        with self.owner.begin() as conn:
            conn.execute(text("DELETE FROM feature_flags"))
            conn.execute(text("DELETE FROM audit_log WHERE event_type = 'feature_flag.authority_changed'"))


@pytest.fixture(scope="module")
def migrated_db() -> Iterator[_Db]:
    """A fresh database built by ``alembic upgrade head`` and an RLS-bound probe role."""
    suffix = uuid.uuid4().hex[:12]
    database = f"flag_rls_{suffix}"
    role = f"flag_rls_probe_{suffix}"
    admin = create_engine(_sync(_DB_URL), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    url = make_url(_DB_URL).set(database=database).render_as_string(hide_password=False)
    db: _Db | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
            conn.execute(text(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS"))
        env = os.environ.copy()
        env["AGENTICORG_DB_URL"] = url
        # A throwaway key for the migration subprocess, made per run so no
        # key-shaped literal sits in the repository.
        env.setdefault("AGENTICORG_SECRET_KEY", secrets.token_urlsafe(32))
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=600,
        )
        assert result.returncode == 0, result.stderr[-4000:]
        db = _Db(url, role)
        # What the application role holds: DML on every table and the sequences
        # behind them. It is not the owner, a superuser or BYPASSRLS, so every
        # FORCE ROW LEVEL SECURITY policy applies to it.
        with db.owner.begin() as conn:
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
            conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))
        yield db
    finally:
        if db is not None:
            db.owner.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.fixture
async def as_rls_role(migrated_db: _Db, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Db]:
    """Route the application's sessions to the migrated database as the probe role."""
    import core.database

    engine = create_async_engine(migrated_db.url, poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _assume_probe_role(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET ROLE {migrated_db.role}")
        cursor.close()

    monkeypatch.setattr(
        core.database,
        "async_session_factory",
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
    )
    migrated_db.reset()
    feature_flags.clear_cache()
    try:
        yield migrated_db
    finally:
        feature_flags.clear_cache()
        migrated_db.reset()
        await engine.dispose()


async def _visible(tenant_id: uuid.UUID, flag_key: str) -> set[uuid.UUID | None]:
    from core.database import get_tenant_session

    async with get_tenant_session(tenant_id) as session:
        rows = await session.execute(select(FeatureFlag.tenant_id).where(FeatureFlag.flag_key == flag_key))
        return set(rows.scalars())


async def test_the_probe_role_is_bound_by_row_level_security(as_rls_role: _Db) -> None:
    from core.database import get_tenant_session

    async with get_tenant_session(uuid.uuid4()) as session:
        row = (
            await session.execute(
                text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == (as_rls_role.role, False, False)


async def test_a_global_row_is_visible_in_every_tenant_session_and_under_the_nil_tenant(as_rls_role: _Db) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    as_rls_role.add(None, "workflow_builder")
    as_rls_role.add(tenant_b, "workflow_builder", enabled=False)

    assert await _visible(tenant_a, "workflow_builder") == {None}
    assert await _visible(_NIL_TENANT, "workflow_builder") == {None}
    # Another tenant's row stays invisible; the owner tenant sees its own and the global one.
    assert await _visible(tenant_b, "workflow_builder") == {None, tenant_b}

    assert await feature_flags._query_flag(None, "workflow_builder") == {"enabled": True, "rollout_percentage": 100}
    rows = await feature_flags.load_flag_rows_strict("workflow_builder", tenant_id=tenant_a)
    assert rows == feature_flags.FlagRows(global_row={"enabled": True, "rollout_percentage": 100}, tenant_row=None)


async def test_a_tenant_row_overrides_the_global_row_on_the_lenient_path(as_rls_role: _Db) -> None:
    tenant_with_row, tenant_without_row = uuid.uuid4(), uuid.uuid4()
    as_rls_role.add(None, "workflow_builder", enabled=True)
    as_rls_role.add(tenant_with_row, "workflow_builder", enabled=False)

    assert await feature_flags.is_enabled("workflow_builder", tenant_id=tenant_without_row) is True
    assert await feature_flags.is_enabled_strict("workflow_builder", tenant_id=tenant_without_row) is True
    assert await feature_flags.is_enabled("workflow_builder", tenant_id=tenant_with_row) is False
    assert await feature_flags.is_enabled_strict("workflow_builder", tenant_id=tenant_with_row) is False
    assert await feature_flags.is_enabled("workflow_builder") is True


async def test_the_role_cannot_insert_update_or_delete_a_global_row(as_rls_role: _Db) -> None:
    from core.database import get_tenant_session

    as_rls_role.add(None, "workflow_builder", enabled=False)
    for context in (uuid.uuid4(), _NIL_TENANT):
        with pytest.raises(DBAPIError, match="row-level security"):
            async with get_tenant_session(context) as session:
                await session.execute(
                    text(
                        "INSERT INTO feature_flags (id, tenant_id, flag_key, enabled, rollout_percentage) "
                        "VALUES (:id, NULL, 'caps.enforce', true, 100)"
                    ),
                    {"id": uuid.uuid4()},
                )
        async with get_tenant_session(context) as session:
            updated = await session.execute(
                text(
                    "UPDATE feature_flags SET enabled = true WHERE tenant_id IS NULL AND flag_key = 'workflow_builder'"
                )
            )
            deleted = await session.execute(
                text("DELETE FROM feature_flags WHERE tenant_id IS NULL AND flag_key = 'workflow_builder'")
            )
        # Postgres skips rows no UPDATE/DELETE policy admits instead of raising.
        assert (updated.rowcount, deleted.rowcount) == (0, 0)
    assert as_rls_role.rows("workflow_builder") == [(None, False, 100)]
    assert as_rls_role.rows("caps.enforce") == []


async def test_the_unevaluable_condition_global_row_takes_effect_for_the_role(as_rls_role: _Db) -> None:
    from core.approvals.policy_engine import unevaluable_condition_mode

    tenant, tenant_with_disabled_row = uuid.uuid4(), uuid.uuid4()
    assert await unevaluable_condition_mode(tenant) == "off"

    feature_flags.clear_cache()
    as_rls_role.add(None, _DENY_FLAG, enabled=True)
    as_rls_role.add(tenant_with_disabled_row, _DENY_FLAG, enabled=False)
    assert await unevaluable_condition_mode(tenant) == "deny"
    # A tenant row can make a tenant stricter but never lift the operator's global row.
    assert await unevaluable_condition_mode(tenant_with_disabled_row) == "deny"


async def test_authority_flags_set_global_refuses_an_rls_bound_role_with_a_clear_message(
    as_rls_role: _Db, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import authority_flags

    parser = authority_flags.build_parser()
    for argv in (
        ["set", _DENY_FLAG, "--global", "--operator", "integration-test"],
        ["clear", _DENY_FLAG, "--global", "--operator", "integration-test"],
    ):
        assert await authority_flags.run(parser.parse_args(argv)) == 2
        err = capsys.readouterr().err
        assert "privileged database role" in err and "row-level security" in err
    assert as_rls_role.rows(_DENY_FLAG) == []

    # Reading global rows and writing tenant rows still work for the role.
    as_rls_role.add(None, _DENY_FLAG, enabled=True)
    assert await authority_flags.run(parser.parse_args(["list", "--global"])) == 0
    assert f"global {_DENY_FLAG} enabled=True rollout=100" in capsys.readouterr().out
    tenant = uuid.uuid4()
    argv = ["set", _DENY_FLAG, "--tenant", str(tenant), "--operator", "integration-test"]
    assert await authority_flags.run(parser.parse_args(argv)) == 0
    assert as_rls_role.rows(_DENY_FLAG) == [(None, True, 100), (tenant, True, 100)]
