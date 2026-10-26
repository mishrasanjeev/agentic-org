# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""A partner's outreach identity: its name, onboarding page, postal address and senders per channel.

Messages carry the partner's name, a link to its onboarding page, and for
email its postal address, so every contact says who is writing and how to stop.
Senders reuse the tenant's configured connectors: Twilio for SMS, SendGrid for
email, and the WhatsApp Business connector for an approved template. Voice is
not an outreach channel in this release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from core.config import settings
from core.onboarding_desk import contacts

SENDER_CHANNELS: tuple[str, ...] = ("sms", "email", "whatsapp")
MAX_PARTNER_NAME = 60
MAX_POSTAL_ADDRESS = 200
MAX_URL = 300
_CONNECTOR_RE = re.compile(r"^registry-[a-z][a-z0-9_]{1,63}$")
_TEMPLATE_RE = re.compile(r"^[a-z0-9_]{1,512}$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


class BrandingError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name
        self.message = message


@dataclass(frozen=True)
class Sender:
    connector: str
    sender: str = ""  # SMS: the E.164 number; email: the from address
    template: str = ""  # WhatsApp: the approved template name
    language: str = ""  # WhatsApp: the template language code, e.g. en_US

    def to_dict(self) -> dict[str, str]:
        return {
            k: v
            for k, v in {
                "connector": self.connector,
                "sender": self.sender,
                "template": self.template,
                "language": self.language,
            }.items()
            if v
        }


@dataclass(frozen=True)
class Branding:
    partner_name: str = ""
    onboarding_url: str = ""
    postal_address: str = ""
    auto_contact: bool = False
    first_contact_delay_minutes: int = 0
    senders: dict[str, Sender] = field(default_factory=dict)
    #: Answer text-message replies with the guided application; off by default.
    sms_conversation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "partner_name": self.partner_name,
            "onboarding_url": self.onboarding_url,
            "postal_address": self.postal_address,
            "auto_contact": self.auto_contact,
            "first_contact_delay_minutes": self.first_contact_delay_minutes,
            "senders": {channel: sender.to_dict() for channel, sender in self.senders.items()},
            "sms_conversation": self.sms_conversation,
        }

    def missing_for(self, channel: str) -> str | None:
        """Why this branding cannot send on ``channel``, or None when it can."""
        if not self.partner_name:
            return "branding_partner_name_missing"
        if not self.onboarding_url:
            return "branding_onboarding_url_missing"
        if channel not in self.senders:
            return "sender_not_configured"
        if channel == "email" and not self.postal_address:
            return "branding_postal_address_missing"
        return None


def _local_env() -> bool:
    return str(settings.env or "").lower() in {"local", "dev", "development", "test"}


def _url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > MAX_URL:
        raise BrandingError("onboarding_url", f"onboarding_url is longer than {MAX_URL} characters")
    parts = urlsplit(url)
    secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS and _local_env())
    if not secure or not parts.hostname or parts.query or parts.fragment:
        raise BrandingError("onboarding_url", "onboarding_url must be an https URL without a query or fragment")
    return url.rstrip("/")


def _sender(channel: str, raw: Any) -> Sender:
    if not isinstance(raw, dict):
        raise BrandingError(f"senders.{channel}", "each sender must be an object")
    connector = str(raw.get("connector") or "").strip()
    if not _CONNECTOR_RE.match(connector):
        raise BrandingError(
            f"senders.{channel}.connector", "connector must be a configured connector id like registry-twilio"
        )
    value = str(raw.get("sender") or "").strip()
    if channel == "sms":
        try:
            value = contacts.normalise_phone(value) or ""
        except contacts.ContactError as exc:
            raise BrandingError("senders.sms.sender", "sender must be the SMS number in E.164 form") from exc
        if not value:
            raise BrandingError("senders.sms.sender", "sender must be the SMS number in E.164 form")
        return Sender(connector, value)
    if channel == "email":
        try:
            value = contacts.normalise_email(value) or ""
        except contacts.ContactError as exc:
            raise BrandingError("senders.email.sender", "sender must be the from address") from exc
        if not value:
            raise BrandingError("senders.email.sender", "sender must be the from address")
        return Sender(connector, value)
    template = str(raw.get("template") or "").strip()
    language = str(raw.get("language") or "").strip()
    if not _TEMPLATE_RE.match(template):
        raise BrandingError("senders.whatsapp.template", "template must be the approved template name")
    if not _LANGUAGE_RE.match(language):
        raise BrandingError("senders.whatsapp.language", "language must be a code like en_US or fr_CA")
    return Sender(connector, "", template, language)


def branding_from(raw: dict[str, Any] | None) -> Branding:
    raw = dict(raw or {})
    name = str(raw.get("partner_name") or "").strip()
    if len(name) > MAX_PARTNER_NAME:
        raise BrandingError("partner_name", f"partner_name is longer than {MAX_PARTNER_NAME} characters")
    address = str(raw.get("postal_address") or "").strip()
    if len(address) > MAX_POSTAL_ADDRESS:
        raise BrandingError("postal_address", f"postal_address is longer than {MAX_POSTAL_ADDRESS} characters")
    auto = raw.get("auto_contact", False)
    if not isinstance(auto, bool):
        raise BrandingError("auto_contact", "auto_contact must be true or false")
    conversation = raw.get("sms_conversation", False)
    if not isinstance(conversation, bool):
        raise BrandingError("sms_conversation", "sms_conversation must be true or false")
    delay = raw.get("first_contact_delay_minutes", 0)
    if not isinstance(delay, int) or not 0 <= delay <= 24 * 60:
        raise BrandingError("first_contact_delay_minutes", "first_contact_delay_minutes must be between 0 and 1440")
    senders_raw = raw.get("senders") or {}
    if not isinstance(senders_raw, dict) or any(k not in SENDER_CHANNELS for k in senders_raw):
        raise BrandingError("senders", f"senders may only name {list(SENDER_CHANNELS)}")
    return Branding(
        partner_name=name,
        onboarding_url=_url(raw.get("onboarding_url")),
        postal_address=address,
        auto_contact=auto,
        first_contact_delay_minutes=delay,
        senders={channel: _sender(channel, value) for channel, value in senders_raw.items()},
        sms_conversation=conversation,
    )
