"""v4.5.0 — Multi-company RLS on agent_task_results

Adds row-level security policy so a user from Company A cannot query
results from Company B even via direct SQL. Backfills any NULL company_id
records with the tenant's first company before enforcing NOT NULL.

Revision ID: v450_company_rls
Revises: v440_persist_stores
Create Date: 2026-04-11
"""

from alembic import op

revision = "v450_company_rls"
down_revision = "v440_persist_stores"
branch_labels = None
depends_on = None


def upgrade():
    # P2.2: Backfill NULL company_id values before enforcing NOT NULL
    # Strategy: pick first company per tenant; rows with no company stay NULL
    # and will be excluded by RLS (visible only to admin/superuser).
    op.execute("""
        UPDATE agent_task_results r
        SET company_id = (
            SELECT id FROM companies c
            WHERE c.tenant_id = r.tenant_id
            ORDER BY c.created_at ASC
            LIMIT 1
        )
        WHERE r.company_id IS NULL;
    """)

    # Enable RLS on agent_task_results
    op.execute("ALTER TABLE agent_task_results ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE agent_task_results FORCE ROW LEVEL SECURITY;")

    # Drop existing policy if it exists (idempotent)
    op.execute("DROP POLICY IF EXISTS agent_task_results_tenant_company_isolation ON agent_task_results;")

    # Combined tenant + company isolation policy
    op.execute("""
        CREATE POLICY agent_task_results_tenant_company_isolation ON agent_task_results
        USING (
            tenant_id::text = current_setting('agenticorg.tenant_id', true)
            AND (
                current_setting('agenticorg.company_id', true) IS NULL
                OR current_setting('agenticorg.company_id', true) = ''
                OR company_id::text = current_setting('agenticorg.company_id', true)
            )
        );
    """)

    # Add an index on company_id for the RLS lookup performance
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_results_company "
        "ON agent_task_results(company_id) WHERE company_id IS NOT NULL;"
    )


def downgrade():
    op.execute("DROP POLICY IF EXISTS agent_task_results_tenant_company_isolation ON agent_task_results;")
    op.execute("ALTER TABLE agent_task_results NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE agent_task_results DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP INDEX IF EXISTS ix_agent_results_company;")
