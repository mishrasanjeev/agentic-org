"""Demo request endpoint — stores in DB, creates lead, triggers sales agent, emails notification."""
from __future__ import annotations

import logging
import uuid as _uuid
from html import escape

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from api.deps import get_current_tenant, require_tenant_admin
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
    firm: str = ""
    role: str = ""
    phone: str = ""
    clients: str = ""
    source: str = ""

    @property
    def effective_company(self) -> str:
        return self.company or self.firm

    @property
    def effective_role(self) -> str:
        if self.role:
            return self.role
        if self.source == "ca-firms-solution":
            return "CA firm trial"
        return ""


# ── Admin: seed demo data ───────────────────────────────────────────


@router.post("/admin/seed-demo", dependencies=[require_tenant_admin])
async def seed_demo_data(tenant_id: str = Depends(get_current_tenant)):
    """Populate the tenant with realistic demo data across all modules.

    Admin-only. Idempotent — safe to call multiple times.
    """
    from core.seed_demo_data import seed_all

    result = await seed_all(tenant_id)
    return {"status": "seeded", "tenant_id": tenant_id, **result}


def _send_email_notification(body: DemoRequest) -> bool:
    """Send demo request notification email via shared email utility."""
    subject = f"AgenticOrg Demo Request - {body.name} ({body.effective_role or 'Not specified'})"
    company = escape(body.effective_company or "-")
    role = escape(body.effective_role or "-")
    clients = escape(body.clients or "-")
    source = escape(body.source or "-")
    html = f"""<h2>New Demo Request</h2>
<table style="border-collapse:collapse;font-family:sans-serif;">
<tr><td style="padding:8px;font-weight:bold;">Name:</td><td style="padding:8px;">{escape(body.name)}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Email:</td><td style="padding:8px;">{escape(body.email)}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Company:</td><td style="padding:8px;">{company}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Role:</td><td style="padding:8px;">{role}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Phone:</td><td style="padding:8px;">{escape(body.phone or '-')}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Client range:</td><td style="padding:8px;">{clients}</td></tr>
<tr><td style="padding:8px;font-weight:bold;">Source:</td><td style="padding:8px;">{source}</td></tr>
</table>
<p style="color:#666;font-size:12px;margin-top:20px;">Sent from agenticorg.ai</p>"""
    return send_email(NOTIFY_TO, subject, html)


def _send_trial_confirmation(body: DemoRequest) -> bool:
    """Send the requester a confirmation instead of only notifying sales."""
    product = "CA Firm trial" if body.source == "ca-firms-solution" else "AgenticOrg demo"
    html = f"""<h2>{escape(product)} request received</h2>
<p>Hi {escape(body.name)},</p>
<p>We received your request for {escape(body.effective_company or 'your organization')}.</p>
<p>Our team will contact you with next steps. No password or OAuth token is required by email.</p>
<p style="color:#666;font-size:12px;margin-top:20px;">AgenticOrg</p>"""
    return send_email(body.email, f"AgenticOrg {product} request received", html)


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
                "company": body.effective_company,
                "role": body.effective_role,
                "phone": body.phone,
            },
        )
        await session.commit()

    # 2. Create lead in sales pipeline
    # Use the default org tenant (00000000-0000-0000-0000-000000000001)
    # This is a single-tenant deployment — hardcoding avoids repeated tenant lookup bugs
    default_tenant_id = "00000000-0000-0000-0000-000000000001"
    lead_id = None
    try:
        async with async_session_factory() as session:
            tid = _uuid.UUID(default_tenant_id)

            # Check for duplicate lead (same email)
            existing = await session.execute(
                text("SELECT id FROM lead_pipeline WHERE email = :email AND tenant_id = :tid"),
                {"email": body.email, "tid": tid},
            )
            dup = existing.fetchone()
            if dup:
                lead_id = str(dup[0])
                logger.info("Lead already exists: %s (%s)", lead_id, body.email)
            else:
                new_id = _uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO lead_pipeline (id, tenant_id, name, email, "
                        "company, role, phone, source, stage, score) "
                        "VALUES (:id, :tid, :name, :email, "
                        ":company, :role, :phone, 'website', 'new', 0)"
                    ),
                    {
                        "id": new_id, "tid": tid,
                        "name": body.name, "email": body.email,
                        "company": body.effective_company,
                        "role": body.effective_role,
                        "phone": body.phone,
                    },
                )
                await session.commit()
                lead_id = str(new_id)
                logger.info("Lead created in pipeline: %s (%s)", lead_id, body.email)
    except Exception:
        logger.exception("Failed to create lead in pipeline (non-blocking)")

    # 3. Send email notification to founder and requester (non-blocking)
    internal_notification_sent = False
    requester_confirmation_sent = False
    try:
        internal_notification_sent = _send_email_notification(body)
    except Exception:
        logger.exception("Email send failed but request was saved")
    try:
        requester_confirmation_sent = _send_trial_confirmation(body)
    except Exception:
        logger.exception("Requester confirmation email failed but request was saved")

    # 4. Trigger sales agent to qualify + send personalized email (non-blocking)
    agent_status = None
    if lead_id:
        try:
            from api.v1.sales import _run_sales_agent_on_lead
            agent_result = await _run_sales_agent_on_lead(default_tenant_id, lead_id)
            agent_status = agent_result.get("status")
            logger.info("sales_agent_triggered: %s status=%s", lead_id, agent_status)
        except Exception:
            logger.exception("Sales agent trigger failed (non-blocking)")

    return {
        "status": "received",
        "message": "We'll be in touch within 2 minutes.",
        "lead_id": lead_id,
        "agent_triggered": agent_status is not None,
        "email": {
            "internal_notification_sent": internal_notification_sent,
            "requester_confirmation_sent": requester_confirmation_sent,
        },
    }
