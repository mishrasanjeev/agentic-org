# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights platform view: cross-tenant aggregates through two SECURITY DEFINER functions.

Revision ID: v6z107_insights_platform
Revises: v6z106_insights_foundation
Create Date: 2026-11-01

The platform operator (``AGENTICORG_PLATFORM_OPERATOR_TENANT_ID``) sees
platform totals and one row per tenant, never a tenant's rows. Both come from
functions owned by the new NOLOGIN NOINHERIT NOBYPASSRLS role
``agenticorg_insights_platform``, created the way v6z85 and v6z105 create
the spend aggregate role and its functions: column-level SELECT on exactly the
columns the functions read, a ``FOR SELECT TO agenticorg_insights_platform
USING (true)`` policy on each row-secured table it reads (FORCE applies to the
owner too), ``SECURITY DEFINER SET search_path = pg_catalog`` with fully
qualified names, refusal of an unsafe pre-existing role or function, EXECUTE
for the installer and ``agenticorg_app`` (where it exists) only, and no
membership or schema CREATE left with the installer.

- ``public.insights_platform_daily(p_from, p_to)``: per day, the tenants with
  runs, runs, failed runs, tokens and cost (USD).
- ``public.insights_platform_tenants(p_from, p_to)``: per live tenant, its
  name and plan, runs, failed runs and cost in the range, active agents, the
  last day with runs (on or before ``p_to``), the number of consecutive most
  recent rollup days with status ``reconciled`` and the latest rollup status.

Both refuse a range longer than 92 days (or reversed, or missing a bound).
No table is created or altered.

Downgrade drops the functions, the read policies and the grants. The inert
NOLOGIN role is kept, as v6z85 keeps its role: a role is cluster-wide and may
serve another database.
"""

# SQL is built only from the module constants above (role, policy, table, column
# and function names); there is no runtime input.
# ruff: noqa: S608

from alembic import op

revision = "v6z107_insights_platform"
down_revision = "v6z106_insights_foundation"
branch_labels = None
depends_on = None

ROLE = "agenticorg_insights_platform"
POLICY = "insights_platform_read"
MAX_RANGE_DAYS = 92

# Table -> the columns the functions read. ``tenants`` has no row-level
# security, so it gets the column grant only.
READS: dict[str, tuple[str, ...]] = {
    "insights_daily": ("tenant_id", "day", "status", "runs", "tokens_total", "cost_usd", "duration_ms_sum", "duration_n"),
    "insights_rollup_state": ("tenant_id", "day", "status"),
    "tenants": ("id", "name", "plan", "created_at", "deleted_at"),
    "agents": ("tenant_id", "status"),
}
POLICY_TABLES = ("insights_daily", "insights_rollup_state", "agents")

DAILY_SIGNATURE = "public.insights_platform_daily(date, date)"
TENANTS_SIGNATURE = "public.insights_platform_tenants(date, date)"
FUNCTIONS = (DAILY_SIGNATURE, TENANTS_SIGNATURE)

_RANGE_GUARD = f"""
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > {MAX_RANGE_DAYS} THEN
        RAISE EXCEPTION 'insights platform range must run forwards over at most {MAX_RANGE_DAYS} days'
            USING ERRCODE = '22023';
    END IF;
"""

DAILY_FUNCTION_SQL = f"""
CREATE FUNCTION public.insights_platform_daily(p_from date, p_to date)
RETURNS TABLE(day date, tenants_active integer, runs bigint, failed bigint, tokens bigint, cost_usd numeric)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
#variable_conflict use_column
BEGIN
{_RANGE_GUARD}
    RETURN QUERY
    SELECT d.day,
           (count(DISTINCT d.tenant_id) FILTER (WHERE d.runs > 0))::integer,
           COALESCE(sum(d.runs), 0)::bigint,
           COALESCE(sum(d.runs) FILTER (WHERE d.status = 'failed'), 0)::bigint,
           COALESCE(sum(d.tokens_total), 0)::bigint,
           COALESCE(sum(d.cost_usd), 0)::numeric
    FROM public.insights_daily d
    WHERE d.day >= p_from AND d.day <= p_to
    GROUP BY d.day
    ORDER BY d.day;
