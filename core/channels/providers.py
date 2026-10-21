# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Native provider wire contracts. Credentials and callback bodies never enter logs."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

MAX_BODY_BYTES = 256 * 1024
MAX_TEXT = 8_000


class _BotUrlFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Telegram requires its token in the URL; httpx logs request URLs at INFO.
        record.msg = re.sub(r"(https://api\.telegram\.org/bot)[^/\s]+", r"\1[redacted]", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_BotUrlFilter())


class ChannelError(ValueError):
    """Safe error code, never a provider response or credential."""


@dataclass(frozen=True)
class Message:
    event_id: str
    sender: str
    text: str


CATALOG: dict[str, dict[str, Any]] = {
    "messenger": {
        "label": "Meta Messenger",
        "fields": ["app_secret", "access_token", "verify_token", "receiver_id", "api_version"],
        "guide": "https://developers.facebook.com/docs/messenger-platform/getting-started/",
        "reply": True,
        "identity_link": True,
    },
    "whatsapp": {
        "label": "WhatsApp Cloud API",
        "fields": ["app_secret", "access_token", "verify_token", "receiver_id", "api_version"],
        "guide": "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started",
        "reply": True,
        "identity_link": True,
    },
    "telegram": {
        "label": "Telegram",
        "fields": ["access_token", "webhook_secret"],
        "guide": "https://core.telegram.org/bots/tutorial",
        "reply": True,
        "identity_link": True,
    },
    "viber": {
        "label": "Viber",
        "fields": ["access_token"],
        "guide": "https://developers.viber.com/docs/api/rest-bot-api/",
        "reply": True,
        "identity_link": True,
    },
    "mailgun": {
        "label": "Inbound email (Mailgun)",
        "fields": ["signing_key", "receiver_id"],
        "guide": "https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/",
        "reply": False,
        "identity_link": False,
    },
    "signed_webhook": {
        "label": "Other channel / signed gateway",
        "fields": ["webhook_secret"],
        "guide": "/docs/channel-workflows",
        "reply": False,
        "identity_link": True,
    },
}


def validate_credentials(provider: str, credentials: dict[str, str]) -> dict[str, str]:
    descriptor = CATALOG.get(provider)
    if not descriptor:
        raise ChannelError("unsupported_provider")
    fields = descriptor["fields"]
    if set(credentials) != set(fields):
        raise ChannelError("credential_fields_mismatch")
    cleaned = {name: value.strip() for name, value in credentials.items()}
    if any(not value or len(value) > 4096 for value in cleaned.values()):
        raise ChannelError("credentials_incomplete")
    if provider in {"messenger", "whatsapp"}:
        if not cleaned["receiver_id"].isdigit() or not re.fullmatch(r"v\d{1,2}\.\d{1,2}", cleaned["api_version"]):
            raise ChannelError("invalid_meta_receiver_or_version")
    if provider == "telegram":
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", cleaned["access_token"]):
            raise ChannelError("invalid_bot_token_format")
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", cleaned["webhook_secret"]):
            raise ChannelError("webhook_secret_requires_24_to_256_url_safe_characters")
    if provider == "signed_webhook" and len(cleaned["webhook_secret"]) < 24:
        raise ChannelError("webhook_secret_requires_at_least_24_characters")
    return cleaned


def _verify_digest(secret: str, body: bytes, provided: str, prefix: str = "") -> None:
    expected = prefix + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise ChannelError("invalid_signature")


def _check_age(timestamp: str, now: float, maximum: int) -> None:
    try:
        age = now - float(timestamp)
    except ValueError as exc:
        raise ChannelError("invalid_timestamp") from exc
    if not math.isfinite(age):
        raise ChannelError("invalid_timestamp")
    if age < -60 or age > maximum:
        raise ChannelError("stale_signature")


def _message(event_id: Any, sender: Any, text: Any) -> Message:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ChannelError("unsupported_message_or_text_limit")
    if not isinstance(sender, (str, int)) or not isinstance(event_id, (str, int)):
        raise ChannelError("missing_message_identity")
    if not str(sender) or not str(event_id) or len(str(sender)) > 255 or len(str(event_id)) > 255:
        raise ChannelError("invalid_message_identity")
    return Message(str(event_id), str(sender), text.strip())


