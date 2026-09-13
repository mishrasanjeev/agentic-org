"""Tenant.plan server default: 'enterprise' -> 'free'.

Revision ID: v6z15_tenant_plan_free
Revises: v6z14_demo_requests
Create Date: 2026-09-13

Billing never wrote ``tenants.plan``; the paid default meant every tenant
was invoiced the enterprise base fee. Only the column default changes —
existing rows are NOT rewritten (billing resolves the effective plan from
the provider activation record and treats ``tenants.plan`` as a fallback).
"""

from alembic import op

revision = "v6z15_tenant_plan_free"
down_revision = "v6z14_demo_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ALTER COLUMN plan SET DEFAULT 'free'")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants ALTER COLUMN plan SET DEFAULT 'enterprise'")
