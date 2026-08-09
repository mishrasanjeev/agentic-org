# ruff: noqa: S608
"""Align PostgreSQL indexes with production query shapes.

Revision ID: v6z9_query_performance
Revises: v6z8_shadow_hitl
Create Date: 2026-08-09

The earlier FK coverage migration protects referential-integrity operations,
but a leading FK column alone does not cover tenant-scoped pagination,
ordering, queue reads, or substring search.  This migration adds the
composite/partial indexes used by those hot paths and removes exact duplicate
indexes left by the legacy SQL + ORM bootstrap combination.
"""

from __future__ import annotations

from alembic import op

revision = "v6z9_query_performance"
down_revision = "v6z8_shadow_hitl"
branch_labels = None
depends_on = None


_BTREE_INDEXES: tuple[tuple[str, str], ...] = (
    # Append-only audit history: tenant pagination plus the optional filters
    # exposed by /audit and /audit/enforcement.
    (
        "ix_audit_log_tenant_created",
        "ON audit_log (tenant_id, created_at DESC)",
    ),
    (
        "ix_audit_log_tenant_agent_created",
        "ON audit_log (tenant_id, agent_id, created_at DESC)",
    ),
    (
        "ix_audit_log_tenant_company_created",
        "ON audit_log (tenant_id, company_id, created_at DESC)",
    ),
    (
        "ix_audit_log_tenant_actor",
        "ON audit_log (tenant_id, actor_id)",
    ),
    (
        "ix_audit_log_tool_outcome_created",
        "ON audit_log (tenant_id, outcome, created_at DESC) "
        "WHERE resource_type = 'tool_call'",
    ),
    (
        "ix_audit_log_tool_connector_created",
        "ON audit_log (tenant_id, (details->>'connector'), created_at DESC) "
        "WHERE resource_type = 'tool_call'",
    ),
    # Approval queue reads always scope by tenant/status and return newest
    # first.  The same prefix accelerates the KPI pending-approval join.
    (
        "ix_hitl_queue_tenant_status_created",
        "ON hitl_queue (tenant_id, status, created_at DESC)",
    ),
    # Recent feedback drives shadow learning.  Leading agent_id also closes
    # the FK coverage gap introduced after v6z4.
    (
        "ix_agent_feedback_agent_tenant_created",
        "ON agent_feedback (agent_id, tenant_id, created_at DESC)",
    ),
    # API-key authentication must never scan all tenants' key hashes.
    (
        "ix_api_keys_active_prefix",
        "ON api_keys (prefix) WHERE status = 'active'",
    ),
    (
        "ix_api_keys_tenant_created",
        "ON api_keys (tenant_id, created_at DESC)",
    ),
    (
        "ix_agent_lifecycle_agent_status_created",
        "ON agent_lifecycle_events (agent_id, to_status, created_at DESC)",
    ),
    (
        "ix_agent_versions_agent_created",
        "ON agent_versions (agent_id, created_at DESC)",
    ),
    (
        "ix_documents_tenant_filename_live",
        "ON documents (tenant_id, filename) WHERE status <> 'deleted'",
    ),
    (
        "ix_documents_tenant_created_live",
        "ON documents (tenant_id, created_at DESC) WHERE status <> 'deleted'",
    ),
    (
        "ix_workflow_runs_definition_tenant_created",
        "ON workflow_runs (workflow_def_id, tenant_id, created_at DESC)",
    ),
    (
        "ix_workflow_runs_tenant_status_created",
        "ON workflow_runs (tenant_id, status, created_at DESC)",
    ),
    (
        "ix_companies_tenant_name",
        "ON companies (tenant_id, name)",
    ),
    (
        "ix_companies_tenant_active_name",
        "ON companies (tenant_id, is_active, name)",
    ),
    # Agent/workflow registries are paginated newest-first and optionally
    # narrowed by the same dimensions used by RBAC and company scope.
    (
        "ix_agents_tenant_created",
        "ON agents (tenant_id, created_at DESC)",
    ),
    (
        "ix_agents_tenant_status_created",
        "ON agents (tenant_id, status, created_at DESC)",
    ),
    (
        "ix_agents_tenant_domain_created",
        "ON agents (tenant_id, domain, created_at DESC)",
    ),
    (
        "ix_agents_tenant_company_created",
        "ON agents (tenant_id, company_id, created_at DESC)",
    ),
    (
        "ix_agent_teams_tenant_created",
        "ON agent_teams (tenant_id, created_at DESC)",
    ),
    (
        "ix_workflow_definitions_tenant_created",
        "ON workflow_definitions (tenant_id, created_at DESC)",
    ),
    (
        "ix_workflow_definitions_tenant_domain_created",
        "ON workflow_definitions (tenant_id, domain, created_at DESC)",
    ),
    (
        "ix_workflow_definitions_tenant_company_created",
        "ON workflow_definitions (tenant_id, company_id, created_at DESC)",
    ),
    (
        "ix_schema_registry_tenant_created",
        "ON schema_registry (tenant_id, created_at DESC)",
    ),
    # ABM and sales dashboards combine filtering, scoring and recency.
    (
        "ix_abm_accounts_tenant_intent",
        "ON abm_accounts (tenant_id, intent_score DESC)",
    ),
    (
        "ix_abm_accounts_tenant_tier_intent",
        "ON abm_accounts (tenant_id, tier, intent_score DESC)",
    ),
    (
        "ix_abm_accounts_tenant_industry_intent",
        "ON abm_accounts (tenant_id, lower(industry), intent_score DESC)",
    ),
    (
        "ix_abm_accounts_tenant_domain_lower",
        "ON abm_accounts (tenant_id, lower(domain))",
    ),
    (
        "ix_lead_pipeline_tenant_score_created",
        "ON lead_pipeline (tenant_id, score DESC, created_at DESC)",
    ),
    (
        "ix_lead_pipeline_tenant_stage_score_created",
        "ON lead_pipeline (tenant_id, stage, score DESC, created_at DESC)",
    ),
    (
        "ix_email_sequences_tenant_status_sent",
        "ON email_sequences (tenant_id, status, sent_at)",
    ),
    # Company operations pages scope by tenant+company and then sort.
    (
        "ix_filing_approvals_scope_created",
        "ON filing_approvals (tenant_id, company_id, created_at DESC)",
    ),
    (
        "ix_gstn_uploads_scope_created",
        "ON gstn_uploads (tenant_id, company_id, created_at DESC)",
    ),
    (
        "ix_compliance_deadlines_scope_due",
        "ON compliance_deadlines (tenant_id, company_id, due_date)",
    ),
    # Commerce runtime reads are merchant-scoped and time ordered.  The old
    # schema had one index per column, which required bitmap scans + sorts.
    (
        "ix_c6z_onboarding_scope_created",
        "ON commerce_c6z_seller_onboarding_packets "
        "(tenant_id, merchant_id, seller_agent_id, created_at DESC)",
    ),
    (
        "ix_c6z_connector_evidence_scope_synced",
        "ON commerce_c6z_connector_evidence_records "
        "(tenant_id, merchant_id, seller_agent_id, synced_at DESC)",
    ),
    (
        "ix_c6z_capability_scope_checked",
        "ON commerce_c6z_provider_capability_evidence "
        "(tenant_id, merchant_id, seller_agent_id, checked_at DESC)",
    ),
    (
        "ix_c6z_merchant_config_scope_created",
        "ON commerce_c6z_merchant_configs "
        "(tenant_id, merchant_id, seller_agent_id, created_at DESC)",
    ),
    (
        "ix_connectors_tenant_category_live",
        "ON connectors (tenant_id, category) "
        "WHERE COALESCE(status, 'active') <> 'deleted'",
    ),
    # ConnectorConfig's common lookup is already covered by its partial
    # unique indexes.  This leading company index is specifically for the FK.
    (
        "ix_connector_configs_company_id",
        "ON connector_configs (company_id)",
    ),
)


