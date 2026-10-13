"""Demo request endpoint — stores in DB, creates lead, triggers sales agent, emails notification."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid as _uuid
from email.errors import HeaderParseError
from email.headerregistry import Address
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from api.client_ip import client_ip as resolve_client_ip
from api.deps import get_current_tenant, require_tenant_admin
from api.route_metadata import route_meta
from core import auth_state, demo_delivery
from core.database import async_session_factory, get_tenant_session
from core.email import send_email
from core.models.lead_pipeline import LeadPipeline

logger = logging.getLogger(__name__)
router = APIRouter()

# Notification config — uses Gmail SMTP (free, 500/day)
NOTIFY_TO = "sanjeev@orchestrum.in"

# Public endpoint: per-IP ceiling so it cannot be used to spam email or LLM.
_DEMO_REQUEST_MAX_PER_HOUR = 5
_DEMO_REQUEST_WINDOW = 3600


class DemoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    company: str = Field(default="", max_length=255)
    firm: str = Field(default="", max_length=255)
    role: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=50)
    clients: str = Field(default="", max_length=80)
    source: str = Field(default="", max_length=80)

    @field_validator("name", "company", "firm", "role", "phone", "clients", "source")
    @classmethod
    def single_line(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Contact fields must be single-line text")
        return value.strip()

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value:
            raise ValueError("A name is required")
        return value

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        try:
            recipient = Address(addr_spec=value)
        except (ValueError, HeaderParseError) as exc:
            raise ValueError("A single email address is required") from exc
        if not recipient.username or not recipient.domain:
            raise ValueError("A single email address is required")
        return recipient.addr_spec.lower()

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
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="demo.seed.control_plane.write",
    rate_limit="demo-seed",
    idempotency="idempotent-demo-seed",
    audit_event="demo.seed",
)
async def seed_demo_data(tenant_id: str = Depends(get_current_tenant)):
    """Populate the tenant with realistic demo data across all modules.

    Admin-only. Idempotent — safe to call multiple times.
    """
    from core.seed_demo_data import seed_all

    result = await seed_all(tenant_id)
    return {"status": "seeded", "tenant_id": tenant_id, **result}


def _send_email_notification(body: DemoRequest) -> bool:
    """Send demo request notification email via shared email utility."""
    try:
        recipient = Address(addr_spec=os.getenv("AGENTICORG_DEMO_NOTIFY_TO", NOTIFY_TO).strip())
    except (ValueError, HeaderParseError):
        logger.warning("Invalid marketing notification recipient; notification not sent")
        return False
    if not recipient.username or not recipient.domain:
        logger.warning("Missing marketing notification recipient; notification not sent")
        return False
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
    return send_email(recipient.addr_spec, subject, html)


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
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:demo_request.external_input.write",
    rate_limit="demo-request-public",
    idempotency="lead-deduped-by-email-best-effort",
    audit_event="demo.request",
    public_reason="public-lead-capture-email-and-sales-agent-trigger",
)
async def submit_demo_request(body: DemoRequest, request: Request, background_tasks: BackgroundTasks):
    """Accept a demo request, persist it, create lead in pipeline, and trigger sales agent.

    Email delivery and the sales-agent run are queued after persistence so
    the public endpoint returns immediately and cannot be used to burn LLM
    budget or SMTP quota synchronously. Per-IP throttled (cross-replica).
    """
    client_ip = resolve_client_ip(request)
    try:
        blocked = await auth_state.check_window_rate(
            "demo_request", client_ip, _DEMO_REQUEST_MAX_PER_HOUR, _DEMO_REQUEST_WINDOW
        )
    except RuntimeError as exc:
        # Strict runtime env without Redis: fail closed rather than accept
        # unthrottled public input that fans out to email + LLM.
        logger.error("Demo request throttle unavailable in strict mode: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from exc
    if blocked:
        raise HTTPException(status_code=429, detail="Too many demo requests — try again later")

    # 1. Store in legacy demo_requests table (schema owned by Alembic
    #    migration v6z14_demo_requests; no request-time DDL).
    async with async_session_factory() as session:
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
    # Public marketing input belongs to the server-owned marketing tenant,
    # never a tenant supplied by the unauthenticated requester.
    default_tenant_id = str(demo_delivery.MARKETING_TENANT)
    lead_id = None
    try:
        tid = _uuid.UUID(default_tenant_id)
        # lead_pipeline is FORCE-RLS (v6z16): the INSERT fails WITH CHECK in a
        # raw session, so bind the default tenant context explicitly.
        async with get_tenant_session(tid) as session:
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
                # ORM defaults are required by fresh Alembic-managed schemas;
                # a raw insert bypassed non-null score/contact/page defaults.
                session.add(LeadPipeline(
                    id=new_id, tenant_id=tid, name=body.name, email=body.email,
                    company=body.effective_company, role=body.effective_role,
                    phone=body.phone, source="website", stage="new", score=0,
                ))
                await session.commit()
                lead_id = str(new_id)
                logger.info("Lead created in pipeline: %s (%s)", lead_id, body.email)
    # enterprise-gate: broad-except-ok reason=demo-lead-sidecar-failure-keeps-request-saved
    except Exception as exc:
        logger.error("Failed to create lead in pipeline (non-blocking): %s", type(exc).__name__)

    # The durable path persists encrypted input before publishing identifiers.
    # The feature flag retains a legacy in-process path for rollback only.
    delivery_id = None
    if demo_delivery.enabled():
        delivery_id = await demo_delivery.prepare(body.model_dump(), lead_id)
        await demo_delivery.enqueue(delivery_id)
    else:
        background_tasks.add_task(_demo_request_followups, body, default_tenant_id, lead_id)

    return {
        "status": "received",
        "message": "Your enquiry has been received. Email and sales follow-up are pending.",
        "lead_id": lead_id,
        "delivery_request_id": str(delivery_id) if delivery_id else None,
        "agent_triggered": False,
        "agent_queued": lead_id is not None,
        "email": {
            "internal_notification_sent": False,
            "requester_confirmation_sent": False,
            "internal_notification_status": "pending",
            "requester_confirmation_status": "pending",
        },
    }


async def _demo_request_followups(body: DemoRequest, default_tenant_id: str, lead_id: str | None) -> None:
    """Background: notify sales, confirm to requester, run the sales agent."""
    try:
        accepted = await asyncio.to_thread(_send_email_notification, body)
        logger.info("demo_notification_status=%s", "smtp_accepted" if accepted is True else "failed")
    # enterprise-gate: broad-except-ok reason=demo-internal-email-sidecar-logged-in-background
    except Exception:
        logger.exception("Demo internal notification email failed (background)")
    try:
        accepted = await asyncio.to_thread(_send_trial_confirmation, body)
        logger.info("demo_confirmation_status=%s", "smtp_accepted" if accepted is True else "failed")
    # enterprise-gate: broad-except-ok reason=demo-confirmation-email-sidecar-logged-in-background
    except Exception:
        logger.exception("Demo requester confirmation email failed (background)")
    if lead_id:
        try:
            from api.v1.sales import _run_sales_agent_on_lead
            agent_result = await _run_sales_agent_on_lead(default_tenant_id, lead_id)
            logger.info("sales_agent_triggered: %s status=%s", lead_id, agent_result.get("status"))
        # enterprise-gate: broad-except-ok reason=demo-sales-agent-sidecar-logged-in-background
        except Exception:
            logger.exception("Sales agent trigger failed (background)")
