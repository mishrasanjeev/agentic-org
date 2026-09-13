"""Row-level security coverage for every tenant-scoped ORM table.

Revision ID: v6z16_rls_coverage
Revises: v6z15_tenant_plan_free
Create Date: 2026-09-13

Audit 2026-09-13 (enterprise bug sweep, RLS finding): 55 tables that carry a
``tenant_id`` column had no enforced row-level security. Two root causes:

1. ~39 tables were never named in any ``ENABLE ROW LEVEL SECURITY``
   statement (``agents``, ``users``, ``api_keys``, ``audit_log``,
   ``workflow_runs``, ``documents``, ``hitl_queue``, ...).
2. ~16 tables *were* covered by pre-baseline migrations (v4.1-v4.7), but
   the documented fresh-database bootstrap (``scripts/alembic_migrate.py``:
   ``create_all`` + ``stamp v480_baseline``) skips those revisions, so a
   fresh deployment never received the policies.

This migration is idempotent and safe on partial schemas: every table is
guarded with a catalog check, legacy policies with conflicting shapes
(``tenant_isolation`` using a non-``missing_ok`` ``current_setting`` and
the permissive ``company_isolation`` ``company_id IS NULL OR ...`` shape
from v4.1.0) are dropped so they cannot OR-widen the new policy, and the
policy shape matches the existing neighbours (v4.8.0 baseline,
``feed_events``, ``a2a_tasks``):

    USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))

``users`` and ``api_keys`` are looked up *before* a tenant context exists
(login by e-mail, API-key prefix match in the auth middleware). They get a
pre-auth shape that isolates whenever a tenant context IS set and leaves
the no-context lookup readable — the same visibility those tables have
today, plus isolation once the request is tenant-bound.

Operational note: the policies only bite for a DB role that is neither a
superuser nor ``BYPASSRLS``. Deployments must run the API with such a role
for RLS to be an actual control (see docs/adr/0002-multi-tenancy-via-rls.md).
"""

from alembic import op

revision = "v6z16_rls_coverage"
down_revision = "v6z15_tenant_plan_free"
branch_labels = None
depends_on = None

TENANT_POLICY_PREDICATE = "tenant_id::text = current_setting('agenticorg.tenant_id', true)"

# Pre-auth lookup tables: readable when no tenant context has been bound
# (login / API-key resolution), tenant-isolated as soon as one is.
PRE_AUTH_POLICY_PREDICATE = (
    "COALESCE(current_setting('agenticorg.tenant_id', true), '') = '' "
    f"OR {TENANT_POLICY_PREDICATE}"
)

# Tables whose policy must keep the existing tenant + company shape
# (v4.5.0). ORing a tenant-only policy next to it would widen company scope.
TENANT_COMPANY_POLICY_PREDICATE = (
    f"{TENANT_POLICY_PREDICATE} AND ("
    "current_setting('agenticorg.company_id', true) IS NULL "
    "OR current_setting('agenticorg.company_id', true) = '' "
    "OR company_id::text = current_setting('agenticorg.company_id', true))"
)

PRE_AUTH_TABLES: tuple[str, ...] = ("users", "api_keys")

TENANT_COMPANY_TABLES: tuple[str, ...] = ("agent_task_results",)

# Every ORM table with a ``tenant_id`` column that had no enforced policy
# on a freshly bootstrapped database at v6z15 (catalog diff, 2026-09-13).
TENANT_TABLES: tuple[str, ...] = (
    "abm_accounts",
    "abm_campaigns",
    "agent_cost_ledger",
    "agent_lifecycle_events",
    "agent_teams",
    "agent_versions",
    "agents",
    "approval_policies",
    "audit_log",
    "bridge_registry",
    "budget_alerts",
    "ca_subscriptions",
    "cdc_event_dead_letters",
    "cdc_events",
    "commerce_c6z_merchant_configs",
    "companies",
    "compliance_deadlines",
    "connectors",
    "cost_centers",
    "departments",
    "documents",
    "email_sequences",
    "feature_flags",
    "filing_approvals",
    "governance_config",
    "gstn_credentials",
    "gstn_uploads",
    "hitl_queue",
    "industry_pack_installs",
    "invoices",
    "knowledge_chunk_sources",
    "kpi_cache",
    "lead_pipeline",
    "prompt_edit_history",
    "prompt_template_edit_history",
    "prompt_templates",
    "rpa_schedules",
    "schema_registry",
    "shadow_comparisons",
    "sso_configs",
    "step_executions",
    "tenant_ai_credentials",
    "tenant_ai_settings",
    "tenant_branding",
    "tool_calls",
    "user_delegations",
    "weekly_report_pilot_proofs",
    "workflow_definitions",
    "workflow_event_waits",
    "workflow_run_states",
    "workflow_runs",
    "workflow_variants",
)

