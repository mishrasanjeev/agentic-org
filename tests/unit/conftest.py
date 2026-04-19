"""tests/unit/conftest.py — PR-D3 schema bootstrap.

Several unit tests exercise FastAPI handlers that hit the ORM (agents,
companies, report schedules, ABM). Previously those tests were
`@pytest.mark.skipif(not AGENTICORG_DB_URL)` which meant they silently
no-op'd in the `unit-tests` CI job because that job didn't provision
Postgres — exactly the kind of zero-skip violation the Enterprise
Readiness plan's Phase 8 eliminates.

PR-D3 adds postgres + redis services to the unit-tests job and
removes those `skipif` decorators. This conftest provides the shared
session-scoped schema bootstrap so the handlers have tables to hit.

Mirrors the pattern in `tests/integration/conftest.py`: once per
session we import every ORM module so `BaseModel.metadata` is
populated, then `create_all`. That way a fresh postgres container
has the schema before any test runs.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _unit_db_schema() -> None:
    """Create ORM tables once per session if AGENTICORG_DB_URL is set.

    No-op locally when the dev doesn't have Postgres on the default
    URL — the tests that actually need DB will fail loudly with a
    connection error, which is the correct signal (install Postgres
    or run them in CI).
    """
    db_url = os.getenv("AGENTICORG_DB_URL", "")
    if not db_url:
        return

    # Convert asyncpg URL to sync for the one-shot create_all.
    sync_url = db_url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")

    from sqlalchemy import create_engine

    import core.models  # noqa: F401 — registers every ORM model
    from core.models.base import BaseModel

    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        BaseModel.metadata.create_all(engine)
    finally:
        engine.dispose()
