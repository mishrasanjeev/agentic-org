# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights opt-in cross-tenant benchmarks.

Revision ID: v6z117_insights_benchmarks
Revises: v6z116_insights_export_formats
Create Date: 2026-11-04

- ``insights_benchmark_optins``: one row per tenant that chose to take part
  (forced row-level security; an administrator adds or removes the tenant's own
  row).
- ``insights_benchmarks``: the published cohort figures (cohort, metric, p25,
  p50, p75, the number of tenants and the window). No tenant id is stored.
- ``public.insights_benchmark_cohorts(p_from, p_to, p_min_tenants, p_min_runs)``:
  over opted-in, live tenants' production rollups measured in the window, each
  tenant's run success rate, cost per run (USD), approvals per 100 runs and
  average run time, and the 25th, 50th and 75th percentiles per cohort (``all``
  and ``plan:<plan>``). A tenant with fewer than ``p_min_runs`` runs (at least
  100) is not counted, and a cohort with fewer than ``p_min_tenants`` tenants
  (at least 20) is not returned: the floors are enforced here, whatever the
  caller passes. Aggregates only; never a tenant's figure or id.
- ``public.insights_benchmark_participation()``: how many live tenants opted in.

Both functions are owned by ``agenticorg_insights_platform`` (v6z107) with the
same safeguards as v6z114: column grants on exactly the columns read, a
``FOR SELECT`` policy for the role on the row-secured tables, ``SECURITY
DEFINER SET search_path = pg_catalog``, EXECUTE for the installer and
``agenticorg_app`` only, and no membership or schema CREATE left with the
installer.

Downgrade drops the functions, the role's policy and grants, and both tables.
"""

# SQL is built only from the module constants below; there is no runtime input.
# ruff: noqa: S608

from alembic import op

revision = "v6z117_insights_benchmarks"
down_revision = "v6z116_insights_export_formats"
branch_labels = None
depends_on = None

ROLE = "agenticorg_insights_platform"
POLICY = "insights_platform_read"
OPTINS = "insights_benchmark_optins"
COHORTS_SIGNATURE = "public.insights_benchmark_cohorts(date, date, integer, integer)"
PARTICIPATION_SIGNATURE = "public.insights_benchmark_participation()"
FUNCTIONS = (COHORTS_SIGNATURE, PARTICIPATION_SIGNATURE)
MIN_TENANTS = 20
MIN_RUNS = 100
MAX_RANGE_DAYS = 92
# insights_daily columns this revision adds to v6z107's grant (the others are v6z107's).
DAILY_COLUMNS = ("hitl_required", "environment", "data_quality")

COHORTS_SQL = f"""
CREATE FUNCTION public.insights_benchmark_cohorts(p_from date, p_to date, p_min_tenants integer, p_min_runs integer)
RETURNS TABLE(cohort text, metric text, p25 numeric, p50 numeric, p75 numeric, tenants integer)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
#variable_conflict use_column
DECLARE
    v_min_tenants integer := GREATEST(COALESCE(p_min_tenants, {MIN_TENANTS}), {MIN_TENANTS});
    v_min_runs integer := GREATEST(COALESCE(p_min_runs, {MIN_RUNS}), {MIN_RUNS});
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > {MAX_RANGE_DAYS} THEN
        RAISE EXCEPTION 'insights benchmark range must run forwards over at most {MAX_RANGE_DAYS} days'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH per_tenant AS (
        SELECT d.tenant_id AS tid,
               sum(d.runs)::numeric AS runs_n,
               COALESCE(sum(d.runs) FILTER (WHERE d.status = 'completed'), 0)::numeric AS ok_n,
               sum(d.cost_usd)::numeric AS cost_n,
               sum(d.hitl_required)::numeric AS hitl_n,
               sum(d.duration_ms_sum)::numeric AS dur_sum,
               sum(d.duration_n)::numeric AS dur_n
        FROM public.insights_daily d
        JOIN public.{OPTINS} o ON o.tenant_id = d.tenant_id
        WHERE d.day >= p_from AND d.day <= p_to
          AND d.environment = 'production' AND d.data_quality = 'measured'
        GROUP BY d.tenant_id
    ), eligible AS (
        SELECT p.*, t.plan::text AS plan
        FROM per_tenant p
        JOIN public.tenants t ON t.id = p.tid
        WHERE t.deleted_at IS NULL AND p.runs_n >= v_min_runs
    ), figures AS (
        SELECT c.cohort AS cohort_key, v.metric_key, v.val
        FROM eligible e
        CROSS JOIN LATERAL (VALUES ('all'::text), ('plan:' || COALESCE(e.plan, 'none'))) AS c(cohort)
        CROSS JOIN LATERAL (VALUES
            ('success_rate'::text, e.ok_n / e.runs_n * 100),
            ('cost_per_run_usd'::text, e.cost_n / e.runs_n),
            ('approvals_per_100_runs'::text, e.hitl_n / e.runs_n * 100),
            ('avg_run_ms'::text, CASE WHEN e.dur_n > 0 THEN e.dur_sum / e.dur_n END)
        ) AS v(metric_key, val)
        WHERE v.val IS NOT NULL
    )
    SELECT f.cohort_key,
           f.metric_key,
           round(percentile_cont(0.25) WITHIN GROUP (ORDER BY f.val)::numeric, 6),
           round(percentile_cont(0.50) WITHIN GROUP (ORDER BY f.val)::numeric, 6),
           round(percentile_cont(0.75) WITHIN GROUP (ORDER BY f.val)::numeric, 6),
           count(*)::integer
    FROM figures f
    GROUP BY f.cohort_key, f.metric_key
    HAVING count(*) >= v_min_tenants
    ORDER BY f.cohort_key, f.metric_key;