ALL_TABLES: tuple[str, ...] = TENANT_TABLES + PRE_AUTH_TABLES + TENANT_COMPANY_TABLES

# Tables that already carried an enforced tenant policy on production-shaped
# databases before this revision (v4.1.0-v4.8.0 chain). ``upgrade`` replaces
# their policy with the uniform shape; ``downgrade`` must not strip them —
# doing so would leave sso_configs / invoices / approval_policies readable
# across tenants on a rollback. Their pre-v6z16 protection is equivalent to
# (never stronger than) the uniform policy, so keeping it is the faithful
# and safe undo.
PREVIOUSLY_COVERED_TABLES: frozenset[str] = frozenset(
    {
        # v4.1.0 - v4.3.0 (ENABLE + tenant_isolation)
        "companies",
        "ca_subscriptions",
        "filing_approvals",
        "gstn_uploads",
        "gstn_credentials",
        "compliance_deadlines",
        # v4.5.0 (FORCE + tenant_company_isolation)
        "agent_task_results",
        # v4.6.0 (FORCE + tenant_isolation)
        "departments",
        "cost_centers",
        "user_delegations",
        "budget_alerts",
        # v4.7.0 / v4.8.0 baseline (FORCE + tenant_isolation)
        "sso_configs",
        "approval_policies",
        "invoices",
        "tenant_branding",
        "workflow_variants",
    }
)

# Legacy policy names from v4.1.0 / v4.2.0 / v4.3.0 that must not remain
# next to the uniform policy (permissive policies are ORed together).
LEGACY_POLICY_NAMES: tuple[str, ...] = ("tenant_isolation", "company_isolation")


def _policy_name(table: str) -> str:
    if table in TENANT_COMPANY_TABLES:
        return f"{table}_tenant_company_isolation"
    return f"{table}_tenant_isolation"


def _predicate(table: str) -> str:
    if table in PRE_AUTH_TABLES:
        return PRE_AUTH_POLICY_PREDICATE
    if table in TENANT_COMPANY_TABLES:
        return TENANT_COMPANY_POLICY_PREDICATE
    return TENANT_POLICY_PREDICATE


def _guarded(table: str, body: str) -> str:
    # Table names are hardcoded above — not user input. The DO block keeps
    # the migration safe on partial schemas (table missing => no-op).
    return (
        "DO $$ BEGIN "
        f"IF to_regclass('public.{table}') IS NOT NULL THEN "
        f"{body} "
        "END IF; END $$;"
    )


def upgrade() -> None:
    for table in ALL_TABLES:
        policy = _policy_name(table)
        predicate = _predicate(table)
        statements = [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
        ]
        statements.extend(
            f"DROP POLICY IF EXISTS {legacy} ON {table};" for legacy in LEGACY_POLICY_NAMES
        )
        statements.append(f"DROP POLICY IF EXISTS {policy} ON {table};")
        statements.append(
            f"CREATE POLICY {policy} ON {table} USING ({predicate}) WITH CHECK ({predicate});"
        )
        op.execute(_guarded(table, " ".join(statements)))  # noqa: S608  # nosec B608


def downgrade() -> None:
    # Reverses only what this revision added. Tables that were already
    # RLS-protected before v6z16 keep their (uniform-shape) policy: the
    # pre-v6z16 state was protected, so disabling RLS here would widen
    # tenant isolation on rollback. The legacy permissive
    # ``company_isolation`` shape is deliberately not restored.
    for table in ALL_TABLES:
        if table in PREVIOUSLY_COVERED_TABLES:
            continue
        policy = _policy_name(table)
        body = (
            f"DROP POLICY IF EXISTS {policy} ON {table}; "
            f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY; "
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"
        )
        op.execute(_guarded(table, body))  # noqa: S608  # nosec B608
