"""Alembic environment for AgenticOrg.

Schema authority: migration files under ``migrations/versions/``.
Strict runtime startup in ``core.database.init_db()`` verifies the
``alembic_version`` table and never issues DDL. Legacy startup repair is
local-only and requires ``AGENTICORG_ENABLE_LEGACY_STARTUP_DDL=1``.

Cutover steps for an existing environment:

    # one-time baseline on an env that already ran init_db()
    alembic stamp v480_baseline

    # normal flow
    alembic upgrade head
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, inspect, pool

# Register every ORM model so MetaData is complete for autogenerate.
import core.models  # noqa: F401
from core.config import settings
from core.models.base import BaseModel
from core.schema_bootstrap import (
    BASELINE_REVISION,
    create_orm_baseline,
    plan_empty_database_bootstrap,
    target_reaches_baseline,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseModel.metadata


def _resolve_sync_url() -> str:
    """Alembic needs a sync driver; strip +asyncpg if present."""
    url = settings.db_url
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _bootstrap_empty_database(conn) -> None:
    """Create and stamp the legacy-compatible baseline for a bare database."""
    migration_context = context.get_context()
    current_heads = migration_context.get_current_heads()
    table_names = inspect(conn).get_table_names()
    if current_heads or set(table_names) - {"alembic_version"}:
        # scripts/alembic_migrate.py creates and stamps the ORM baseline before
        # upgrading. Do not reinterpret that programmatic stamp as an upgrade.
        return
    should_bootstrap = plan_empty_database_bootstrap(
        command=context.get_revision_argument() and "upgrade",
        current_heads=current_heads,
        table_names=table_names,
        reaches_baseline=target_reaches_baseline(
            context.script,
            context.get_revision_argument() or "head",
        ),
    )
    if should_bootstrap:
        create_orm_baseline(conn)
        migration_context.stamp(context.script, BASELINE_REVISION)


def run_migrations_online() -> None:
    ini_section = config.get_section(config.config_ini_section) or {}
    ini_section["sqlalchemy.url"] = _resolve_sync_url()
    connectable = engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            _bootstrap_empty_database(connection)
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
