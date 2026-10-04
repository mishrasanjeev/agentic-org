# SPDX-License-Identifier: Apache-2.0
"""Tamper-evident audit: the hash chain over audit rows and the model call digests.

Revision ID: v6z41_tamper_evident_audit
Revises: v6z40_knowledge_full_text
Create Date: 2026-10-02

``audit_log`` gains the chain columns the sealing task fills (the sequence
number, the previous link, the link hash and when the row was sealed), a
unique index on the sequence per tenant and an index over the unsealed rows.
The append-only trigger function keeps refusing every UPDATE and DELETE but
one: the sealing transition, which fills the chain columns of an unsealed row
and changes nothing else. ``audit_chain_anchors`` keeps each tenant's head as
the last sealing left it (tenant-scoped under row-level security), so a chain
cut at its end is found.
``model_gateway_records`` gains the digests of the prompt, of what the model
saw and of what it answered, so a call is tamper-evident without its content
being stored.
"""

from alembic import op

revision = "v6z41_tamper_evident_audit"
down_revision = "v6z40_knowledge_full_text"
branch_labels = None
depends_on = None

# The same text as core.governance.audit_chain.SEAL_TRIGGER_SQL.
_SEAL_TRIGGER_SQL = """CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.chain_seq IS NULL AND OLD.chain_prev IS NULL
       AND OLD.chain_hash IS NULL AND OLD.sealed_at IS NULL
       AND NEW.chain_seq IS NOT NULL AND NEW.chain_prev IS NOT NULL
       AND NEW.chain_hash IS NOT NULL AND NEW.sealed_at IS NOT NULL
       AND (to_jsonb(NEW) - ARRAY['chain_seq', 'chain_prev', 'chain_hash', 'sealed_at'])
         = (to_jsonb(OLD) - ARRAY['chain_seq', 'chain_prev', 'chain_hash', 'sealed_at'])
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
      'audit_log is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""

_APPEND_ONLY_SQL = """CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
      'audit_log is append-only — UPDATE/DELETE rejected'
      USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;"""


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_seq BIGINT NULL;")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_prev VARCHAR(64) NULL;")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_hash VARCHAR(64) NULL;")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS sealed_at TIMESTAMPTZ NULL;")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_log_tenant_chain_seq "
        "ON audit_log(tenant_id, chain_seq) WHERE chain_seq IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_unsealed "
        "ON audit_log(tenant_id, created_at, id) WHERE chain_seq IS NULL;"
    )
    op.execute(_SEAL_TRIGGER_SQL)
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_chain_anchors (
            tenant_id UUID PRIMARY KEY,
            head_seq BIGINT NOT NULL,
            head_hash VARCHAR(64) NOT NULL,
            sealed_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_audit_chain_anchors_seq CHECK (head_seq > 0)
        );
    """)
    op.execute("ALTER TABLE audit_chain_anchors ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_chain_anchors FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS audit_chain_anchors_tenant_isolation ON audit_chain_anchors;")
    op.execute("""
        CREATE POLICY audit_chain_anchors_tenant_isolation
        ON audit_chain_anchors
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
    """)
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS prompt_digest VARCHAR(64) NULL;")
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS request_digest VARCHAR(64) NULL;")
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS response_digest VARCHAR(64) NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS response_digest;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS request_digest;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS prompt_digest;")
    op.execute("DROP TABLE IF EXISTS audit_chain_anchors;")
    op.execute(_APPEND_ONLY_SQL)
    op.execute("DROP INDEX IF EXISTS ix_audit_log_tenant_unsealed;")
    op.execute("DROP INDEX IF EXISTS ux_audit_log_tenant_chain_seq;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS sealed_at;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_hash;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_prev;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_seq;")
