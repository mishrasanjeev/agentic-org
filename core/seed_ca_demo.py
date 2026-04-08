"""Seed 7 realistic Indian client companies + demo CA user for demos.

Usage:
    from core.seed_ca_demo import seed_ca_demo
    await seed_ca_demo(session, tenant_id)

Idempotent -- checks for existing records before inserting.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC

import bcrypt as _bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.ca_subscription import CASubscription
from core.models.company import Company
from core.models.tenant import Tenant
from core.models.user import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo tenant / user constants
# ---------------------------------------------------------------------------
DEMO_TENANT_SLUG = "demo-ca-firm"
DEMO_TENANT_NAME = "Demo CA Firm"
DEMO_USER_EMAIL = "demo@cafirm.agenticorg.ai"
DEMO_USER_PASSWORD = "demo123!"
DEMO_USER_NAME = "Demo Partner"
DEMO_USER_ROLE = "admin"  # platform role -- company-level role is "partner"

# ---------------------------------------------------------------------------
# 7 realistic Indian companies
# ---------------------------------------------------------------------------
DEMO_COMPANIES: list[dict] = [
    {
        "name": "Sharma Manufacturing Pvt Ltd",
        "gstin": "27AABCS1234F1Z5",
        "pan": "AABCS1234F",
        "tan": "MUMS12345E",
        "cin": "U28100MH2018PTC123456",
        "state_code": "27",
        "industry": "Manufacturing",
        "registered_address": "Plot 42, MIDC Andheri East, Mumbai 400093",
        "signatory_name": "Rajesh Sharma",
        "signatory_designation": "Managing Director",
        "compliance_email": "compliance@sharmamanufacturing.co.in",
        "bank_name": "HDFC Bank",
        "bank_account_number": "50100123456789",
        "bank_ifsc": "HDFC0001234",
        "pf_registration": "MHBAN0012345000",
        "esi_registration": "3100012345600001",
        "client_health_score": 95,
        "gst_auto_file": True,
    },
    {
        "name": "Gupta Traders",
        "gstin": "07AADCG5678H1Z3",
        "pan": "AADCG5678H",
        "tan": "DELS56789F",
        "state_code": "07",
        "industry": "Retail",
        "registered_address": "B-12, Karol Bagh, New Delhi 110005",
        "signatory_name": "Amit Gupta",
        "signatory_designation": "Proprietor",
        "compliance_email": "accounts@guptatraders.in",
        "bank_name": "ICICI Bank",
        "bank_account_number": "123456789012",
        "bank_ifsc": "ICIC0001234",
        "client_health_score": 88,
    },
    {
        "name": "Patel Pharma Ltd",
        "gstin": "24AABCP9876L1Z8",
        "pan": "AABCP9876L",
        "tan": "AHMP98765G",
        "cin": "L24230GJ2015PLC098765",
        "state_code": "24",
        "industry": "Pharmaceutical",
        "registered_address": "Survey No 15, Sanand GIDC, Ahmedabad 382110",
        "signatory_name": "Nikhil Patel",
        "signatory_designation": "Director",
        "compliance_email": "ca@patelpharma.com",
        "bank_name": "State Bank of India",
        "bank_account_number": "38765432109876",
        "bank_ifsc": "SBIN0005432",
        "pf_registration": "GJAHD0098765000",
        "esi_registration": "2400098765400001",
        "client_health_score": 72,
    },
    {
        "name": "Reddy Constructions",
        "gstin": "36AABCR3456K1Z2",
        "pan": "AABCR3456K",
        "tan": "HYDR34567H",
        "state_code": "36",
        "industry": "Construction",
        "registered_address": "Plot 78, Jubilee Hills, Hyderabad 500033",
        "signatory_name": "Venkat Reddy",
        "signatory_designation": "Partner",
        "compliance_email": "finance@reddyconstructions.in",
        "bank_name": "Axis Bank",
        "bank_account_number": "920020012345678",
        "bank_ifsc": "UTIB0002345",
        "pf_registration": "APHYD0034567000",
        "client_health_score": 45,
    },
    {
        "name": "Singh Logistics Pvt Ltd",
        "gstin": "03AABCS7890M1Z6",
        "pan": "AABCS7890M",
        "tan": "CHNS78901A",
        "cin": "U60230PB2020PTC045678",
        "state_code": "03",
        "industry": "Transport & Logistics",
        "registered_address": "Warehouse Complex, GT Road, Ludhiana 141001",
        "signatory_name": "Harpreet Singh",
        "signatory_designation": "Director",
        "compliance_email": "accounts@singhlogistics.co.in",
        "bank_name": "Punjab National Bank",
        "bank_account_number": "0123456789012345",
        "bank_ifsc": "PUNB0123400",
        "pf_registration": "PBLDH0045678000",
        "esi_registration": "0300045678900001",
        "client_health_score": 91,
        "gst_auto_file": True,
    },
    {
        "name": "Joshi IT Solutions Pvt Ltd",
        "gstin": "29AABCJ2345N1Z4",
        "pan": "AABCJ2345N",
        "tan": "BLRJ23456B",
        "cin": "U72200KA2019PTC056789",
        "state_code": "29",
        "industry": "Information Technology",
        "registered_address": "3rd Floor, Prestige Tower, Whitefield, Bengaluru 560066",
        "signatory_name": "Priya Joshi",
        "signatory_designation": "CEO",
        "compliance_email": "finance@joshiitsolutions.com",
        "bank_name": "Kotak Mahindra Bank",
        "bank_account_number": "4567890123456",
        "bank_ifsc": "KKBK0005678",
        "pf_registration": "KABNG0056789000",
        "esi_registration": "5300056789100001",
        "client_health_score": 85,
    },
    {
        "name": "Agarwal Textiles",
        "gstin": "08AABCA6789P1Z1",
        "pan": "AABCA6789P",
        "tan": "JPRA67890C",
        "state_code": "08",
        "industry": "Textiles",
        "registered_address": "Nehru Bazaar, Jodhpur 342001",
        "signatory_name": "Deepak Agarwal",
        "signatory_designation": "Proprietor",
        "compliance_email": "tax@agarwaltextiles.in",
        "bank_name": "Bank of Baroda",
        "bank_account_number": "78901234567890",
        "bank_ifsc": "BARB0JODHPU",
        "client_health_score": 67,
    },
]


async def _ensure_demo_tenant(session: AsyncSession) -> uuid.UUID:
    """Return the demo tenant id, creating it if it does not exist."""
    result = await session.execute(
        select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG)
    )
    tenant = result.scalar_one_or_none()
    if tenant:
        return tenant.id

    tenant = Tenant(
        id=uuid.uuid4(),
        name=DEMO_TENANT_NAME,
        slug=DEMO_TENANT_SLUG,
        settings={"onboarding_complete": True},
    )
    session.add(tenant)
    await session.flush()
    logger.info("Created demo CA tenant %s (%s)", tenant.id, DEMO_TENANT_SLUG)
    return tenant.id


async def _ensure_demo_user(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Create the demo partner user if it does not exist."""
    result = await session.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.email == DEMO_USER_EMAIL,
        )
    )
    if result.scalar_one_or_none():
        return

    pw_hash = _bcrypt.hashpw(
        DEMO_USER_PASSWORD.encode(), _bcrypt.gensalt(rounds=12)
    ).decode()
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=DEMO_USER_EMAIL,
        name=DEMO_USER_NAME,
        role=DEMO_USER_ROLE,
        domain="all",
        password_hash=pw_hash,
        status="active",
    )
    session.add(user)
    await session.flush()
    logger.info("Created demo user %s for tenant %s", DEMO_USER_EMAIL, tenant_id)


