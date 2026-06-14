#!/usr/bin/env python3
"""Idempotent Alembic migrate entrypoint used by the deploy pipeline.

Handles three environment states safely:

1. Fresh DB — no tables at all.
   Creates the ORM baseline schema, stamps the Alembic cutover revision,
   then runs ``alembic upgrade head``. The Alembic chain starts after
   the original raw-SQL baseline, so an empty DB cannot safely run the
   first Alembic revision directly.

2. Legacy DB — schema was created by ``init_db()`` with no
   ``alembic_version`` table.
   Detects by checking for a known-baseline table
   (``connector_configs``) and then stamps the baseline revision
   (``BASELINE_REVISION``) before running ``upgrade head``.

3. Already managed — ``alembic_version`` exists.
   Just runs ``upgrade head``.

Design goals:
- Safe to run on every deploy.
- No operator action required for the Alembic cutover.
- Logs exactly which path was taken so the deploy log is auditable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402

BASELINE_REVISION = "v480_baseline"
# A table that exists after the full init_db() / v480 chain.
BASELINE_PROBE_TABLE = "connector_configs"
ALEMBIC_VERSION_TABLE = "alembic_version"
REQUIRED_RUNTIME_TABLES = frozenset(
    {
        # A2A task execution writes here synchronously before dispatch. A
        # stamped-but-missing table caused prod A2A commerce to return 500
        # on 2026-06-14; migration success must verify more than version_num.
        "a2a_tasks",
    }
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("alembic_migrate")


def _sync_url() -> str:
    return settings.db_url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _sync_url())
    return cfg


def _ensure_bootstrap_extensions(engine) -> None:
    """Install extensions required by the historical baseline schema."""
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))


def _create_orm_baseline(engine) -> None:
    """Create the legacy baseline shape before stamping the cutover revision."""
    import core.models  # noqa: F401, PLC0415 - register every ORM model
    from core.models.base import BaseModel  # noqa: PLC0415

    _ensure_bootstrap_extensions(engine)
    BaseModel.metadata.create_all(engine)


def _assert_required_runtime_tables(engine) -> None:
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
    missing = sorted(REQUIRED_RUNTIME_TABLES - tables)
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "Alembic reported success but required runtime tables are missing: "
            f"{joined}. Add or repair the forward migration before deploying."
        )


def _upgrade_head_and_verify(cfg: Config, engine, complete_message: str) -> None:
    command.upgrade(cfg, "head")
    _assert_required_runtime_tables(engine)
    logger.info(complete_message)


def main() -> int:
    url = _sync_url()
    logger.info("connecting to database for pre-rollout migration check")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())

    cfg = _alembic_cfg()

    if ALEMBIC_VERSION_TABLE in tables:
        logger.info("alembic_version present — running upgrade head")
        _upgrade_head_and_verify(cfg, engine, "alembic upgrade head complete")
        return 0

    if BASELINE_PROBE_TABLE in tables:
        logger.info(
            "legacy schema detected (no alembic_version, %s present) — "
            "stamping %s then upgrading head",
            BASELINE_PROBE_TABLE,
            BASELINE_REVISION,
        )
        command.stamp(cfg, BASELINE_REVISION)
        _upgrade_head_and_verify(cfg, engine, "stamp + upgrade complete")
        return 0

    if not tables:
        logger.info(
            "empty database — creating ORM baseline, stamping %s, then upgrading head",
            BASELINE_REVISION,
        )
        _create_orm_baseline(engine)
        command.stamp(cfg, BASELINE_REVISION)
        _upgrade_head_and_verify(cfg, engine, "empty database bootstrap + upgrade complete")
        return 0

    table_sample = ", ".join(sorted(tables)[:10])
    raise RuntimeError(
        "Database has existing tables but no alembic_version and no "
        f"{BASELINE_PROBE_TABLE!r} baseline probe table. Refusing to guess "
        f"migration state. Existing tables include: {table_sample}"
    )


if __name__ == "__main__":
    sys.exit(main())
