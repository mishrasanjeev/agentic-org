# SPDX-License-Identifier: Apache-2.0
"""Merchant custom domains (self-service onboarding of a merchant's own host).

Revision ID: v6z91_merchant_custom_domains
Revises: v6z90_mandates_rails
Create Date: 2026-10-21

* ``merchant_custom_domains``: one row per merchant scope (tenant, merchant,
  seller agent) and host, moving ``pending_dns`` -> ``pending_mapping`` ->
  ``pending_certificate`` -> ``live`` (or ``failed``). Removing a mapped domain
  leaves ``pending_unmap`` (the domain mapping still to delete, a durable
  operator queue item) before ``removed``. A host in a claimed state
  (``pending_mapping``, ``pending_certificate``, ``live``, ``failed``) is unique
  across every tenant; a scope has at most one row that is not
  ``pending_unmap`` or ``removed``.

Tenant-scoped with ENABLE + FORCE row-level security and a ``USING`` +
``WITH CHECK`` tenant policy. A second, SELECT-only policy admits the platform
context (``agenticorg.platform_ctx = '1'``, as in v6z87) so the status task and
the operator queue can list rows across tenants; every write runs in the
owning tenant's session. Additive: ``downgrade`` drops the table (it holds
onboarding state only; the serving-host attestation lives in the merchant
config).
"""

from alembic import op

revision = "v6z91_merchant_custom_domains"
down_revision = "v6z90_mandates_rails"
branch_labels = None
depends_on = None

TABLE = "merchant_custom_domains"
PLATFORM_CTX_PREDICATE = "current_setting('agenticorg.platform_ctx', true) = '1'"
TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_custom_domains (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL DEFAULT '',
            host VARCHAR(253) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'pending_dns',
            cname_target VARCHAR(253) NOT NULL,
            mapping_source VARCHAR(16) NULL,
            search_console_owner_confirmed_at TIMESTAMPTZ NULL,
            last_error_code VARCHAR(64) NULL,
            last_error_detail VARCHAR(500) NULL,
            check_count INTEGER NOT NULL DEFAULT 0,
            created_by VARCHAR(255) NOT NULL,
            last_checked_at TIMESTAMPTZ NULL,
            verified_at TIMESTAMPTZ NULL,
            mapping_requested_at TIMESTAMPTZ NULL,
            operator_notified_at TIMESTAMPTZ NULL,
            mapping_created_at TIMESTAMPTZ NULL,
            live_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_merchant_custom_domains_status CHECK
                (status IN ('pending_dns', 'pending_mapping', 'pending_certificate', 'live', 'failed',
                            'pending_unmap', 'removed')),
            CONSTRAINT ck_merchant_custom_domains_mapping_source CHECK
                (mapping_source IS NULL OR mapping_source IN ('operator', 'cloud_run_api'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_custom_domains_scope_active "
        "ON merchant_custom_domains (tenant_id, merchant_id, seller_agent_id) "
        "WHERE status NOT IN ('pending_unmap', 'removed');"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_custom_domains_claimed_host "
        "ON merchant_custom_domains (host) "
        "WHERE status IN ('pending_mapping', 'pending_certificate', 'live', 'failed');"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_custom_domains_status ON merchant_custom_domains (status, updated_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_custom_domains_tenant "
        "ON merchant_custom_domains (tenant_id, created_at DESC);"
    )
    op.execute("ALTER TABLE merchant_custom_domains ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE merchant_custom_domains FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE};")
    op.execute(
        f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_platform_ctx ON {TABLE};")
    op.execute(f"CREATE POLICY {TABLE}_platform_ctx ON {TABLE} FOR SELECT USING ({PLATFORM_CTX_PREDICATE});")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE};")