async def _seed_companies(
    session: AsyncSession, tenant_id: uuid.UUID, demo_user_id: uuid.UUID | None = None
) -> int:
    """Insert demo companies that do not already exist.  Returns count of new rows."""
    count = 0
    for data in DEMO_COMPANIES:
        # Check by GSTIN within the tenant (unique constraint)
        existing = await session.execute(
            select(Company.id).where(
                Company.tenant_id == tenant_id,
                Company.gstin == data["gstin"],
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        # Build user_roles mapping -- give the demo user the "partner" role
        user_roles: dict[str, str] = {}
        if demo_user_id:
            user_roles[str(demo_user_id)] = "partner"

        company = Company(
            tenant_id=tenant_id,
            name=data["name"],
            gstin=data.get("gstin"),
            pan=data["pan"],
            tan=data.get("tan"),
            cin=data.get("cin"),
            state_code=data.get("state_code"),
            industry=data.get("industry"),
            registered_address=data.get("registered_address"),
            signatory_name=data.get("signatory_name"),
            signatory_designation=data.get("signatory_designation"),
            compliance_email=data.get("compliance_email"),
            bank_name=data.get("bank_name"),
            bank_account_number=data.get("bank_account_number"),
            bank_ifsc=data.get("bank_ifsc"),
            pf_registration=data.get("pf_registration"),
            esi_registration=data.get("esi_registration"),
            gst_auto_file=data.get("gst_auto_file", False),
            client_health_score=data.get("client_health_score", 100),
            user_roles=user_roles,
        )
        session.add(company)
        count += 1

    if count:
        await session.flush()
    return count


async def seed_ca_demo(session: AsyncSession, tenant_id: uuid.UUID | None = None) -> None:
    """Top-level entry point: seed demo tenant, user, and 7 companies.

    If *tenant_id* is provided the companies are created under that
    existing tenant.  Otherwise a dedicated demo tenant is created.
    """
    if tenant_id is None:
        tenant_id = await _ensure_demo_tenant(session)

    await _ensure_demo_user(session, tenant_id)

    # Fetch demo user id for role mapping
    result = await session.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.email == DEMO_USER_EMAIL,
        )
    )
    demo_user_id = result.scalar_one_or_none()

    inserted = await _seed_companies(session, tenant_id, demo_user_id)

    # Ensure CA subscription exists for demo tenant
    existing_sub = await session.execute(
        select(CASubscription.id).where(
            CASubscription.tenant_id == tenant_id,
        ).limit(1)
    )
    if not existing_sub.scalar_one_or_none():
        from datetime import datetime, timedelta

        sub = CASubscription(
            tenant_id=tenant_id,
            plan="ca_pro",
            status="trial",
            max_clients=7,
            price_inr=4999,
            price_usd=59,
            trial_ends_at=datetime.now(UTC) + timedelta(days=14),
        )
        session.add(sub)
        await session.flush()
        logger.info("Created CA subscription for demo tenant %s", tenant_id)

    logger.info(
        "CA demo seed complete: tenant=%s, new_companies=%d",
        tenant_id,
        inserted,
    )
