# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights operations: the platform view's export and rollup figures, and the landing-view synthetic check.

Revision ID: v6z114_insights_operations
Revises: v6z113_insights_saved_views
Create Date: 2026-11-04

- ``public.insights_platform_operations(p_from, p_to)``: across tenants, the
  export jobs made in the range by status (queued, running, ready, failed,
  expired), the oldest job still queued or running (minutes), and the rollup
  lag: tenants whose newest rollup is older than two hours, and the largest
  such lag (hours). Aggregates only; never a tenant's rows. It is owned by
  ``agenticorg_insights_platform`` (v6z107) with the same safeguards: column
  grants on exactly the columns read, a ``FOR SELECT`` policy for the role on
  the row-secured table, ``SECURITY DEFINER SET search_path = pg_catalog``,
  EXECUTE for the installer and ``agenticorg_app`` only, and no membership or
  schema CREATE left with the installer.
- ``synthetic_checks.kind`` also accepts ``insights_landing`` (an hourly load of
  each landing view against its budget).

Downgrade drops the function, the policy and the grants, and restores the kind
check (refusing while ``insights_landing`` checks exist).
"""

# SQL is built only from the module constants below; there is no runtime input.
# ruff: noqa: S608

from alembic import op

revision = "v6z114_insights_operations"
down_revision = "v6z113_insights_saved_views"
branch_labels = None
depends_on = None

ROLE = "agenticorg_insights_platform"
POLICY = "insights_platform_read"
SIGNATURE = "public.insights_platform_operations(date, date)"
MAX_RANGE_DAYS = 92
LATE_AFTER_HOURS = 2
EXPORT_COLUMNS = ("tenant_id", "status", "created_at")
_KINDS_OLD = "kind IN ('model','knowledge','audit_chain','guardrail','adversarial','eval_dataset')"
_KINDS_NEW = "kind IN ('model','knowledge','audit_chain','guardrail','adversarial','eval_dataset','insights_landing')"

FUNCTION_SQL = f"""
CREATE FUNCTION public.insights_platform_operations(p_from date, p_to date)
RETURNS TABLE(exports_queued bigint, exports_running bigint, exports_ready bigint, exports_failed bigint,
              exports_expired bigint, oldest_open_minutes integer, tenants_late integer, max_lag_hours numeric)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
#variable_conflict use_column
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > {MAX_RANGE_DAYS} THEN
        RAISE EXCEPTION 'insights platform range must run forwards over at most {MAX_RANGE_DAYS} days'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH jobs AS (
        SELECT e.status AS st, e.created_at AS made
        FROM public.insights_exports e
        WHERE e.created_at >= p_from::timestamptz AND e.created_at < (p_to + 1)::timestamptz
    ), latest AS (
        SELECT r.tenant_id AS tid, max(r.rolled_at) AS rolled
        FROM public.insights_rollup_state r
        GROUP BY r.tenant_id
    ), lag AS (
        SELECT extract(epoch FROM (now() - l.rolled)) / 3600.0 AS hours
        FROM latest l
        WHERE l.rolled IS NOT NULL AND l.rolled < now() - interval '{LATE_AFTER_HOURS} hours'
    )
    SELECT
        (SELECT count(*) FROM jobs WHERE st = 'queued')::bigint,
        (SELECT count(*) FROM jobs WHERE st = 'running')::bigint,
        (SELECT count(*) FROM jobs WHERE st = 'ready')::bigint,
        (SELECT count(*) FROM jobs WHERE st = 'failed')::bigint,
        (SELECT count(*) FROM jobs WHERE st = 'expired')::bigint,
        (SELECT (extract(epoch FROM (now() - min(made))) / 60)::integer FROM jobs WHERE st IN ('queued', 'running')),
        (SELECT count(*) FROM lag)::integer,
        (SELECT round(max(hours)::numeric, 1) FROM lag);
END
$function$
"""


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                RAISE EXCEPTION 'v6z107 role {ROLE} is missing';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting insights platform role';
            END IF;
        END $$
    """)
    op.execute(f"GRANT SELECT({','.join(EXPORT_COLUMNS)}) ON public.insights_exports TO {ROLE}")
    # tenant_id is also v6z107's grant (and its policy covers this table); granting it again is a no-op.
    op.execute(f"GRANT SELECT(tenant_id, rolled_at) ON public.insights_rollup_state TO {ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON public.insights_exports")
    op.execute(f"CREATE POLICY {POLICY} ON public.insights_exports FOR SELECT TO {ROLE} USING (true)")
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('GRANT {ROLE} TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO {ROLE};
            IF to_regprocedure('{SIGNATURE}') IS NULL THEN
                EXECUTE $ddl${FUNCTION_SQL}$ddl$;
                REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC;
                ALTER FUNCTION {SIGNATURE} OWNER TO {ROLE};
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                WHERE p.oid='{SIGNATURE}'::regprocedure
                    AND r.rolname='{ROLE}' AND p.prosecdef AND p.proretset
                    AND p.pronargs=2 AND p.proconfig=ARRAY['search_path=pg_catalog']
            ) THEN
                RAISE EXCEPTION 'Unsafe preexisting insights platform function {SIGNATURE}';
            END IF;
            REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC;
            EXECUTE format('GRANT EXECUTE ON FUNCTION {SIGNATURE} TO %I',current_user);
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                GRANT EXECUTE ON FUNCTION {SIGNATURE} TO agenticorg_app;
            END IF;
            REVOKE CREATE ON SCHEMA public FROM {ROLE};
            EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                NULL;
            ELSIF current_setting('server_version_num')::integer >= 160000 THEN
                IF pg_has_role(current_user,'{ROLE}','USAGE') OR pg_has_role(current_user,'{ROLE}','SET') THEN
                    RAISE EXCEPTION 'Installer retained insights platform row access';
                END IF;
            ELSIF pg_has_role(current_user,'{ROLE}','MEMBER') THEN
                RAISE EXCEPTION 'Installer retained insights platform membership';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE synthetic_checks DROP CONSTRAINT IF EXISTS ck_synthetic_checks_kind")
    op.execute(f"ALTER TABLE synthetic_checks ADD CONSTRAINT ck_synthetic_checks_kind CHECK ({_KINDS_NEW})")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM synthetic_checks WHERE kind = 'insights_landing') THEN
                RAISE EXCEPTION 'insights_landing checks exist; delete them before downgrading v6z114';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE synthetic_checks DROP CONSTRAINT IF EXISTS ck_synthetic_checks_kind")
    op.execute(f"ALTER TABLE synthetic_checks ADD CONSTRAINT ck_synthetic_checks_kind CHECK ({_KINDS_OLD})")
    op.execute(f"""
        DO $$ BEGIN
            IF to_regprocedure('{SIGNATURE}') IS NOT NULL THEN
                EXECUTE format('GRANT {ROLE} TO %I',current_user);
                DROP FUNCTION {SIGNATURE};
                EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            END IF;
        END $$
    """)
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON public.insights_exports")
    op.execute(f"REVOKE ALL ON public.insights_exports FROM {ROLE}")
    # Only this revision's column: tenant_id stays granted for v6z107's functions.
    op.execute(f"REVOKE SELECT(rolled_at) ON public.insights_rollup_state FROM {ROLE}")
