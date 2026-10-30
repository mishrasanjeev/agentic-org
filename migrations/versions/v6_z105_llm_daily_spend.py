# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""LLM router daily spend: the ledger recorded-spend cost controls read.

Revision ID: v6z105_llm_daily_spend
Revises: v6z104_payment_handoff_action
Create Date: 2026-10-30

The per-tenant and platform Gemini daily caps (core/llm/router.py) sum
``agent_task_results``, which only the demo seeder writes, so they never trip
on real usage. With ``AGENTICORG_RECORDED_SPEND_CONTROLS_ENABLED`` (off by
default) the router records every priced call in ``llm_daily_spend`` (one row
per tenant, UTC day, provider and model; tenant row-level security forced) and
the caps sum it instead. The platform-wide sum is the new
``public.gemini_daily_ledger_spend()``: SECURITY DEFINER, owned by the v6z85
aggregate role, search path ``pg_catalog``, created the way v6z85 created
``public.gemini_daily_platform_spend()``, and granted to ``agenticorg_app``
where that role exists (docs/RLS_APP_ROLE.md). The aggregate role reads only
the cost, day and provider columns of the new table. The v6z85 function and
its grants are unchanged, so with the flag off nothing behaves differently.

Downgrade drops the function and the table. The rows are daily counters, not
history.
"""

from alembic import op

revision = "v6z105_llm_daily_spend"
down_revision = "v6z104_payment_handoff_action"
branch_labels = None
depends_on = None


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
    # As in v6z85: transferring ownership needs temporary schema CREATE and
    # membership, and the installer must not keep either afterwards.
    op.execute("""
        DO $$ BEGIN
            EXECUTE format('GRANT agenticorg_spend_aggregate TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO agenticorg_spend_aggregate;
            IF to_regprocedure('public.gemini_daily_ledger_spend()') IS NULL THEN
                EXECUTE $ddl$
                    CREATE FUNCTION public.gemini_daily_ledger_spend()
                    RETURNS NUMERIC LANGUAGE SQL STABLE SECURITY DEFINER
                    SET search_path = pg_catalog
                    AS $function$
                        SELECT COALESCE(SUM(cost_usd),0) FROM public.llm_daily_spend
                        WHERE provider = 'gemini'
                            AND day = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
                    $function$
                $ddl$;
                REVOKE ALL ON FUNCTION public.gemini_daily_ledger_spend() FROM PUBLIC;
                ALTER FUNCTION public.gemini_daily_ledger_spend() OWNER TO agenticorg_spend_aggregate;
            ELSE
                IF NOT EXISTS (
                    SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                    WHERE p.oid='public.gemini_daily_ledger_spend()'::regprocedure
                        AND r.rolname='agenticorg_spend_aggregate' AND p.prosecdef
                        AND p.pronargs=0 AND p.prorettype='numeric'::regtype
                        AND p.proconfig=ARRAY['search_path=pg_catalog']
                ) THEN
                    RAISE EXCEPTION 'Unsafe preexisting ledger spend function';
                END IF;
            END IF;
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.gemini_daily_ledger_spend() TO %I',current_user);
            -- docs/RLS_APP_ROLE.md: a migration adding a function the application
            -- calls grants it itself, so a run after scripts/create_app_role.sql
            -- leaves the same privileges.
            IF to_regrole('agenticorg_app') IS NOT NULL THEN
                GRANT EXECUTE ON FUNCTION public.gemini_daily_ledger_spend() TO agenticorg_app;
            END IF;
            REVOKE CREATE ON SCHEMA public FROM agenticorg_spend_aggregate;
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
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regprocedure('public.gemini_daily_ledger_spend()') IS NOT NULL THEN
                EXECUTE format('GRANT agenticorg_spend_aggregate TO %I',current_user);
                DROP FUNCTION public.gemini_daily_ledger_spend();
                EXECUTE format('REVOKE agenticorg_spend_aggregate FROM %I',current_user);
            END IF;
            IF to_regclass('public.llm_daily_spend') IS NOT NULL THEN
                DROP POLICY IF EXISTS spend_aggregate_read ON public.llm_daily_spend;
                REVOKE ALL ON public.llm_daily_spend FROM agenticorg_spend_aggregate;
            END IF;
        END $$
    """)
    op.execute("DROP TABLE IF EXISTS llm_daily_spend")
