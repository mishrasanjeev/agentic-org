# SPDX-License-Identifier: Apache-2.0
"""Personalisation: consents, profiles, rules and render events.

Revision ID: v6z77_personalisation
Revises: v6z76_lineage_sync
Create Date: 2026-10-08

``personalisation_consents``: the current consent of a subject for one
purpose (granted or withdrawn, with its expiry and evidence).
``personalisation_profiles``: a subject's attributes, encrypted for the
tenant. ``personalisation_rules``: conditions on attributes and the
variant they select, with the attributes the rule may use.
``personalisation_events``: every render and refusal with the consent,
the rule, the attribute names used and a hash of the output
(``core/personalisation/service.py``). All tenant scoped under forced
row-level policies; every foreign key carries a leading index.
"""

from alembic import op

revision = "v6z77_personalisation"
down_revision = "v6z76_lineage_sync"
branch_labels = None
depends_on = None

TABLES = (
    "personalisation_consents",
    "personalisation_profiles",
    "personalisation_rules",
    "personalisation_events",
)


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {table}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true));
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personalisation_consents (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            subject_ref VARCHAR(128) NOT NULL,
            purpose VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'granted',
            granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NULL,
            withdrawn_at TIMESTAMPTZ NULL,
            evidence VARCHAR(500) NOT NULL DEFAULT '',
            recorded_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_personalisation_consents_status CHECK (status IN ('granted', 'withdrawn'))
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_personalisation_consents_tenant_subject_purpose "
        "ON personalisation_consents(tenant_id, subject_ref, purpose);"
    )
    _tenant_policy("personalisation_consents")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personalisation_profiles (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            subject_ref VARCHAR(128) NOT NULL,
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_personalisation_profiles_tenant_subject "
        "ON personalisation_profiles(tenant_id, subject_ref);"
    )
    _tenant_policy("personalisation_profiles")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personalisation_rules (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            purpose VARCHAR(64) NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            enabled BOOLEAN NOT NULL DEFAULT true,
            conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
            variant JSONB NOT NULL DEFAULT '{}'::jsonb,
            allowed_attributes JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_by VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_personalisation_rules_tenant_name "
        "ON personalisation_rules(tenant_id, name);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personalisation_rules_tenant_purpose "
        "ON personalisation_rules(tenant_id, purpose, priority);"
    )
    _tenant_policy("personalisation_rules")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personalisation_events (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            subject_ref VARCHAR(128) NOT NULL,
            purpose VARCHAR(64) NOT NULL,
            consent_id UUID NULL REFERENCES personalisation_consents(id) ON DELETE SET NULL,
            rule_id UUID NULL REFERENCES personalisation_rules(id) ON DELETE SET NULL,
            attributes_used JSONB NOT NULL DEFAULT '[]'::jsonb,
            content_hash VARCHAR(64) NOT NULL DEFAULT '',
            channel VARCHAR(32) NOT NULL DEFAULT '',
            outcome VARCHAR(16) NOT NULL,
            refusal VARCHAR(64) NOT NULL DEFAULT '',
            actor VARCHAR(128) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_personalisation_events_outcome CHECK (outcome IN ('rendered', 'refused'))
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_personalisation_events_consent ON personalisation_events(consent_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_personalisation_events_rule ON personalisation_events(rule_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personalisation_events_tenant_subject "
        "ON personalisation_events(tenant_id, subject_ref, created_at);"
    )
    _tenant_policy("personalisation_events")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS personalisation_events;")
    op.execute("DROP TABLE IF EXISTS personalisation_rules;")
    op.execute("DROP TABLE IF EXISTS personalisation_profiles;")
    op.execute("DROP TABLE IF EXISTS personalisation_consents;")
