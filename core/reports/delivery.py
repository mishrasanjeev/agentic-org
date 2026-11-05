"""Multi-channel report delivery — email, Slack, WhatsApp.

Each channel method is async and uses the corresponding AgenticOrg
connector (SendGrid, Slack, WhatsApp).  The top-level ``deliver``
function dispatches to the right handler based on the channel config.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Email delivery (SendGrid)
# ══════════════════════════════════════════════════════════════════════

async def deliver_email(
    report_path: str,
    recipient_email: str,
    subject: str,
    body_text: str = "",
) -> dict[str, Any]:
    """Send a report as an email attachment via SendGrid.

    Requires ``SENDGRID_API_KEY`` and ``AGENTICORG_FROM_EMAIL`` env vars.
    """
    log.info(
        "delivery_email_start",
        recipient=recipient_email,
        report_path=report_path,
    )

    api_key = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("AGENTICORG_FROM_EMAIL", "reports@agenticorg.com")

    if not api_key:
        log.warning("delivery_email_skip", reason="SENDGRID_API_KEY not set")
        return {"status": "skipped", "reason": "SENDGRID_API_KEY not configured"}

    try:
        import base64

        import httpx

        file_path = Path(report_path)
        file_content = file_path.read_bytes()
        encoded = base64.b64encode(file_content).decode("ascii")

        # Determine MIME type from extension.
        ext = file_path.suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        payload = {
            "personalizations": [{"to": [{"email": recipient_email}]}],
            "from": {"email": from_email, "name": "AgenticOrg Reports"},
            "subject": subject,
            "content": [
                {
                    "type": "text/plain",
                    "value": body_text or f"Please find attached your scheduled report: {subject}",
                },
            ],
            "attachments": [
                {
                    "content": encoded,
                    "filename": file_path.name,
                    "type": mime_type,
                    "disposition": "attachment",
                },
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()

        log.info("delivery_email_sent", recipient=recipient_email, status=resp.status_code)
        return {"status": "sent", "channel": "email", "recipient": recipient_email}

    # enterprise-gate: broad-except-ok reason=report-email-delivery-boundary-reraises-failure
    except Exception as exc:
        log.error("delivery_email_failed", recipient=recipient_email, error=str(exc))
        raise


# ══════════════════════════════════════════════════════════════════════
# Slack delivery
# ══════════════════════════════════════════════════════════════════════

async def deliver_slack(
    report_path: str,
    channel_id: str,
    message: str = "",
) -> dict[str, Any]:
    """Upload a report file to a Slack channel.

    Requires ``SLACK_BOT_TOKEN`` env var with ``files:write`` scope.
    """
    log.info(
        "delivery_slack_start",
        channel=channel_id,
        report_path=report_path,
    )

    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        log.warning("delivery_slack_skip", reason="SLACK_BOT_TOKEN not set")
        return {"status": "skipped", "reason": "SLACK_BOT_TOKEN not configured"}

    try:
        import httpx

        file_path = Path(report_path)

        # Step 1: files.getUploadURLExternal
        async with httpx.AsyncClient(timeout=60) as client:
            url_resp = await client.post(
                "https://slack.com/api/files.getUploadURLExternal",
                data={
                    "filename": file_path.name,
                    "length": file_path.stat().st_size,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            url_data = url_resp.json()
            if not url_data.get("ok"):
                raise RuntimeError(f"Slack getUploadURLExternal failed: {url_data.get('error')}")

            upload_url = url_data["upload_url"]
            file_id = url_data["file_id"]

            # Step 2: Upload the file bytes
            await client.post(
                upload_url,
                content=file_path.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
            )

            # Step 3: files.completeUploadExternal
            complete_resp = await client.post(
                "https://slack.com/api/files.completeUploadExternal",
                json={
                    "files": [{"id": file_id, "title": file_path.name}],
                    "channel_id": channel_id,
                    "initial_comment": message or "Scheduled report from AgenticOrg",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            complete_data = complete_resp.json()
            if not complete_data.get("ok"):
                raise RuntimeError(f"Slack completeUploadExternal failed: {complete_data.get('error')}")

        log.info("delivery_slack_sent", channel=channel_id, file_id=file_id)
        return {"status": "sent", "channel": "slack", "target": channel_id, "file_id": file_id}

    # enterprise-gate: broad-except-ok reason=report-slack-delivery-boundary-reraises-failure
    except Exception as exc:
        log.error("delivery_slack_failed", channel=channel_id, error=str(exc))
        raise


# ══════════════════════════════════════════════════════════════════════
# WhatsApp delivery (Cloud API)
# ══════════════════════════════════════════════════════════════════════

# Document types the WhatsApp Cloud API accepts as a document message.
WHATSAPP_DOCUMENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}
# WhatsApp refuses a free-form message outside the 24-hour customer service window with this code.
WHATSAPP_OUTSIDE_WINDOW = 131047


def mask_phone(phone: str) -> str:
    """A phone number for logs and run history: its last four digits only."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return f"***{digits[-4:]}" if digits else "***"


