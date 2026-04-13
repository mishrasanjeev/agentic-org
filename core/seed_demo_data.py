"""Comprehensive demo data seeder — populates all modules for a tenant.

Run manually via:
    python -c "import asyncio; from core.seed_demo_data import seed_all; asyncio.run(seed_all('TENANT_ID'))"

Or via the management endpoint:
    POST /api/v1/admin/seed-demo

This seeds:
  - 50+ agent_task_results across all domains (makes dashboards non-zero)
  - ABM accounts + campaigns
  - Sales leads
  - Approval policies
  - Feature flags
  - Department structure
  - Audit log entries
  - All 53 connectors registered for the tenant
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import structlog

logger = structlog.get_logger()

# Domain → agent types for realistic task results
_DOMAIN_AGENTS = {
    "finance": ["ap_processor", "ar_collections", "reconciliation_agent", "tax_compliance", "payroll_engine"],
    "hr": ["onboarding", "offboarding", "leave_policy_agent"],
    "marketing": ["lead_qualifier", "drip_campaign", "content_generator", "abm_outreach"],
    "ops": ["it_operations", "compliance_monitor"],
    "sales": ["lead_qualifier", "abm_outreach"],
}

_LLM_MODELS = ["gemini-2.5-flash", "claude-3-5-sonnet-20241022", "gemini-2.5-flash-preview-05-20"]

_TASK_TYPES = {
    "finance": ["invoice_processing", "payment_reconciliation", "gst_filing", "tds_computation", "bank_reconciliation"],
    "hr": ["employee_onboarding", "leave_approval", "payroll_run", "exit_processing"],
    "marketing": ["lead_scoring", "email_campaign", "content_generation", "social_post"],
    "ops": ["ticket_triage", "compliance_check", "sla_monitoring", "incident_response"],
    "sales": ["lead_qualification", "pipeline_update", "quote_generation"],
}


async def seed_task_results(tenant_id: uuid.UUID, count: int = 60) -> int:
    """Insert realistic agent_task_results for the past 30 days."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    now = datetime.now(UTC)
    created = 0

    async with get_tenant_session(tenant_id) as session:
        for _i in range(count):
            domain = random.choice(list(_DOMAIN_AGENTS.keys()))
            agent_type = random.choice(_DOMAIN_AGENTS[domain])
            task_type = random.choice(_TASK_TYPES[domain])
            status = random.choices(
                ["completed", "failed", "hitl_triggered"], weights=[85, 10, 5]
            )[0]
            confidence = (
                round(random.uniform(0.65, 0.99), 3)
                if status == "completed"
                else round(random.uniform(0.3, 0.7), 3)
            )
            tokens = random.randint(200, 5000)
            cost = round(tokens * 0.000375 / 1000, 6)
            duration = random.randint(500, 15000)
            created_at = now - timedelta(days=random.randint(0, 29), hours=random.randint(0, 23))

            await session.execute(
                text("""
                    INSERT INTO agent_task_results
                    (id, tenant_id, agent_id, agent_type, domain, task_type,
                     task_input, task_output, confidence, llm_model, tokens_used,
                     cost_usd, duration_ms, status, hitl_required, created_at)
                    VALUES
                    (:id, :tenant_id, :agent_id, :agent_type, :domain, :task_type,
                     :task_input, :task_output, :confidence, :llm_model, :tokens_used,
                     :cost_usd, :duration_ms, :status, :hitl_required, :created_at)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "agent_id": str(uuid.uuid4()),
                    "agent_type": agent_type,
                    "domain": domain,
                    "task_type": task_type,
                    "task_input": f'{{"action":"{task_type}","demo":true}}',
                    "task_output": f'{{"result":"Demo {task_type}","items":{random.randint(1, 50)}}}',
                    "confidence": confidence,
                    "llm_model": random.choice(_LLM_MODELS),
                    "tokens_used": tokens,
                    "cost_usd": cost,
                    "duration_ms": duration,
                    "status": status,
                    "hitl_required": status == "hitl_triggered",
                    "created_at": created_at,
                },
            )
            created += 1

    logger.info("seed_task_results", tenant_id=str(tenant_id), count=created)
    return created


async def seed_connectors(tenant_id: uuid.UUID) -> int:
    """Register all 53 connectors for the tenant (with empty auth_config)."""
    from sqlalchemy import text

    from connectors.registry import ConnectorRegistry
    from core.database import get_tenant_session

    names = ConnectorRegistry.all_names()
    created = 0

    async with get_tenant_session(tenant_id) as session:
        for name in names:
            # Check if already exists
            result = await session.execute(
                text("SELECT 1 FROM connectors WHERE tenant_id = :tid AND name = :name LIMIT 1"),
                {"tid": str(tenant_id), "name": name},
            )
            if result.scalar_one_or_none():
                continue

            cls = ConnectorRegistry.get(name)
            category = getattr(cls, "category", "general") if cls else "general"

            await session.execute(
                text("""
                    INSERT INTO connectors (id, tenant_id, name, category, auth_type, auth_config, status, created_at)
                    VALUES (:id, :tid, :name, :category, 'api_key', '{}'::jsonb, 'configured', now())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tenant_id),
                    "name": name,
                    "category": category,
                },
            )
            created += 1

    logger.info("seed_connectors", tenant_id=str(tenant_id), count=created)
    return created


