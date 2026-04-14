#!/usr/bin/env python3
"""Idempotent Alembic migrate entrypoint used by the deploy pipeline.

Handles three environment states safely:

1. Fresh DB — no tables at all.
   Runs ``alembic upgrade head`` from scratch.

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

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from core.config import settings

BASELINE_REVISION = "v480_baseline"
# A table that exists after the full init_db() / v480 chain.
BASELINE_PROBE_TABLE = "connector_configs"
ALEMBIC_VERSION_TABLE = "alembic_version"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("alembic_migrate")


def _sync_url() -> str:
    return settings.db_url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _sync_url())
    return cfg


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
        command.upgrade(cfg, "head")
        logger.info("alembic upgrade head complete")
        return 0

    if BASELINE_PROBE_TABLE in tables:
        logger.info(
            "legacy schema detected (no alembic_version, %s present) — "
            "stamping %s then upgrading head",
            BASELINE_PROBE_TABLE,
            BASELINE_REVISION,
        )
        command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
        logger.info("stamp + upgrade complete")
        return 0

    logger.info("empty database — running full alembic upgrade head from base")
    command.upgrade(cfg, "head")
    logger.info("initial alembic upgrade complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
