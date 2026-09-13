"""Durable billing entitlement + tenant-scoped CDC triggers (RLS).

Revision ID: v6z18_billing_cdc_state
Revises: v6z17_sessions_dsar
Create Date: 2026-09-13

Audit 2026-09-13 (enterprise bug sweep, billing/connector track):

* ``billing_subscriptions`` (created in v4.0.0, never written) becomes the
  source of truth for tenant entitlement — Stripe and Plural activations,
  cancellations and provider webhooks upsert one row per tenant and Redis
  is only a cache. Adds ``provider_customer_id`` (NOT NULL DEFAULT '' so
  existing rows need no backfill) and an index for the beat task that
  expires Plural (one-time order) periods. Tenant-scoped => RLS enforced
  with the v6z16 policy shape.

* ``cdc_triggers`` (created in v4.0.0) replaces the process-global trigger
  registry: ``evaluate_triggers`` reads only the event tenant's rows.
  ``active`` is the enabled flag. Tenant-scoped => RLS enforced; index on
  ``(tenant_id, active)`` for the per-event lookup.

Both tables are guarded with ``to_regclass`` so the revision is a no-op on
partial schemas, matching v6z16. Downgrade removes only what this revision
added (policies, index, column); the tables stay (v4.0.0 owns them).
"""

from alembic import op

revision = "v6z18_billing_cdc_state"
down_revision = "v6z17_sessions_dsar"
branch_labels = None
depends_on = None

TENANT_POLICY_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"

TABLES: tuple[str, ...] = ("billing_subscriptions", "cdc_triggers")


def _guarded(table: str, body: str) -> str:
    # Table names are hardcoded above — not user input.
    return (
        "DO $$ BEGIN "
        f"IF to_regclass('public.{table}') IS NOT NULL THEN "
        f"{body} "
        "END IF; END $$;"
    )


def _rls_statements(table: str) -> str:
    policy = f"{table}_tenant_isolation"
    return " ".join(
        [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
            f"DROP POLICY IF EXISTS {policy} ON {table};",
            f"CREATE POLICY {policy} ON {table} "
            f"USING ({TENANT_POLICY_PREDICATE}) WITH CHECK ({TENANT_POLICY_PREDICATE});",
        ]
    )


def upgrade() -> None:
    op.execute(
        _guarded(
            "billing_subscriptions",
            "ALTER TABLE billing_subscriptions "
            "ADD COLUMN IF NOT EXISTS provider_customer_id VARCHAR(200) NOT NULL DEFAULT ''; "
            "CREATE INDEX IF NOT EXISTS ix_billing_subscriptions_provider_period_end "
            "ON billing_subscriptions (provider, status, current_period_end);",
        )
    )
    op.execute(
        _guarded(
            "cdc_triggers",
            "CREATE INDEX IF NOT EXISTS ix_cdc_triggers_tenant_active "
            "ON cdc_triggers (tenant_id, active);",
        )
    )
    for table in TABLES:
        op.execute(_guarded(table, _rls_statements(table)))  # noqa: S608  # nosec B608


def downgrade() -> None:
    for table in TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(
            _guarded(
                table,
                f"DROP POLICY IF EXISTS {policy} ON {table}; "
                f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY; "
                f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;",
            )
        )
    op.execute("DROP INDEX IF EXISTS ix_cdc_triggers_tenant_active")
    op.execute("DROP INDEX IF EXISTS ix_billing_subscriptions_provider_period_end")
    op.execute(
        _guarded(
            "billing_subscriptions",
            "ALTER TABLE billing_subscriptions DROP COLUMN IF EXISTS provider_customer_id;",
        )
    )
