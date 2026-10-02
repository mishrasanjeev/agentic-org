# SPDX-License-Identifier: Apache-2.0
"""Provider residency attestations for residency enforcement.

Revision ID: v6z33_provider_attestations
Revises: v6z32_operator_overrides
Create Date: 2026-10-02

One row per (provider, data region) an administrator attests: processing stays in
the region and the provider has committed not to train on the institution's data.
``core.governance.residency`` refuses, with enforcement on, any provider without an
active row for the tenant's region. Tenant-scoped under row-level security.
"""

from alembic import op

revision = "v6z33_provider_attestations"
down_revision = "v6z32_operator_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS provider_residency_attestations (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            data_region VARCHAR(8) NOT NULL,
            in_region BOOLEAN NOT NULL DEFAULT false,
            no_training BOOLEAN NOT NULL DEFAULT false,
            evidence_ref TEXT NOT NULL DEFAULT '',
            attested_by VARCHAR(255) NOT NULL,
            attested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            revoked_by VARCHAR(255) NULL,
            CONSTRAINT ck_provider_attestations_region CHECK (data_region IN ('IN','EU','US'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provider_attestations_tenant_active "
        "ON provider_residency_attestations(tenant_id, revoked_at);"
    )
    op.execute("ALTER TABLE provider_residency_attestations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE provider_residency_attestations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "DROP POLICY IF EXISTS provider_residency_attestations_tenant_isolation ON provider_residency_attestations;"
    )
    op.execute("""
        CREATE POLICY provider_residency_attestations_tenant_isolation
        ON provider_residency_attestations
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_residency_attestations;")
