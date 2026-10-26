# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: link each submitted application to its governed case.

Revision ID: v6z99_onboarding_cases
Revises: v6z98_onboarding_applications
Create Date: 2026-10-26

Adds the case reference, when the case was opened, and why opening it last
failed, with an index for finding an application by its case.
"""

from alembic import op

revision = "v6z99_onboarding_cases"
down_revision = "v6z98_onboarding_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE onboarding_desk_applications ADD COLUMN IF NOT EXISTS case_ref VARCHAR(128) NULL;")
    op.execute("ALTER TABLE onboarding_desk_applications ADD COLUMN IF NOT EXISTS case_opened_at TIMESTAMPTZ NULL;")
    op.execute("ALTER TABLE onboarding_desk_applications ADD COLUMN IF NOT EXISTS case_error VARCHAR(64) NULL;")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_onboarding_applications_case "
        "ON onboarding_desk_applications(tenant_id, case_ref) WHERE case_ref IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_onboarding_applications_case;")
    op.execute("ALTER TABLE onboarding_desk_applications DROP COLUMN IF EXISTS case_error;")
    op.execute("ALTER TABLE onboarding_desk_applications DROP COLUMN IF EXISTS case_opened_at;")
    op.execute("ALTER TABLE onboarding_desk_applications DROP COLUMN IF EXISTS case_ref;")
