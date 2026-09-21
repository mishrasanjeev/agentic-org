# SPDX-License-Identifier: Apache-2.0
"""The passage behind every excerpt a governed case's documents cite, encrypted.

Revision ID: v6z28_case_excerpts
Revises: v6z27_case_decisions
Create Date: 2026-09-21

PRD A-6 attaches cited excerpts to the memo for the human reviewer. The memo carries them by
reference - provider, record, media type and digest - and ``governed_cases.excerpts_encrypted``
holds the passage itself so a reviewer can read what a citation points at instead of only which
record it named. A passage is a provider record about a person (a sanctions entry carries a name,
a date of birth, a nationality and an address), so the application encrypts it with the tenant's
key (``core.cases.excerpts``) before writing; the reference, the digest and the fields cited stay
in clear, because they are already in the memo.

Additive and forward-only: one JSONB column with a server default, so existing rows read as an
empty list without a backfill. ``downgrade`` keeps the column; dropping it would discard the only
copy of the passages behind a decided case's citations.

**No encrypted-column ceremony here, deliberately.** ``core.crypto.migration_helpers`` exists for
migrations that *transform* existing ciphertext: dry run, row counts, decrypt-verify, resumable
batches, audit record. This migration adds an empty column and transforms nothing, and the
ceremony's resumable batching reads ``alembic_migration_progress``, a table created by revision
``v4_9_7_migration_progress`` that has no ORM model - so on the legacy path this repository
supports (``BaseModel.metadata.create_all`` plus ``alembic stamp``) that table does not exist and
the ceremony fails with ``UndefinedTable``, blocking such a database from ever upgrading past
``v6z27``. Adding the column is the whole change; the first passage is encrypted by the
application when a case is investigated.

**Note for a developer machine that ran an earlier state of this branch:** that state added a
plaintext ``excerpts`` column under this same revision id. Alembic therefore considers such a
database already upgraded, so it never gains ``excerpts_encrypted`` and keeps the plaintext
column, which nothing reads. Drop ``excerpts`` and re-run the column addition by hand (or rebuild
the development database) - no deployed environment is in that state, because the revision has
never been released.
"""

# ENCRYPTED_MIGRATION_HELPER_EXEMPT: adds an empty column, transforms no ciphertext, and the
# helper's resumable batching reads alembic_migration_progress, which does not exist on the
# create_all + stamp path this repository supports (see the docstring above).

from alembic import op
from sqlalchemy import text

revision = "v6z28_case_excerpts"
down_revision = "v6z27_case_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE governed_cases ADD COLUMN IF NOT EXISTS excerpts_encrypted JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )
    op.get_bind().execute(
        text("UPDATE governed_cases SET excerpts_encrypted = '[]'::jsonb WHERE excerpts_encrypted IS NULL")
    )


def downgrade() -> None:
    # Forward-only: the passages behind a case's citations are not discarded by a rollback.
    pass
