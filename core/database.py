"""Async SQLAlchemy engine, session management, and tenant RLS middleware."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

from core.config import settings


class Base(DeclarativeBase, MappedAsDataclass):
    """Declarative base for all ORM models."""

    pass


engine: AsyncEngine = create_async_engine(
    settings.db_url,
    echo=settings.env == "development",
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_tenant_session(tenant_id: UUID) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with RLS tenant context set."""
    async with async_session_factory() as session:
        # asyncpg does not support bound parameters in SET LOCAL.
        # We MUST validate the UUID format before interpolating.
        import re as _re

        tid_str = str(tenant_id)
        if not _re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", tid_str):
            raise ValueError(f"Invalid tenant_id format: {tid_str}")
        # Construct the SQL statement from the validated, safe UUID string.
        # This is NOT user-controlled — tenant_id comes from JWT claims validated
        # by auth middleware. The regex above is defense-in-depth.
        stmt = "SET LOCAL agenticorg.tenant_id = '" + tid_str + "'"  # noqa: S608
        await session.execute(text(stmt))
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a raw session (for non-tenant-scoped operations like health checks)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Run on startup — verify connectivity and apply safe schema additions."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

        # v4.0.0: Ensure prompt_amendments column exists on agents table.
        # Safe to run every startup (IF NOT EXISTS check).
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'agents' AND column_name = 'prompt_amendments'
                ) THEN
                    ALTER TABLE agents ADD COLUMN prompt_amendments JSONB DEFAULT '[]'::jsonb;
                END IF;
            END $$;
        """))

        # v4.3.0: Ensure connector_ids column exists on agents table.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'agents' AND column_name = 'connector_ids'
                ) THEN
                    ALTER TABLE agents ADD COLUMN connector_ids JSONB DEFAULT '[]'::jsonb;
                END IF;
            END $$;
        """))

        # v4.1.0: Ensure company_id column exists on operational tables.
        # Enables CA multi-tenant use case where a tenant manages N client
        # companies.  Nullable FK — existing rows keep company_id = NULL.
        _company_tables = [
            "agents",
            "workflow_definitions",
            "workflow_runs",
            "audit_log",
            "tool_calls",
            "connectors",
        ]
        for _tbl in _company_tables:
            # Table names come from the hardcoded _company_tables list above,
            # never from user input — safe to interpolate.
            await conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{_tbl}' AND column_name = 'company_id'
                    ) THEN
                        ALTER TABLE {_tbl} ADD COLUMN company_id UUID;
                    END IF;
                END $$;
            """))  # noqa: S608  # nosec B608

        # v4.1.0: Ensure the companies table exists (CA multi-company model).
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS companies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                name VARCHAR(255) NOT NULL,
                gstin VARCHAR(15),
                pan VARCHAR(10) NOT NULL,
                tan VARCHAR(10),
                cin VARCHAR(21),
                state_code VARCHAR(2),
                registered_address TEXT,
                industry VARCHAR(100),
                fy_start_month VARCHAR(2) NOT NULL DEFAULT '04',
                fy_end_month VARCHAR(2) NOT NULL DEFAULT '03',
                signatory_name VARCHAR(255),
                signatory_designation VARCHAR(100),
                signatory_email VARCHAR(255),
                compliance_email VARCHAR(255),
                dsc_serial VARCHAR(100),
                dsc_expiry DATE,
                pf_registration VARCHAR(50),
                esi_registration VARCHAR(50),
                pt_registration VARCHAR(50),
                bank_name VARCHAR(255),
                bank_account_number VARCHAR(50),
                bank_ifsc VARCHAR(11),
                bank_branch VARCHAR(255),
                tally_config JSONB,
                gst_auto_file BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                user_roles JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (tenant_id, gstin)
            );
        """))

        # v4.2.0: Add new columns to companies if missing.
        for _col, _type, _default in [
            ("subscription_status", "VARCHAR(20) NOT NULL DEFAULT 'trial'", None),
            ("client_health_score", "INT DEFAULT 100", None),
            ("document_vault_enabled", "BOOLEAN NOT NULL DEFAULT TRUE", None),
            ("compliance_alerts_email", "VARCHAR(255)", None),
        ]:
            await conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'companies' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE companies ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """))  # noqa: S608  # nosec B608

        # v4.2.0: Ensure ca_subscriptions table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ca_subscriptions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                plan VARCHAR(50) NOT NULL DEFAULT 'ca_pro',
                status VARCHAR(20) NOT NULL DEFAULT 'trial',
                max_clients INT NOT NULL DEFAULT 7,
                price_inr INT NOT NULL DEFAULT 4999,
                price_usd INT NOT NULL DEFAULT 59,
                billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
                trial_ends_at TIMESTAMPTZ,
                current_period_start TIMESTAMPTZ,
                current_period_end TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (tenant_id)
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS industry_pack_installs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                pack_name VARCHAR(100) NOT NULL,
                installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                workflow_ids JSONB NOT NULL DEFAULT '[]'::jsonb
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_industry_pack_installs_tenant_id "
            "ON industry_pack_installs(tenant_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_industry_pack_installs_pack_name "
            "ON industry_pack_installs(pack_name)"
        ))

        # v4.2.0: Ensure filing_approvals table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS filing_approvals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                filing_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                filing_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                requested_by VARCHAR(255) NOT NULL,
                approved_by VARCHAR(255),
                approved_at TIMESTAMPTZ,
                rejection_reason TEXT,
                auto_approved BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            );
        """))

        # v4.2.0: Ensure gstn_uploads table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS gstn_uploads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                upload_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                file_name VARCHAR(500) NOT NULL,
                file_path VARCHAR(1000),
                file_size_bytes BIGINT,
                status VARCHAR(20) NOT NULL DEFAULT 'generated',
                gstn_arn VARCHAR(100),
                uploaded_at TIMESTAMPTZ,
                uploaded_by VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            );
        """))

        # v5.0.0: Ensure kpi_cache table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kpi_cache (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID REFERENCES companies(id),
                role VARCHAR(20) NOT NULL,
                metric_name VARCHAR(100) NOT NULL,
                metric_value JSONB NOT NULL,
                source VARCHAR(50) NOT NULL DEFAULT 'agent',
                computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ttl_seconds INT NOT NULL DEFAULT 3600,
                stale BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_kpi_cache_tenant_role "
            "ON kpi_cache(tenant_id, role)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_kpi_cache_metric "
            "ON kpi_cache(tenant_id, role, metric_name)"
        ))

        # v5.0.0: Ensure agent_task_results table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_task_results (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                agent_id UUID NOT NULL,
                agent_type VARCHAR(100) NOT NULL,
                domain VARCHAR(50) NOT NULL,
                task_type VARCHAR(100) NOT NULL,
                task_input JSONB NOT NULL DEFAULT '{}'::jsonb,
                task_output JSONB NOT NULL DEFAULT '{}'::jsonb,
                confidence FLOAT,
                tool_calls JSONB DEFAULT '[]'::jsonb,
                llm_model VARCHAR(100),
                tokens_used INT DEFAULT 0,
                cost_usd FLOAT DEFAULT 0.0,
                duration_ms INT DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'completed',
                error_message TEXT,
                hitl_required BOOLEAN NOT NULL DEFAULT FALSE,
                hitl_decision VARCHAR(20),
                company_id UUID REFERENCES companies(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_results_tenant "
            "ON agent_task_results(tenant_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_results_domain "
            "ON agent_task_results(tenant_id, domain)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_agent_results_created "
            "ON agent_task_results(created_at)"
        ))

        # v5.0.0: Ensure connector_configs table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS connector_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                connector_name VARCHAR(100) NOT NULL,
                display_name VARCHAR(255),
                auth_type VARCHAR(50) NOT NULL DEFAULT 'api_key',
                credentials_encrypted JSONB NOT NULL DEFAULT '{}'::jsonb,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                status VARCHAR(20) NOT NULL DEFAULT 'configured',
                last_health_check TIMESTAMPTZ,
                health_status VARCHAR(20) DEFAULT 'unknown',
                last_sync_at TIMESTAMPTZ,
                sync_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ,
                CONSTRAINT uq_connector_config_tenant
                    UNIQUE (tenant_id, connector_name)
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_connector_configs_tenant "
            "ON connector_configs(tenant_id)"
        ))

        # v4.3.0: gstn_auto_upload flag on companies
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'companies' AND column_name = 'gstn_auto_upload'
                ) THEN
                    ALTER TABLE companies ADD COLUMN gstn_auto_upload BOOLEAN NOT NULL DEFAULT FALSE;
                END IF;
            END $$;
        """))

        # v4.3.0: Ensure gstn_credentials table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS gstn_credentials (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                gstin VARCHAR(15) NOT NULL,
                username VARCHAR(255) NOT NULL,
                password_encrypted TEXT NOT NULL,
                encryption_key_ref VARCHAR(100) NOT NULL DEFAULT 'default',
                portal_type VARCHAR(20) NOT NULL DEFAULT 'gstn',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_verified_at TIMESTAMPTZ,
                last_login_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (company_id, portal_type)
            );
        """))

        # v4.3.0: Ensure compliance_deadlines table exists.
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS compliance_deadlines (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id),
                company_id UUID NOT NULL REFERENCES companies(id),
                deadline_type VARCHAR(50) NOT NULL,
                filing_period VARCHAR(20) NOT NULL,
                due_date DATE NOT NULL,
                alert_7d_sent BOOLEAN NOT NULL DEFAULT FALSE,
                alert_1d_sent BOOLEAN NOT NULL DEFAULT FALSE,
                filed BOOLEAN NOT NULL DEFAULT FALSE,
                filed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ,
                UNIQUE (company_id, deadline_type, filing_period)
            );
        """))

        # ── v4.6.0: Enterprise readiness — run every startup, idempotent ──

        # 1. User i18n + department assignment
        for _col, _type in [
            ("timezone", "VARCHAR(64) NOT NULL DEFAULT 'UTC'"),
            ("locale", "VARCHAR(10) NOT NULL DEFAULT 'en'"),
            ("department_id", "UUID"),
        ]:
            await conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE users ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """))  # noqa: S608  # nosec B608

        # 2. Company.currency (ISO 4217)
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'companies' AND column_name = 'currency'
                ) THEN
                    ALTER TABLE companies ADD COLUMN currency CHAR(3) NOT NULL DEFAULT 'INR';
                END IF;
            END $$;
        """))

        # 3. Departments + cost centers (org hierarchy)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS departments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                code VARCHAR(50),
                parent_id UUID REFERENCES departments(id) ON DELETE SET NULL,
                manager_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, company_id, name)
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_departments_tenant_company "
            "ON departments(tenant_id, company_id);"
        ))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cost_centers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
                code VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                budget_limit NUMERIC(14, 2),
                fiscal_year INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, company_id, code)
            );
        """))

        # Add FK from users.department_id to departments.id (now that the
        # table exists).  PostgreSQL doesn't support IF NOT EXISTS on FK so
        # we check information_schema.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'fk_users_department'
                      AND table_name = 'users'
                ) THEN
                    ALTER TABLE users
                    ADD CONSTRAINT fk_users_department
                    FOREIGN KEY (department_id)
                    REFERENCES departments(id) ON DELETE SET NULL;
                END IF;
            END $$;
        """))

        # 4. Agent maturity + cost center pointer
        for _col, _type in [
            ("maturity", "VARCHAR(20) NOT NULL DEFAULT 'beta'"),
            ("cost_center_id", "UUID"),
        ]:
            await conn.execute(text(f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'agents' AND column_name = '{_col}'
                    ) THEN
                        ALTER TABLE agents ADD COLUMN {_col} {_type};
                    END IF;
                END $$;
            """))  # noqa: S608  # nosec B608

        # 5. User delegation table (approval forwarding)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_delegations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                delegator_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                delegate_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                reason VARCHAR(255),
                starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                ends_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_delegation_different_users CHECK (delegator_id <> delegate_id)
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_delegations_active "
            "ON user_delegations(tenant_id, delegator_id) "
            "WHERE revoked_at IS NULL;"
        ))

        # 6. Feature flags table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS feature_flags (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID,
                flag_key VARCHAR(100) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                rollout_percentage INTEGER NOT NULL DEFAULT 0
                    CHECK (rollout_percentage BETWEEN 0 AND 100),
                description VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, flag_key)
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_feature_flags_key "
            "ON feature_flags(flag_key);"
        ))

        # 7. Budget alerts table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
                cost_center_id UUID REFERENCES cost_centers(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                period VARCHAR(20) NOT NULL,
                threshold_usd NUMERIC(14, 2) NOT NULL,
                warn_at_percent INTEGER NOT NULL DEFAULT 80
                    CHECK (warn_at_percent BETWEEN 1 AND 100),
                notify_channels VARCHAR(255) NOT NULL DEFAULT 'email',
                last_triggered_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """))

        # 8. SSO configuration per tenant
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sso_configs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                provider_key VARCHAR(50) NOT NULL,
                provider_type VARCHAR(20) NOT NULL DEFAULT 'oidc',
                display_name VARCHAR(100) NOT NULL,
                config JSONB NOT NULL DEFAULT '{}'::jsonb,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                jit_provisioning BOOLEAN NOT NULL DEFAULT TRUE,
                default_role VARCHAR(50) NOT NULL DEFAULT 'analyst',
                allowed_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, provider_key)
            );
        """))

        # 8b. Tenant BYOK KEK resource — customer-managed KMS key
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'tenants' AND column_name = 'byok_kek_resource'
                ) THEN
                    ALTER TABLE tenants ADD COLUMN byok_kek_resource VARCHAR(500) NOT NULL DEFAULT '';
                END IF;
            END $$;
        """))

        # 8c. Invoices
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS invoices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                invoice_number VARCHAR(50) NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                issue_date DATE NOT NULL,
                due_date DATE NOT NULL,
                currency CHAR(3) NOT NULL DEFAULT 'USD',
                subtotal NUMERIC(14, 2) NOT NULL,
                tax NUMERIC(14, 2) NOT NULL DEFAULT 0,
                total NUMERIC(14, 2) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
                pdf_url VARCHAR(500),
                payment_provider VARCHAR(20),
                payment_ref VARCHAR(100),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, invoice_number)
            );
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_invoices_tenant_period "
            "ON invoices(tenant_id, period_start);"
        ))

        # 9. Approval policies (configurable multi-step approval chains)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS approval_policies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                name VARCHAR(100) NOT NULL,
                description VARCHAR(500),
                workflow_id UUID,
                agent_id UUID,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, name)
            );
        """))

        # v4.7.0 hotfix: the initial v4.7.0 ship created approval_policies
        # with is_active as VARCHAR(10). Convert to BOOLEAN if still varchar.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'approval_policies'
                      AND column_name = 'is_active'
                      AND data_type = 'character varying'
                ) THEN
                    ALTER TABLE approval_policies
                        ALTER COLUMN is_active DROP DEFAULT,
                        ALTER COLUMN is_active TYPE BOOLEAN
                        USING (is_active::text IN ('true', 't', '1')),
                        ALTER COLUMN is_active SET DEFAULT TRUE;
                END IF;
            END $$;
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS approval_steps (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                policy_id UUID NOT NULL REFERENCES approval_policies(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                approver_role VARCHAR(50) NOT NULL,
                quorum_required INTEGER NOT NULL DEFAULT 1,
                quorum_total INTEGER NOT NULL DEFAULT 1,
                mode VARCHAR(20) NOT NULL DEFAULT 'sequential',
                condition VARCHAR(500),
                step_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (policy_id, sequence),
                CHECK (quorum_required >= 1),
                CHECK (quorum_required <= quorum_total)
            );
        """))

        # 9a. Tenant branding (white-label)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_branding (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL UNIQUE,
                product_name VARCHAR(100) NOT NULL DEFAULT 'AgenticOrg',
                logo_url VARCHAR(500),
                favicon_url VARCHAR(500),
                primary_color VARCHAR(7) NOT NULL DEFAULT '#7c3aed',
                accent_color VARCHAR(7) NOT NULL DEFAULT '#1e293b',
                custom_domain VARCHAR(255),
                support_email VARCHAR(255),
                footer_text VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """))

        # 9b. Workflow A/B variants
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS workflow_variants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                workflow_id UUID NOT NULL,
                variant_name VARCHAR(100) NOT NULL,
                weight INTEGER NOT NULL DEFAULT 50 CHECK (weight BETWEEN 0 AND 100),
                definition JSONB NOT NULL DEFAULT '{}'::jsonb,
                run_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (workflow_id, variant_name)
            );
        """))

        # 9c. RLS for ALL v4.7 tenant-scoped tables
        # (Missing from the original v4.7.0 ship — found in gap analysis #6)
        _v47_rls_tables = [
            "sso_configs",
            "approval_policies",
            "invoices",
            "tenant_branding",
            "workflow_variants",
        ]
        for _rls_tbl in _v47_rls_tables:
            await conn.execute(text(f"ALTER TABLE {_rls_tbl} ENABLE ROW LEVEL SECURITY;"))  # noqa: S608
            await conn.execute(text(f"ALTER TABLE {_rls_tbl} FORCE ROW LEVEL SECURITY;"))  # noqa: S608
            await conn.execute(text(
                f"DROP POLICY IF EXISTS {_rls_tbl}_tenant_isolation ON {_rls_tbl};"  # noqa: S608
            ))
            await conn.execute(text(
                f"CREATE POLICY {_rls_tbl}_tenant_isolation ON {_rls_tbl} "  # noqa: S608
                "USING (tenant_id::text = current_setting('agenticorg.tenant_id', true));"
            ))
        # approval_steps is a child of approval_policies — RLS via FK cascade,
        # but add direct policy too for defense in depth.
        await conn.execute(text("ALTER TABLE approval_steps ENABLE ROW LEVEL SECURITY;"))
        await conn.execute(text("ALTER TABLE approval_steps FORCE ROW LEVEL SECURITY;"))
        await conn.execute(text("DROP POLICY IF EXISTS approval_steps_tenant_isolation ON approval_steps;"))
        await conn.execute(text("""
            CREATE POLICY approval_steps_tenant_isolation ON approval_steps
            USING (policy_id IN (
                SELECT id FROM approval_policies
                WHERE tenant_id::text = current_setting('agenticorg.tenant_id', true)
            ));
        """))

        # 10. Audit log immutability trigger — rejects UPDATE/DELETE
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                  'audit_log is append-only — UPDATE/DELETE rejected'
                  USING ERRCODE = 'insufficient_privilege';
            END;
            $$ LANGUAGE plpgsql;
        """))
        await conn.execute(text("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;"))
        await conn.execute(text("""
            CREATE TRIGGER audit_log_immutable
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
        """))

    # Seed demo CA companies ONLY in demo/dev environments — never in production
    if os.getenv("AGENTICORG_ENV", "production").lower() in ("demo", "development", "dev"):
        try:
            from core.seed_ca_demo import seed_ca_demo

            async with async_session_factory() as session:
                await seed_ca_demo(session)
                await session.commit()
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).debug("CA demo seed skipped: %s", exc)


async def close_db() -> None:
    """Run on shutdown."""
    await engine.dispose()
