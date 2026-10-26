# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk outreach: next-contact scheduling and the sweep's tenant listing.

Revision ID: v6z97_onboarding_outreach
Revises: v6z96_onboarding_desk
Create Date: 2026-10-26

Adds ``next_contact_at`` and ``contacted_at`` to ``onboarding_desk_leads`` with
an index for the due-lead query, and a platform-context SELECT policy on
``onboarding_desk_policies`` so the beat sweep can list the tenants that use
the desk without reading any lead.
"""

from alembic import op

revision = "v6z97_onboarding_outreach"
down_revision = "v6z96_onboarding_desk"
branch_labels = None
depends_on = None

PLATFORM_CTX_PREDICATE = "current_setting('agenticorg.platform_ctx', true) = '1'"


def upgrade() -> None:
    op.execute("ALTER TABLE onboarding_desk_leads ADD COLUMN IF NOT EXISTS next_contact_at TIMESTAMPTZ NULL;")
    op.execute("ALTER TABLE onboarding_desk_leads ADD COLUMN IF NOT EXISTS contacted_at TIMESTAMPTZ NULL;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_onboarding_leads_tenant_next_contact "
        "ON onboarding_desk_leads(tenant_id, next_contact_at) WHERE next_contact_at IS NOT NULL;"
    )
    op.execute("DROP POLICY IF EXISTS onboarding_desk_policies_platform_ctx ON onboarding_desk_policies;")
    op.execute(
        "CREATE POLICY onboarding_desk_policies_platform_ctx ON onboarding_desk_policies "
        f"FOR SELECT USING ({PLATFORM_CTX_PREDICATE});"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS onboarding_desk_policies_platform_ctx ON onboarding_desk_policies;")
    op.execute("DROP INDEX IF EXISTS ix_onboarding_leads_tenant_next_contact;")
    op.execute("ALTER TABLE onboarding_desk_leads DROP COLUMN IF EXISTS contacted_at;")
    op.execute("ALTER TABLE onboarding_desk_leads DROP COLUMN IF EXISTS next_contact_at;")
