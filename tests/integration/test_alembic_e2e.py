"""End-to-end Alembic migration verification.

Runs against the CI Postgres service. Two scenarios exercise
``scripts/alembic_migrate.py`` against realistic DB shapes:

1. Legacy-shaped DB (full schema from ``ORMBase.metadata.create_all``,
   no ``alembic_version`` row) -> wrapper must stamp v480_baseline
   and return cleanly.

2. Already-managed DB (subsequent wrapper invocation) -> wrapper
   takes the ``upgrade head`` path, stays idempotent, and exits 0.

We intentionally do NOT exercise the "empty DB" path here. The
production baseline was historically bootstrapped by raw SQL files
(see ``migrations/0*_*.sql``) before the Alembic chain took over.
The Alembic versions chain assumes the base ``tenants`` / ``users``
tables already exist, so ``alembic upgrade head`` against a
truly-empty DB fails with a foreign-key reference error. That is
a legitimate constraint of the cutover path — every environment we
ship against already has at least the v4.0.0 schema.

Skipped when Postgres is not reachable (e.g. local Windows machine
without Docker). CI ``integration-tests`` always has Postgres.
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


def _build_legacy_schema() -> None:
    """Populate a realistic legacy schema: every ORM table, no
    ``alembic_version`` row. Matches the shape of a prod DB that
    was bootstrapped by ``init_db()`` before Alembic took over."""
    import core.models  # noqa: F401 — register every model
    from core.models.base import BaseModel

    engine = create_engine(_SYNC_URL)
    BaseModel.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _reset_after_module():
    """Leave the schema clean at module exit so downstream integration
    tests (which rely on ORMBase.metadata.create_all) do not see a
    partially-populated state left over from this module's manipulations."""
    yield
    _reset_schema()


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


def test_legacy_db_gets_stamped_at_baseline():
    """A legacy-shaped DB (no alembic_version) must be stamped at the
    baseline and then upgraded to the current head.

    The wrapper runs ``stamp v480_baseline`` followed by
    ``upgrade head``, so the resulting version is whatever the Alembic
    head is today, not necessarily the baseline. Read the expected
    head dynamically so this test does not regress every time a new
    migration file is added."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    expected_head = ScriptDirectory.from_config(cfg).get_current_head()
    assert expected_head, "Alembic has no head revision"

    _reset_schema()
    _build_legacy_schema()
    tables = _table_names()
    assert "connector_configs" in tables  # probe table for the wrapper
    assert "alembic_version" not in tables

    result = _run_migrate_wrapper()
    assert "legacy schema detected" in result.stderr

    with create_engine(_SYNC_URL).connect() as conn:
        version = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    assert version == expected_head


def test_already_managed_db_is_noop():
    """A DB that already has alembic_version must take the ``upgrade
    head`` branch, complete cleanly, and exit 0."""
    # Previous test has already stamped; exercise the wrapper again.
    result = _run_migrate_wrapper()
    assert result.returncode == 0
    assert "alembic_version present" in result.stderr
    assert "alembic_version" in _table_names()
