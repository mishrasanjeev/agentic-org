# SPDX-License-Identifier: Apache-2.0
"""Decision requests made for a governed case.

Revision ID: v6z27_case_decisions
Revises: v6z26_case_push
Create Date: 2026-09-20

PRD G-3: a human decision on a governed case needs decision grants that only the Grantex auth
service's approval page can mint. ``governed_cases.decision_requests`` records each request the
platform made there - its id, the semantic action, the case version it was bound to, who asked for
it and the link to the approval page - so the console can show the state of an approval and the
case carries the audit trail. It never holds a decision grant: the tokens stay at the issuer and
are fetched only to consume them.

Additive and forward-only: one JSONB column with a server default, so existing rows read as an
empty list without a backfill. ``downgrade`` keeps the column; dropping it would discard the record
of who was asked to decide.
"""

from alembic import op
from sqlalchemy import text

revision = "v6z27_case_decisions"
down_revision = "v6z26_case_push"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE governed_cases "
        "ADD COLUMN IF NOT EXISTS decision_requests JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )
    # Make sure the default applies to rows written before this release.
    op.get_bind().execute(text("UPDATE governed_cases SET decision_requests = '[]'::jsonb WHERE decision_requests IS NULL"))


def downgrade() -> None:
    # Forward-only: keep the record of every decision request made for a case.
    pass
