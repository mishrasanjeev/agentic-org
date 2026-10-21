# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Private encrypted channel bindings, sender links and intake. No existing rows rewritten."""

from alembic import op

revision = "v6z83_channel_bindings"
down_revision = "v6z82_spend_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = """
        CREATE TABLE IF NOT EXISTS channel_bindings (
            id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
            owner_user_id UUID NOT NULL REFERENCES users(id), name VARCHAR(100) NOT NULL,
            provider VARCHAR(32) NOT NULL, target_kind VARCHAR(16) NOT NULL,
            target_id UUID NOT NULL, credentials_ciphertext TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT false, auto_reply BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id, id),
            CHECK(target_kind IN ('agent','workflow'))
        );
        CREATE INDEX IF NOT EXISTS ix_channel_bindings_owner ON channel_bindings(tenant_id, owner_user_id);
        CREATE INDEX IF NOT EXISTS ix_channel_bindings_owner_fk ON channel_bindings(owner_user_id);
        CREATE TABLE IF NOT EXISTS channel_sender_links (
            id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
            binding_id UUID NOT NULL, user_id UUID NOT NULL REFERENCES users(id),
            sender_hash VARCHAR(64), challenge_hash VARCHAR(64) UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY(tenant_id, binding_id) REFERENCES channel_bindings(tenant_id, id),
            UNIQUE(tenant_id, binding_id, sender_hash)
        );
        CREATE INDEX IF NOT EXISTS ix_channel_links_binding ON channel_sender_links(tenant_id, binding_id);
        CREATE INDEX IF NOT EXISTS ix_channel_links_user_fk ON channel_sender_links(user_id);
        CREATE TABLE IF NOT EXISTS channel_deliveries (
            id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id), binding_id UUID NOT NULL,
            event_id VARCHAR(255) NOT NULL, content_hash VARCHAR(64) NOT NULL,
            message_ciphertext TEXT NOT NULL, user_id UUID REFERENCES users(id),
            delegation_id UUID, human_approved BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(32) NOT NULL DEFAULT 'needs_review', result_ciphertext TEXT,
            error_code VARCHAR(64), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY(tenant_id, binding_id) REFERENCES channel_bindings(tenant_id, id),
            UNIQUE(tenant_id, binding_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS ix_channel_deliveries_pending ON channel_deliveries(status, created_at);
        CREATE INDEX IF NOT EXISTS ix_channel_deliveries_user_fk ON channel_deliveries(user_id);
        CREATE INDEX IF NOT EXISTS ix_channel_deliveries_binding ON channel_deliveries(tenant_id, binding_id, created_at);
        ALTER TABLE channel_bindings ALTER COLUMN enabled SET DEFAULT false;
        ALTER TABLE channel_bindings ALTER COLUMN auto_reply SET DEFAULT false;
        ALTER TABLE channel_deliveries ALTER COLUMN human_approved SET DEFAULT false;
        ALTER TABLE channel_deliveries ALTER COLUMN status SET DEFAULT 'needs_review';
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)
    for table in ("channel_bindings", "channel_sender_links", "channel_deliveries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"""CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))""")


def downgrade() -> None:
    # Retain encrypted intake and RLS during version traversal/application rollback.
    # A destructive schema rollback requires a separately reviewed migration.
    pass