def decode_messages(
    provider: str,
    credentials: dict[str, str],
    headers: dict[str, str],
    body: bytes,
    *,
    now: float | None = None,
    email_form: dict[str, str] | None = None,
) -> list[Message]:
    """Authenticate first, normalize only text; statuses, echoes and unsupported media never run tasks."""
    if len(body) > MAX_BODY_BYTES:
        raise ChannelError("body_too_large")
    now = time.time() if now is None else now
    if provider == "mailgun":
        # Mailgun routes sign timestamp+token, NOT the email body. Email From is
        # not a verified user identity, so these messages always require review.
        try:
            form = (
                email_form
                if email_form is not None
                else {k: values[0] for k, values in parse_qs(body.decode(), keep_blank_values=True).items()}
            )
        except UnicodeDecodeError as exc:
            raise ChannelError("invalid_payload") from exc
        timestamp, token = form.get("timestamp", ""), form.get("token", "")
        _verify_digest(credentials["signing_key"], (timestamp + token).encode(), form.get("signature", ""))
        _check_age(timestamp, now, 900)
        if form.get("recipient", "").lower() != credentials["receiver_id"].lower():
            raise ChannelError("receiver_mismatch")
        return [_message(token, form.get("sender"), form.get("stripped-text") or form.get("body-plain"))]
    if provider in {"messenger", "whatsapp"}:
        _verify_digest(credentials["app_secret"], body, headers.get("x-hub-signature-256", ""), "sha256=")
    elif provider == "viber":
        _verify_digest(credentials["access_token"], body, headers.get("x-viber-content-signature", ""))
    elif provider == "telegram":
        if not hmac.compare_digest(
            headers.get("x-telegram-bot-api-secret-token", "").encode(), credentials["webhook_secret"].encode()
        ):
            raise ChannelError("invalid_signature")
    elif provider == "signed_webhook":
        timestamp = headers.get("x-agenticorg-timestamp", "")
        _verify_digest(
            credentials["webhook_secret"], timestamp.encode() + b"." + body, headers.get("x-agenticorg-signature", "")
        )
        _check_age(timestamp, now, 300)
    else:
        raise ChannelError("unsupported_provider")
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ChannelError("invalid_payload")
        if provider == "signed_webhook":
            return [_message(data.get("event_id"), data.get("sender_id"), data.get("text"))]
        if provider == "telegram":
            item = data.get("message") or {}
            if not item or not item.get("text") or item.get("chat", {}).get("type") != "private":
                return []
            # A group chat id does not identify a human and must never inherit one.
            if str(item.get("from", {}).get("id")) != str(item.get("chat", {}).get("id")):
                raise ChannelError("sender_mismatch")
            return [_message(data.get("update_id"), item["from"]["id"], item["text"])]
        if provider == "viber":
            if data.get("event") != "message" or data.get("message", {}).get("type") != "text":
                return []
            return [_message(data.get("message_token"), data.get("sender", {}).get("id"), data["message"]["text"])]
        messages = []
        for entry in data.get("entry", []):
            if provider == "messenger":
                if str(entry.get("id")) != credentials["receiver_id"]:
                    raise ChannelError("receiver_mismatch")
                for item in entry.get("messaging", []):
                    msg = item.get("message", {})
                    if msg.get("is_echo") or not msg.get("text"):
                        continue
                    if str(item.get("recipient", {}).get("id")) != credentials["receiver_id"]:
                        raise ChannelError("receiver_mismatch")
                    messages.append(_message(msg.get("mid"), item.get("sender", {}).get("id"), msg["text"]))
            else:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if str(value.get("metadata", {}).get("phone_number_id")) != credentials["receiver_id"]:
                        raise ChannelError("receiver_mismatch")
                    for item in value.get("messages", []):
                        if item.get("type") == "text":
                            messages.append(
                                _message(item.get("id"), item.get("from"), item.get("text", {}).get("body"))
                            )
        if len(messages) > 50:
            raise ChannelError("batch_too_large")
        return messages
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ChannelError("invalid_payload") from exc


