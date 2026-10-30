# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""LLM router daily spend: the ledger the Gemini daily caps read.

Revision ID: v6z105_llm_daily_spend
Revises: v6z104_payment_handoff_action
Create Date: 2026-10-30

The per-tenant and platform Gemini daily caps (core/llm/router.py) summed
``agent_task_results``, which only the demo seeder writes, so neither cap ever
tripped. ``llm_daily_spend`` keeps one row per tenant, UTC day, provider and
model; the router adds every priced call to it. Tenant row-level security is
forced. ``public.gemini_daily_platform_spend()`` keeps its name, owner,
SECURITY DEFINER and search path and now sums this table; the aggregate role
reads only the cost, day and provider columns, and its read of
``agent_task_results`` is withdrawn. The function is replaced the way v6z85
created it: the installer joins the aggregate role for the statement only and
must not keep its rights afterwards.

Downgrade restores the previous function body and grants and drops the table.
The rows are daily counters, not history: after a downgrade the caps read
``agent_task_results`` again and stop tripping.
"""

from alembic import op

revision = "v6z105_llm_daily_spend"
down_revision = "v6z104_payment_handoff_action"
branch_labels = None
depends_on = None

_LEDGER_BODY = """
    SELECT COALESCE(SUM(cost_usd),0) FROM public.llm_daily_spend
    WHERE provider = 'gemini'
        AND day = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
"""

# The v6z85 body, restored on downgrade.
_TASK_RESULTS_BODY = """
    SELECT COALESCE(SUM(cost_usd),0) FROM public.agent_task_results
    WHERE llm_model LIKE 'gemini%'
        AND created_at >= (date_trunc('day',CURRENT_TIMESTAMP AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        AND created_at < ((date_trunc('day',CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC')
"""


def _replace_platform_spend(body: str) -> str:
    """Replace the function body as its owner, then prove the installer kept no aggregate rights."""
    return f"""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                WHERE p.oid=to_regprocedure('public.gemini_daily_platform_spend()')
                    AND r.rolname='agenticorg_spend_aggregate' AND p.prosecdef
                    AND p.pronargs=0 AND p.prorettype='numeric'::regtype
                    AND p.proconfig=ARRAY['search_path=pg_catalog']
            ) THEN
                RAISE EXCEPTION 'Missing or unsafe platform spend function';
            END IF;
            EXECUTE format('GRANT agenticorg_spend_aggregate TO %I',current_user);
            EXECUTE $ddl$
                CREATE OR REPLACE FUNCTION public.gemini_daily_platform_spend()
                RETURNS NUMERIC LANGUAGE SQL STABLE SECURITY DEFINER
                SET search_path = pg_catalog
                AS $function${body}$function$
            $ddl$;
            EXECUTE format('REVOKE agenticorg_spend_aggregate FROM %I',current_user);
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                -- A superuser has implicit access independently of membership.
                NULL;
            ELSIF current_setting('server_version_num')::integer >= 160000 THEN
                IF pg_has_role(current_user,'agenticorg_spend_aggregate','USAGE')
                    OR pg_has_role(current_user,'agenticorg_spend_aggregate','SET') THEN
                    RAISE EXCEPTION 'Installer retained aggregate row access';
                END IF;
            ELSIF pg_has_role(current_user,'agenticorg_spend_aggregate','MEMBER') THEN
                RAISE EXCEPTION 'Installer retained aggregate membership';
            END IF;
        END $$
    """  # noqa: S608 - the body is one of the two constants above, never input


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_daily_spend (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            day DATE NOT NULL,
            provider VARCHAR(16) NOT NULL,
            model VARCHAR(128) NOT NULL,
            call_count INTEGER NOT NULL DEFAULT 0,
            cost_usd NUMERIC(14,6) NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_llm_daily_spend_key UNIQUE (tenant_id, day, provider, model)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_llm_daily_spend_day_provider ON llm_daily_spend(day, provider)")
    op.execute("ALTER TABLE llm_daily_spend ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_daily_spend FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS llm_daily_spend_tenant ON llm_daily_spend")
    op.execute("""
        CREATE POLICY llm_daily_spend_tenant ON llm_daily_spend
        USING (tenant_id::text = current_setting('agenticorg.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id', true))
    """)
    # The aggregate role sums across tenants through the SECURITY DEFINER
    # function only; FORCE applies to it too, hence its own read policy.
    op.execute("GRANT SELECT(cost_usd,day,provider) ON public.llm_daily_spend TO agenticorg_spend_aggregate")
    op.execute("DROP POLICY IF EXISTS spend_aggregate_read ON public.llm_daily_spend")
    op.execute("""
        CREATE POLICY spend_aggregate_read ON public.llm_daily_spend FOR SELECT
        TO agenticorg_spend_aggregate USING (true)
    """)
    op.execute(_replace_platform_spend(_LEDGER_BODY))
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('public.agent_task_results') IS NOT NULL THEN
                DROP POLICY IF EXISTS spend_aggregate_read ON public.agent_task_results;
                REVOKE ALL ON public.agent_task_results FROM agenticorg_spend_aggregate;
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("GRANT SELECT(cost_usd,created_at,llm_model) ON public.agent_task_results TO agenticorg_spend_aggregate")
    op.execute("DROP POLICY IF EXISTS spend_aggregate_read ON public.agent_task_results")
    op.execute("""
        CREATE POLICY spend_aggregate_read ON public.agent_task_results FOR SELECT
        TO agenticorg_spend_aggregate USING (true)
    """)
    op.execute(_replace_platform_spend(_TASK_RESULTS_BODY))
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('public.llm_daily_spend') IS NOT NULL THEN
                DROP POLICY IF EXISTS spend_aggregate_read ON public.llm_daily_spend;
                REVOKE ALL ON public.llm_daily_spend FROM agenticorg_spend_aggregate;
            END IF;
        END $$
    """)
    op.execute("DROP TABLE IF EXISTS llm_daily_spend")
