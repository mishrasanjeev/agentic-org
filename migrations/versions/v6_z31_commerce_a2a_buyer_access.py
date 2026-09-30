# SPDX-License-Identifier: Apache-2.0
"""Tenant-isolated merchant approval for external A2A buyers.

Revision ID: v6z31_a2a_buyers
Revises: v6z30_flag_global_read
"""

from alembic import op

revision = "v6z31_a2a_buyers"
down_revision = "v6z30_flag_global_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE commerce_a2a_buyer_access (
            id UUID PRIMARY KEY,
            tenant_id VARCHAR(160) NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL,
            buyer_agent_id VARCHAR(160) NOT NULL,
            token_hash VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_commerce_a2a_buyer_access_status CHECK (status IN ('active', 'revoked'))
        );
    """)
    op.execute("CREATE UNIQUE INDEX uq_commerce_a2a_buyer_access_hash ON commerce_a2a_buyer_access(token_hash);")
    op.execute("CREATE INDEX ix_commerce_a2a_buyer_access_tenant ON commerce_a2a_buyer_access(tenant_id);")
    op.execute("CREATE INDEX ix_commerce_a2a_buyer_access_scope ON commerce_a2a_buyer_access(tenant_id, merchant_id, seller_agent_id);")
    op.execute("ALTER TABLE commerce_a2a_buyer_access ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE commerce_a2a_buyer_access FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY commerce_a2a_buyer_access_tenant_isolation
        ON commerce_a2a_buyer_access
        USING (tenant_id = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('agenticorg.tenant_id', true));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS commerce_a2a_buyer_access;")
