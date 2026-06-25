# ruff: noqa: S608
"""Backfill missing leading-column indexes for foreign keys.

Revision ID: v6z4_fk_index_coverage
Revises: v6z3_merchant_config_selfsvc
Create Date: 2026-06-25

The production schema has accumulated some FK columns without a leading
index. Each index below is created only when no valid existing index already
starts with the FK column, avoiding duplicate indexes from older migrations
that used different names.
"""

from __future__ import annotations

from alembic import op

revision = "v6z4_fk_index_coverage"
down_revision = "v6z3_merchant_config_selfsvc"
branch_labels = None
depends_on = None


_FK_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_fk_departments_company_id", "departments", "company_id"),
    ("ix_fk_departments_parent_id", "departments", "parent_id"),
    ("ix_fk_departments_manager_user_id", "departments", "manager_user_id"),
    ("ix_fk_users_department_id", "users", "department_id"),
    ("ix_fk_abm_campaigns_account_id", "abm_campaigns", "account_id"),
    ("ix_fk_api_keys_tenant_id", "api_keys", "tenant_id"),
    ("ix_fk_api_keys_user_id", "api_keys", "user_id"),
    ("ix_fk_documents_tenant_id", "documents", "tenant_id"),
    ("ix_fk_governance_config_updated_by", "governance_config", "updated_by"),
    ("ix_fk_prompt_templates_created_by", "prompt_templates", "created_by"),
    ("ix_fk_schema_registry_created_by", "schema_registry", "created_by"),
    ("ix_fk_user_delegations_delegator_id", "user_delegations", "delegator_id"),
    ("ix_fk_user_delegations_delegate_id", "user_delegations", "delegate_id"),
    ("ix_fk_agent_task_results_company_id", "agent_task_results", "company_id"),
    ("ix_fk_ca_client_invoices_service_plan_id", "ca_client_invoices", "service_plan_id"),
    ("ix_fk_cost_centers_company_id", "cost_centers", "company_id"),
    ("ix_fk_cost_centers_department_id", "cost_centers", "department_id"),
    ("ix_fk_kpi_cache_company_id", "kpi_cache", "company_id"),
    ("ix_fk_prompt_template_edit_history_template_id", "prompt_template_edit_history", "template_id"),
    ("ix_fk_prompt_template_edit_history_edited_by", "prompt_template_edit_history", "edited_by"),
    ("ix_fk_rpa_schedules_company_id", "rpa_schedules", "company_id"),
    ("ix_fk_workflow_definitions_company_id", "workflow_definitions", "company_id"),
    ("ix_fk_workflow_definitions_created_by", "workflow_definitions", "created_by"),
    ("ix_fk_agents_company_id", "agents", "company_id"),
    ("ix_fk_agents_parent_agent_id", "agents", "parent_agent_id"),
    ("ix_fk_agents_shadow_comparison_agent_id", "agents", "shadow_comparison_agent_id"),
    ("ix_fk_agents_cost_center_id", "agents", "cost_center_id"),
    ("ix_fk_budget_alerts_company_id", "budget_alerts", "company_id"),
    ("ix_fk_budget_alerts_cost_center_id", "budget_alerts", "cost_center_id"),
    ("ix_fk_workflow_runs_company_id", "workflow_runs", "company_id"),
    ("ix_fk_workflow_runs_workflow_def_id", "workflow_runs", "workflow_def_id"),
    ("ix_fk_agent_cost_ledger_agent_id", "agent_cost_ledger", "agent_id"),
    ("ix_fk_agent_lifecycle_events_tenant_id", "agent_lifecycle_events", "tenant_id"),
    ("ix_fk_agent_lifecycle_events_agent_id", "agent_lifecycle_events", "agent_id"),
    ("ix_fk_agent_lifecycle_events_triggered_by_user", "agent_lifecycle_events", "triggered_by_user"),
    ("ix_fk_agent_team_members_agent_id", "agent_team_members", "agent_id"),
    ("ix_fk_agent_versions_tenant_id", "agent_versions", "tenant_id"),
    ("ix_fk_agent_versions_created_by", "agent_versions", "created_by"),
    ("ix_fk_hitl_queue_tenant_id", "hitl_queue", "tenant_id"),
    ("ix_fk_hitl_queue_agent_id", "hitl_queue", "agent_id"),
    ("ix_fk_hitl_queue_decision_by", "hitl_queue", "decision_by"),
    ("ix_fk_lead_pipeline_assigned_agent_id", "lead_pipeline", "assigned_agent_id"),
    ("ix_fk_prompt_edit_history_agent_id", "prompt_edit_history", "agent_id"),
    ("ix_fk_prompt_edit_history_edited_by", "prompt_edit_history", "edited_by"),
    ("ix_fk_shadow_comparisons_tenant_id", "shadow_comparisons", "tenant_id"),
    ("ix_fk_shadow_comparisons_shadow_agent_id", "shadow_comparisons", "shadow_agent_id"),
    ("ix_fk_shadow_comparisons_reference_agent_id", "shadow_comparisons", "reference_agent_id"),
    ("ix_fk_step_executions_tenant_id", "step_executions", "tenant_id"),
    ("ix_fk_step_executions_agent_id", "step_executions", "agent_id"),
    ("ix_fk_tool_calls_agent_id", "tool_calls", "agent_id"),
    ("ix_fk_workflow_event_waits_workflow_run_id", "workflow_event_waits", "workflow_run_id"),
    ("ix_fk_email_sequences_tenant_id", "email_sequences", "tenant_id"),
)


def _create_fk_index_if_missing(index_name: str, table_name: str, column_name: str) -> None:
    sql = f"""
        DO $$
        BEGIN
            IF to_regclass('{table_name}') IS NOT NULL
               AND EXISTS (
                    SELECT 1
                    FROM pg_class t
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    JOIN pg_attribute a
                      ON a.attrelid = t.oid
                     AND a.attname = '{column_name}'
                     AND a.attisdropped IS FALSE
                    WHERE n.nspname = current_schema()
                      AND t.relname = '{table_name}'
               )
               AND NOT EXISTS (
                    SELECT 1
                    FROM pg_index i
                    JOIN pg_class t ON t.oid = i.indrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    JOIN pg_attribute a
                      ON a.attrelid = t.oid
                     AND a.attname = '{column_name}'
                     AND a.attisdropped IS FALSE
                    WHERE n.nspname = current_schema()
                      AND t.relname = '{table_name}'
                      AND i.indisvalid
                      AND i.indisready
                      AND i.indkey[0] = a.attnum
               ) THEN
                EXECUTE format(
                    'CREATE INDEX %I ON %I (%I)',
                    '{index_name}',
                    '{table_name}',
                    '{column_name}'
                );
            END IF;
        END $$;
    """
    op.execute(sql)


def upgrade() -> None:
    for index_name, table_name, column_name in _FK_INDEXES:
        _create_fk_index_if_missing(index_name, table_name, column_name)


def downgrade() -> None:
    for index_name, _, _ in reversed(_FK_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {index_name};")
