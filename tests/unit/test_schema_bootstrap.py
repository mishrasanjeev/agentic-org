# SPDX-License-Identifier: Apache-2.0
"""Empty-database bootstrap decisions for `alembic upgrade` (core.schema_bootstrap).

The database side (a real `alembic upgrade head` on an empty Postgres and the
comparison with the ORM models) is in tests/integration/test_alembic_e2e.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from core import schema_bootstrap
from core.schema_bootstrap import (
    BASELINE_REVISION,
    REASON_TARGET_BEFORE_BASELINE,
    REASON_TARGET_UNRESOLVED,
    REASON_UNMANAGED_DATABASE,
    EmptyDatabaseBootstrapError,
    plan_empty_database_bootstrap,
    target_reaches_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script() -> ScriptDirectory:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg)


def test_baseline_revision_exists_and_precedes_head(script: ScriptDirectory) -> None:
    assert script.get_revision(BASELINE_REVISION) is not None
    assert target_reaches_baseline(script, "head") is True


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("head", True),
        ("heads", True),
        (("head",), True),
        (BASELINE_REVISION, True),
        ("v470_sso_invoices", False),
        ("v400_apex", False),
        (None, False),
        ("+1", None),
        ("-1", None),
        ("heads@branch", None),
        ("no_such_revision", None),
        ((1,), None),
    ],
)
def test_target_reaches_baseline(script: ScriptDirectory, destination: object, expected: bool | None) -> None:
    assert target_reaches_baseline(script, destination) is expected


def test_empty_database_upgrade_to_head_is_bootstrapped() -> None:
    assert plan_empty_database_bootstrap(
        command="upgrade", current_heads=(), table_names=[], reaches_baseline=True
    )


def test_version_table_alone_counts_as_empty() -> None:
    assert plan_empty_database_bootstrap(
        command="upgrade", current_heads=(), table_names=["alembic_version"], reaches_baseline=True
    )


@pytest.mark.parametrize("command", ["downgrade", "do_stamp", "display_version", None])
def test_commands_other_than_upgrade_never_bootstrap(command: str | None) -> None:
    assert not plan_empty_database_bootstrap(
        command=command, current_heads=(), table_names=[], reaches_baseline=True
    )


def test_managed_database_is_never_bootstrapped() -> None:
    assert not plan_empty_database_bootstrap(
        command="upgrade",
        current_heads=("v6z24_case_pseudonym_maps",),
        table_names=["tenants", "alembic_version"],
        reaches_baseline=True,
    )


def test_unmanaged_database_with_tables_is_refused_with_reason() -> None:
    with pytest.raises(EmptyDatabaseBootstrapError) as exc_info:
        plan_empty_database_bootstrap(
            command="upgrade",
            current_heads=(),
            table_names=["tenants", "connector_configs", "alembic_version"],
            reaches_baseline=True,
        )
    assert exc_info.value.reason == REASON_UNMANAGED_DATABASE
    assert "connector_configs, tenants" in str(exc_info.value)
    assert "scripts/alembic_migrate.py" in str(exc_info.value)


def test_target_before_baseline_is_refused_with_reason() -> None:
    with pytest.raises(EmptyDatabaseBootstrapError) as exc_info:
        plan_empty_database_bootstrap(
            command="upgrade", current_heads=(), table_names=[], reaches_baseline=False
        )
    assert exc_info.value.reason == REASON_TARGET_BEFORE_BASELINE


def test_unresolved_target_is_refused_with_reason() -> None:
    with pytest.raises(EmptyDatabaseBootstrapError) as exc_info:
        plan_empty_database_bootstrap(
            command="upgrade", current_heads=(), table_names=[], reaches_baseline=None
        )
    assert exc_info.value.reason == REASON_TARGET_UNRESOLVED


def test_create_orm_baseline_installs_extensions_then_creates_orm_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.models.base import BaseModel

    calls: list[str] = []
    connection = MagicMock()
    connection.execute.side_effect = lambda statement: calls.append(str(statement))
    monkeypatch.setattr(BaseModel.metadata, "create_all", lambda bind: calls.append(f"create_all:{bind is connection}"))

    schema_bootstrap.create_orm_baseline(connection)

    assert calls == [
        'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "create_all:True",
    ]


def test_upgrade_command_function_is_still_named_upgrade() -> None:
    """migrations/env.py recognises an upgrade by this name; fail if Alembic renames it."""
    import inspect

    from alembic import command

    source = inspect.getsource(command.upgrade)
    assert "def upgrade(rev, context):" in source
    assert "fn=upgrade" in source


def test_env_py_takes_the_advisory_lock_before_deciding_a_database_is_empty() -> None:
    source = (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    lock = source.index("pg_advisory_xact_lock")
    assert source.index("MIGRATION_ADVISORY_LOCK = 4815162342") < lock
    # The emptiness check and the bootstrap both happen after the lock.
    assert lock < source.index("existing_relations(connection)")
    assert lock < source.index("create_orm_baseline(connection)")
    # And the revision is read again once the lock is held.
    assert source.count("get_current_heads()") >= 2


def test_env_py_bootstraps_inside_the_upgrade_transaction() -> None:
    source = (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    bootstrap_call = source.index("_bootstrap_empty_database(connection)")
    assert source.index("with context.begin_transaction():", source.index("def run_migrations_online")) < bootstrap_call
    assert bootstrap_call < source.index("context.run_migrations()", bootstrap_call)
