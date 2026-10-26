# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Physical PostgreSQL checks, opt-in only against a dedicated local test DB."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url

from core.models.user import User

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/v6_z102_mfa_challenge_generation.py"
DB_ENV = "MFA_MIGRATION_TEST_DB_URL"


def _safe_test_url(value):
    url = make_url(value)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg2", "postgresql+psycopg"}
        or url.host not in {"localhost", "127.0.0.1", "::1", "postgres", "mfa-test-postgres"}
        or url.database != "mfa_generation_test"
        or url.username != "mfa_test"
        or url.port not in {None, 5432}
        or url.query
    ):
        raise ValueError("Refusing a non-local or non-dedicated migration test database")
    return url


def _migration():
    spec = importlib.util.spec_from_file_location("mfa_generation_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _migration().upgrade()


@pytest.fixture
def pg():
    value = os.environ.get(DB_ENV)
    if not value:
        if os.environ.get("MFA_MIGRATION_TEST_REQUIRED") == "1":
            pytest.fail(f"Required dedicated {DB_ENV} is missing")
        pytest.skip(f"Set {DB_ENV} to a dedicated local PostgreSQL test database")
    # Validate before opening any connection; never fall back to application URLs.
    url = _safe_test_url(value)
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            schema = "mfa_migration_" + uuid.uuid4().hex
            connection.execute(sa.schema.CreateSchema(schema))
            connection.execute(sa.text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            try:
                yield connection
            finally:
                # Roll back both fixture DDL and migration DDL, even after a refusal.
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://mfa_test@db.example/mfa_generation_test",
        "postgresql://mfa_test@localhost/mfa_security",
        "postgresql://mfa_test@localhost/postgres",
        "postgresql://other@localhost/mfa_generation_test",
        "postgresql://mfa_test@localhost:6543/mfa_generation_test",
        "postgresql://mfa_test@localhost/mfa_generation_test?host=db.example",
        "postgresql://mfa_test@localhost/mfa_generation_test?service=production",
        "postgresql+asyncpg://mfa_test@localhost/mfa_generation_test",
    ],
)
def test_database_gate_refuses_unsafe_targets(value):
    with pytest.raises(ValueError):
        _safe_test_url(value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "postgres", "mfa-test-postgres"])
def test_database_gate_accepts_dedicated_local_targets(host):
    assert _safe_test_url(f"postgresql://mfa_test@{host}/mfa_generation_test").database == "mfa_generation_test"


def test_model_contract():
    column = User.__table__.c.mfa_challenge_generation
    assert isinstance(column.type, sa.BigInteger)
    assert column.nullable is False
    assert column.default.arg == 0
    assert str(column.server_default.arg) == "0"


def test_revision_and_forward_only_downgrade(monkeypatch):
    module = _migration()
    assert module.revision == "v6z102_mfa_challenge_generation"
    assert len(module.revision) <= 32
    assert module.down_revision == "v6z99_onboarding_cases"
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()
    assert statements == []


def _shape(connection):
    return next(
        column for column in sa.inspect(connection).get_columns("users")
        if column["name"] == "mfa_challenge_generation"
    )


def _assert_shape(connection):
    column = _shape(connection)
    assert isinstance(column["type"], sa.BigInteger)
    assert column["nullable"] is False
    assert column["default"] in {"0", "0::bigint", "'0'::bigint"}


def test_model_schema_and_insert_defaults(pg):
    pg.execute(sa.text("CREATE TABLE tenants (id UUID PRIMARY KEY)"))
    pg.execute(sa.text("CREATE TABLE departments (id UUID PRIMARY KEY)"))
    User.__table__.create(pg)
    _assert_shape(pg)
    tenant_id = uuid.uuid4()
    pg.execute(sa.text("INSERT INTO tenants VALUES (:id)"), {"id": tenant_id})
    pg.execute(User.__table__.insert().values(
        tenant_id=tenant_id, email="member@example.test", role="analyst",
    ))
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users")).scalar_one() == 0


def test_upgrade_adds_column_to_populated_table_and_is_idempotent(pg):
    pg.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    pg.execute(sa.text("INSERT INTO users VALUES (1), (2)"))
    _upgrade(pg)
    _assert_shape(pg)
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users ORDER BY id")).scalars().all() == [0, 0]
    pg.execute(sa.text("UPDATE users SET mfa_challenge_generation = 4294967297 WHERE id = 2"))
    _upgrade(pg)
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users ORDER BY id")).scalars().all() == [
        0, 4294967297,
    ]
    pg.execute(sa.text("INSERT INTO users (id) VALUES (3)"))
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users WHERE id = 3")).scalar_one() == 0
    with pytest.raises(sa.exc.IntegrityError), pg.begin_nested():
        pg.execute(sa.text("INSERT INTO users VALUES (4, NULL)"))


def test_upgrade_backfills_nulls_preserves_epochs_and_repairs_default(pg):
    pg.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, mfa_challenge_generation BIGINT DEFAULT 17)"))
    pg.execute(sa.text("INSERT INTO users VALUES (1, NULL), (2, 9), (3, 9223372036854775807)"))
    _upgrade(pg)
    _upgrade(pg)
    _assert_shape(pg)
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users ORDER BY id")).scalars().all() == [
        0, 9, 9223372036854775807,
    ]


def test_upgrade_empty_table(pg):
    pg.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    _upgrade(pg)
    _assert_shape(pg)
    assert pg.execute(sa.text("SELECT count(*) FROM users")).scalar_one() == 0


def test_negative_epoch_refused_without_mutating_rows_or_shape(pg):
    pg.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, mfa_challenge_generation BIGINT DEFAULT 17)"))
    pg.execute(sa.text("INSERT INTO users VALUES (1, NULL), (2, -1), (3, 9)"))
    with pytest.raises(sa.exc.DBAPIError, match="negative"), pg.begin_nested():
        _upgrade(pg)
    assert _shape(pg)["nullable"] is True
    assert _shape(pg)["default"] == "17"
    assert pg.execute(sa.text("SELECT mfa_challenge_generation FROM users ORDER BY id")).scalars().all() == [
        None, -1, 9,
    ]


@pytest.mark.parametrize("definition", [
    "INTEGER", "TEXT", "NUMERIC(20, 0)",
    "BIGINT GENERATED ALWAYS AS (id::bigint) STORED",
    "BIGINT GENERATED ALWAYS AS IDENTITY",
])
def test_wrong_column_shape_refused_without_altering_it(pg, definition):
    pg.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, mfa_challenge_generation " + definition + ")"))
    before = _shape(pg)
    with pytest.raises(sa.exc.DBAPIError, match="ordinary BIGINT"), pg.begin_nested():
        _upgrade(pg)
    after = _shape(pg)
    assert str(after["type"]) == str(before["type"])
    assert after["nullable"] == before["nullable"]
    assert after["default"] == before["default"]


def test_missing_users_table_refused(pg):
    with pytest.raises(sa.exc.DBAPIError, match="users table"), pg.begin_nested():
        _upgrade(pg)
    assert not sa.inspect(pg).has_table("users")


def test_users_view_refused(pg):
    pg.execute(sa.text("CREATE VIEW users AS SELECT 1 AS id"))
    with pytest.raises(sa.exc.DBAPIError, match="users table"), pg.begin_nested():
        _upgrade(pg)
    assert sa.inspect(pg).get_view_names() == ["users"]
