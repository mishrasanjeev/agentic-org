"""Add tenant_ai_credentials for tenant-scoped encrypted AI provider tokens.

Revision ID: v492_tenant_ai_creds
Revises: v491_merge_v490_heads
Create Date: 2026-04-24

Closes S0-09 from docs/STRICT_REPO_AUDIT_AND_TEST_MATRIX_2026-04-24.md
(PR-1 of the four-PR closure plan). Stores tenant BYO provider tokens
for LLM, embedding, RAG, STT, and TTS calls, encrypted via
``core.crypto.encrypt_for_tenant`` and resolved by
``core/ai_providers/resolver.py`` before the platform-env fallback.

Idempotent: CREATE TABLE IF NOT EXISTS + FK guarded on
information_schema.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars per preflight gate.
revision = "v492_tenant_ai_creds"
down_revision = "v491_merge_v490_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_ai_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            credential_kind VARCHAR(32) NOT NULL,
            credentials_encrypted JSONB NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'unverified',
            display_prefix VARCHAR(8),
            display_suffix VARCHAR(8),
            label VARCHAR(255),
            provider_config JSONB,
            last_health_check_at TIMESTAMPTZ,
            last_health_check_error VARCHAR(500),
            last_used_at TIMESTAMPTZ,
            rotated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tenants') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE table_name = 'tenant_ai_credentials'
                      AND constraint_name = 'tenant_ai_credentials_tenant_id_fkey'
                ) THEN
                    ALTER TABLE tenant_ai_credentials
                        ADD CONSTRAINT tenant_ai_credentials_tenant_id_fkey
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
                END IF;
            END IF;
        END$$;
        """
    )

    # One BYO token per (tenant, provider, kind). Admins rotate in place.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_ai_cred "
        "ON tenant_ai_credentials (tenant_id, provider, credential_kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tenant_ai_credentials_tenant_id "
        "ON tenant_ai_credentials (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tenant_ai_credentials_tenant_id")
    op.execute("DROP INDEX IF EXISTS uq_tenant_ai_cred")
    op.execute("DROP TABLE IF EXISTS tenant_ai_credentials")
