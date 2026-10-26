# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: applications, applicant documents and hand-offs to a person.

Revision ID: v6z98_onboarding_applications
Revises: v6z97_onboarding_outreach
Create Date: 2026-10-26

One application per lead, its answers and transcript encrypted under the
tenant key; uploaded documents encrypted the same way, with the values they
suggest, kept with the time when the applicant removes one; and requests to talk to a person, at most one open per application.
All three tables are tenant-scoped under forced row-level security.
"""

from alembic import op

revision = "v6z98_onboarding_applications"
down_revision = "v6z97_onboarding_outreach"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"
TABLES = ("onboarding_desk_applications", "onboarding_desk_documents", "onboarding_desk_handoffs")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_applications (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            lead_id UUID NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'in_progress',
            merchant_type VARCHAR(64) NOT NULL DEFAULT 'general',
            country VARCHAR(2) NOT NULL,
            region VARCHAR(2) NOT NULL,
            locale VARCHAR(2) NOT NULL DEFAULT 'en',
            answers_ciphertext TEXT NULL,
            transcript_ciphertext TEXT NULL,
            answered_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            pending_item VARCHAR(32) NULL,
            last_channel VARCHAR(16) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NULL,
            submitted_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_onboarding_applications_status CHECK (status IN ('in_progress','submitted','withdrawn'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_applications_lead "
        "ON onboarding_desk_applications(tenant_id, lead_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_applications_status "
        "ON onboarding_desk_applications(tenant_id, status, created_at DESC);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_documents (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            application_id UUID NOT NULL,
            item VARCHAR(32) NOT NULL,
            filename VARCHAR(100) NOT NULL,
            mime_type VARCHAR(32) NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 VARCHAR(64) NOT NULL,
            content_ciphertext TEXT NOT NULL,
            extraction_status VARCHAR(16) NOT NULL,
            suggestions_ciphertext TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at TIMESTAMPTZ NULL
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_documents_application "
        "ON onboarding_desk_documents(tenant_id, application_id);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_handoffs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            application_id UUID NOT NULL,
            lead_id UUID NOT NULL,
            channel VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            message_ciphertext TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ NULL,
            resolved_by VARCHAR(128) NULL,
            resolution_note VARCHAR(500) NULL,
            CONSTRAINT ck_onboarding_handoffs_status CHECK (status IN ('open','resolved'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_handoffs_open "
        "ON onboarding_desk_handoffs(tenant_id, application_id) WHERE status = 'open';"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_handoffs_status "
        "ON onboarding_desk_handoffs(tenant_id, status, created_at DESC);"
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