END
$function$
"""

TENANTS_FUNCTION_SQL = f"""
CREATE FUNCTION public.insights_platform_tenants(p_from date, p_to date)
RETURNS TABLE(tenant_id uuid, tenant_name text, plan text, runs bigint, failed bigint, cost_usd numeric,
              active_agents integer, last_active_day date, reconciled_nights integer, last_rollup_status text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
#variable_conflict use_column
BEGIN
{_RANGE_GUARD}
    RETURN QUERY
    WITH range_usage AS (
        SELECT d.tenant_id AS tid,
               sum(d.runs)::bigint AS runs_n,
               COALESCE(sum(d.runs) FILTER (WHERE d.status = 'failed'), 0)::bigint AS failed_n,
               sum(d.cost_usd) AS cost_n
        FROM public.insights_daily d
        WHERE d.day >= p_from AND d.day <= p_to
        GROUP BY d.tenant_id
    ), agent_counts AS (
        SELECT a.tenant_id AS tid, count(*)::integer AS active_n
        FROM public.agents a
        WHERE a.status = 'active'
        GROUP BY a.tenant_id
    ), rollups AS (
        SELECT r.tenant_id AS tid, r.day AS rday, r.status AS rstatus,
               row_number() OVER (PARTITION BY r.tenant_id ORDER BY r.day DESC) AS rn,
               max(r.day) OVER (PARTITION BY r.tenant_id) AS last_day
        FROM public.insights_rollup_state r
        WHERE r.day <= p_to
    ), streaks AS (
        -- The streak ends at the first most-recent day that is not reconciled
        -- or that leaves a gap (a day with no rollup row).
        SELECT s.tid,
               COALESCE(min(s.rn) FILTER (WHERE s.rstatus <> 'reconciled'
                                             OR s.rday <> s.last_day - (s.rn - 1)::integer) - 1,
                        count(*))::integer AS nights,
               max(s.rstatus) FILTER (WHERE s.rn = 1) AS latest_status
        FROM rollups s
        GROUP BY s.tid
    )
    SELECT t.id,
           t.name::text,
           t.plan::text,
           COALESCE(u.runs_n, 0)::bigint,
           COALESCE(u.failed_n, 0)::bigint,
           COALESCE(u.cost_n, 0)::numeric,
           COALESCE(ac.active_n, 0)::integer,
           (SELECT max(d2.day) FROM public.insights_daily d2
             WHERE d2.tenant_id = t.id AND d2.day <= p_to AND d2.runs > 0),
           COALESCE(st.nights, 0)::integer,
           st.latest_status::text
    FROM public.tenants t
    LEFT JOIN range_usage u ON u.tid = t.id
    LEFT JOIN agent_counts ac ON ac.tid = t.id
    LEFT JOIN streaks st ON st.tid = t.id
    WHERE t.deleted_at IS NULL
    ORDER BY COALESCE(u.runs_n, 0) DESC, t.name, t.id;
END
$function$
"""

_FUNCTION_SQL = {DAILY_SIGNATURE: DAILY_FUNCTION_SQL, TENANTS_SIGNATURE: TENANTS_FUNCTION_SQL}


def _create_or_verify(signature: str) -> str:
    """One DO-block fragment: create the function owned by the role, or prove the existing one is ours."""
    return f"""
            IF to_regprocedure('{signature}') IS NULL THEN
                EXECUTE $ddl${_FUNCTION_SQL[signature]}$ddl$;
                REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
                ALTER FUNCTION {signature} OWNER TO {ROLE};
            ELSE
                IF NOT EXISTS (
                    SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                    WHERE p.oid='{signature}'::regprocedure
                        AND r.rolname='{ROLE}' AND p.prosecdef AND p.proretset
                        AND p.pronargs=2 AND p.proconfig=ARRAY['search_path=pg_catalog']
                ) THEN
                    RAISE EXCEPTION 'Unsafe preexisting insights platform function {signature}';
                END IF;
            END IF;
            REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
            EXECUTE format('GRANT EXECUTE ON FUNCTION {signature} TO %I',current_user);
            -- docs/RLS_APP_ROLE.md: a migration adding a function the application
            -- calls grants it itself, so a run after scripts/create_app_role.sql
            -- leaves the same privileges.
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                GRANT EXECUTE ON FUNCTION {signature} TO agenticorg_app;
            END IF;
"""


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
                RAISE EXCEPTION 'Unsafe preexisting insights platform role';
            END IF;
        END $$
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table, columns in READS.items():
        op.execute(f"GRANT SELECT({','.join(columns)}) ON public.{table} TO {ROLE}")
    # FORCE ROW LEVEL SECURITY applies to the function owner too: the role
    # reads every tenant's rows of these tables, and only through the functions.
    for table in POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON public.{table}")
        op.execute(f"CREATE POLICY {POLICY} ON public.{table} FOR SELECT TO {ROLE} USING (true)")
    # As in v6z85: transferring ownership needs temporary schema CREATE and
    # membership, and the installer must not keep either afterwards.
    body = "".join(_create_or_verify(signature) for signature in FUNCTIONS)
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('GRANT {ROLE} TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO {ROLE};
{body}
            REVOKE CREATE ON SCHEMA public FROM {ROLE};
            EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                -- A superuser has implicit access independently of membership.
                NULL;
            ELSIF current_setting('server_version_num')::integer >= 160000 THEN
                IF pg_has_role(current_user,'{ROLE}','USAGE')
                    OR pg_has_role(current_user,'{ROLE}','SET') THEN
                    RAISE EXCEPTION 'Installer retained insights platform row access';
                END IF;
            ELSIF pg_has_role(current_user,'{ROLE}','MEMBER') THEN
                RAISE EXCEPTION 'Installer retained insights platform membership';
            END IF;
        END $$
    """)


def downgrade() -> None:
    drops = "".join(
        f"""
            IF to_regprocedure('{signature}') IS NOT NULL THEN
                DROP FUNCTION {signature};
            END IF;"""
        for signature in FUNCTIONS
    )
    op.execute(f"""
        DO $$ BEGIN
            IF to_regrole('{ROLE}') IS NOT NULL THEN
                EXECUTE format('GRANT {ROLE} TO %I',current_user);
{drops}
                EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            END IF;
        END $$
    """)
    op.execute(f"""
        DO $$ BEGIN
            IF to_regrole('{ROLE}') IS NOT NULL THEN
                {"".join(f"DROP POLICY IF EXISTS {POLICY} ON public.{table}; " for table in POLICY_TABLES)}
                {"".join(f"REVOKE ALL ON public.{table} FROM {ROLE}; " for table in READS)}
                REVOKE ALL ON SCHEMA public FROM {ROLE};
            END IF;
        END $$
    """)
