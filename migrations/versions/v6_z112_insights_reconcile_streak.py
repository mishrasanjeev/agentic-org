# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights platform view: today's pending rollup no longer resets the reconciled-nights streak.

Revision ID: v6z112_insights_reconcile_streak
Revises: v6z111_report_schedule_runs
Create Date: 2026-11-03

``public.insights_platform_tenants`` (v6z107) counts the consecutive most
recent rollup days with status ``reconciled``. The rollup re-marks the
tenant's current day ``rolled`` every 15 minutes, and yesterday stays
``rolled`` until the reconcile after 02:30 tenant-local, so the newest row was
almost always pending and the streak read 0: the Phase 1 gate (14 reconciled
nights) could never show as met.

The function now skips ``rolled`` rows dated on or after the day before
``p_to``: those days are still open or not yet due for reconciliation. A day
older than that which is still ``rolled`` (the reconcile never ran) still ends
the streak, as do a ``mismatch`` and a gap. ``last_rollup_status`` is the
status of the newest row the streak considered.

The function keeps its owner (``agenticorg_insights_platform``), signature,
``SECURITY DEFINER``, ``search_path`` and grants; only its body changes, with
the owner's membership held for the replacement and dropped afterwards, as
v6z107 does. Downgrade restores the v6z107 body.
"""

# SQL is built only from the module constants below; there is no runtime input.
# ruff: noqa: S608

from alembic import op

revision = "v6z112_insights_reconcile_streak"
down_revision = "v6z111_report_schedule_runs"
branch_labels = None
depends_on = None

ROLE = "agenticorg_insights_platform"
SIGNATURE = "public.insights_platform_tenants(date, date)"
MAX_RANGE_DAYS = 92

V6Z107_ROLLUP_FILTER = "r.day <= p_to"
PENDING_SKIPPED_FILTER = "r.day <= p_to AND NOT (r.status = 'rolled' AND r.day >= p_to - 1)"

_TENANTS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.insights_platform_tenants(p_from date, p_to date)
RETURNS TABLE(tenant_id uuid, tenant_name text, plan text, runs bigint, failed bigint, cost_usd numeric,
              active_agents integer, last_active_day date, reconciled_nights integer, last_rollup_status text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
#variable_conflict use_column
BEGIN
    IF p_from IS NULL OR p_to IS NULL OR p_to < p_from OR p_to - p_from > {max_days} THEN
        RAISE EXCEPTION 'insights platform range must run forwards over at most {max_days} days'
            USING ERRCODE = '22023';
    END IF;
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
        WHERE {rollup_filter}
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


def _replace(rollup_filter: str) -> None:
    ddl = _TENANTS_FUNCTION_SQL.format(max_days=MAX_RANGE_DAYS, rollup_filter=rollup_filter)
    op.execute(f"""
        DO $$ BEGIN
            IF to_regprocedure('{SIGNATURE}') IS NULL THEN
                RAISE EXCEPTION 'insights platform function {SIGNATURE} is missing';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                WHERE p.oid='{SIGNATURE}'::regprocedure
                    AND r.rolname='{ROLE}' AND p.prosecdef AND p.proretset
                    AND p.pronargs=2 AND p.proconfig=ARRAY['search_path=pg_catalog']
            ) THEN
                RAISE EXCEPTION 'Unsafe preexisting insights platform function {SIGNATURE}';
            END IF;
            -- Replacing the owner's function needs its membership and schema
            -- CREATE for it; the installer keeps neither (as in v6z107, v6z108).
            EXECUTE format('GRANT {ROLE} TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO {ROLE};
            EXECUTE $ddl${ddl}$ddl$;
            REVOKE CREATE ON SCHEMA public FROM {ROLE};
            EXECUTE format('REVOKE {ROLE} FROM %I',current_user);
            REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC;
            IF NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                WHERE p.oid='{SIGNATURE}'::regprocedure
                    AND r.rolname='{ROLE}' AND p.prosecdef AND p.proretset
                    AND p.pronargs=2 AND p.proconfig=ARRAY['search_path=pg_catalog']
            ) THEN
                RAISE EXCEPTION 'insights platform function {SIGNATURE} changed owner or settings';
            END IF;
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
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


def upgrade() -> None:
    _replace(PENDING_SKIPPED_FILTER)


def downgrade() -> None:
    _replace(V6Z107_ROLLUP_FILTER)
