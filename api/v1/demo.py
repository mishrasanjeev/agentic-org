"""Demo request endpoint — stores in DB and emails notification."""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from core.database import async_session_factory

logger = logging.getLogger(__name__)
router = APIRouter()

# Notification config — uses Gmail SMTP (free, 500/day)
# Set AGENTICORG_DEMO_NOTIFY_EMAIL and AGENTICORG_GMAIL_APP_PASSWORD in env/secrets
NOTIFY_TO = "mishra.sanjeev@gmail.com"


class DemoRequest(BaseModel):
    name: str
    email: str
    company: str = ""
    role: str = ""
    phone: str = ""


def _send_email(body: DemoRequest) -> None:
    """Send email notification via Gmail SMTP. Fails silently."""
    import os

    app_password = os.getenv("AGENTICORG_GMAIL_APP_PASSWORD", "")
    sender = os.getenv("AGENTICORG_DEMO_SENDER", "agenticorg.alerts@gmail.com")
    if not app_password:
        logger.warning("AGENTICORG_GMAIL_APP_PASSWORD not set — skipping email")
        return

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

    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = NOTIFY_TO
    msg["Reply-To"] = body.email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, app_password)
            smtp.send_message(msg)
        logger.info("Demo request email sent to %s", NOTIFY_TO)
    except Exception:
        logger.exception("Failed to send demo request email")


@router.post("/demo-request", status_code=201)
async def submit_demo_request(body: DemoRequest):
    """Accept a demo request, persist it, and email notification."""
    # Store in DB
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

    # Send email notification (non-blocking — don't fail the request)
    try:
        _send_email(body)
    except Exception:
        logger.exception("Email send failed but request was saved")

    return {"status": "received", "message": "We'll be in touch within 24 hours."}
