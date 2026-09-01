"""Email utility with domain validation and fail-closed SMTP transport."""
import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText

import dns.resolver

from core.config import is_relaxed_env

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
    # enterprise-gate: broad-except-ok reason=mx-lookup-failure-fails-closed-domain-invalid
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


def send_email(to: str, subject: str, html: str) -> bool:
    """Send HTML email through the configured SMTP transport.

    Gmail over implicit TLS remains the default. Passwordless plaintext SMTP is
    accepted only for a loopback capture service in an explicitly relaxed
    environment, which lets the real delivery path be tested with Mailpit
    without weakening production transport requirements.
    """
    # Validate email domain BEFORE the fake-mail seam so tests
    # asserting "invalid domain → no send" stay accurate even when
    # the fake is active. Foundation #8 forbids the false-green
    # pattern where a fake masks a production validation contract.
    is_valid, reason = validate_email_domain(to)
    if not is_valid:
        logger.warning("Skipping email to %s: %s", to, reason)
        return False

    # Foundation #7 PR-B: hermetic-CI seam. When the env flag is
    # set, capture the email in-process instead of opening a real
    # SMTP connection. See docs/hermetic_test_doubles.md.
    from core.test_doubles import fake_mail  # noqa: PLC0415 — lazy keeps prod cold-path lean

    if fake_mail.is_active():
        display_sender = os.getenv("AGENTICORG_DEMO_SENDER", "sanjeev@agenticorg.ai")
        fake_mail.capture(to=to, subject=subject, html=html, sender=display_sender)
        logger.info("[fake_mail] captured email to=%s subject=%r", to, subject)
        return True

    host = os.getenv("AGENTICORG_SMTP_HOST", "smtp.gmail.com").strip()
    security = os.getenv("AGENTICORG_SMTP_SECURITY", "ssl").strip().lower()
    try:
        port = int(os.getenv("AGENTICORG_SMTP_PORT", "465"))
    except ValueError:
        logger.warning("Invalid AGENTICORG_SMTP_PORT; refusing email delivery")
        return False
    if not host or not 1 <= port <= 65535:
        logger.warning("Invalid SMTP host or port; refusing email delivery")
        return False
    if security not in {"ssl", "starttls", "plain"}:
        logger.warning("Unsupported SMTP security mode; refusing email delivery")
        return False

    runtime_env = os.getenv("AGENTICORG_ENV", "development")
    loopback_capture = host.lower() in {"localhost", "127.0.0.1", "::1"}
    local_plain_capture = security == "plain" and loopback_capture and is_relaxed_env(runtime_env)
    if security == "plain" and not local_plain_capture:
        logger.warning("Plain SMTP is restricted to loopback local/test capture services")
        return False

    password = os.getenv("AGENTICORG_GMAIL_APP_PASSWORD", "")
    smtp_login = os.getenv("AGENTICORG_SMTP_LOGIN", os.getenv("AGENTICORG_DEMO_SENDER", ""))
    display_sender = os.getenv("AGENTICORG_DEMO_SENDER", "sanjeev@agenticorg.ai")
    if not local_plain_capture and (not password or not smtp_login):
        logger.warning("AGENTICORG_GMAIL_APP_PASSWORD or SMTP_LOGIN not set - skipping email")
        return False
    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = f"AgenticOrg <{display_sender}>"
    msg["Reply-To"] = display_sender
    msg["To"] = to
    try:
        tls_context = ssl.create_default_context()
        connection = (
            smtplib.SMTP_SSL(host, port, timeout=15, context=tls_context)
            if security == "ssl"
            else smtplib.SMTP(host, port, timeout=15)
        )
        with connection as smtp:
            if security == "starttls":
                smtp.ehlo()
                smtp.starttls(context=tls_context)
                smtp.ehlo()
            if smtp_login and password:
                smtp.login(smtp_login, password)
            smtp.send_message(msg)
        logger.info("Email sent to %s from %s via SMTP host %s", to, display_sender, host)
        return True
    # enterprise-gate: broad-except-ok reason=smtp-send-failure-returns-false-no-delivery-success
    except Exception:
        logger.exception("Failed to send email")
        return False


def send_welcome_email(to: str, org_name: str, name: str) -> None:
    app_url = os.getenv("AGENTICORG_APP_URL", "https://app.agenticorg.ai")
    html = (
        f"<h2>Welcome, {name}!</h2>"
        f"<p>Your organization <b>{org_name}</b> is ready on AgenticOrg.</p>"
        f"<p><a href='{app_url}/dashboard'>Go to Dashboard</a></p>"
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
