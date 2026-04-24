"""Add tenant_ai_settings for per-tenant LLM + embedding model choice.

Revision ID: v493_tenant_ai_settings
Revises: v492_tenant_ai_creds
Create Date: 2026-04-24

Closes S0-08 (PR-2). One row per tenant. Values validated against
``core.ai_providers.catalog`` at the API boundary.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars.
revision = "v493_tenant_ai_settings"
down_revision = "v492_tenant_ai_creds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_ai_settings (
            tenant_id UUID PRIMARY KEY,
            llm_provider VARCHAR(64),
            llm_model VARCHAR(128),
            llm_fallback_model VARCHAR(128),
            llm_routing_policy VARCHAR(32) NOT NULL DEFAULT 'auto',
            max_input_tokens INTEGER,
            embedding_provider VARCHAR(64),
            embedding_model VARCHAR(128),
            embedding_dimensions INTEGER,
            chunk_size INTEGER,
            chunk_overlap INTEGER,
            ai_fallback_policy VARCHAR(16) NOT NULL DEFAULT 'allow',
            updated_by UUID,
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
                    WHERE table_name = 'tenant_ai_settings'
                      AND constraint_name = 'tenant_ai_settings_tenant_id_fkey'
                ) THEN
                    ALTER TABLE tenant_ai_settings
                        ADD CONSTRAINT tenant_ai_settings_tenant_id_fkey
                        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
                END IF;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_ai_settings")
