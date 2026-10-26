# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The guided application over text message: one question at a time, each with why it is needed.

A reply from a lead's phone continues that lead's application, the same one
the web page shows. The conversation asks for the next required item that
fits in a text message, checks the answer with the same validation as the
web page, and sends a link to the secure page for the rest: the address,
the owners and the documents. HELP explains who is writing and how to stop;
PERSON (PERSONNE) asks for a person. Nothing an applicant writes is given to
a model.

Replies go only to a lead with a current SMS consent, and only for partners
that turn the conversation on (``sms_conversation`` in the branding). A reply
answers a message the applicant just sent, so contact hours do not hold it
back; outreach is still governed by the contact rules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from core.onboarding_desk import applications, messages, outreach, questions, store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

HELP_KEYWORDS = frozenset({"HELP", "INFO", "AIDE"})
PERSON_KEYWORDS = frozenset({"PERSON", "HUMAN", "AGENT", "REP", "REPRESENTATIVE", "PERSONNE", "HUMAIN"})

_TEXT = {
    "en": {
        "help": "{partner}: This is {partner}'s AI assistant helping with your payments application. "
        "Reply PERSON to reach a person, or STOP to opt out. Msg & data rates may apply.",
        "person": "{partner}: Thanks. A person from {partner} will contact you soon.",
        "ask": "{partner}: {error}{label}? {reason}",
        "choices": " Reply with a number: {choices}",
        "web": "{partner}: Thanks. The rest is easiest on the secure page, where you can also upload documents: {link}",
        "done": "{partner}: Everything is in. Please review and submit here: {link}",
        "submitted": "{partner}: Your application is submitted. We'll be in touch. Reply PERSON to reach a person.",
    },
    "fr": {
        "help": "{partner} : Ceci est l'assistant IA de {partner}, qui vous aide avec votre demande de paiements. "
        "Répondez PERSONNE pour joindre une personne, ou STOP pour vous désabonner. Des frais peuvent s'appliquer.",
        "person": "{partner} : Merci. Une personne de {partner} communiquera avec vous sous peu.",
        "ask": "{partner} : {error}{label}? {reason}",
        "choices": " Répondez par un chiffre : {choices}",
        "web": "{partner} : Merci. La suite est plus simple sur la page sécurisée, où vous pouvez aussi téléverser "
        "vos documents : {link}",
        "done": "{partner} : Tout est là. Veuillez vérifier et soumettre ici : {link}",
        "submitted": "{partner} : Votre demande est soumise. Nous vous contacterons. Répondez PERSONNE pour joindre "
        "une personne.",
    },
}


def _keyword(body: str) -> str:
    return "".join(ch for ch in body.strip().upper() if ch.isalpha())


def _has_sms_consent(consents: list[Any], now: datetime) -> bool:
    return any(
        c.channel == "sms" and c.status == "granted" and (c.expires_at is None or c.expires_at > now) for c in consents
    )


def _question(item_id: str, lang: str, partner: str, *, error: str = "") -> str:
    item = questions.ITEMS[item_id]
    text = _TEXT[lang]["ask"].format(
        partner=partner, error=f"{error} " if error else "", label=item.label[lang], reason=item.reason[lang]
    )
    if item.choices:
        listed = ", ".join(f"{n} {labels[lang]}" for n, (_, labels) in enumerate(item.choices, start=1))
        text += _TEXT[lang]["choices"].format(choices=listed)
    return text


async def handle_sms(tenant_id: uuid.UUID, phone: str, body: str, *, now: datetime | None = None) -> str | None:
    """The reply to an inbound text, or None when the desk should not answer it."""
    moment = now or datetime.now(UTC)
    branding = await store.get_branding(tenant_id)
    if not branding.sms_conversation or not branding.partner_name or not branding.onboarding_url:
        return None
    leads = await store.open_leads_by_phone(tenant_id, phone)
    if len(leads) != 1:
        # No lead, or several sharing one phone: the desk cannot tell whose application this is.
        return None
    lead, consents = await store.get_lead(tenant_id, leads[0].id)
    if not _has_sms_consent(consents, moment):
        return None
    try:
        application = await applications.open_for_lead(tenant_id, lead.id, now=moment)
    except DeskError:
        return None
    lang = "fr" if application.locale == "fr" else "en"
    text = _TEXT[lang]
    partner = branding.partner_name
    inbound = " ".join(body.split())[: applications.MAX_TRANSCRIPT_TEXT]
    keyword = _keyword(body)
    reply = await _reply(tenant_id, application, keyword, inbound, lang, partner, branding, lead.id, moment)
    if len(reply) > messages.MAX_SMS:
        link = outreach.onboarding_link(branding.onboarding_url, tenant_id, lead.id, moment)
        reply = text["web"].format(partner=partner, link=link)
    await applications.append_transcript(
        tenant_id,
        application.id,
        [{"channel": "sms", "from": "applicant", "text": inbound}, {"channel": "sms", "from": "desk", "text": reply}],
        now=moment,
    )
    return reply


async def _reply(
    tenant_id: uuid.UUID,
    application: Any,
    keyword: str,
    inbound: str,
    lang: str,
    partner: str,
    branding: Any,
    lead_id: uuid.UUID,
    moment: datetime,
) -> str:
    text = _TEXT[lang]
    if keyword in HELP_KEYWORDS:
        return text["help"].format(partner=partner)
    if keyword in PERSON_KEYWORDS:
        await applications.request_person(tenant_id, application.id, channel="sms", now=moment)
        return text["person"].format(partner=partner)
    if application.status != "in_progress":
        return text["submitted"].format(partner=partner)
    question_set = (await applications.get_application_policy(tenant_id)).for_type(application.merchant_type)
    pending = application.pending_item
    if pending and pending in question_set.required and questions.ITEMS[pending].kind in questions.SMS_KINDS:
        errors = await applications.save_answers(
            tenant_id, application.id, {pending: inbound}, source="sms", now=moment
        )
        if pending in errors:
            return _question(pending, lang, partner, error=questions.error_text(errors[pending], lang))
        application = await applications.get_application(tenant_id, application.id)
    state = await applications.state(tenant_id, application)
    link = outreach.onboarding_link(branding.onboarding_url, tenant_id, lead_id, moment)
    if not state["missing"]:
        await applications.set_pending_item(tenant_id, application.id, None)
        return text["done"].format(partner=partner, link=link)
    next_item = state["missing"][0]
    if questions.ITEMS[next_item].kind in questions.SMS_KINDS:
        await applications.set_pending_item(tenant_id, application.id, next_item)
        return _question(next_item, lang, partner)
    await applications.set_pending_item(tenant_id, application.id, None)
    return text["web"].format(partner=partner, link=link)
