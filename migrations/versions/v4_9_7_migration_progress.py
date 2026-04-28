"""Foundation #5 — alembic_migration_progress resumability table.

Revision ID: v497_migration_progress
Revises: v496_report_sched_recreate
Create Date: 2026-04-28

Backing store for ``core.crypto.migration_helpers.encrypted_migration``.
A wrapped migration writes per-table progress here so a crash midway
through a long rewrap/backfill resumes from the last completed batch
instead of restarting from row 0.

Schema choices:
- (revision, table_name) is the natural key — one row per
  (migration, table) pass.
- ``rows_processed`` is monotonic; ``rows_total`` is the snapshot
  taken at start.
- ``last_processed_pk`` is opaque text so any PK type works
  (uuid, bigint, composite).
- ``completed_at`` IS NULL means the pass is in flight.

Idempotent: CREATE IF NOT EXISTS so re-running on an env that
already has the table is safe.
"""

from __future__ import annotations

from alembic import op

revision = "v497_migration_progress"
down_revision = "v496_report_sched_recreate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS alembic_migration_progress (
            revision VARCHAR(64) NOT NULL,
            table_name VARCHAR(128) NOT NULL,
            last_processed_pk TEXT,
            rows_processed BIGINT NOT NULL DEFAULT 0,
            rows_total BIGINT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            PRIMARY KEY (revision, table_name)
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_amp_in_flight
            ON alembic_migration_progress (completed_at)
            WHERE completed_at IS NULL;
    """)


def downgrade() -> None:
    # Intentional no-op: dropping this table during a downgrade
    # would orphan resumability markers that other in-flight
    # migrations depend on.
    pass
