# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: agent identity on agent actions, and agent-ready enrolments.

Revision ID: v6z121_desk_agent_identity
Revises: v6z115_insights_counters
Create Date: 2026-11-04

Contact attempts gain the acting agent's identity identifier, attestation hash and trust tier.
``onboarding_desk_agent_actions`` records each desk agent action and the identity check it ran
under. Consents may now be captured on the ``web`` channel (the agent-enrolment consent) and keep
the consenting person, encrypted. ``onboarding_desk_enrolments`` keeps each agent-ready merchant's
consent grant, what was shared, the provider's reference and status, and when. Both new tables are
tenant-scoped under forced row-level security. Every column is nullable or defaulted, so rows
written with agent identity off are unchanged.
"""

from alembic import op

revision = "v6z121_desk_agent_identity"
down_revision = "v6z115_insights_counters"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"
TABLES = ("onboarding_desk_agent_actions", "onboarding_desk_enrolments")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE});"
    )


def upgrade() -> None:
    for column, kind in (
        ("agent_identity_id", "VARCHAR(256)"),
        ("agent_attestation_sha256", "VARCHAR(80)"),
        ("agent_trust_tier", "VARCHAR(64)"),
    ):
        op.execute(f"ALTER TABLE onboarding_desk_contact_attempts ADD COLUMN IF NOT EXISTS {column} {kind} NULL;")
    op.execute("ALTER TABLE onboarding_desk_consents ADD COLUMN IF NOT EXISTS subject_ciphertext TEXT NULL;")
    op.execute("ALTER TABLE onboarding_desk_consents DROP CONSTRAINT IF EXISTS ck_onboarding_consents_channel;")
    op.execute(
        "ALTER TABLE onboarding_desk_consents ADD CONSTRAINT ck_onboarding_consents_channel "
        "CHECK (channel IN ('sms','email','whatsapp','voice','web'));"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_agent_actions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            lead_id UUID NULL,
            application_id UUID NULL,
            role VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            channel VARCHAR(16) NULL,
            outcome VARCHAR(16) NOT NULL,
            reason VARCHAR(64) NULL,
            agent_identity_id VARCHAR(256) NULL,
            agent_attestation_sha256 VARCHAR(80) NULL,
            agent_trust_tier VARCHAR(64) NULL,
            grant_id VARCHAR(128) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_onboarding_agent_actions_outcome CHECK (outcome IN ('allowed','held','stopped'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_agent_actions_lead "
        "ON onboarding_desk_agent_actions(tenant_id, lead_id, created_at DESC);"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_desk_enrolments (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            application_id UUID NOT NULL,
            lead_id UUID NOT NULL,
            consent_id UUID NOT NULL,
            case_ref VARCHAR(128) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'consent_pending',
            consent_request_id VARCHAR(128) NULL,
            consent_grant_id VARCHAR(128) NULL,
            provider VARCHAR(64) NULL,
            provider_ref VARCHAR(256) NULL,
            provider_status VARCHAR(64) NULL,
            shared_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
            verification_ref JSONB NULL,
            verified_at TIMESTAMPTZ NULL,
            refusal_reason VARCHAR(64) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            shared_at TIMESTAMPTZ NULL,
            revoked_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_onboarding_enrolments_status
                CHECK (status IN ('consent_pending','shared','refused','failed','revoked'))
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_enrolments_live "
        "ON onboarding_desk_enrolments(tenant_id, application_id) WHERE status <> 'revoked';"
    )
    for table in TABLES:
        _rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table};")
    op.execute("DELETE FROM onboarding_desk_consents WHERE channel = 'web';")
    op.execute("ALTER TABLE onboarding_desk_consents DROP CONSTRAINT IF EXISTS ck_onboarding_consents_channel;")
    op.execute(
        "ALTER TABLE onboarding_desk_consents ADD CONSTRAINT ck_onboarding_consents_channel "
        "CHECK (channel IN ('sms','email','whatsapp','voice'));"
    )
    op.execute("ALTER TABLE onboarding_desk_consents DROP COLUMN IF EXISTS subject_ciphertext;")
    for column in ("agent_trust_tier", "agent_attestation_sha256", "agent_identity_id"):
        op.execute(f"ALTER TABLE onboarding_desk_contact_attempts DROP COLUMN IF EXISTS {column};")
