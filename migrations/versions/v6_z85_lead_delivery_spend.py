# SPDX-License-Identifier: Apache-2.0
"""Durable marketing delivery and a scalar-only platform spend capability.

The runtime never inherits aggregation privileges or SET ROLE access. Existing
tenant policies and FORCE ROW LEVEL SECURITY are unchanged. PostgreSQL 16 keeps
the role creator's admin-only management grant, as it does for other roles.
"""
from alembic import op
from sqlalchemy import text

from core.crypto.migration_helpers import encrypted_migration

revision = "v6z85_lead_delivery_spend"
down_revision = "v6z84_customer_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existed = op.get_bind().execute(text("SELECT to_regclass('public.demo_delivery_jobs') IS NOT NULL")).scalar()
    op.execute("""
        CREATE TABLE IF NOT EXISTS demo_delivery_jobs (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            lead_id UUID,
            payload_encrypted TEXT NOT NULL,
            notification_status TEXT NOT NULL DEFAULT 'pending',
            confirmation_status TEXT NOT NULL DEFAULT 'pending',
            agent_status TEXT NOT NULL DEFAULT 'pending',
            error_codes JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT demo_notification_state CHECK (notification_status IN
                ('pending','processing','smtp_accepted','failed','unknown')),
            CONSTRAINT demo_confirmation_state CHECK (confirmation_status IN
                ('pending','processing','smtp_accepted','failed','unknown')),
            CONSTRAINT demo_agent_state CHECK (agent_status IN
                ('pending','processing','completed','needs_review','failed','skipped','unknown'))
        )
    """)
    if not existed:
        with encrypted_migration(
            revision=revision, table="demo_delivery_jobs", columns=["payload_encrypted"],
            rollback_doc="Disable durable delivery before rollback. Preserve encrypted outbox history; "
                         "this migration creates an empty table and never rewrites or discards existing ciphertext.",
        ) as ctx:
            pre_count = ctx.snapshot_row_count()
            ctx.dry_run_decrypt_sample(n=50)
            for _offset in ctx.iter_resumable_batches(batch=500):
                raise RuntimeError("new delivery outbox unexpectedly contained rows")
            ctx.assert_decrypt_after(n=50)
            ctx.record_audit({"pre_count": pre_count, "post_count": ctx.snapshot_row_count()})
    op.execute("CREATE INDEX IF NOT EXISTS ix_demo_delivery_tenant_created ON demo_delivery_jobs(tenant_id,created_at)")
    op.execute("ALTER TABLE demo_delivery_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE demo_delivery_jobs FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS demo_delivery_tenant ON demo_delivery_jobs")
    op.execute("""
        CREATE POLICY demo_delivery_tenant ON demo_delivery_jobs
        USING (tenant_id::text = current_setting('agenticorg.tenant_id',true))
        WITH CHECK (tenant_id::text = current_setting('agenticorg.tenant_id',true))
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticorg_spend_aggregate') THEN
                CREATE ROLE agenticorg_spend_aggregate NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenticorg_spend_aggregate'
                AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole)) THEN
                RAISE EXCEPTION 'Unsafe preexisting spend aggregation role';
            END IF;
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO agenticorg_spend_aggregate")
    op.execute("GRANT SELECT(cost_usd,created_at,llm_model) ON public.agent_task_results TO agenticorg_spend_aggregate")
    op.execute("DROP POLICY IF EXISTS spend_aggregate_read ON public.agent_task_results")
    op.execute("""
        CREATE POLICY spend_aggregate_read ON public.agent_task_results FOR SELECT
        TO agenticorg_spend_aggregate USING (true)
    """)
    # Transferring ownership requires temporary schema CREATE and membership.
    # The role creator's ADMIN-only grant may remain on PostgreSQL 16+, but
    # INHERIT and SET access must not. Ordinary runtime roles get EXECUTE only.
    op.execute("""
        DO $$ BEGIN
            EXECUTE format('GRANT agenticorg_spend_aggregate TO %I',current_user);
            GRANT CREATE ON SCHEMA public TO agenticorg_spend_aggregate;
            IF to_regprocedure('public.gemini_daily_platform_spend()') IS NULL THEN
                EXECUTE $ddl$
                    CREATE FUNCTION public.gemini_daily_platform_spend()
                    RETURNS NUMERIC LANGUAGE SQL STABLE SECURITY DEFINER
                    SET search_path = pg_catalog
                    AS $function$
                        SELECT COALESCE(SUM(cost_usd),0) FROM public.agent_task_results
                        WHERE llm_model LIKE 'gemini%'
                            AND created_at >= (date_trunc('day',CURRENT_TIMESTAMP AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                            AND created_at < ((date_trunc('day',CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC')
                    $function$
                $ddl$;
                REVOKE ALL ON FUNCTION public.gemini_daily_platform_spend() FROM PUBLIC;
                ALTER FUNCTION public.gemini_daily_platform_spend() OWNER TO agenticorg_spend_aggregate;
            ELSE
                IF NOT EXISTS (
                    SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
                    WHERE p.oid='public.gemini_daily_platform_spend()'::regprocedure
                        AND r.rolname='agenticorg_spend_aggregate' AND p.prosecdef
                        AND p.pronargs=0 AND p.prorettype='numeric'::regtype
                        AND p.proconfig=ARRAY['search_path=pg_catalog']
                ) THEN
                    RAISE EXCEPTION 'Unsafe preexisting platform spend function';
                END IF;
            END IF;
            EXECUTE format('GRANT EXECUTE ON FUNCTION public.gemini_daily_platform_spend() TO %I',current_user);
            REVOKE CREATE ON SCHEMA public FROM agenticorg_spend_aggregate;
            EXECUTE format('REVOKE agenticorg_spend_aggregate FROM %I',current_user);
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                -- A superuser has implicit access independently of membership.
                -- The ordinary runtime role must still pass the checks below.
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
            IF to_regprocedure('public.gemini_daily_platform_spend()') IS NOT NULL THEN
                EXECUTE format('GRANT agenticorg_spend_aggregate TO %I',current_user);
                DROP FUNCTION public.gemini_daily_platform_spend();
                EXECUTE format('REVOKE agenticorg_spend_aggregate FROM %I',current_user);
            END IF;
        END $$
    """)
    op.execute("DROP POLICY IF EXISTS spend_aggregate_read ON public.agent_task_results")
    op.execute("REVOKE ALL ON public.agent_task_results FROM agenticorg_spend_aggregate")
    # Keep delivery history and its RLS on rollback. The inert NOLOGIN role can
    # be shared with another database; do not DROP ROLE across database scopes.
