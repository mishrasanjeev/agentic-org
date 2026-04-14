"""End-to-end Alembic migration verification.

Runs against the CI Postgres service. Three scenarios:

1. fresh DB -> ``alembic upgrade head`` creates the full schema.
2. legacy-shaped DB (schema exists, ``alembic_version`` missing) ->
   ``scripts/alembic_migrate.py`` stamps v480_baseline then upgrades head.
3. already-managed DB -> the wrapper just runs ``upgrade head`` (no-op).

Skipped when Postgres is not reachable (e.g. local Windows machine
without Docker). The CI ``integration-tests`` job always has Postgres.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

_DB_URL = os.getenv(
    "AGENTICORG_DB_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
_SYNC_URL = _DB_URL.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _pg_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="Postgres is not reachable; skipping Alembic e2e test",
)


def _reset_schema() -> None:
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    engine.dispose()


def _run_migrate_wrapper() -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENTICORG_DB_URL"] = _DB_URL
    env.setdefault("AGENTICORG_SECRET_KEY", "ci-test-secret-key-minimum-16")
    return subprocess.run(  # noqa: S603
        [sys.executable, "scripts/alembic_migrate.py"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _table_names() -> set[str]:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
    engine.dispose()
    return names


def test_fresh_db_runs_full_chain():
    _reset_schema()
    result = _run_migrate_wrapper()
    assert "empty database" in result.stderr or "alembic upgrade" in result.stderr

    tables = _table_names()
    assert "alembic_version" in tables
    # Baseline tables added in v480
    assert "connector_configs" in tables
    assert "agent_task_results" in tables
    assert "kpi_cache" in tables


def test_idempotent_second_run():
    # First run already executed by prior test's state; run again and expect no-op.
    result = _run_migrate_wrapper()
    assert result.returncode == 0
    assert "alembic_version present" in result.stderr

    tables = _table_names()
    assert "alembic_version" in tables


def test_legacy_db_gets_stamped_then_upgraded():
    # Simulate a legacy environment: schema populated by init_db() but no
    # alembic_version table yet.
    _reset_schema()
    engine = create_engine(_SYNC_URL)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tenants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL
            );
        """))
        conn.execute(text("""
            CREATE TABLE connector_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                connector_name VARCHAR(100) NOT NULL
            );
        """))
    engine.dispose()

    result = _run_migrate_wrapper()
    assert "legacy schema detected" in result.stderr
    assert "stamp + upgrade complete" in result.stderr

    with create_engine(_SYNC_URL).connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == "v480_baseline"
