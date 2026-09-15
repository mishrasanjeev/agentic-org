# SPDX-License-Identifier: Apache-2.0
"""Encrypted per-case pseudonym maps for pre-model pseudonymisation.

Revision ID: v6z22_case_pseudonym_maps
Revises: v6z21_resource_ownership
Create Date: 2026-09-15

PRD F-5: ``core.pii.pseudonymiser`` replaces names, dates of birth,
addresses and identifiers with tokens that are stable for a whole case, and
restores them at the tool boundary. The token-to-value map must survive a
human-in-the-loop pause and a process restart, so it is persisted here, one
row per (tenant, case), encrypted with the tenant's key
(``core.crypto.tenant_secrets.encrypt_for_tenant``). Plaintext values are
never written to this table.

Additive and forward-only: a new table with row-level security in the same
shape as its neighbours. ``downgrade`` deliberately keeps the table, because
dropping it would make every paused case that was pseudonymised impossible to
resume. ``upgrade`` is idempotent: when the table already exists (a database
bootstrapped from the ORM, or a re-run after a downgrade) it holds no rows this
revision could transform, so only the row-level security statements are
re-applied and the encrypted-column gates, which exist for transformations,
are skipped.
"""

from alembic import op
from sqlalchemy import text

from core.crypto.migration_helpers import encrypted_migration

revision = "v6z22_case_pseudonym_maps"
down_revision = "v6z21_resource_ownership"
branch_labels = None
depends_on = None


def _enable_row_level_security() -> None:
    # The unique constraint's index leads with tenant_id, so it also serves the
    # tenants foreign key (scripts/check_database_indexes.py).
    op.execute("ALTER TABLE case_pseudonym_maps ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE case_pseudonym_maps FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS case_pseudonym_maps_tenant_isolation ON case_pseudonym_maps;")
    op.execute("""
        CREATE POLICY case_pseudonym_maps_tenant_isolation ON case_pseudonym_maps
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def upgrade() -> None:
    existed = op.get_bind().execute(text("SELECT to_regclass('public.case_pseudonym_maps') IS NOT NULL")).scalar()
    op.execute("""
        CREATE TABLE IF NOT EXISTS case_pseudonym_maps (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            case_id VARCHAR(200) NOT NULL,
            mapping_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
            entry_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_case_pseudonym_maps_tenant_case UNIQUE (tenant_id, case_id),
            CONSTRAINT ck_case_pseudonym_maps_entry_count CHECK (entry_count >= 0)
        );
    """)
    if existed:
        _enable_row_level_security()
        return

    with encrypted_migration(
        revision=revision,
        table="case_pseudonym_maps",
        columns=["mapping_encrypted"],
        rollback_doc=(
            "The table is new in this revision and starts empty, so rollback does "
            "not transform or discard pre-existing ciphertext. Turn the "
            "pseudonymisation.pre_model flag off first; the table can then be "
            "dropped manually once no paused case depends on it."
        ),
    ) as ctx:
        pre_count = ctx.snapshot_row_count()
        ctx.dry_run_decrypt_sample(n=50)
        # Initialise resumability/audit state even though a newly created table
        # has no rows to transform.
        for _offset in ctx.iter_resumable_batches(batch=500):
            raise RuntimeError("new case_pseudonym_maps table unexpectedly contained rows")

        _enable_row_level_security()

        ctx.assert_decrypt_after(n=50)
        ctx.record_audit({"pre_count": pre_count, "post_count": ctx.snapshot_row_count()})


def downgrade() -> None:
    # Forward-only: keep the encrypted maps so paused cases stay resumable.
    pass
