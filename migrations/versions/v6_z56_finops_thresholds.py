# SPDX-License-Identifier: Apache-2.0
"""FinOps thresholds.

Revision ID: v6z56_finops_thresholds
Revises: v6z55_finops_attribution
Create Date: 2026-10-07

``finops_thresholds``: a scope (organisation, application, use case or
business unit), a period, an amount in USD and an action (alert, throttle,
suspend), with the last breach recorded on the row
(``core/finops/thresholds.py``). Tenant scoped under a row-level policy.
"""

from alembic import op

revision = "v6z56_finops_thresholds"
down_revision = "v6z55_finops_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finops_thresholds (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            scope_kind VARCHAR(16) NOT NULL,
            scope_value VARCHAR(64) NOT NULL DEFAULT '',
            period VARCHAR(8) NOT NULL DEFAULT 'monthly',
            threshold_usd DOUBLE PRECISION NOT NULL,
            action VARCHAR(16) NOT NULL DEFAULT 'alert',
            throttle_seconds INTEGER NOT NULL DEFAULT 5,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            notify_channels VARCHAR(64) NOT NULL DEFAULT 'email',
            last_breach_period VARCHAR(16) NULL,
            last_breach_at TIMESTAMPTZ NULL,
            last_breach_spend_usd DOUBLE PRECISION NULL,
            lifted_until TIMESTAMPTZ NULL,
            created_by UUID NULL,
            updated_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_finops_thresholds_scope
                CHECK (scope_kind IN ('organisation', 'application', 'use_case', 'business_unit')),
            CONSTRAINT ck_finops_thresholds_period CHECK (period IN ('daily', 'monthly')),
            CONSTRAINT ck_finops_thresholds_action CHECK (action IN ('alert', 'throttle', 'suspend')),
            CONSTRAINT ck_finops_thresholds_amount CHECK (threshold_usd > 0),
            CONSTRAINT ck_finops_thresholds_throttle CHECK (throttle_seconds >= 1 AND throttle_seconds <= 30)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_finops_thresholds_tenant_name ON finops_thresholds(tenant_id, name);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_finops_thresholds_tenant_enabled ON finops_thresholds(tenant_id, enabled);"
    )
    op.execute("ALTER TABLE finops_thresholds ENABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS finops_thresholds_tenant_isolation ON finops_thresholds;")
    op.execute(
        """
        CREATE POLICY finops_thresholds_tenant_isolation
        ON finops_thresholds
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finops_thresholds;")
