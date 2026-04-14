"""Alembic environment for AgenticOrg.

Schema authority: migration files under ``migrations/versions/``.
Runtime startup DDL in ``core.database.init_db()`` is legacy compat only,
and is skipped when ``AGENTICORG_DDL_MANAGED_BY_ALEMBIC=true``.

Cutover steps for an existing environment:

    # one-time baseline on an env that already ran init_db()
    alembic stamp v480_baseline

    # normal flow
    alembic upgrade head
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Register every ORM model so MetaData is complete for autogenerate.
import core.models  # noqa: F401
from core.config import settings
from core.models.base import BaseModel

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
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