END
$function$
"""

PARTICIPATION_SQL = f"""
CREATE FUNCTION public.insights_benchmark_participation()
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
    SELECT count(*)::integer
    FROM public.{OPTINS} o
    JOIN public.tenants t ON t.id = o.tenant_id
    WHERE t.deleted_at IS NULL
$function$
"""

_FUNCTION_SQL = {COHORTS_SIGNATURE: (COHORTS_SQL, 4, True), PARTICIPATION_SIGNATURE: (PARTICIPATION_SQL, 0, False)}


def _install(signature: str, sql: str, nargs: int, returns_set: bool) -> str:
    retset = "p.proretset" if returns_set else "NOT p.proretset"
    return f"""
            IF to_regprocedure('{signature}') IS NULL THEN
                EXECUTE $ddl${sql}$ddl$;
                REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
                ALTER FUNCTION {signature} OWNER TO {ROLE};
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                WHERE p.oid='{signature}'::regprocedure
                    AND r.rolname='{ROLE}' AND p.prosecdef AND {retset}
                    AND p.pronargs={nargs} AND p.proconfig=ARRAY['search_path=pg_catalog']
            ) THEN
                RAISE EXCEPTION 'Unsafe preexisting insights benchmark function {signature}';
            END IF;
            REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;
            EXECUTE format('GRANT EXECUTE ON FUNCTION {signature} TO %I',current_user);
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                GRANT EXECUTE ON FUNCTION {signature} TO agenticorg_app;
            END IF;
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
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {OPTINS} (
            tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            opted_in_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            opted_in_by UUID NULL
        )
    """)
    op.execute("ALTER TABLE insights_benchmark_optins ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE insights_benchmark_optins FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {OPTINS}_tenant_isolation ON {OPTINS}")
    op.execute(f"""
        CREATE POLICY {OPTINS}_tenant_isolation ON {OPTINS}
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS insights_benchmarks (
            id UUID PRIMARY KEY,
            cohort VARCHAR(64) NOT NULL,
            metric VARCHAR(64) NOT NULL,
            window_from DATE NOT NULL,
            window_to DATE NOT NULL,
            p25 NUMERIC(20, 6) NOT NULL,
            p50 NUMERIC(20, 6) NOT NULL,
            p75 NUMERIC(20, 6) NOT NULL,
            tenants INTEGER NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_insights_benchmarks_tenants CHECK (tenants >= 20),
            CONSTRAINT uq_insights_benchmarks_cohort_metric UNIQUE (cohort, metric)
        )
    """)
    op.execute(f"GRANT SELECT(tenant_id) ON public.{OPTINS} TO {ROLE}")
    op.execute(f"GRANT SELECT({','.join(DAILY_COLUMNS)}) ON public.insights_daily TO {ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON public.{OPTINS}")
    op.execute(f"CREATE POLICY {POLICY} ON public.{OPTINS} FOR SELECT TO {ROLE} USING (true)")
    installs = "".join(_install(sig, *spec) for sig, spec in _FUNCTION_SQL.items())
    op.execute(f"""
        DO $$ BEGIN
            EXECUTE format('GRANT {ROLE} TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO {ROLE};
            {installs}
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


def downgrade() -> None:
    drops = "".join(f"DROP FUNCTION {sig}; " for sig in FUNCTIONS)
    checks = " OR ".join(f"to_regprocedure('{sig}') IS NOT NULL" for sig in FUNCTIONS)
    op.execute(f"""
        DO $$ BEGIN
            IF {checks} THEN
                EXECUTE format('GRANT {ROLE} TO %I',current_user);
                {drops.replace("DROP FUNCTION", "DROP FUNCTION IF EXISTS")}
                EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            END IF;
        END $$
    """)
    # Only this revision's columns: the others stay granted for v6z107's functions.
    op.execute(f"REVOKE SELECT({','.join(DAILY_COLUMNS)}) ON public.insights_daily FROM {ROLE}")
    op.execute("DROP TABLE IF EXISTS insights_benchmarks")
    op.execute(f"DROP TABLE IF EXISTS {OPTINS}")
