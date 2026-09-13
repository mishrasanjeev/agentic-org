"""Repair missing billing_subscriptions / cdc_triggers on bootstrapped databases.

Revision ID: v6z19_repair_billing_cdc
Revises: v6z18_billing_cdc_state
Create Date: 2026-09-13

Audit 2026-09-13 (fresh-environment drift, same class as v6y4 / v6z13):

``billing_subscriptions`` and ``cdc_triggers`` are created only by
``v400_apex``. They have no ORM model, and ``scripts/alembic_migrate.py``
bootstraps an empty database with ``metadata.create_all()`` and then stamps
``v480_baseline`` — so on every fresh install (compose volume, CI, DR
region, self-hosted) the v4.0.0 chain is skipped and neither table exists.
``v6z18`` guards everything with ``to_regclass`` and therefore silently
no-ops, leaving the new billing source of truth and the tenant CDC trigger
store as missing relations: paid activations raise ``UndefinedTable`` and
``get_subscription`` falls back to ``free`` for every tenant.

This revision repairs at the current head, idempotently, with the full
shape v4.0.0 + v6z18 expect (columns, indexes, FORCE RLS + policy). On an
environment that already has the tables every statement is a no-op.
"""

from __future__ import annotations

from alembic import op

revision = "v6z19_repair_billing_cdc"
down_revision = "v6z18_billing_cdc_state"
branch_labels = None
depends_on = None

TENANT_POLICY_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"


def _rls(table: str) -> str:
    policy = f"{table}_tenant_isolation"
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; "
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY; "
        f"DROP POLICY IF EXISTS {policy} ON {table}; "
        f"CREATE POLICY {policy} ON {table} "
        f"USING ({TENANT_POLICY_PREDICATE}) WITH CHECK ({TENANT_POLICY_PREDICATE});"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_subscriptions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL UNIQUE REFERENCES tenants(id),
            provider VARCHAR(30) NOT NULL,
            external_id VARCHAR(200) NOT NULL,
            provider_customer_id VARCHAR(200) NOT NULL DEFAULT '',
            plan VARCHAR(30) NOT NULL DEFAULT 'free',
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMPTZ,
            current_period_end TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        );
        """
    )
    # Environments that have the v4.0.0 table but were stamped past v6z18's
    # guarded ALTER get the column here as well.
    op.execute(
        "ALTER TABLE billing_subscriptions "
        "ADD COLUMN IF NOT EXISTS provider_customer_id VARCHAR(200) NOT NULL DEFAULT ''"
    )
    # tenant_id is UNIQUE, so its constraint index already serves lookups;
    # the v4.0.0 ``ix_billing_subscriptions_tenant_id`` duplicates it and
    # fails the post-upgrade index audit (scripts/check_database_indexes.py).
    op.execute("DROP INDEX IF EXISTS ix_billing_subscriptions_tenant_id")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_billing_subscriptions_provider_period_end "
        "ON billing_subscriptions (provider, status, current_period_end)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cdc_triggers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            connector VARCHAR(100) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            workflow_id UUID NOT NULL REFERENCES workflow_definitions(id),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_cdc_triggers_tenant_id ON cdc_triggers (tenant_id)")
    # Leading index for the workflow_id foreign key (index audit requirement).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_triggers_workflow_id ON cdc_triggers (workflow_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cdc_triggers_tenant_active "
        "ON cdc_triggers (tenant_id, active)"
    )

    for table in ("billing_subscriptions", "cdc_triggers"):
        op.execute(_rls(table))  # noqa: S608  # nosec B608


def downgrade() -> None:
    # No-op by design (same contract as v6y4 / v6z13): v4.0.0 remains the
    # canonical owner of both tables; this revision only repairs
    # environments whose bootstrap skipped it. Dropping the tables here
    # would destroy billing entitlement on a rollback.
    pass