_TRIGRAM_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_audit_log_event_type_trgm",
        "ON audit_log USING gin (event_type gin_trgm_ops)",
    ),
    (
        "ix_companies_name_trgm",
        "ON companies USING gin (name gin_trgm_ops)",
    ),
    (
        "ix_companies_gstin_trgm",
        "ON companies USING gin (gstin gin_trgm_ops)",
    ),
    (
        "ix_companies_industry_trgm",
        "ON companies USING gin (industry gin_trgm_ops)",
    ),
)


_REDUNDANT_INDEXES: tuple[str, ...] = (
    "ix_a2a_tasks_task_id",
    "ix_a2a_tasks_tenant_id",
    "ix_bridge_registry_tenant_id",
    "ix_ca_subscriptions_tenant_id",
    "ix_feed_events_tenant_sequence",
    "ix_report_schedules_tenant_id",
    "idx_agents_tenant_domain",
    "idx_lead_pipeline_tenant",
    # Superseded by the same prefix plus created_at ordering above.
    "idx_wf_runs_tenant_status",
)


def _drop_duplicate_connector_config_fk() -> None:
    """Remove only a structurally duplicate auto-named company FK.

    Fresh bootstrap creates the FK from ORM metadata before v6z6 adds its
    canonical named constraint.  Existing upgraded databases normally have
    only the canonical constraint, so the guarded repair is a no-op there.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint automatic
                JOIN pg_constraint canonical
                  ON canonical.conrelid = automatic.conrelid
                 AND canonical.contype = 'f'
                 AND canonical.conkey = automatic.conkey
                 AND canonical.confrelid = automatic.confrelid
                 AND canonical.confkey = automatic.confkey
                 AND canonical.conname = 'fk_connector_configs_company_id'
                WHERE automatic.conrelid = 'connector_configs'::regclass
                  AND automatic.contype = 'f'
                  AND automatic.conname = 'connector_configs_company_id_fkey'
            ) THEN
                ALTER TABLE connector_configs
                DROP CONSTRAINT connector_configs_company_id_fkey;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # Required for the two user-facing substring searches.  GIN trigram
    # indexes are the only practical PostgreSQL index for leading-wildcard
    # ILIKE predicates.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Large audit/queue tables must remain writable while indexes build.
    # Dropping the new name first also repairs a not-valid index left by an
    # interrupted CREATE INDEX CONCURRENTLY before Alembic retries the revision.
    with op.get_context().autocommit_block():
        for index_name, definition in (*_BTREE_INDEXES, *_TRIGRAM_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
            op.execute(f"CREATE INDEX CONCURRENTLY {index_name} {definition}")

        for index_name in _REDUNDANT_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")

    _drop_duplicate_connector_config_fk()


def downgrade() -> None:
    # Restore only indexes that predated this revision.  The duplicate FK is
    # intentionally not recreated: duplicate constraints are never useful.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_wf_runs_tenant_status "
            "ON workflow_runs (tenant_id, status)"
        )
        for index_name, _ in reversed((*_TRIGRAM_INDEXES, *_BTREE_INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")
