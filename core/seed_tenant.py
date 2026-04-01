"""Seed built-in agents and prompt templates for a new tenant.

Called during signup to ensure every new organization starts with the 24 system
agents and their prompt templates ready to use.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.agent import Agent
from core.models.prompt_template import PromptTemplate

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "core" / "agents" / "prompts"

SYSTEM_AGENTS = [
    # Finance
    {"agent_type": "ap_processor", "domain": "finance", "confidence_floor": 0.880},
    {"agent_type": "ar_collections", "domain": "finance", "confidence_floor": 0.850},
    {"agent_type": "recon_agent", "domain": "finance", "confidence_floor": 0.950},
    {"agent_type": "tax_compliance", "domain": "finance", "confidence_floor": 0.920},
    {"agent_type": "close_agent", "domain": "finance", "confidence_floor": 0.800},
    {"agent_type": "fpa_agent", "domain": "finance", "confidence_floor": 0.780},
    # HR
    {"agent_type": "talent_acquisition", "domain": "hr", "confidence_floor": 0.880},
    {"agent_type": "onboarding_agent", "domain": "hr", "confidence_floor": 0.950},
    {"agent_type": "payroll_engine", "domain": "hr", "confidence_floor": 0.990},
    {"agent_type": "performance_coach", "domain": "hr", "confidence_floor": 0.800},
    {"agent_type": "ld_coordinator", "domain": "hr", "confidence_floor": 0.820},
    {"agent_type": "offboarding_agent", "domain": "hr", "confidence_floor": 0.950},
    # Marketing
    {"agent_type": "content_factory", "domain": "marketing", "confidence_floor": 0.880},
    {"agent_type": "campaign_pilot", "domain": "marketing", "confidence_floor": 0.850},
    {"agent_type": "seo_strategist", "domain": "marketing", "confidence_floor": 0.900},
    {"agent_type": "crm_intelligence", "domain": "marketing", "confidence_floor": 0.880},
    {"agent_type": "brand_monitor", "domain": "marketing", "confidence_floor": 0.850},
    # Ops
    {"agent_type": "vendor_manager", "domain": "ops", "confidence_floor": 0.880},
    {"agent_type": "contract_intelligence", "domain": "ops", "confidence_floor": 0.820},
    {"agent_type": "support_triage", "domain": "ops", "confidence_floor": 0.850},
    {"agent_type": "compliance_guard", "domain": "ops", "confidence_floor": 0.950},
    {"agent_type": "it_operations", "domain": "ops", "confidence_floor": 0.880},
    # Backoffice
    {"agent_type": "legal_ops", "domain": "backoffice", "confidence_floor": 0.900},
    {"agent_type": "risk_sentinel", "domain": "backoffice", "confidence_floor": 0.950},
    {"agent_type": "facilities_agent", "domain": "backoffice", "confidence_floor": 0.800},
    # Comms
    {"agent_type": "email_agent", "domain": "comms", "confidence_floor": 0.900},
    {"agent_type": "notification_agent", "domain": "comms", "confidence_floor": 0.880},
    {"agent_type": "chat_agent", "domain": "comms", "confidence_floor": 0.850},
]


def _humanise(agent_type: str) -> str:
    return agent_type.replace("_", " ").title()


async def seed_tenant_defaults(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Seed built-in agents and prompt templates for a new tenant.

    Idempotent — checks for existence before inserting.
    """
    await _seed_agents(session, tenant_id)
    await _seed_prompt_templates(session, tenant_id)
    logger.info("Seeded defaults for tenant %s", tenant_id)


async def _seed_agents(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    for agent_cfg in SYSTEM_AGENTS:
        agent_type = agent_cfg["agent_type"]

        existing = await session.execute(
            select(Agent.id).where(
                Agent.tenant_id == tenant_id,
                Agent.agent_type == agent_type,
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        name = _humanise(agent_type)
        agent = Agent(
            tenant_id=tenant_id,
            name=name,
            agent_type=agent_type,
            domain=agent_cfg["domain"],
            description=f"System {agent_cfg['domain']} agent: {name}",
            system_prompt_ref=f"prompts/{agent_type}.prompt.txt",
            confidence_floor=agent_cfg["confidence_floor"],
            hitl_condition=f"confidence < {agent_cfg['confidence_floor']}",
            status="shadow",
            is_builtin=True,
            employee_name=name,
        )
        session.add(agent)

    await session.flush()
    logger.info("Seeded %d agents for tenant %s", len(SYSTEM_AGENTS), tenant_id)


async def _seed_prompt_templates(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    if not PROMPTS_DIR.exists():
        logger.warning("Prompts directory not found at %s", PROMPTS_DIR)
        return

    count = 0
    for path in sorted(PROMPTS_DIR.glob("*.prompt.txt")):
        agent_type = path.stem.replace(".prompt", "")

        existing = await session.execute(
            select(PromptTemplate.id).where(
                PromptTemplate.tenant_id == tenant_id,
                PromptTemplate.name == agent_type,
                PromptTemplate.agent_type == agent_type,
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        agent_cfg = next(
            (a for a in SYSTEM_AGENTS if a["agent_type"] == agent_type), None
        )
        domain = agent_cfg["domain"] if agent_cfg else "general"

        template_text = path.read_text(encoding="utf-8")
        variables = [
            {"name": v, "description": "", "default": ""}
            for v in sorted(set(re.findall(r"\{\{(\w+)\}\}", template_text)))
        ]

        template = PromptTemplate(
            tenant_id=tenant_id,
            name=agent_type,
            agent_type=agent_type,
            domain=domain,
            template_text=template_text,
            variables=variables,
            is_builtin=True,
        )
        session.add(template)
        count += 1

    await session.flush()
    logger.info("Seeded %d prompt templates for tenant %s", count, tenant_id)
