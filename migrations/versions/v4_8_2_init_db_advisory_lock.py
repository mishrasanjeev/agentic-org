"""No-op migration — documents init_db advisory lock for the CI migration guard.

Revision ID: v482_init_db_advisory_lock
Revises: v481_orm_sync
Create Date: 2026-04-16

This revision intentionally does not change any schema. It exists so the CI
``check_migration_required`` guard is satisfied when ``core/database.py`` is
edited purely for runtime behavior changes.

The accompanying code change in ``core/database.py`` serializes the startup
DDL block with ``SELECT pg_advisory_xact_lock(...)``. Concurrent pod boots
during a rolling deploy were deadlocking each other on
``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` (``AccessExclusiveLock``),
which took production down on 2026-04-16. The lock is transaction-scoped,
released automatically at COMMIT, and has no persistent effect on the
database schema — hence no up/down DDL here.

See ``core/database.py::init_db`` for the lock call, and PR #154 for the
full write-up of the outage and fix.
"""

from __future__ import annotations

# Alembic identifiers
revision = "v482_init_db_advisory_lock"
down_revision = "v481_orm_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op. See module docstring."""


def downgrade() -> None:
    """No-op. See module docstring."""
