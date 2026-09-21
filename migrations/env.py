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

An upgrade of an empty database first creates the ORM schema and stamps
``v480_baseline`` (``core.schema_bootstrap``), because the revisions before
the baseline alter a pre-Alembic schema. A database with tables but no
revision is refused; ``scripts/alembic_migrate.py`` stamps those.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

# Register every ORM model so MetaData is complete for autogenerate.
import core.models  # noqa: F401
from core.config import settings
from core.models.base import BaseModel
from core.schema_bootstrap import (
    BASELINE_REVISION,
    create_orm_baseline,
    existing_relations,
    plan_empty_database_bootstrap,
    target_reaches_baseline,
)

# Transaction-scoped lock shared with ``core.database.init_db``: two migrate
# jobs started together serialize on the bootstrap decision instead of both
# deciding the database is empty and racing to create the baseline. It guards
# that decision only — it is taken just when there is no recorded revision,
# and a revision that opens an ``autocommit_block()`` commits and releases it
# part-way through the chain (migrations/README.md).
MIGRATION_ADVISORY_LOCK = 4815162342

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseModel.metadata
logger = logging.getLogger("alembic.env")


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


def _bootstrap_empty_database(connection: Connection) -> None:
    """Build and stamp the baseline schema before upgrading an empty database."""
    migration_context = context.get_context()
    # env.py runs for every command (current, stamp, check, ...); only the
    # function `alembic.command.upgrade` hands to the context is named "upgrade".
    command_name = getattr(migration_context.opts.get("fn"), "__name__", None)
    current_heads = migration_context.get_current_heads()
    if command_name != "upgrade" or current_heads:
        return
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_ADVISORY_LOCK})
    # Another job may have created and stamped the baseline while this one
    # waited for the lock. This reread sees that commit because the connection
    # runs at READ COMMITTED, where each statement takes a fresh snapshot; at
    # REPEATABLE READ it would still see the empty database it started with.
    current_heads = migration_context.get_current_heads()
    if current_heads:
        return
    script = context.script
    if not plan_empty_database_bootstrap(
        command=command_name,
        current_heads=current_heads,
        table_names=existing_relations(connection),
        reaches_baseline=target_reaches_baseline(script, migration_context.opts.get("destination_rev")),
    ):
        return
    logger.info("empty database: creating the ORM baseline schema and stamping %s", BASELINE_REVISION)
    create_orm_baseline(connection)
    migration_context.stamp(script, BASELINE_REVISION)


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
