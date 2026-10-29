# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: leads, per-channel consents, suppressions, sources, policy, attempts.

Revision ID: v6z96_onboarding_desk
Revises: v6z95_network_listings
Create Date: 2026-10-26

Six tenant-scoped tables under forced row-level security. A lead's contact
details are stored encrypted; keyed hashes match opt-outs. Consents keep the
exact wording, its hash, the capture method and source, and whether consent
was captured in writing.
"""

from alembic import op

revision = "v6z96_onboarding_desk"
down_revision = "v6z95_network_listings"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"
TABLES = (
    "onboarding_desk_lead_sources",
    "onboarding_desk_leads",
    "onboarding_desk_consents",
    "onboarding_desk_suppressions",
    "onboarding_desk_policies",
    "onboarding_desk_contact_attempts",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_lead_sources (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(120) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            secret_ciphertext TEXT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_onboarding_sources_kind CHECK (kind IN ('webhook','form','csv','crm','manual'))
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_leads (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            source_id UUID NULL,
            source_kind VARCHAR(16) NOT NULL DEFAULT 'manual',
            external_ref VARCHAR(128) NULL,
            business_name VARCHAR(200) NOT NULL,
            country VARCHAR(2) NOT NULL,
            region VARCHAR(2) NOT NULL,
            locale VARCHAR(2) NOT NULL DEFAULT 'en',
            merchant_type VARCHAR(64) NOT NULL DEFAULT 'general',
            contact_ciphertext TEXT NOT NULL,
            email_hash VARCHAR(64) NULL,
            phone_hash VARCHAR(64) NULL,
            email_masked VARCHAR(260) NULL,
            phone_masked VARCHAR(16) NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'new',
            created_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_onboarding_leads_country CHECK (country IN ('US','CA')),
            CONSTRAINT ck_onboarding_leads_locale CHECK (locale IN ('en','fr')),
            CONSTRAINT ck_onboarding_leads_status
                CHECK (status IN ('new','contacted','engaged','applying','submitted','closed','opted_out'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_leads_tenant_created ON onboarding_desk_leads(tenant_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_leads_tenant_email_hash ON onboarding_desk_leads(tenant_id, email_hash);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_leads_tenant_phone_hash ON onboarding_desk_leads(tenant_id, phone_hash);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_leads_source_ref "
        "ON onboarding_desk_leads(tenant_id, source_id, external_ref) WHERE external_ref IS NOT NULL;"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_consents (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            lead_id UUID NOT NULL,
            channel VARCHAR(16) NOT NULL,
            purpose VARCHAR(32) NOT NULL DEFAULT 'onboarding',
            wording TEXT NOT NULL,
            wording_sha256 VARCHAR(64) NOT NULL,
            method VARCHAR(32) NOT NULL,
            source_ref VARCHAR(512) NOT NULL DEFAULT '',
            written BOOLEAN NOT NULL DEFAULT FALSE,
            captured_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'granted',
            withdrawn_at TIMESTAMPTZ NULL,
            withdrawn_via VARCHAR(32) NULL,
            recorded_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_onboarding_consents_channel CHECK (channel IN ('sms','email','whatsapp','voice')),
            CONSTRAINT ck_onboarding_consents_status CHECK (status IN ('granted','withdrawn'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_consents_lead_channel "
        "ON onboarding_desk_consents(tenant_id, lead_id, channel);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_suppressions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            contact_hash VARCHAR(64) NOT NULL,
            channel VARCHAR(16) NOT NULL DEFAULT 'all',
            reason VARCHAR(32) NOT NULL,
            source VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_onboarding_suppressions_channel
                CHECK (channel IN ('all','sms','email','whatsapp','voice'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_suppressions_hash_channel "
        "ON onboarding_desk_suppressions(tenant_id, contact_hash, channel);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_policies (
            tenant_id UUID PRIMARY KEY,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_contact_attempts (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            lead_id UUID NOT NULL,
            channel VARCHAR(16) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'first_contact',
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            refusal_reason VARCHAR(48) NULL,
            provider_ref VARCHAR(128) NULL,
            attempt_no INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sent_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_onboarding_attempts_status CHECK (status IN ('queued','sent','failed','refused'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_attempts_lead_created "
        "ON onboarding_desk_contact_attempts(tenant_id, lead_id, created_at DESC);"
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
