"""Widen audit log action to text.

Revision ID: v6x9_audit_log_action_text
Revises: v6x8_oacp_operator_decisions
Create Date: 2026-06-13

Production incident: creating an agent with API-allowed name and type lengths
could build an audit action longer than VARCHAR(100), causing the whole
request transaction to fail. The audit action is descriptive event text, not a
bounded enum, so the database contract must match the API contract.
"""

from __future__ import annotations

from alembic import op

revision = "v6x9_audit_log_action_text"
down_revision = "v6x8_oacp_operator_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ALTER COLUMN action TYPE TEXT;")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE audit_log ALTER COLUMN action TYPE VARCHAR(100) "
        "USING left(action, 100);"
    )
