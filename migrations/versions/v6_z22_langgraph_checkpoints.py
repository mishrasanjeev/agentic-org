# SPDX-License-Identifier: Apache-2.0
"""LangGraph Postgres checkpoint tables.

Revision ID: v6z22_langgraph_checkpoints
Revises: v6z21_resource_ownership
Create Date: 2026-09-15

Human-in-the-loop resume has to survive a process restart, so agent graph
checkpoints move from process memory to Postgres (``core/langgraph/checkpointer.py``,
setting ``AGENTICORG_LANGGRAPH_CHECKPOINTER=postgres``). The tables belong to
``langgraph-checkpoint-postgres``; the library can create them itself with
``AsyncPostgresSaver.setup()``, but runtime DDL is not how this repository
delivers schema, so this revision creates them explicitly.

``CHECKPOINT_MIGRATIONS`` is the library's own migration list for the pinned
version (3.1.2), index for index, so ``checkpoint_migrations`` can be stamped
with the versions it would have recorded and a later ``setup()`` is a no-op.
The only deviation is ``CREATE INDEX`` without ``CONCURRENTLY``: Alembic runs
inside a transaction and the tables are new. ``tests/unit/test_langgraph_checkpointer.py``
fails if the pinned library's list drifts from this one, and
``tests/integration/test_langgraph_checkpoint_postgres.py`` compares the
resulting catalog with what ``setup()`` builds.

The tables carry no ``tenant_id``, so row-level security cannot cover them.
Isolation comes from the thread id instead: the server generates it with the
tenant id as a prefix and it is only reachable through the RLS-protected
``hitl_queue`` row. Nothing accepts a thread id from a client.

Upgrading a library version that adds a migration needs a new revision that
applies the new entries; the application refuses to start the Postgres
checkpointer against a stale ``checkpoint_migrations`` version.
"""

from __future__ import annotations

from alembic import op

revision = "v6z22_langgraph_checkpoints"
down_revision = "v6z21_resource_ownership"
branch_labels = None
depends_on = None

CHECKPOINT_MIGRATIONS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);""",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    "SELECT 1;",
    "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);",
    "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);",
    "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';",
)

CHECKPOINT_TABLES: tuple[str, ...] = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    for statement in CHECKPOINT_MIGRATIONS:
        op.execute(statement)
    # Record every library version as applied, exactly as setup() would.
    op.execute(
        "INSERT INTO checkpoint_migrations (v) "
        f"SELECT generate_series(0, {len(CHECKPOINT_MIGRATIONS) - 1}) "
        "ON CONFLICT (v) DO NOTHING"
    )


def downgrade() -> None:
    # Checkpoints are transient run state: dropping them abandons any run
    # that is paused for approval. Its approval row stays, and resuming it
    # fails closed because the checkpoint is gone.
    for table in reversed(CHECKPOINT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
