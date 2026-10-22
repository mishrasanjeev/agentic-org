# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant custom domains served through Firebase Hosting.

Revision ID: v6z93_custom_domain_firebase
Revises: v6z92_c6z_demo_source
Create Date: 2026-10-22

* ``merchant_custom_domains.provider``: how the host reaches the API,
  ``cloud_run_mapping`` (every existing row, the default) or
  ``firebase_hosting`` (Firebase Hosting rewrites the agent paths to the API;
  AgenticOrg creates the domain through the Firebase Hosting API).
* ``mapping_source`` admits ``firebase_hosting``.

Additive on a tenant-scoped table that already has forced row-level security
(v6z91); the policies are unchanged. ``downgrade`` removes the column and
restores the earlier check once no row uses Firebase Hosting.
"""

from alembic import op

revision = "v6z93_custom_domain_firebase"
down_revision = "v6z92_c6z_demo_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE merchant_custom_domains "
        "ADD COLUMN IF NOT EXISTS provider VARCHAR(24) NOT NULL DEFAULT 'cloud_run_mapping';"
    )
    op.execute("ALTER TABLE merchant_custom_domains DROP CONSTRAINT IF EXISTS ck_merchant_custom_domains_provider;")
    op.execute(
        "ALTER TABLE merchant_custom_domains ADD CONSTRAINT ck_merchant_custom_domains_provider "
        "CHECK (provider IN ('cloud_run_mapping', 'firebase_hosting'));"
    )
    op.execute(
        "ALTER TABLE merchant_custom_domains DROP CONSTRAINT IF EXISTS ck_merchant_custom_domains_mapping_source;"
    )
    op.execute(
        "ALTER TABLE merchant_custom_domains ADD CONSTRAINT ck_merchant_custom_domains_mapping_source "
        "CHECK (mapping_source IS NULL OR mapping_source IN ('operator', 'cloud_run_api', 'firebase_hosting'));"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE merchant_custom_domains DROP CONSTRAINT IF EXISTS ck_merchant_custom_domains_mapping_source;"
    )
    op.execute(
        "ALTER TABLE merchant_custom_domains ADD CONSTRAINT ck_merchant_custom_domains_mapping_source "
        "CHECK (mapping_source IS NULL OR mapping_source IN ('operator', 'cloud_run_api'));"
    )
    op.execute("ALTER TABLE merchant_custom_domains DROP CONSTRAINT IF EXISTS ck_merchant_custom_domains_provider;")
    op.execute("ALTER TABLE merchant_custom_domains DROP COLUMN IF EXISTS provider;")
