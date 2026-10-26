# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Outreach messages in English and French: who is writing, why, an AI disclosure, the link, and how to stop.

Every message names the partner, says an AI assistant can help, carries the
onboarding link, and says how to opt out. Emails also carry an unsubscribe
link and the partner's postal address. WhatsApp first contact uses the
partner's approved template, filled with the partner name and the link, in
that order; the template text itself is approved by Meta, not written here.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

from core.onboarding_desk.branding import Branding

KINDS: tuple[str, ...] = ("first_contact", "reminder")
MAX_SMS = 480


@dataclass(frozen=True)
class Message:
    text: str
    subject: str = ""
    html: str = ""
    template_params: tuple[str, ...] = field(default_factory=tuple)


_SMS = {
    ("first_contact", "en"): (
        "{partner}: Hi{name}, thanks for your interest in accepting payments with {partner}. "
        "Our AI assistant can help you finish your application: {link} Reply STOP to opt out."
    ),
    ("reminder", "en"): (
        "{partner}: Your application is waiting: {link} Our AI assistant can help, "
        "or reply to reach a person. Reply STOP to opt out."
    ),
    ("first_contact", "fr"): (
        "{partner} : Bonjour{name}, merci de votre intérêt pour l'acceptation des paiements avec {partner}. "
        "Notre assistant IA peut vous aider à terminer votre demande : {link} "
        "Répondez STOP ou ARRET pour vous désabonner."
    ),
    ("reminder", "fr"): (
        "{partner} : Votre demande vous attend : {link} Notre assistant IA peut vous aider, "
        "ou répondez pour joindre une personne. Répondez STOP ou ARRET pour vous désabonner."
    ),
}
_SUBJECT = {
    ("first_contact", "en"): "Finish your {partner} application",
    ("reminder", "en"): "Your {partner} application is waiting",
    ("first_contact", "fr"): "Terminez votre demande {partner}",
    ("reminder", "fr"): "Votre demande {partner} vous attend",
}
_EMAIL = {
    "en": (
        "Hi{name},\n\n"
        "Thanks for your interest in accepting payments with {partner}. You can finish your application here:\n"
        "{link}\n\n"
        "Our AI assistant can guide you through it, and you can ask for a person at any time.\n\n"
        "You are receiving this because you asked {partner} to contact you about payments. "
        "Unsubscribe: {unsubscribe}\n\n{partner}\n{address}\n"
    ),
    "fr": (
        "Bonjour{name},\n\n"
        "Merci de votre intérêt pour l'acceptation des paiements avec {partner}. "
        "Vous pouvez terminer votre demande ici :\n{link}\n\n"
        "Notre assistant IA peut vous guider, et vous pouvez demander une personne à tout moment.\n\n"
        "Vous recevez ce message parce que vous avez demandé à {partner} de vous contacter au sujet des paiements. "
        "Se désabonner : {unsubscribe}\n\n{partner}\n{address}\n"
    ),
}


def _first_name(contact_name: str) -> str:
    first = (contact_name or "").strip().split(" ")[0][:40]
    return f" {first}" if first else ""


def render(
    kind: str,
    channel: str,
    locale: str,
    branding: Branding,
    *,
    contact_name: str,
    link: str,
    unsubscribe_link: str = "",
) -> Message:
    """The message for one contact; ``ValueError`` for a kind, channel or locale this module does not write."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    lang = "fr" if locale == "fr" else "en"
    values = {"partner": branding.partner_name, "name": _first_name(contact_name), "link": link}
    if channel == "whatsapp":
        return Message(text="", template_params=(branding.partner_name, link))
    if channel == "sms":
        text = _SMS[(kind, lang)].format(**values)
        if len(text) > MAX_SMS:
            raise ValueError("the SMS is too long; shorten the partner name or the onboarding URL")
        return Message(text=text)
    if channel == "email":
        subject = _SUBJECT[(kind, lang)].format(**values)
        text = _EMAIL[lang].format(**values, unsubscribe=unsubscribe_link, address=branding.postal_address)
        escaped = {k: html.escape(v) for k, v in values.items()}
        body = "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in text.split("\n\n"))
        for raw, safe in ((link, escaped["link"]), (unsubscribe_link, html.escape(unsubscribe_link))):
            if raw:
                body = body.replace(safe, f'<a href="{safe}">{safe}</a>', 1)
        return Message(text=text, subject=subject, html=body)
    raise ValueError(f"no message for channel {channel}")
