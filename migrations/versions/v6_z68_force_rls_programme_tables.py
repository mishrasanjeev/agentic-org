# SPDX-License-Identifier: Apache-2.0
"""Force row-level security on the programme's tenant tables.

Revision ID: v6z68_force_rls_programme_tables
Revises: v6z67_business_settings
Create Date: 2026-10-07

The tables added since ``v6z52`` enabled row-level security but did not
force it, so the table-owning role (the one the local and default
deployments run migrations and the application as) bypassed the tenant
policy. Forcing it closes that gap for every one of them; a table that a
deployment does not have yet is skipped and forced by its own migration.
"""

from alembic import op

revision = "v6z68_force_rls_programme_tables"
down_revision = "v6z67_business_settings"
branch_labels = None
depends_on = None

TABLES = (
    "knowledge_entities",
    "knowledge_retrieval_metrics",
    "model_cards",
    "finops_cost_ledger",
    "finops_thresholds",
    "agent_memories",
    "tool_registrations",
    "agent_debug_sessions",
    "conversation_sessions",
    "content_drafts",
    "content_clauses",
    "idp_documents",
    "workbench_assignments",
    "business_settings",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {table} FORCE ROW LEVEL SECURITY;")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY;")
