# SPDX-License-Identifier: Apache-2.0
"""Reviewed differences between the migrated schema and the ORM models.

``test_alembic_e2e.py`` builds an empty database with ``alembic upgrade head``
and compares it with ``core.models`` using Alembic's autogenerate comparison
(tables, columns, types, nullability, indexes, unique constraints and foreign
keys). Every difference must be listed here with its reason, and every entry
here must still be a difference, so the list cannot go stale.

Adding an entry is a review decision, not a way to make the test pass: a
column, type or nullability difference is never allowed, and a new table or
index normally belongs in the ORM model as well as in its migration.
"""

from __future__ import annotations

# Tables created and owned by migrations with no ORM model. Their indexes are
# allowed with them.
MIGRATION_OWNED_TABLES: dict[str, str] = {
    "alembic_migration_progress": "v4_9_7: resumable-migration bookkeeping",
    "billing_subscriptions": "v4_0_0 / v6_z18 / v6_z19: raw-SQL billing state",
    "cdc_triggers": "v4_0_0 / v6_z18 / v6_z19: raw-SQL CDC trigger registry",
    "checkpoint_blobs": "v6_z22: LangGraph Postgres checkpointer schema",
    "checkpoint_migrations": "v6_z22: LangGraph Postgres checkpointer schema",
    "checkpoint_writes": "v6_z22: LangGraph Postgres checkpointer schema",
    "checkpoints": "v6_z22: LangGraph Postgres checkpointer schema",
    "demo_requests": "v6_z14: public demo request capture",
    "health_check_history": "v4_9_8: health check history",
    "knowledge_chunk_sources": "v4_9_4 / v6_z16: multimodal RAG chunk sources",
    "knowledge_documents": "v4_0_0 / v6_z13: knowledge base documents (pgvector columns)",
}

