"""Industry-pack agent idempotency guard.

Revision ID: v4910_pack_agent_idempotency
Revises: v499_pool_config_noop
Create Date: 2026-05-11

Ramesh 2026-05-11: CA/industry pack installs must be idempotent. A
reinstall created duplicate company-scoped shadow agents because the
installer lookup included ``employee_name`` and ``version``. This
migration suppresses pre-existing duplicate pack agents and adds a
partial unique index so future inserts fail loudly instead of silently
creating ghosts.

Safety:
- Filter is tight: only rows with ``company_id IS NOT NULL`` and
  ``config->pack_install->>source == 'industry_pack'`` are touched.
  Customer-created agents and tenant-default agents are untouched.
- Duplicate suppression uses a soft delete (``status='deleted'``); rows
  remain on disk for auditing.
- The unique index is partial and uses the same predicate, so it never
  conflicts with non-pack or deleted rows.
"""

from __future__ import annotations

from alembic import op

revision = "v4910_pack_agent_idempotency"
down_revision = "v499_pool_config_noop"
branch_labels = None
depends_on = None


_DEDUPE_SQL = """
WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY tenant_id, company_id, agent_type
      ORDER BY
        CASE WHEN status = 'active' THEN 0 ELSE 1 END,
        shadow_sample_count DESC,
        created_at ASC
    ) AS rn
  FROM agents
  WHERE company_id IS NOT NULL
    AND status <> 'deleted'
    AND config->'pack_install'->>'source' = 'industry_pack'
)
UPDATE agents
SET status = 'deleted',
    updated_at = now()
FROM ranked
WHERE agents.id = ranked.id
  AND ranked.rn > 1
"""


_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_industry_pack_company_type
ON agents (tenant_id, company_id, agent_type)
WHERE company_id IS NOT NULL
  AND status <> 'deleted'
  AND config->'pack_install'->>'source' = 'industry_pack'
"""


def upgrade() -> None:
    op.execute(_DEDUPE_SQL)
    op.execute(_INDEX_SQL)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agents_industry_pack_company_type")
