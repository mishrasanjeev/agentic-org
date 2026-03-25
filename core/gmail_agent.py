"""Gmail inbox monitor — reads replies, triggers sales agent, sends responses.

Uses Google Service Account with domain-wide delegation to access
sanjeev@agenticorg.ai inbox via Gmail API.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText

from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Gmail API scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

DELEGATED_USER = os.getenv("AGENTICORG_GMAIL_USER", "sanjeev@agenticorg.ai")
SA_KEY_PATH = os.getenv("AGENTICORG_GMAIL_SA_KEY", "/app/secrets/gmail-sa-key.json")


def _get_gmail_service():
    """Build authenticated Gmail API service using service account + delegation."""
    from googleapiclient.discovery import build

    # Try env var for key JSON, then file path
    sa_key_json = os.getenv("AGENTICORG_GMAIL_SA_KEY_JSON")
    if sa_key_json:
        info = json.loads(sa_key_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    elif os.path.exists(SA_KEY_PATH):
        creds = service_account.Credentials.from_service_account_file(SA_KEY_PATH, scopes=SCOPES)
    else:
        raise FileNotFoundError(f"Gmail SA key not found at {SA_KEY_PATH} and AGENTICORG_GMAIL_SA_KEY_JSON not set")

    # Delegate to the actual user's inbox
    creds = creds.with_subject(DELEGATED_USER)
    return build("gmail", "v1", credentials=creds)


def get_recent_replies(since_hours: int = 24) -> list[dict]:
    """Fetch recent reply emails from inbox.

    Returns list of dicts: {message_id, thread_id, from_email, from_name, subject, body, received_at}
    """
    service = _get_gmail_service()

    # Search for replies to our sales emails in the last N hours
    after_epoch = int((datetime.now(UTC) - timedelta(hours=since_hours)).timestamp())
    query = f"is:inbox after:{after_epoch} -from:me"

    results = service.users().messages().list(
        userId="me", q=query, maxResults=20
    ).execute()

    messages = results.get("messages", [])
    replies = []

    for msg_summary in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_summary["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        from_header = headers.get("from", "")
        subject = headers.get("subject", "")

        # Extract email from "Name <email>" format
        email_match = re.search(r"<([^>]+)>", from_header)
        from_email = email_match.group(1) if email_match else from_header
        from_name = from_header.split("<")[0].strip().strip('"') if "<" in from_header else from_email

        # Skip our own emails
        if DELEGATED_USER in from_email:
            continue

        # Get body
        body = _extract_body(msg.get("payload", {}))

        replies.append({
            "message_id": msg_summary["id"],
            "thread_id": msg_summary.get("threadId"),
            "from_email": from_email.lower().strip(),
            "from_name": from_name,
            "subject": subject,
            "body": body[:2000],  # Truncate long bodies
            "received_at": datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=UTC).isoformat(),
        })

    return replies


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Recurse into multipart
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result

    # Fallback: try HTML
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html).strip()

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html).strip()

    return ""


def send_reply(thread_id: str, to: str, subject: str, html_body: str) -> str:
    """Send a reply email in the same thread."""
    service = _get_gmail_service()

    msg = MIMEText(html_body, "html")
    msg["to"] = to
    msg["from"] = f"Sanjeev Kumar <{DELEGATED_USER}>"
    msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": thread_id},
    ).execute()

    return sent.get("id", "")


def mark_as_read(message_id: str) -> None:
    """Remove UNREAD label from a message."""
    service = _get_gmail_service()
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