async def send_reply(provider: str, credentials: dict[str, str], sender: str, text: str) -> None:
    """One reply attempt to the authenticated sender, never arbitrary hosts or unsolicited contacts."""
    text = text[:2000]
    headers: dict[str, str] = {}
    if provider == "telegram":
        url = f"https://api.telegram.org/bot{credentials['access_token']}/sendMessage"
        payload: dict[str, Any] = {"chat_id": sender, "text": text}
    elif provider == "viber":
        url = "https://chatapi.viber.com/pa/send_message"
        headers = {"X-Viber-Auth-Token": credentials["access_token"]}
        payload = {"receiver": sender, "min_api_version": 1, "type": "text", "text": text, "sender": {"name": "Agent"}}
    elif provider in {"messenger", "whatsapp"}:
        base = f"https://graph.facebook.com/{credentials['api_version']}"
        headers = {"Authorization": f"Bearer {credentials['access_token']}"}
        if provider == "messenger":
            url = f"{base}/{credentials['receiver_id']}/messages"
            payload = {"recipient": {"id": sender}, "messaging_type": "RESPONSE", "message": {"text": text}}
        else:
            url = f"{base}/{credentials['receiver_id']}/messages"
            payload = {"messaging_product": "whatsapp", "to": sender, "type": "text", "text": {"body": text}}
    else:
        raise ChannelError("native_reply_not_supported")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
            if not 200 <= response.status_code < 300:
                raise ChannelError("provider_reply_rejected")
            result = response.json()
            if not isinstance(result, dict):
                raise ChannelError("provider_reply_unconfirmed")
            if (provider == "telegram" and result.get("ok") is not True) or (
                provider == "viber" and result.get("status") != 0
            ):
                raise ChannelError("provider_reply_rejected")
            if provider == "messenger" and not isinstance(result.get("message_id"), str):
                raise ChannelError("provider_reply_unconfirmed")
            if provider == "whatsapp":
                messages = result.get("messages")
                if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
                    raise ChannelError("provider_reply_unconfirmed")
                if not isinstance(messages[0].get("id"), str):
                    raise ChannelError("provider_reply_unconfirmed")
    except (httpx.HTTPError, ValueError) as exc:
        # A timeout may have delivered the message. Never retry a reply blindly.
        raise ChannelError("provider_reply_unconfirmed") from exc


async def connect_telegram(credentials: dict[str, str], callback_url: str) -> str:
    """Register this binding only; never replace another application's bot webhook."""
    try:
        parsed = urlsplit(callback_url)
        port = parsed.port
    except ValueError as exc:
        raise ChannelError("public_https_callback_required") from exc
    host = parsed.hostname or ""
    try:
        private_host = not ipaddress.ip_address(host).is_global
    except ValueError:
        private_host = host == "localhost" or host.endswith((".localhost", ".local"))
    if (
        parsed.scheme != "https"
        or not host
        or private_host
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ChannelError("public_https_callback_required")
    base = f"https://api.telegram.org/bot{credentials['access_token']}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:

            async def call(method: str, payload: dict[str, Any] | None = None) -> Any:
                response = await client.post(f"{base}/{method}", json=payload or {})
                data = response.json()
                if response.status_code != 200 or not isinstance(data, dict) or data.get("ok") is not True:
                    raise ChannelError("telegram_setup_rejected")
                return data.get("result")

            bot = await call("getMe")
            if not isinstance(bot, dict) or bot.get("is_bot") is not True:
                raise ChannelError("telegram_bot_identity_unconfirmed")
            username = bot.get("username")
            if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
                raise ChannelError("telegram_bot_identity_unconfirmed")
            existing = await call("getWebhookInfo")
            if not isinstance(existing, dict) or not isinstance(existing.get("url"), str):
                raise ChannelError("telegram_webhook_state_unconfirmed")
            if existing["url"] not in {"", callback_url}:
                raise ChannelError("telegram_bot_connected_elsewhere")
            # Telegram Bot API setWebhook: retain pending updates and authenticate callbacks with secret_token.
            accepted = await call(
                "setWebhook",
                {"url": callback_url, "secret_token": credentials["webhook_secret"], "allowed_updates": ["message"]},
            )
            if accepted is not True:
                raise ChannelError("telegram_webhook_state_unconfirmed")
            current = await call("getWebhookInfo")
            if not isinstance(current, dict) or current.get("url") != callback_url:
                raise ChannelError("telegram_webhook_state_unconfirmed")
            return username
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ChannelError):
            raise
        # Do not expose Telegram's token-bearing URL or raw error response.
        raise ChannelError("telegram_setup_unconfirmed") from exc
