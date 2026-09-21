# SPDX-License-Identifier: Apache-2.0
"""The passage behind every excerpt a governed case's documents cite.

Revision ID: v6z28_case_excerpts
Revises: v6z27_case_decisions
Create Date: 2026-09-21

PRD A-6 attaches cited excerpts to the memo for the human reviewer. The memo carries them by
reference - provider, record, media type and digest - and ``governed_cases.excerpts`` holds the
passage itself, as the provider returned it, so a reviewer can read what a citation points at
instead of only which record it named. The content is provider data about the case, kept beside
the memo and the screening results it belongs to.

Additive and forward-only: one JSONB column with a server default, so existing rows read as an
empty list without a backfill. ``downgrade`` keeps the column; dropping it would discard the only
copy of the passages behind a decided case's citations.
"""

from alembic import op
from sqlalchemy import text

revision = "v6z28_case_excerpts"
down_revision = "v6z27_case_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE governed_cases ADD COLUMN IF NOT EXISTS excerpts JSONB NOT NULL DEFAULT '[]'::jsonb;")
    op.get_bind().execute(text("UPDATE governed_cases SET excerpts = '[]'::jsonb WHERE excerpts IS NULL"))


def downgrade() -> None:
    # Forward-only: the passages behind a case's citations are not discarded.
    pass
