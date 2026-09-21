# SPDX-License-Identifier: Apache-2.0
"""The passage behind every excerpt a governed case's documents cite, encrypted.

Revision ID: v6z28_case_excerpts
Revises: v6z27_case_decisions
Create Date: 2026-09-21

PRD A-6 attaches cited excerpts to the memo for the human reviewer. The memo carries them by
reference - provider, record, media type and digest - and ``governed_cases.excerpts_encrypted``
holds the passage itself so a reviewer can read what a citation points at instead of only which
record it named. A passage is a provider record about a person (a sanctions entry carries a name,
a date of birth, a nationality and an address), so it is encrypted with the tenant's key
(``core.crypto.tenant_secrets``) like every other sensitive column here; the reference, the digest
and the fields cited stay in clear, because they are already in the memo.

Additive and forward-only: one JSONB column with a server default, so existing rows read as an
empty list without a backfill. ``downgrade`` keeps the column; dropping it would discard the only
copy of the passages behind a decided case's citations.
"""

from alembic import op
from sqlalchemy import text

from core.crypto.migration_helpers import encrypted_migration

revision = "v6z28_case_excerpts"
down_revision = "v6z27_case_decisions"
branch_labels = None
depends_on = None

_ROLLBACK_DOC = (
    "The column is new in this revision and starts empty, so rollback transforms and discards no "
    "pre-existing ciphertext. To remove the passages from a live database, call "
    "DELETE /api/v1/governed-cases/{case_ref}/excerpts first (it keeps the references and the "
    "memo); the column can then be dropped manually once nothing reads it."
)


def _add_column() -> None:
    op.execute(
        "ALTER TABLE governed_cases ADD COLUMN IF NOT EXISTS excerpts_encrypted JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )
    op.get_bind().execute(
        text("UPDATE governed_cases SET excerpts_encrypted = '[]'::jsonb WHERE excerpts_encrypted IS NULL")
    )


def upgrade() -> None:
    existed = (
        op.get_bind()
        .execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'governed_cases' AND column_name = 'excerpts_encrypted')"
            )
        )
        .scalar()
    )
    _add_column()
    if existed:
        return

    with encrypted_migration(
        revision=revision,
        table="governed_cases",
        columns=["excerpts_encrypted"],
        rollback_doc=_ROLLBACK_DOC,
    ) as ctx:
        pre_count = ctx.snapshot_row_count()
        ctx.dry_run_decrypt_sample(n=50)
        for _offset in ctx.iter_resumable_batches(batch=500):
            # The column is new and empty: there is no ciphertext to transform.
            raise RuntimeError("new excerpts_encrypted column unexpectedly contained ciphertext")
        ctx.assert_decrypt_after(n=50)
        ctx.record_audit({"pre_count": pre_count, "post_count": ctx.snapshot_row_count()})


def downgrade() -> None:
    # Forward-only: the passages behind a case's citations are not discarded by a rollback.
    pass
