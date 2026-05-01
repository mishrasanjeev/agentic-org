"""Phase 5 — health_check_history table for SLA telemetry persistence.

Revision ID: v498_health_check_history
Revises: v497_migration_progress
Create Date: 2026-05-01

Closes the "data_source: live_snapshot" honest note left in PR #399.
The /health/checks and /health/uptime endpoints currently return only
the current live probe; without persistence the SLA Monitor page can't
show real history or compute uptime percentage.

This migration creates ``health_check_history`` — a single platform-
wide observability table (NOT tenant-scoped, so no RLS). A periodic
Celery task records one row every 5 minutes via
``core.tasks.health_snapshot.record_health_snapshot``. The rate-limit
endpoints query this table for the last 24h.

Schema choices:
- ``recorded_at`` is the natural sort key for time-window queries.
- ``status`` is the same string the live /health endpoint returns
  (``"healthy"`` / ``"unhealthy"``) so endpoint logic stays consistent.
- ``checks`` keeps the per-component dict (db, redis, ...) in JSONB
  so we can extend without schema churn.
- ``version`` and ``commit`` capture the running deploy at snapshot
  time, useful for correlating health dips with deploys.

Idempotent: CREATE TABLE / INDEX use IF NOT EXISTS so re-running on
an environment that already has the table is a no-op. This matches
the local pattern (see v497_migration_progress).
"""

from __future__ import annotations

from alembic import op

revision = "v498_health_check_history"
down_revision = "v497_migration_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS health_check_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status TEXT NOT NULL,
            checks JSONB NOT NULL DEFAULT '{}'::jsonb,
            version TEXT,
            commit TEXT
        );
    """)
    # DESC index — every read is "last N hours, newest first".
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_health_check_history_recorded_at
            ON health_check_history (recorded_at DESC);
    """)


def downgrade() -> None:
    # Reversible. Index is dropped implicitly with the table, but be
    # explicit so a partial-state environment cleans up cleanly.
    op.execute("DROP INDEX IF EXISTS ix_health_check_history_recorded_at;")
    op.execute("DROP TABLE IF EXISTS health_check_history;")
