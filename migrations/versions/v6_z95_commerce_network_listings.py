# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""AgenticOrg shopping network: one listing per merchant that turned on "Sell everywhere".

Revision ID: v6z95_network_listings
Revises: v6z94_user_totp_mfa
Create Date: 2026-10-25

* ``commerce_network_listings``: the merchant scope (tenant, merchant, seller
  agent), its network ``slug`` (unique across every tenant: it names the shop
  in the public directory, in ``<slug>.<platform apex>`` and in the Telegram
  deep link), the published serving host, ``listed`` or ``paused``, and the
  network terms the merchant accepted, by whom and when. ``subdomain_host`` is
  the platform subdomain AgenticOrg claimed for the shop, if any.

Tenant-scoped with ENABLE + FORCE row-level security and a ``USING`` +
``WITH CHECK`` tenant policy. A SELECT-only policy admits the platform context
(``agenticorg.platform_ctx = '1'``, as in v6z87 and v6z91) so the public
directory and the network checkout gateway can resolve a slug across tenants;
every write runs in the owning tenant's session. Additive: ``downgrade`` drops
the table.
"""

from alembic import op

revision = "v6z95_network_listings"
down_revision = "v6z94_user_totp_mfa"
branch_labels = None
depends_on = None

TABLE = "commerce_network_listings"
PLATFORM_CTX_PREDICATE = "current_setting('agenticorg.platform_ctx', true) = '1'"
TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS commerce_network_listings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            merchant_id VARCHAR(160) NOT NULL,
            seller_agent_id VARCHAR(160) NOT NULL DEFAULT '',
            slug VARCHAR(40) NOT NULL,
            display_name VARCHAR(200) NOT NULL,
            serving_host VARCHAR(253) NULL,
            subdomain_host VARCHAR(253) NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'listed',
            terms_version VARCHAR(32) NOT NULL,
            opted_in_by VARCHAR(255) NOT NULL,
            opted_in_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_commerce_network_listings_status CHECK (status IN ('listed', 'paused')),
            CONSTRAINT ck_commerce_network_listings_slug CHECK (slug ~ '^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$')
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commerce_network_listings_scope "
        "ON commerce_network_listings (tenant_id, merchant_id, seller_agent_id);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_commerce_network_listings_slug ON commerce_network_listings (slug);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_commerce_network_listings_status "
        "ON commerce_network_listings (status, updated_at);"
    )
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE};")
    op.execute(
        f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_platform_ctx ON {TABLE};")
    op.execute(f"CREATE POLICY {TABLE}_platform_ctx ON {TABLE} FOR SELECT USING ({PLATFORM_CTX_PREDICATE});")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE};")
