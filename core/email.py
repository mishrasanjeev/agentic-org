"""Email utility — Gmail SMTP for notifications with domain validation."""
import logging
import os
import smtplib
from email.mime.text import MIMEText

import dns.resolver

logger = logging.getLogger(__name__)

# Domains that are known to bounce or are test-only
_BLOCKED_DOMAINS = {
    "example.com", "test.com", "localhost", "mailinator.com",
    "guerrillamail.com", "sharklasers.com", "yopmail.com",
}


def _has_mx_record(domain: str) -> bool:
    """Check if a domain has valid MX records (can receive email)."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


def validate_email_domain(email: str) -> tuple[bool, str]:
    """Validate that an email address can receive mail.

    Returns (is_valid, reason).
    """
    if "@" not in email:
        return False, "Invalid email format"

    domain = email.split("@")[-1].lower().strip()

    # Block test/fake domains
    if domain.endswith(".local") or domain.endswith(".test"):
        return False, f"Test domain: {domain}"
    if domain in _BLOCKED_DOMAINS:
        return False, f"Blocked domain: {domain}"

    # Check MX records
    if not _has_mx_record(domain):
        return False, f"No MX records for {domain} — cannot receive email"

    return True, "OK"


def send_email(to: str, subject: str, html: str) -> None:
    """Send HTML email via Gmail SMTP. Validates domain before sending.

    Uses AGENTICORG_SMTP_LOGIN for SMTP authentication (Gmail account)
    and AGENTICORG_DEMO_SENDER for the display From address.
    """
    password = os.getenv("AGENTICORG_GMAIL_APP_PASSWORD", "")
    smtp_login = os.getenv("AGENTICORG_SMTP_LOGIN", os.getenv("AGENTICORG_DEMO_SENDER", ""))
    display_sender = os.getenv("AGENTICORG_DEMO_SENDER", "sanjeev@agenticorg.ai")
    if not password or not smtp_login:
        logger.warning("AGENTICORG_GMAIL_APP_PASSWORD or SMTP_LOGIN not set — skipping email")
        return

    # Validate email domain before sending
    is_valid, reason = validate_email_domain(to)
    if not is_valid:
        logger.warning("Skipping email to %s: %s", to, reason)
        return
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = f"AgenticOrg <{display_sender}>"
    msg["Reply-To"] = display_sender
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(smtp_login, password)
            smtp.send_message(msg)
        logger.info("Email sent to %s from %s (via %s)", to, display_sender, smtp_login)
    except Exception:
        logger.exception("Failed to send email")


def send_welcome_email(to: str, org_name: str, name: str) -> None:
    html = (
        f"<h2>Welcome, {name}!</h2>"
        f"<p>Your organization <b>{org_name}</b> is ready on AgenticOrg.</p>"
        "<p><a href='https://app.agenticorg.ai/dashboard'>Go to Dashboard</a></p>"
    )
    send_email(to, f"Welcome to AgenticOrg — {org_name}", html)


def send_password_reset_email(to: str, reset_link: str) -> None:
    html = (
        "<h2>Reset Your Password</h2>"
        "<p>We received a request to reset your AgenticOrg password.</p>"
        f"<p><a href='{reset_link}'>Reset Password</a></p>"
        "<p>This link expires in 1 hour. If you didn't request this, ignore this email.</p>"
    )
    send_email(to, "Password Reset — AgenticOrg", html)


def send_invite_email(
    to: str, org_name: str, inviter: str, role: str, invite_link: str,
) -> None:
    html = (
        f"<h2>Join {org_name} on AgenticOrg</h2>"
        f"<p><b>{inviter}</b> invited you as <b>{role}</b>.</p>"
        f"<p><a href='{invite_link}'>Accept Invitation</a></p>"
    )
    send_email(to, f"You're invited to {org_name} on AgenticOrg", html)