def _whatsapp_error(response: Any) -> tuple[int | None, str]:
    """WhatsApp's error code and title from a Cloud API response (never the token or the payload)."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, f"HTTP {getattr(response, 'status_code', '?')}"
    code = error.get("code")
    data = error.get("error_data")
    details = data.get("details") if isinstance(data, dict) else None
    title = str(error.get("title") or details or error.get("message") or "error")[:160]
    return (int(code) if isinstance(code, int) else None), title


def whatsapp_message(phone_number: str, media_id: str, filename: str, caption: str) -> dict[str, Any]:
    """The send payload: the approved report template when one is set, else a plain document."""
    from core.config import settings

    template = (settings.whatsapp_report_template or "").strip()
    if template:
        header = {"type": "document", "document": {"id": media_id, "filename": filename}}
        components: list[dict[str, Any]] = [{"type": "header", "parameters": [header]}]
        if settings.whatsapp_report_template_body_text:
            text = (caption or "Scheduled report from AgenticOrg")[:1024]
            components.append({"type": "body", "parameters": [{"type": "text", "text": text}]})
        return {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": settings.whatsapp_report_template_language},
                "components": components,
            },
        }
    return {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "document",
        "document": {"id": media_id, "caption": caption or "Scheduled report from AgenticOrg", "filename": filename},
    }


async def deliver_whatsapp(
    report_path: str,
    phone_number: str,
    caption: str = "",
) -> dict[str, Any]:
    """Send a report over the WhatsApp Cloud API: upload the file, then send it.

    Requires ``WHATSAPP_TOKEN`` and ``WHATSAPP_PHONE_ID``. With
    ``AGENTICORG_WHATSAPP_REPORT_TEMPLATE`` set the file goes as that approved
    template's document header, which WhatsApp delivers to anyone; without it, as
    a plain document, which WhatsApp delivers only to someone who wrote in the
    last 24 hours (otherwise the send fails with error 131047 and the reason says
    so). A file type WhatsApp does not accept (a ZIP pack) is skipped. Every
    response is checked; a refusal raises with WhatsApp's code and title only.
    Logs carry the number's last four digits.
    """
    from core.config import settings

    masked = mask_phone(phone_number)
    log.info("delivery_whatsapp_start", phone=masked)

    token = os.getenv("WHATSAPP_TOKEN", "")
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "")

    if not token or not phone_id:
        log.warning("delivery_whatsapp_skip", reason="WHATSAPP_TOKEN or WHATSAPP_PHONE_ID not set")
        return {"status": "skipped", "reason": "WhatsApp credentials not configured"}

    file_path = Path(report_path)
    mime_type = WHATSAPP_DOCUMENT_TYPES.get(file_path.suffix.lower())
    if mime_type is None:
        log.warning("delivery_whatsapp_skip", reason="unsupported_file_type", suffix=file_path.suffix.lower()[:8])
        kind = file_path.suffix or "this"
        return {
            "status": "skipped",
            "channel": "whatsapp",
            "reason": f"WhatsApp does not accept {kind} files; the file is on the Downloads page",
        }
    base = f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/{phone_id}"
    mode = "template" if (settings.whatsapp_report_template or "").strip() else "document"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            upload_resp = await client.post(
                f"{base}/media",
                files={"file": (file_path.name, file_path.read_bytes(), mime_type)},
                data={"messaging_product": "whatsapp", "type": mime_type},
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                upload_data = upload_resp.json()
            except ValueError:
                upload_data = {}
            media_id = upload_data.get("id") if isinstance(upload_data, dict) else None
            if upload_resp.status_code >= 400 or not media_id:
                code, title = _whatsapp_error(upload_resp)
                raise RuntimeError(f"WhatsApp media upload failed: {code or ''} {title}".replace("  ", " ").strip())

            send_resp = await client.post(
                f"{base}/messages",
                json=whatsapp_message(phone_number, media_id, file_path.name, caption),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            try:
                sent = send_resp.json()
            except ValueError:
                sent = {}
            messages = sent.get("messages") if isinstance(sent, dict) else None
            if send_resp.status_code >= 400 or not messages:
                code, title = _whatsapp_error(send_resp)
                if code == WHATSAPP_OUTSIDE_WINDOW:
                    raise RuntimeError(
                        "WhatsApp refused a plain message outside the 24-hour window (131047); "
                        "set AGENTICORG_WHATSAPP_REPORT_TEMPLATE to an approved template"
                    )
                raise RuntimeError(f"WhatsApp send failed: {code or ''} {title}".replace("  ", " ").strip())

        log.info("delivery_whatsapp_sent", phone=masked, mode=mode)
        return {
            "status": "sent",
            "channel": "whatsapp",
            "target": phone_number,
            "media_id": media_id,
            "mode": mode,
        }

    # enterprise-gate: broad-except-ok reason=report-whatsapp-delivery-boundary-reraises-failure
    except Exception as exc:
        log.error("delivery_whatsapp_failed", phone=masked, error=str(exc)[:200])
        raise


# ══════════════════════════════════════════════════════════════════════
# Dispatcher
# ══════════════════════════════════════════════════════════════════════

async def deliver(report_path: str, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dispatch delivery to the appropriate channel handler.

    Parameters
    ----------
    report_path : str
        Path to the rendered report file (PDF or Excel).
    channels : list[dict]
        Each dict must contain ``type`` (email|slack|whatsapp) and
        ``target`` (email address / channel ID / phone number).
        Optional keys: ``subject``, ``message``, ``caption``.

    Returns
    -------
    list[dict]
        Per-channel delivery result dicts.
    """
    results: list[dict[str, Any]] = []

    for ch in channels:
        ch_type = ch.get("type", "").lower()
        target = ch.get("target", "")

        if not target:
            log.warning("delivery_skip_no_target", channel_type=ch_type)
            results.append({"status": "skipped", "channel": ch_type, "reason": "no target"})
            continue

        try:
            if ch_type == "email":
                result = await deliver_email(
                    report_path=report_path,
                    recipient_email=target,
                    subject=ch.get("subject", "AgenticOrg Scheduled Report"),
                    body_text=ch.get("message", ""),
                )
            elif ch_type == "slack":
                result = await deliver_slack(
                    report_path=report_path,
                    channel_id=target,
                    message=ch.get("message", ""),
                )
            elif ch_type == "whatsapp":
                result = await deliver_whatsapp(
                    report_path=report_path,
                    phone_number=target,
                    caption=ch.get("caption", ""),
                )
            else:
                log.warning("delivery_unknown_channel", channel_type=ch_type)
                result = {"status": "skipped", "channel": ch_type, "reason": "unknown channel type"}

            results.append(result)

        # enterprise-gate: broad-except-ok reason=report-delivery-dispatch-records-failed-channel
        except Exception as exc:
            shown = mask_phone(target) if ch_type == "whatsapp" else target
            log.error("delivery_channel_error", channel_type=ch_type, target=shown, error=str(exc)[:200])
            results.append(
                {"status": "failed", "channel": ch_type, "target": target, "error": str(exc), "reason": str(exc)[:200]}
            )

    return results
