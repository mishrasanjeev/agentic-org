"""Add prompt_template_edit_history for real template governance.

Revision ID: v489_tpl_edit_history
Revises: v488_report_schedule_comp
Create Date: 2026-04-22

Codex 2026-04-22 audit gap #8 — marketing copy claimed prompt
templates had "full edit history, rollback, and RBAC", but the only
history table (``prompt_edit_history``) recorded agent prompt
changes, not template-level ones. This migration adds a parallel
table for template edits so the governance claim has teeth.

Idempotent — ``CREATE TABLE IF NOT EXISTS`` + guarded index.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers — kept <=32 chars.
revision = "v489_tpl_edit_history"
# Chained onto v488 after the report_schedule_comp migration merged to
# main in #275. Originally authored against v487; rebased post-merge.
down_revision = "v488_report_schedule_comp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Same defensive pattern as v4_8_8: the FK references assume
    # ``tenants``, ``users`` and ``prompt_templates`` exist. On a fresh
    # environment where ORM-created tables haven't run yet, the
    # CREATE TABLE fails with "relation does not exist". Skip the
    # migration body entirely when any referenced table is missing —
    # the table will be created on a subsequent run once the ORM has
    # stood up the prerequisites.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'prompt_templates'
            ) THEN
                RAISE NOTICE 'skipping v489: prompt_templates not yet created';
                RETURN;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'prompt_template_edit_history'
            ) THEN
                CREATE TABLE prompt_template_edit_history (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES tenants(id),
                    template_id UUID NOT NULL REFERENCES prompt_templates(id),
                    edited_by UUID NULL REFERENCES users(id),
                    name_before VARCHAR(255) NULL,
                    template_text_before TEXT NULL,
                    variables_before JSONB NULL,
                    description_before TEXT NULL,
                    name_after VARCHAR(255) NULL,
                    template_text_after TEXT NULL,
                    variables_after JSONB NULL,
                    description_after TEXT NULL,
                    change_reason VARCHAR(500) NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'prompt_template_edit_history'
                  AND indexname = 'idx_prompt_template_edit_history_template'
            ) THEN
                CREATE INDEX idx_prompt_template_edit_history_template
                    ON prompt_template_edit_history (tenant_id, template_id, created_at);
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS idx_prompt_template_edit_history_template;
    """)
    op.execute("""
        DROP TABLE IF EXISTS prompt_template_edit_history;
    """)
