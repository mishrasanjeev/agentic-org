"""Email utility — Gmail SMTP for notifications."""
import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, html: str) -> None:
    """Send HTML email via Gmail SMTP. Fails silently."""
    password = os.getenv("AGENTICORG_GMAIL_APP_PASSWORD", "")
    sender = os.getenv("AGENTICORG_DEMO_SENDER", "mishra.sanjeev@gmail.com")
    if not password:
        logger.warning("AGENTICORG_GMAIL_APP_PASSWORD not set — skipping email")
        return
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    except Exception:
        logger.exception("Failed to send email")


def send_welcome_email(to: str, org_name: str, name: str) -> None:
    html = (
        f"<h2>Welcome, {name}!</h2>"
        f"<p>Your organization <b>{org_name}</b> is ready on AgenticOrg.</p>"
        "<p><a href='https://app.agenticorg.ai/dashboard'>Go to Dashboard</a></p>"
    )
    send_email(to, f"Welcome to AgenticOrg — {org_name}", html)


def send_invite_email(
    to: str, org_name: str, inviter: str, role: str, invite_link: str,
) -> None:
    html = (
        f"<h2>Join {org_name} on AgenticOrg</h2>"
        f"<p><b>{inviter}</b> invited you as <b>{role}</b>.</p>"
        f"<p><a href='{invite_link}'>Accept Invitation</a></p>"
    )
    send_email(to, f"You're invited to {org_name} on AgenticOrg", html)
