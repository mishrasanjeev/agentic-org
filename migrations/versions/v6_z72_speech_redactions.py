# SPDX-License-Identifier: Apache-2.0
"""Speech redactions.

Revision ID: v6z72_speech_redactions
Revises: v6z71_speech_live_sessions
Create Date: 2026-10-07

``speech_recordings.redactions``: what was cut from a recording and its
transcript as kinds and times, never the digits; ``redacted_at``: when
(``core/speech/redaction.py``).
"""

from alembic import op

revision = "v6z72_speech_redactions"
down_revision = "v6z71_speech_live_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE speech_recordings ADD COLUMN IF NOT EXISTS redactions JSONB NOT NULL DEFAULT '[]'::jsonb;")
    op.execute("ALTER TABLE speech_recordings ADD COLUMN IF NOT EXISTS redacted_at TIMESTAMPTZ NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE speech_recordings DROP COLUMN IF EXISTS redactions;")
    op.execute("ALTER TABLE speech_recordings DROP COLUMN IF EXISTS redacted_at;")
