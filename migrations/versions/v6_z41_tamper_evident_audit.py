# SPDX-License-Identifier: Apache-2.0
"""Tamper-evident audit: the hash chain over audit rows and the model call digests.

Revision ID: v6z41_tamper_evident_audit
Revises: v6z39_run_spans
Create Date: 2026-10-02

``audit_log`` gains the chain columns the sealing task fills (the sequence
number, the previous link, the link hash and when the row was sealed), a
unique index on the sequence per tenant and an index over the unsealed rows.
``model_gateway_records`` gains the digests of the prompt, of what the model
saw and of what it answered, so a call is tamper-evident without its content
being stored.
"""

from alembic import op

revision = "v6z41_tamper_evident_audit"
down_revision = "v6z40_knowledge_full_text"
branch_labels = None
depends_on = None


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
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS prompt_digest VARCHAR(64) NULL;")
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS request_digest VARCHAR(64) NULL;")
    op.execute("ALTER TABLE model_gateway_records ADD COLUMN IF NOT EXISTS response_digest VARCHAR(64) NULL;")


def downgrade() -> None:
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS response_digest;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS request_digest;")
    op.execute("ALTER TABLE model_gateway_records DROP COLUMN IF EXISTS prompt_digest;")
    op.execute("DROP INDEX IF EXISTS ix_audit_log_tenant_unsealed;")
    op.execute("DROP INDEX IF EXISTS ux_audit_log_tenant_chain_seq;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS sealed_at;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_hash;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_prev;")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS chain_seq;")