async def seed_abm(tenant_id: uuid.UUID) -> int:
    """Create sample ABM accounts."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    companies = [
        ("Reliance Industries", "reliance.com", "1", 85),
        ("Tata Consultancy Services", "tcs.com", "1", 92),
        ("Infosys Ltd", "infosys.com", "1", 78),
        ("HDFC Bank", "hdfcbank.com", "2", 65),
        ("Wipro Ltd", "wipro.com", "2", 71),
        ("Bajaj Finance", "bajajfinance.in", "2", 58),
        ("Tech Mahindra", "techmahindra.com", "3", 45),
        ("Larsen & Toubro", "larsentoubro.com", "3", 52),
    ]
    created = 0

    async with get_tenant_session(tenant_id) as session:
        for name, domain, tier, intent in companies:
            await session.execute(
                text("""
                    INSERT INTO abm_accounts (id, tenant_id, name, domain, tier, intent_score, created_at)
                    VALUES (:id, :tid, :name, :domain, :tier, :intent, now())
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tenant_id),
                    "name": name,
                    "domain": domain,
                    "tier": tier,
                    "intent": intent,
                },
            )
            created += 1

    logger.info("seed_abm", tenant_id=str(tenant_id), count=created)
    return created


async def seed_approval_policy(tenant_id: uuid.UUID) -> int:
    """Create a default approval policy with 2-step chain."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    policy_id = str(uuid.uuid4())

    async with get_tenant_session(tenant_id) as session:
        # Check if default policy exists
        result = await session.execute(
            text("SELECT 1 FROM approval_policies WHERE tenant_id = :tid AND name = 'default' LIMIT 1"),
            {"tid": str(tenant_id)},
        )
        if result.scalar_one_or_none():
            return 0

        await session.execute(
            text("""
                INSERT INTO approval_policies (id, tenant_id, name, description, is_active, created_at, updated_at)
                VALUES (:id, :tid, 'default', 'Default 2-step approval: manager then CFO', TRUE, now(), now())
            """),
            {"id": policy_id, "tid": str(tenant_id)},
        )

        # Step 1: Manager approval
        await session.execute(
            text("""
                INSERT INTO approval_steps (id, policy_id, sequence, approver_role, quorum_required, quorum_total, mode)
                VALUES (:id, :pid, 1, 'manager', 1, 1, 'sequential')
            """),
            {"id": str(uuid.uuid4()), "pid": policy_id},
        )

        # Step 2: CFO approval for amounts > 50000
        await session.execute(
            text(
                "INSERT INTO approval_steps "
                "(id, policy_id, sequence, approver_role, "
                "quorum_required, quorum_total, mode, condition) "
                "VALUES (:id, :pid, 2, 'cfo', 1, 1, "
                "'sequential', 'amount > 50000')"
            ),
            {"id": str(uuid.uuid4()), "pid": policy_id},
        )

    logger.info("seed_approval_policy", tenant_id=str(tenant_id))
    return 1


async def seed_feature_flags(tenant_id: uuid.UUID) -> int:
    """Create default feature flags."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    flags = [
        ("workflow_builder", True, 100, "Visual workflow builder"),
        ("sso_oidc", True, 100, "SSO via OpenID Connect"),
        ("byok_encryption", False, 0, "Customer-managed encryption keys"),
        ("abm_module", True, 100, "Account-based marketing"),
        ("voice_agents", False, 50, "Voice agent integration (beta)"),
    ]
    created = 0

    async with get_tenant_session(tenant_id) as session:
        for key, enabled, rollout, desc in flags:
            result = await session.execute(
                text("SELECT 1 FROM feature_flags WHERE tenant_id = :tid AND flag_key = :key LIMIT 1"),
                {"tid": str(tenant_id), "key": key},
            )
            if result.scalar_one_or_none():
                continue

            await session.execute(
                text(
                    "INSERT INTO feature_flags "
                    "(id, tenant_id, flag_key, enabled, "
                    "rollout_percentage, description, "
                    "created_at, updated_at) "
                    "VALUES (:id, :tid, :key, :enabled, "
                    ":rollout, :desc, now(), now())"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tenant_id),
                    "key": key,
                    "enabled": enabled,
                    "rollout": rollout,
                    "desc": desc,
                },
            )
            created += 1

    logger.info("seed_feature_flags", tenant_id=str(tenant_id), count=created)
    return created


