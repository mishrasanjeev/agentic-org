# SPDX-License-Identifier: Apache-2.0
"""Content draft edits.

Revision ID: v6z66_content_draft_edits
Revises: v6z65_workbench_assignments
Create Date: 2026-10-07

``content_drafts.edits``: a reviewer's amendments to a waiting draft
before the decision, with the original of every field edited
(``core/content/drafts.py::edit``).
"""

from alembic import op

revision = "v6z66_content_draft_edits"
down_revision = "v6z65_workbench_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE content_drafts ADD COLUMN IF NOT EXISTS edits JSONB NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE content_drafts DROP COLUMN IF EXISTS edits;")
