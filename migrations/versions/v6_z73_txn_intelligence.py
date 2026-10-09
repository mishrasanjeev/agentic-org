# SPDX-License-Identifier: Apache-2.0
"""Transaction intelligence: records and findings.

Revision ID: v6z73_txn_intelligence
Revises: v6z72_speech_redactions
Create Date: 2026-10-07

``txn_records``: one movement on one account, idempotent under its
reference (``core/txn/records.py``). ``txn_findings``: what the detectors
raised, kept once under its fingerprint, under human disposition
(``core/txn/findings.py``). Both tenant scoped under forced row-level
policies, every foreign-key-like lookup column under a leading index.
"""

from alembic import op

revision = "v6z73_txn_intelligence"
down_revision = "v6z72_speech_redactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS txn_records (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            record_ref VARCHAR(128) NOT NULL,
            account VARCHAR(64) NOT NULL,
            customer_ref VARCHAR(64) NULL,
            counterparty VARCHAR(64) NULL,
            counterparty_name VARCHAR(200) NULL,
            direction VARCHAR(8) NOT NULL,
            amount NUMERIC(18, 2) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'INR',
            channel VARCHAR(16) NOT NULL DEFAULT 'other',
            branch VARCHAR(64) NULL,
            booked_at TIMESTAMPTZ NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source VARCHAR(64) NOT NULL DEFAULT 'api',
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_records_tenant_ref ON txn_records(tenant_id, record_ref);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_txn_records_tenant_account_booked ON txn_records(tenant_id, account, booked_at);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_records_tenant_customer ON txn_records(tenant_id, customer_ref);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_records_tenant_counterparty ON txn_records(tenant_id, counterparty);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_records_tenant_booked ON txn_records(tenant_id, booked_at);")
    op.execute("ALTER TABLE txn_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE txn_records FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS txn_records_tenant_isolation ON txn_records;")
    op.execute(
        """
        CREATE POLICY txn_records_tenant_isolation
        ON txn_records
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS txn_findings (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            kind VARCHAR(32) NOT NULL,
            entity_kind VARCHAR(16) NOT NULL DEFAULT 'account',
            entity_ref VARCHAR(64) NOT NULL,
            severity VARCHAR(16) NOT NULL DEFAULT 'medium',
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            summary TEXT NOT NULL DEFAULT '',
            facts JSONB NOT NULL DEFAULT '{}'::jsonb,
            record_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            fingerprint VARCHAR(64) NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            disposition JSONB NOT NULL DEFAULT '{}'::jsonb,
            case_ref VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_findings_tenant_fingerprint ON txn_findings(tenant_id, fingerprint);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_findings_tenant_status ON txn_findings(tenant_id, status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_findings_tenant_entity ON txn_findings(tenant_id, entity_ref);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_txn_findings_tenant_detected ON txn_findings(tenant_id, detected_at);")
    op.execute("ALTER TABLE txn_findings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE txn_findings FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS txn_findings_tenant_isolation ON txn_findings;")
    op.execute(
        """
        CREATE POLICY txn_findings_tenant_isolation
        ON txn_findings
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS txn_findings;")
    op.execute("DROP TABLE IF EXISTS txn_records;")