async def seed_departments(tenant_id: uuid.UUID) -> int:
    """Create a basic department structure."""
    from sqlalchemy import text

    from core.database import get_tenant_session

    # Get the first company for this tenant
    async with get_tenant_session(tenant_id) as session:
        result = await session.execute(
            text("SELECT id FROM companies WHERE tenant_id = :tid LIMIT 1"),
            {"tid": str(tenant_id)},
        )
        company_row = result.first()
        if not company_row:
            return 0
        company_id = str(company_row[0])

    departments = [
        ("Finance", "FIN"),
        ("Human Resources", "HR"),
        ("Marketing", "MKT"),
        ("Operations", "OPS"),
        ("Sales", "SALES"),
        ("Engineering", "ENG"),
    ]
    created = 0

    async with get_tenant_session(tenant_id) as session:
        for name, code in departments:
            result = await session.execute(
                text("SELECT 1 FROM departments WHERE tenant_id = :tid AND company_id = :cid AND name = :name LIMIT 1"),
                {"tid": str(tenant_id), "cid": company_id, "name": name},
            )
            if result.scalar_one_or_none():
                continue

            await session.execute(
                text("""
                    INSERT INTO departments (id, tenant_id, company_id, name, code, created_at)
                    VALUES (:id, :tid, :cid, :name, :code, now())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tenant_id),
                    "cid": company_id,
                    "name": name,
                    "code": code,
                },
            )
            created += 1

    logger.info("seed_departments", tenant_id=str(tenant_id), count=created)
    return created


async def seed_all(tenant_id_str: str) -> dict:
    """Run all seeders for a tenant. Returns a summary."""
    tid = uuid.UUID(tenant_id_str)
    results = {}

    results["task_results"] = await seed_task_results(tid, count=60)
    results["connectors"] = await seed_connectors(tid)
    results["abm_accounts"] = await seed_abm(tid)
    results["approval_policies"] = await seed_approval_policy(tid)
    results["feature_flags"] = await seed_feature_flags(tid)
    results["departments"] = await seed_departments(tid)

    logger.info("seed_all_complete", tenant_id=tenant_id_str, **results)
    return results