# Indexes the migrations create on ORM tables that the models do not declare:
# foreign-key coverage (``ix_fk_*``, v6_z4), query-performance and trigram
# indexes (v6_z9), ownership indexes (v6_z21), and unique, partial or
# expression indexes written as raw SQL (for example v4_9_10).
MIGRATION_ONLY_INDEXES: frozenset[tuple[str, str]] = frozenset(
    {
        ("a2a_tasks", "ix_a2a_tasks_tenant"),
        ("abm_campaigns", "ix_fk_abm_campaigns_account_id"),
        ("agent_cost_ledger", "ix_fk_agent_cost_ledger_agent_id"),
        ("agent_feedback", "ix_agent_feedback_agent_id"),
        ("agent_feedback", "ix_agent_feedback_tenant_id"),
        ("agent_lifecycle_events", "ix_fk_agent_lifecycle_events_tenant_id"),
        ("agent_lifecycle_events", "ix_fk_agent_lifecycle_events_triggered_by_user"),
        ("agent_task_results", "ix_fk_agent_task_results_company_id"),
        ("agent_team_members", "ix_fk_agent_team_members_agent_id"),
        ("agent_versions", "ix_fk_agent_versions_created_by"),
        ("agent_versions", "ix_fk_agent_versions_tenant_id"),
        ("agents", "ix_agents_owner_user_id"),
        ("agents", "ix_fk_agents_company_id"),
        ("agents", "ix_fk_agents_cost_center_id"),
        ("agents", "ix_fk_agents_parent_agent_id"),
        ("agents", "ix_fk_agents_shadow_comparison_agent_id"),
        ("agents", "uq_agents_industry_pack_company_type"),
        ("api_keys", "ix_fk_api_keys_user_id"),
        ("audit_log", "ix_audit_log_event_type_trgm"),
        ("bridge_registry", "ix_bridge_registry_tenant"),
        ("budget_alerts", "ix_fk_budget_alerts_company_id"),
        ("budget_alerts", "ix_fk_budget_alerts_cost_center_id"),
        ("ca_client_invoices", "ix_ca_client_invoices_status_due"),
        ("ca_client_invoices", "ix_ca_client_invoices_tenant_company"),
        ("ca_client_invoices", "ix_fk_ca_client_invoices_service_plan_id"),
        ("ca_client_payments", "ix_ca_client_payments_invoice"),
        ("client_portal_documents", "ix_client_portal_documents_tenant_company"),
        ("client_portal_invites", "ix_client_portal_invites_email"),
        ("client_portal_invites", "ix_client_portal_invites_tenant_company"),
        ("commerce_c6z_connector_evidence_records", "uq_c6z_connector_evidence_idempotency"),
        ("commerce_c6z_offline_pos_handoff_packets", "uq_c6z_pos_handoff_idempotency"),
        ("commerce_c6z_seller_onboarding_packets", "uq_c6z_onboarding_scope"),
        ("companies", "ix_companies_gstin_trgm"),
        ("companies", "ix_companies_industry_trgm"),
        ("companies", "ix_companies_name_trgm"),
        ("connectors", "ix_connectors_owner_user_id"),
        ("cost_centers", "ix_fk_cost_centers_company_id"),
        ("cost_centers", "ix_fk_cost_centers_department_id"),
        ("departments", "ix_fk_departments_company_id"),
        ("departments", "ix_fk_departments_manager_user_id"),
        ("departments", "ix_fk_departments_parent_id"),
        ("governance_config", "ix_fk_governance_config_updated_by"),
        ("hitl_queue", "ix_fk_hitl_queue_agent_id"),
        ("hitl_queue", "ix_fk_hitl_queue_decision_by"),
        ("hitl_queue", "ix_hitl_queue_requested_by_user_id"),
        ("kpi_cache", "ix_fk_kpi_cache_company_id"),
        ("lead_pipeline", "ix_fk_lead_pipeline_assigned_agent_id"),
        ("oacp_artifact_cache_records", "uq_oacp_cache_artifact_scope"),
        ("oacp_audit_review_manifest_records", "uq_oacp_review_manifest_bundle_retention_scope"),
        ("oacp_operator_decision_records", "uq_oacp_operator_decision_packet_kind_reviewer"),
        (
            "oacp_retention_disposition_decision_records",
            "uq_oacp_retention_disposition_decision_packet_kind_reviewer",
        ),
        ("professional_tax_registrations", "ix_pt_registrations_tenant_company"),
        ("professional_tax_returns", "ix_pt_returns_tenant_company_period"),
        ("prompt_edit_history", "ix_fk_prompt_edit_history_agent_id"),
        ("prompt_edit_history", "ix_fk_prompt_edit_history_edited_by"),
        ("prompt_template_edit_history", "ix_fk_prompt_template_edit_history_edited_by"),
        ("prompt_template_edit_history", "ix_fk_prompt_template_edit_history_template_id"),
        ("prompt_templates", "ix_fk_prompt_templates_created_by"),
        ("report_schedules", "ix_report_schedules_tenant"),
        ("report_schedules", "ix_report_schedules_tenant_company"),
        ("rpa_schedules", "ix_fk_rpa_schedules_company_id"),
        ("rpa_schedules", "ix_rpa_schedules_tenant_name"),
        ("schema_registry", "ix_fk_schema_registry_created_by"),
        ("shadow_comparisons", "ix_fk_shadow_comparisons_reference_agent_id"),
        ("shadow_comparisons", "ix_fk_shadow_comparisons_shadow_agent_id"),
        ("shadow_comparisons", "ix_fk_shadow_comparisons_tenant_id"),
        ("step_executions", "ix_fk_step_executions_agent_id"),
        ("step_executions", "ix_fk_step_executions_tenant_id"),
        ("tool_calls", "ix_fk_tool_calls_agent_id"),
        ("user_delegations", "ix_fk_user_delegations_delegate_id"),
        ("user_delegations", "ix_fk_user_delegations_delegator_id"),
        ("users", "ix_fk_users_department_id"),
        ("workflow_definitions", "ix_fk_workflow_definitions_company_id"),
        ("workflow_definitions", "ix_fk_workflow_definitions_created_by"),
        ("workflow_event_waits", "ix_fk_workflow_event_waits_workflow_run_id"),
        ("workflow_runs", "ix_fk_workflow_runs_company_id"),
    }
)

# Indexes the models declare (``index=True``) that v6_z9 drops as redundant
# with a composite index leading on the same column. See FINDINGS.md.
ORM_INDEXES_DROPPED_BY_MIGRATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("a2a_tasks", "ix_a2a_tasks_tenant_id"),
        ("bridge_registry", "ix_bridge_registry_tenant_id"),
        ("ca_subscriptions", "ix_ca_subscriptions_tenant_id"),
        ("report_schedules", "ix_report_schedules_tenant_id"),
    }
)
