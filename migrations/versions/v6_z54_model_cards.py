# SPDX-License-Identifier: Apache-2.0
"""Model cards.

Revision ID: v6z54_model_cards
Revises: v6z53_retrieval_metrics
Create Date: 2026-10-07

``model_cards``: the part of a model card an administrator writes
(``core/governance/model_cards.py``): intended use, limitations, data
handling, notes, the owner, and the approval by a second person. One row per
tenant and model; the rest of the card is assembled live. Tenant scoped under
a row-level policy.
"""

from alembic import op

revision = "v6z54_model_cards"
down_revision = "v6z53_retrieval_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_cards (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            provider VARCHAR(64) NOT NULL,
            model VARCHAR(128) NOT NULL,
            intended_use TEXT NULL,
            limitations TEXT NULL,
            data_handling TEXT NULL,
            notes TEXT NULL,
            owner_user_id UUID NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'draft',
            approved_by UUID NULL,
            reviewed_at TIMESTAMPTZ NULL,
            updated_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_model_cards_status CHECK (status IN ('draft', 'approved'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_model_cards_tenant_model ON model_cards(tenant_id, provider, model);"
    )
    op.execute("ALTER TABLE model_cards ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS model_cards_tenant_isolation ON model_cards;")
    op.execute(
        """
        CREATE POLICY model_cards_tenant_isolation
        ON model_cards
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_cards;")
