# ruff: noqa: S608
"""Repair leading foreign-key indexes missing from legacy databases.

Revision ID: v6z10_legacy_fk_indexes
Revises: v6z9_query_performance
Create Date: 2026-08-09

Some production databases were stamped past historical CA-firm migrations
without receiving the single-column indexes emitted by current ORM metadata.
The post-migration catalog audit correctly rejects those schemas. Build the
missing indexes concurrently so the repair does not block writes to live
tables.
"""

from __future__ import annotations

from alembic import op

revision = "v6z10_legacy_fk_indexes"
down_revision = "v6z9_query_performance"
branch_labels = None
depends_on = None


_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_ca_client_invoices_company_id", "ca_client_invoices", "company_id"),
    ("ix_ca_client_payments_company_id", "ca_client_payments", "company_id"),
    ("ix_ca_client_payments_invoice_id", "ca_client_payments", "invoice_id"),
    ("ix_client_portal_documents_company_id", "client_portal_documents", "company_id"),
    ("ix_client_portal_invites_company_id", "client_portal_invites", "company_id"),
    ("ix_filing_approvals_company_id", "filing_approvals", "company_id"),
    ("ix_gstn_credentials_tenant_id", "gstn_credentials", "tenant_id"),
    ("ix_gstn_uploads_company_id", "gstn_uploads", "company_id"),
    (
        "ix_professional_tax_registrations_company_id",
        "professional_tax_registrations",
        "company_id",
    ),
    (
        "ix_professional_tax_returns_company_id",
        "professional_tax_returns",
        "company_id",
    ),
    (
        "ix_professional_tax_returns_registration_id",
        "professional_tax_returns",
        "registration_id",
    ),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, table_name, column_name in _INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
                f"ON {table_name} ({column_name})"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, _, _ in reversed(_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
