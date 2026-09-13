"""Own the ``demo_requests`` lead-capture table in Alembic.

Revision ID: v6z14_demo_requests
Revises: v6z13_knowledge_docs
Create Date: 2026-09-12

Pre-fix, ``POST /api/v1/demo-request`` ran ``CREATE TABLE IF NOT EXISTS``
on every public request (request-time DDL on an unauthenticated path). The
handler now only INSERTs; this migration delivers the schema. Idempotent so
installations that already have the runtime-created table are unchanged.
"""

from alembic import op

revision = "v6z14_demo_requests"
down_revision = "v6z13_knowledge_docs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS demo_requests (
            id SERIAL PRIMARY KEY,
            name TEXT,
            email TEXT,
            company TEXT,
            role TEXT,
            phone TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    # Lead-capture rows are business data; keep the table on downgrade.
    pass
