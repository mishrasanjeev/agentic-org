# SPDX-License-Identifier: Apache-2.0
"""Conversation supervision.

Revision ID: v6z61_conversation_supervision
Revises: v6z60_conversation_sessions
Create Date: 2026-10-07

``conversation_sessions`` gains the hand-off record (``escalation``) and who
holds the session (``taken_over_by``, ``taken_over_at``) for the supervisor's
live view and takeover (``core/conversation/supervisor.py``).
"""

from alembic import op

revision = "v6z61_conversation_supervision"
down_revision = "v6z60_conversation_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS escalation JSONB NULL;")
    op.execute("ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS taken_over_by VARCHAR(128) NULL;")
    op.execute("ALTER TABLE conversation_sessions ADD COLUMN IF NOT EXISTS taken_over_at TIMESTAMPTZ NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE conversation_sessions DROP COLUMN IF EXISTS taken_over_at;")
    op.execute("ALTER TABLE conversation_sessions DROP COLUMN IF EXISTS taken_over_by;")
    op.execute("ALTER TABLE conversation_sessions DROP COLUMN IF EXISTS escalation;")
