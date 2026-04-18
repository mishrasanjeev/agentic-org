"""Add per-tenant governance_config table.

Revision ID: v485_governance_config
Revises: v484_docs_kb_upload_cols
Create Date: 2026-04-18

Enterprise Readiness Plan P4 — persists the Settings page's compliance
controls (PII masking, data region, audit retention) so they survive a
reload. Pre-PR-B those values were UI-only React state.

Idempotent: CREATE TABLE IF NOT EXISTS so a legacy-schema build (which
goes through ORM create_all + alembic) doesn't DuplicateTable.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars (alembic_version.version_num size).
revision = "v485_governance_config"
down_revision = "v484_docs_kb_upload_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS governance_config (
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            pii_masking BOOLEAN NOT NULL DEFAULT TRUE,
            data_region VARCHAR(8) NOT NULL DEFAULT 'IN',
            audit_retention_years INTEGER NOT NULL DEFAULT 7,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id),
            CHECK (data_region IN ('IN', 'EU', 'US')),
            CHECK (audit_retention_years BETWEEN 1 AND 10)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS governance_config")
