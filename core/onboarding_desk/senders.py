# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Sending one outreach message through the tenant's configured connector.

The desk never holds messaging credentials of its own: the partner's branding
names a connector the tenant configured (Twilio for SMS, SendGrid for email,
the WhatsApp Business connector for templates), and the credentials resolve
through the tenant's connector configuration. Any failure, a missing
connector included, is returned as a failed send with a reason; nothing is
retried here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from core.onboarding_desk.branding import Branding, Sender
from core.onboarding_desk.messages import Message

logger = structlog.get_logger()


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_ref: str | None = None
    error: str | None = None


def _ref(result: dict[str, Any]) -> str | None:
    for key in ("sid", "id", "message_id"):
        if result.get(key):
            return str(result[key])[:128]
    messages = result.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict) and messages[0].get("id"):
        return str(messages[0]["id"])[:128]
    return None


async def _connector(tenant_id: Any, sender: Sender) -> Any:
    """A connected connector instance for the sender, or None when the tenant has not configured it."""
    import connectors  # noqa: F401  # registers the built-in connectors
    from api.v1.agents import _resolve_connector_configs
    from connectors.registry import ConnectorRegistry

    config, names = await _resolve_connector_configs(str(tenant_id), [sender.connector], None, None)
    if not names:
        return None
    cls = ConnectorRegistry.get(names[0])
    if cls is None:
        return None
    instance = cls(config)
    await instance.connect()
    return instance


async def _call(instance: Any, channel: str, sender: Sender, branding: Branding, to: str, message: Message) -> Any:
    if channel == "sms":
        return await instance.send_sms(to=to, body=message.text, from_number=sender.sender)
    if channel == "email":
        return await instance.send_email(
            to=to,
            from_email=sender.sender,
            from_name=branding.partner_name,
            subject=message.subject,
            html=message.html,
            text=message.text,
        )
    if channel == "whatsapp":
        return await instance.send_template_message(
            to=to.lstrip("+"),
            template_name=sender.template,
            language_code=sender.language,
            components=[{"type": "body", "parameters": [{"type": "text", "text": p} for p in message.template_params]}],
        )
    raise ValueError(f"no sender for channel {channel}")


async def send(tenant_id: Any, channel: str, branding: Branding, to: str, message: Message) -> SendResult:
    sender = branding.senders.get(channel)
    if sender is None:
        return SendResult(False, error="sender_not_configured")
    instance = None
    try:
        instance = await _connector(tenant_id, sender)
        if instance is None:
            return SendResult(False, error="sender_connector_not_configured")
        result = await _call(instance, channel, sender, branding, to, message)
    # enterprise-gate: broad-except-ok reason=a-provider-failure-is-recorded-as-a-failed-attempt-never-raised
    except Exception as exc:
        logger.warning("onboarding_outreach_send_failed", channel=channel, error_type=type(exc).__name__)
        return SendResult(False, error=f"send_failed:{type(exc).__name__}")
    finally:
        if instance is not None:
            try:
                await instance.disconnect()
            # enterprise-gate: broad-except-ok reason=disconnect-failure-degrades-to-a-log-line
            except Exception:
                logger.debug("onboarding_outreach_disconnect_failed", channel=channel)
    if isinstance(result, dict) and result.get("error"):
        return SendResult(False, error=f"provider_error:{str(result['error'])[:80]}")
    return SendResult(True, provider_ref=_ref(result) if isinstance(result, dict) else None)
