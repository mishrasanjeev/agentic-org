"""Async SQLAlchemy engine, session management, and tenant RLS middleware."""

from __future__ import annotations

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

    # Seed demo CA companies (non-transactional — runs after DDL commit)
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
