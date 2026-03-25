"""Demo request endpoint — stores in DB, creates lead, triggers sales agent, emails notification."""
from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, text

from core.database import async_session_factory
from core.email import send_email

logger = logging.getLogger(__name__)
router = APIRouter()

# Notification config — uses Gmail SMTP (free, 500/day)
NOTIFY_TO = "sanjeev@agenticorg.ai"


class DemoRequest(BaseModel):
    name: str
    email: str
    company: str = ""
    role: str = ""
    phone: str = ""


def _send_email_notification(body: DemoRequest) -> None:
    """Send demo request notification email via shared email utility."""
    subject = f"AgenticOrg Demo Request — {body.name} ({body.role or 'Not specified'})"
    html = f"""<h2>New Demo Request</h2>
<table style="border-collapse:collapse;font-family:sans-serif;">
<tr><td style="padding:8px;font-weight:bold;">Name:</td><td style="padding:8px;">{body.name}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Email:</td><td style="padding:8px;">{body.email}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Company:</td><td style="padding:8px;">{body.company or '—'}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Role:</td><td style="padding:8px;">{body.role or '—'}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Phone:</td><td style="padding:8px;">{body.phone or '—'}</td></tr>
</table>
<p style="color:#666;font-size:12px;margin-top:20px;">Sent from agenticorg.ai</p>"""
    send_email(NOTIFY_TO, subject, html)


@router.post("/demo-request", status_code=201)
async def submit_demo_request(body: DemoRequest):
    """Accept a demo request, persist it, create lead in pipeline, and trigger sales agent."""

    # 1. Store in legacy demo_requests table
    async with async_session_factory() as session:
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS demo_requests ("
                "id SERIAL PRIMARY KEY, name TEXT, email TEXT, company TEXT, "
                "role TEXT, phone TEXT, created_at TIMESTAMPTZ DEFAULT NOW())"
            )
        )
        await session.execute(
            text(
                "INSERT INTO demo_requests (name, email, company, role, phone) "
                "VALUES (:name, :email, :company, :role, :phone)"
            ),
            {
                "name": body.name,
                "email": body.email,
                "company": body.company,
                "role": body.role,
                "phone": body.phone,
            },
        )
        await session.commit()

    # 2. Create lead in sales pipeline
    # Use the default org tenant (00000000-0000-0000-0000-000000000001)
    # This is a single-tenant deployment — hardcoding avoids repeated tenant lookup bugs
    DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
    lead_id = None
    try:
        async with async_session_factory() as session:
            tid = _uuid.UUID(DEFAULT_TENANT_ID)

            # Check for duplicate lead (same email)
            existing = await session.execute(
                text("SELECT id FROM lead_pipeline WHERE email = :email AND tenant_id = :tid"),
                {"email": body.email, "tid": tid},
            )
            dup = existing.fetchone()
            if dup:
                lead_id = str(dup[0])
                logger.info("Lead already exists", lead_id=lead_id, email=body.email)
            else:
                new_id = _uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO lead_pipeline (id, tenant_id, name, email, company, role, phone, source, stage, score) "
                        "VALUES (:id, :tid, :name, :email, :company, :role, :phone, 'website', 'new', 0)"
                    ),
                    {
                        "id": new_id, "tid": tid,
                        "name": body.name, "email": body.email,
                        "company": body.company, "role": body.role, "phone": body.phone,
                    },
                )
                await session.commit()
                lead_id = str(new_id)
                logger.info("Lead created in pipeline", lead_id=lead_id, email=body.email)
    except Exception:
        logger.exception("Failed to create lead in pipeline (non-blocking)")

    # 3. Send email notification to founder (non-blocking)
    try:
        _send_email_notification(body)
    except Exception:
        logger.exception("Email send failed but request was saved")

    # 4. Trigger sales agent to qualify + send personalized email (non-blocking)
    agent_status = None
    if lead_id:
        try:
            from api.v1.sales import _run_sales_agent_on_lead
            agent_result = await _run_sales_agent_on_lead(DEFAULT_TENANT_ID, lead_id)
            agent_status = agent_result.get("status")
            logger.info("sales_agent_triggered", lead_id=lead_id, status=agent_status)
        except Exception:
            logger.exception("Sales agent trigger failed (non-blocking)")

    return {
        "status": "received",
        "message": "We'll be in touch within 2 minutes.",
        "lead_id": lead_id,
        "agent_status": agent_status,
    }
