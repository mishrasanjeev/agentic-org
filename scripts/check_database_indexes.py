"""Fail fast on PostgreSQL index correctness and query-index regressions.

Usage::

    python scripts/check_database_indexes.py

The command reads ``AGENTICORG_DB_URL``.  It checks the effective database
catalog rather than ORM declarations, so it catches migration/bootstrap drift,
invalid indexes, unindexed foreign keys, and exact duplicate index structures.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from sqlalchemy import create_engine, text


REQUIRED_QUERY_INDEXES = {
    "ix_abm_accounts_tenant_domain_lower",
    "ix_abm_accounts_tenant_industry_intent",
    "ix_abm_accounts_tenant_intent",
    "ix_abm_accounts_tenant_tier_intent",
    "ix_agent_feedback_agent_tenant_created",
    "ix_agent_lifecycle_agent_status_created",
    "ix_agent_teams_tenant_created",
    "ix_agent_versions_agent_created",
    "ix_agents_tenant_company_created",
    "ix_agents_tenant_created",
    "ix_agents_tenant_domain_created",
    "ix_agents_tenant_status_created",
    "ix_api_keys_active_prefix",
    "ix_api_keys_tenant_created",
    "ix_audit_log_event_type_trgm",
    "ix_audit_log_tenant_actor",
    "ix_audit_log_tenant_agent_created",
    "ix_audit_log_tenant_company_created",
    "ix_audit_log_tenant_created",
    "ix_audit_log_tool_connector_created",
    "ix_audit_log_tool_outcome_created",
    "ix_companies_gstin_trgm",
    "ix_companies_industry_trgm",
    "ix_companies_name_trgm",
    "ix_companies_tenant_active_name",
    "ix_companies_tenant_name",
    "ix_compliance_deadlines_scope_due",
    "ix_connector_configs_company_id",
    "ix_connectors_tenant_category_live",
    "ix_c6z_capability_scope_checked",
    "ix_c6z_connector_evidence_scope_synced",
    "ix_c6z_merchant_config_scope_created",
    "ix_c6z_onboarding_scope_created",
    "ix_documents_tenant_created_live",
    "ix_documents_tenant_filename_live",
    "ix_email_sequences_tenant_status_sent",
    "ix_filing_approvals_scope_created",
    "ix_gstn_uploads_scope_created",
    "ix_hitl_queue_tenant_status_created",
    "ix_lead_pipeline_tenant_score_created",
    "ix_lead_pipeline_tenant_stage_score_created",
    "ix_schema_registry_tenant_created",
    "ix_workflow_definitions_tenant_company_created",
    "ix_workflow_definitions_tenant_created",
    "ix_workflow_definitions_tenant_domain_created",
    "ix_workflow_runs_definition_tenant_created",
    "ix_workflow_runs_tenant_status_created",
}


_MISSING_FK_SQL = text(
    """
    SELECT child.relname AS table_name,
           constraint_row.conname AS constraint_name,
           attribute_row.attname AS column_name
    FROM pg_constraint constraint_row
    JOIN pg_class child ON child.oid = constraint_row.conrelid
    JOIN pg_namespace namespace_row ON namespace_row.oid = child.relnamespace
    JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY key_column(attnum, position)
      ON key_column.position = 1
    JOIN pg_attribute attribute_row
      ON attribute_row.attrelid = child.oid
     AND attribute_row.attnum = key_column.attnum
    WHERE namespace_row.nspname = current_schema()
      AND constraint_row.contype = 'f'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_index index_row
          WHERE index_row.indrelid = child.oid
            AND index_row.indisvalid
            AND index_row.indisready
            AND index_row.indkey[0] = key_column.attnum
      )
    ORDER BY child.relname, constraint_row.conname
    """
)


_DUPLICATE_INDEX_SQL = text(
    """
    WITH indexes AS (
        SELECT index_row.indexrelid,
               index_row.indrelid,
               table_row.relname AS table_name,
               index_class.relname AS index_name,
               index_row.indisunique,
               index_row.indisprimary,
               index_row.indkey::text AS key_attnums,
               index_row.indclass::text AS opclasses,
               index_row.indcollation::text AS collations,
               index_row.indoption::text AS options,
               pg_get_expr(index_row.indexprs, index_row.indrelid) AS expressions,
               pg_get_expr(index_row.indpred, index_row.indrelid) AS predicate,
               access_method.amname
        FROM pg_index index_row
        JOIN pg_class table_row ON table_row.oid = index_row.indrelid
        JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
        JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
        JOIN pg_am access_method ON access_method.oid = index_class.relam
        WHERE namespace_row.nspname = current_schema()
          AND index_row.indisvalid
          AND index_row.indisready
    )
    SELECT left_index.table_name,
           left_index.index_name,
           right_index.index_name
    FROM indexes left_index
    JOIN indexes right_index
      ON left_index.indrelid = right_index.indrelid
     AND left_index.indexrelid < right_index.indexrelid
     AND left_index.key_attnums = right_index.key_attnums
     AND left_index.opclasses = right_index.opclasses
     AND left_index.collations = right_index.collations
     AND left_index.options = right_index.options
     AND COALESCE(left_index.expressions, '') = COALESCE(right_index.expressions, '')
     AND COALESCE(left_index.predicate, '') = COALESCE(right_index.predicate, '')
     AND left_index.amname = right_index.amname
    ORDER BY left_index.table_name, left_index.index_name
    """
)


_DUPLICATE_FK_SQL = text(
    """
    SELECT child.relname, left_fk.conname, right_fk.conname
    FROM pg_constraint left_fk
    JOIN pg_constraint right_fk
      ON left_fk.conrelid = right_fk.conrelid
     AND left_fk.oid < right_fk.oid
     AND left_fk.contype = 'f'
     AND right_fk.contype = 'f'
     AND left_fk.conkey = right_fk.conkey
     AND left_fk.confrelid = right_fk.confrelid
     AND left_fk.confkey = right_fk.confkey
    JOIN pg_class child ON child.oid = left_fk.conrelid
    JOIN pg_namespace namespace_row ON namespace_row.oid = child.relnamespace
    WHERE namespace_row.nspname = current_schema()
    ORDER BY child.relname, left_fk.conname
    """
)


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _format_rows(rows: Sequence[object]) -> str:
    return "\n".join(
        f"  - {row if isinstance(row, str) else tuple(row)}" for row in rows
    )


def audit_database(database_url: str) -> list[str]:
    """Return human-readable catalog failures for ``database_url``."""
    engine = create_engine(_sync_url(database_url), pool_pre_ping=True)
    failures: list[str] = []
    try:
        with engine.connect() as connection:
            missing_fk = connection.execute(_MISSING_FK_SQL).all()
            duplicate_indexes = connection.execute(_DUPLICATE_INDEX_SQL).all()
            duplicate_fks = connection.execute(_DUPLICATE_FK_SQL).all()
            invalid_indexes = connection.execute(
                text(
                    """
                    SELECT table_row.relname, index_row.relname
                    FROM pg_index catalog_row
                    JOIN pg_class table_row ON table_row.oid = catalog_row.indrelid
                    JOIN pg_class index_row ON index_row.oid = catalog_row.indexrelid
                    JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND (NOT catalog_row.indisvalid OR NOT catalog_row.indisready)
                    ORDER BY table_row.relname, index_row.relname
                    """
                )
            ).all()
            present = set(
                connection.execute(
                    text(
                        """
                        SELECT index_class.relname
                        FROM pg_index index_row
                        JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
                        JOIN pg_class table_row ON table_row.oid = index_row.indrelid
                        JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
                        WHERE namespace_row.nspname = current_schema()
                          AND index_row.indisvalid
                          AND index_row.indisready
                        """
                    )
                ).scalars()
            )
    finally:
        engine.dispose()

    if missing_fk:
        failures.append("foreign keys without a leading index:\n" + _format_rows(missing_fk))
    if duplicate_indexes:
        failures.append("structurally duplicate indexes:\n" + _format_rows(duplicate_indexes))
    if duplicate_fks:
        failures.append("structurally duplicate foreign keys:\n" + _format_rows(duplicate_fks))
    if invalid_indexes:
        failures.append("invalid or not-ready indexes:\n" + _format_rows(invalid_indexes))
    missing_required = sorted(REQUIRED_QUERY_INDEXES - present)
    if missing_required:
        failures.append("missing required query indexes:\n" + _format_rows(missing_required))
    return failures


def main() -> int:
    database_url = os.getenv("AGENTICORG_DB_URL")
    if not database_url:
        print("AGENTICORG_DB_URL is required", file=sys.stderr)
        return 2

    failures = audit_database(database_url)
    if failures:
        print("DATABASE INDEX AUDIT FAILED\n\n" + "\n\n".join(failures), file=sys.stderr)
        return 1

    print(
        "DATABASE INDEX AUDIT PASSED: all foreign keys are covered, "
        "all query-contract indexes are valid, and no exact duplicates remain."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
