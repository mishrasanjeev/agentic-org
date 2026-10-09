# SPDX-License-Identifier: Apache-2.0
"""Speech summaries and analytics.

Revision ID: v6z70_speech_summaries
Revises: v6z69_speech_recordings
Create Date: 2026-10-07

``speech_recordings.summary_encrypted``: the call summary under the
tenant's key, as the transcript; ``speech_recordings.analytics``: the
figures computed from the transcript (sentiment, empathy, interaction,
signals), which hold no words (``core/speech/analytics.py``).
"""

# ENCRYPTED_MIGRATION_HELPER_EXEMPT: adds an empty JSONB column with no ciphertext
# transformation or backfill; reruns leave existing summaries unchanged.
# PostgreSQL preservation coverage: test_speech_migration_security.py.
from alembic import op

revision = "v6z70_speech_summaries"
down_revision = "v6z69_speech_recordings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE speech_recordings ADD COLUMN IF NOT EXISTS summary_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb;"
    )
    op.execute("ALTER TABLE speech_recordings ADD COLUMN IF NOT EXISTS analytics JSONB NOT NULL DEFAULT '{}'::jsonb;")


def downgrade() -> None:
    op.execute("ALTER TABLE speech_recordings DROP COLUMN IF EXISTS summary_encrypted;")
    op.execute("ALTER TABLE speech_recordings DROP COLUMN IF EXISTS analytics;")
