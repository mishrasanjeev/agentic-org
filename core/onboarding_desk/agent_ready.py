# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The agent-ready merchant offer: after approval, a merchant may enrol its verified business for AI agents.

Per tenant (``agent_ready`` in the desk settings) and only while agent identity is on for the
deployment. The offer appears after the hand-off, once a person has approved the governed case, on
the applicant page and in the text conversation. It never changes a decision: it is read after the
decision and writes nothing the case reads.

Accepting records a consent with purpose ``agent_enrolment`` that names the person (name, role and
contact, encrypted), and asks Grantex for a consent grant scoped to agent enrolment for that person.
Once the person has approved the grant at Grantex, the enrolment hand-off goes to the identity
provider with only the business verification reference and the fields the desk verified. The
provider's reference and status are recorded as received.

A business verification older than ``max_verification_age_days`` is never handed off: the merchant
is asked to verify again. Withdrawing the consent revokes the Grantex grant and blocks any further
sharing; a grant revoked at Grantex blocks it too.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.onboarding_desk import store
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

SETTINGS_KEY = "agent_ready"
CONSENT_PURPOSE = "agent_enrolment"
CONSENT_CHANNEL = "web"
MAX_OFFER = 320
MAX_AGE_DAYS = 365
DEFAULT_MAX_AGE_DAYS = 90
LIVE_STATUSES = ("consent_pending", "shared", "refused", "failed")

DEFAULT_OFFER = {
    "en": "Your business is verified. You can now make it ready for AI agents, using this verification. "
    "It is optional, it does not change your application, and you can withdraw at any time.",
    "fr": "Votre entreprise est vérifiée. Vous pouvez maintenant la rendre accessible aux agents IA, avec cette "
    "vérification. C'est facultatif, cela ne change pas votre demande, et vous pouvez retirer votre accord en tout "
    "temps.",
}

TEXT = {
    "en": {
        "consent": "I, {name} ({role}), agree that {partner} shares the business verification of {business} with "
        "its identity provider to enrol the business for AI agents. I can withdraw this at any time.",
        "reverify": "Your business verification is too old to share. Please verify your business again with {partner}.",
        "sms": "{partner}: {offer} Reply PERSON for help, or accept on the secure page: {link}",
        "sms_short": "{partner}: Your verified business can now be made ready for AI agents. It is optional. "
        "Details on the secure page: {link}",
        "sms_reverify": "{partner}: {message} Reply PERSON to reach a person.",
    },
    "fr": {
        "consent": "Je, {name} ({role}), accepte que {partner} transmette la vérification de l'entreprise "
        "{business} à son fournisseur d'identité afin de l'inscrire auprès des agents IA. Je peux retirer cet accord "
        "en tout temps.",
        "reverify": "La vérification de votre entreprise est trop ancienne pour être transmise. Veuillez faire "
        "vérifier de nouveau votre entreprise auprès de {partner}.",
        "sms": "{partner} : {offer} Répondez PERSONNE pour de l'aide, ou acceptez sur la page sécurisée : {link}",
        "sms_short": "{partner} : Votre entreprise vérifiée peut maintenant être rendue accessible aux agents IA. "
        "C'est facultatif. Détails sur la page sécurisée : {link}",
        "sms_reverify": "{partner} : {message} Répondez PERSONNE pour joindre une personne.",
    },
}

_CONTACT = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$|^\+?[0-9 ().-]{7,24}$")


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentReadySettings:
    enabled: bool = False
    offer_en: str = DEFAULT_OFFER["en"]
    offer_fr: str = DEFAULT_OFFER["fr"]
    max_verification_age_days: int = DEFAULT_MAX_AGE_DAYS

    def offer(self, lang: str) -> str:
        return self.offer_fr if lang == "fr" else self.offer_en

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "offer": {"en": self.offer_en, "fr": self.offer_fr},
            "max_verification_age_days": self.max_verification_age_days,
        }


def _invalid(message: str) -> DeskError:
    return DeskError(422, "agent_ready_settings_invalid", message)


def settings_from(raw: dict[str, Any] | None) -> AgentReadySettings:
    raw = dict(raw or {})
    if set(raw) - {"enabled", "offer", "max_verification_age_days"}:
        raise _invalid("agent-ready settings may only set enabled, offer and max_verification_age_days")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _invalid("enabled must be true or false")
    offer = raw.get("offer") or {}
    if not isinstance(offer, dict) or set(offer) - {"en", "fr"}:
        raise _invalid("offer may only give en and fr wording")
    texts: dict[str, str] = {}
    for lang in ("en", "fr"):
        value = offer.get(lang)
        if value is None or value == "":
            texts[lang] = DEFAULT_OFFER[lang]
            continue
        if not isinstance(value, str) or len(" ".join(value.split())) > MAX_OFFER:
            raise _invalid(f"offer.{lang} must be text of at most {MAX_OFFER} characters")
        texts[lang] = " ".join(value.split())
    age = raw.get("max_verification_age_days", DEFAULT_MAX_AGE_DAYS)
    if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= MAX_AGE_DAYS:
        raise _invalid(f"max_verification_age_days must be a whole number from 1 to {MAX_AGE_DAYS}")
    return AgentReadySettings(
        enabled=enabled, offer_en=texts["en"], offer_fr=texts["fr"], max_verification_age_days=age
    )


async def get_settings(tenant_id: uuid.UUID) -> AgentReadySettings:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
    try:
        return settings_from((row.settings or {}).get(SETTINGS_KEY) if row else None)
    except DeskError:
        logger.warning("onboarding_agent_ready_settings_unreadable")
        return AgentReadySettings()


async def put_settings(tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str) -> AgentReadySettings:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    parsed = settings_from(raw)
    async with store._session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(
                OnboardingPolicy(tenant_id=tenant_id, settings={SETTINGS_KEY: parsed.to_dict()}, updated_by=actor[:128])
            )
        else:
            row.settings = {**(row.settings or {}), SETTINGS_KEY: parsed.to_dict()}
            row.updated_by = actor[:128]
            row.updated_at = datetime.now(UTC)
    return parsed


def available() -> bool:
    """The offer needs agent identity on for the deployment: the enrolment goes through its provider."""
    from core.agent_identity.guard import enabled

    return enabled()


# ── Eligibility and verified data ────────────────────────────────────────────


@dataclass(frozen=True)
class Eligibility:
    settings: AgentReadySettings
    case: Any
    verified_at: datetime | None
    stale: bool


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def verified_at(case: Any) -> datetime | None:
    """When the business was last verified: the memo the decision was taken on."""
    return _parse((getattr(case, "memo", None) or {}).get("created_at"))


def approved(case: Any) -> bool:
    return case is not None and (getattr(case, "decision", None) or {}).get("outcome") == "approve"


async def eligibility(tenant_id: uuid.UUID, application: Any, *, now: datetime | None = None) -> Eligibility | None:
    """Whether the offer applies: identity on, the tenant's offer on, and the case approved by a person."""
    from core.onboarding_desk import cases

    if not available() or application is None or application.status != "submitted" or not application.case_ref:
        return None
    current = await get_settings(tenant_id)
    if not current.enabled:
        return None
    case = await cases.get_case(tenant_id, application.case_ref)
    if not approved(case):
        return None
    moment = now or datetime.now(UTC)
    at = verified_at(case)
    stale = at is None or moment - at > timedelta(days=current.max_verification_age_days)
    return Eligibility(settings=current, case=case, verified_at=at, stale=stale)


_NAME_FINDINGS = frozenset({"name_mismatch", "name_check_inconclusive"})


def _latest_underwriter_evidence(case: Any) -> dict[str, Any]:
    for record in reversed(list(getattr(case, "agent_records", None) or [])):
        if (
            isinstance(record, dict)
            and record.get("agent") == "business_underwriter"
            and record.get("status") == "completed"
        ):
            evidence = record.get("policy_evidence") or {}
            return dict(evidence.get("verification") or {}) if isinstance(evidence, dict) else {}
    return {}


def verified_payload(case: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The verification reference and the fields the desk verified; nothing the applicant only declared.

    The registry's own record (provider, reference, jurisdiction, identifiers) comes from the case
    subject the provider returned. The legal name is included only when the registry matched the
    business, reported it active, and raised no name finding. Owners, addresses, activity and contact
    details are never included: the desk did not verify them against the registry.
    """
    subject = dict(getattr(case, "subject", None) or {})
    memo = dict(getattr(case, "memo", None) or {})
    verification = _latest_underwriter_evidence(case)
    reference = {
        "case_ref": case.case_ref,
        "provider": subject.get("provider") or case.provider,
        "provider_ref": subject.get("provider_ref"),
        "verified_at": memo.get("created_at"),
    }
    fields: dict[str, Any] = {}
    if subject.get("jurisdiction"):
        fields["jurisdiction"] = subject["jurisdiction"]
    if subject.get("identifiers"):
        fields["registry_identifiers"] = list(subject["identifiers"])
    registry_active = verification.get("status") == "active"
    if registry_active:
        fields["registry_status"] = "active"
    registry = next((s for s in memo.get("sections") or [] if s.get("section_id") == "registry"), None)
    codes = {f.get("code") for f in (registry or {}).get("findings") or []}
    application = dict(getattr(case, "application", None) or {})
    if (
        verification.get("registry_match") is True
        and registry_active
        and registry is not None
        and registry.get("status") == "complete"
        and not codes & _NAME_FINDINGS
        and application.get("legal_name")
    ):
        fields["legal_name"] = application["legal_name"]
    return reference, fields


# ── Enrolments ───────────────────────────────────────────────────────────────


def _principal(tenant_id: uuid.UUID, lead_id: uuid.UUID) -> str:
    """An opaque, stable Grantex principal for the person consenting for this lead."""
    return "obd_" + hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).hexdigest()[:32]


def enrolment_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "status": row.status,
        "consent_id": str(row.consent_id),
        "consent_request_id": row.consent_request_id,
        "consent_grant_id": row.consent_grant_id,
        "shared_fields": list(row.shared_fields or []),
        "verification_ref": row.verification_ref,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "provider": row.provider,
        "provider_ref": row.provider_ref,
        "provider_status": row.provider_status,
        "refusal_reason": row.refusal_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "shared_at": row.shared_at.isoformat() if row.shared_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


async def list_enrolments(tenant_id: uuid.UUID, application_id: uuid.UUID) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingEnrolment as Enrolment

    async with store._session(tenant_id) as session:
        return list(
            (
                await session.execute(
                    select(Enrolment)
                    .where(Enrolment.tenant_id == tenant_id, Enrolment.application_id == application_id)
                    .order_by(Enrolment.created_at)
                )
            ).scalars()
        )


async def _live(tenant_id: uuid.UUID, application_id: uuid.UUID) -> Any:
    rows = [r for r in await list_enrolments(tenant_id, application_id) if r.status in LIVE_STATUSES]
    return rows[-1] if rows else None


def _lang(application: Any) -> str:
    return "fr" if getattr(application, "locale", "en") == "fr" else "en"


async def offer_view(tenant_id: uuid.UUID, application: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    """What the applicant page shows after approval, or None when there is no offer."""
    found = await eligibility(tenant_id, application, now=now)
    if found is None:
        return None
    lang = _lang(application)
    partner = (await store.get_branding(tenant_id)).partner_name
    live = await _live(tenant_id, application.id)
    if found.stale and (live is None or live.status == "consent_pending"):
        return {"status": "reverify_required", "message": TEXT[lang]["reverify"].format(partner=partner)}
    return {
        "status": live.status if live is not None else "offered",
        "offer": found.settings.offer(lang),
        "enrolment": enrolment_view(live) if live is not None else None,
        "consent_url": _pending_url(live),
    }


def _pending_url(row: Any) -> str | None:
    """The Grantex consent page, only while the enrolment still waits for the person's approval."""
    if row is None or row.status != "consent_pending":
        return None
    return getattr(row, "consent_url", None) or None


def _safe_url(value: Any) -> str | None:
    text = str(value or "")
    return text[:2048] if text.startswith("https://") and len(text) <= 2048 else None


async def sms_offer(tenant_id: uuid.UUID, application: Any, *, partner: str, link: str) -> str | None:
    """The text-message offer after approval, or None (the conversation then says the usual)."""
    found = await eligibility(tenant_id, application)
    if found is None:
        return None
    lang = _lang(application)
    if found.stale:
        message = TEXT[lang]["reverify"].format(partner=partner)
        return TEXT[lang]["sms_reverify"].format(partner=partner, message=message)
    from core.onboarding_desk.messages import MAX_SMS

    text = TEXT[lang]["sms"].format(partner=partner, offer=found.settings.offer(lang), link=link)
    # The full wording with the link can pass the text limit; the short form keeps the offer and the link.
    return text if len(text) <= MAX_SMS else TEXT[lang]["sms_short"].format(partner=partner, link=link)


def _text(value: Any, field: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > limit:
        raise DeskError(422, "representative_invalid", f"{field} is required, at most {limit} characters")
    return text


def _reverify(application: Any, partner: str) -> DeskError:
    return DeskError(409, "verification_stale", TEXT[_lang(application)]["reverify"].format(partner=partner))


async def _grantex_agent(tenant_id: uuid.UUID) -> str:
    from core.agent_identity import scopes
    from core.cases.grant_authorizer import _active_agent

    agent = await _active_agent(str(tenant_id), scopes.CONVERSATION_ROLE)
    grantex_agent_id = str((agent[1] or {}).get("grantex_agent_id") or "") if agent else ""
    if not grantex_agent_id:
        raise DeskError(
            503, "desk_agent_not_configured", "the desk's conversation agent is not registered with Grantex"
        )
    return grantex_agent_id


def _service() -> Any:
    from core.agent_identity.grantex import grantex_identity_service

    service = grantex_identity_service()
    if service is None:
        raise DeskError(503, "consent_service_not_configured", "the consent grant service is not configured")
    return service


async def accept(
    tenant_id: uuid.UUID,
    application: Any,
    *,
    name: str,
    role: str,
    contact: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record the person's agent-enrolment consent and ask Grantex for the consent grant."""
    from core.agent_identity import scopes
    from core.agent_identity.grantex import ENROLMENT_PURPOSE, GrantexIdentityError
    from core.models.onboarding_desk import OnboardingConsent, OnboardingEnrolment

    moment = now or datetime.now(UTC)
    found = await eligibility(tenant_id, application, now=moment)
    if found is None:
        raise DeskError(409, "agent_ready_not_offered", "the agent-ready offer is not open for this application")
    branding = await store.get_branding(tenant_id)
    if found.stale:
        raise _reverify(application, branding.partner_name)
    live = await _live(tenant_id, application.id)
    if live is not None:
        return {"enrolment": enrolment_view(live), "consent_url": _pending_url(live)}
    person = {
        "name": _text(name, "name", 120),
        "role": _text(role, "role", 80),
        "contact": _text(contact, "contact", 254),
    }
    if not _CONTACT.match(person["contact"]):
        raise DeskError(422, "representative_invalid", "contact must be an email address or a phone number")
    service = _service()
    grantex_agent_id = await _grantex_agent(tenant_id)
    lead, _ = await store.get_lead(tenant_id, application.lead_id)
    try:
        requested = await service.authorize_consent(
            agent_id=grantex_agent_id,
            principal_id=_principal(tenant_id, application.lead_id),
            scopes=[scopes.ENROLMENT],
            purpose=ENROLMENT_PURPOSE,
        )
    except GrantexIdentityError as exc:
        raise DeskError(503, "consent_service_unavailable", "the consent grant could not be requested") from exc
    wording = TEXT[_lang(application)]["consent"].format(
        name=person["name"], role=person["role"], partner=branding.partner_name, business=lead.business_name
    )
    subject = await store._encrypt(tenant_id, json.dumps(person, separators=(",", ":")))
    consent = OnboardingConsent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        lead_id=application.lead_id,
        channel=CONSENT_CHANNEL,
        purpose=CONSENT_PURPOSE,
        wording=wording,
        wording_sha256=hashlib.sha256(wording.encode("utf-8")).hexdigest(),
        method="web_form",
        source_ref=(branding.onboarding_url or "")[:512],
        written=True,
        captured_at=moment,
        status="granted",
        recorded_by="applicant",
        subject_ciphertext=subject,
    )
    reference, _ = verified_payload(found.case)
    enrolment = OnboardingEnrolment(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        application_id=application.id,
        lead_id=application.lead_id,
        consent_id=consent.id,
        case_ref=application.case_ref,
        status="consent_pending",
        consent_request_id=str(requested.get("authRequestId") or "")[:128] or None,
        consent_url=_safe_url(requested.get("consentUrl")),
        shared_fields=[],
        verification_ref=reference,
        verified_at=found.verified_at,
        created_at=moment,
    )
    from sqlalchemy.exc import IntegrityError

    try:
        await _add(tenant_id, consent, enrolment)
    except IntegrityError as exc:
        # Another accept for this application won the race; its consent and enrolment stand, this one is dropped.
        live = await _live(tenant_id, application.id)
        if live is None:
            raise DeskError(409, "enrolment_in_progress", "the offer is being accepted; try again") from exc
        return {"enrolment": enrolment_view(live), "consent_url": _pending_url(live)}
    logger.info("onboarding_agent_ready_accepted")
    return {"enrolment": enrolment_view(enrolment), "consent_url": enrolment.consent_url}


async def _add(tenant_id: uuid.UUID, *rows: Any) -> None:
    async with store._session(tenant_id) as session:
        for row in rows:
            session.add(row)


async def _withdraw_consent(tenant_id: uuid.UUID, consent_id: uuid.UUID, moment: datetime) -> None:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingConsent

    async with store._session(tenant_id) as session:
        consent = (
            await session.execute(
                select(OnboardingConsent)
                .where(OnboardingConsent.tenant_id == tenant_id, OnboardingConsent.id == consent_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if consent is not None and consent.status == "granted":
            consent.status = "withdrawn"
            consent.withdrawn_at = moment
            consent.withdrawn_via = "applicant"


async def _consent(tenant_id: uuid.UUID, consent_id: uuid.UUID) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingConsent

    async with store._session(tenant_id) as session:
        return (
            await session.execute(
                select(OnboardingConsent).where(
                    OnboardingConsent.tenant_id == tenant_id, OnboardingConsent.id == consent_id
                )
            )
        ).scalar_one_or_none()


async def _update(tenant_id: uuid.UUID, enrolment_id: uuid.UUID, **values: Any) -> Any:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingEnrolment as Enrolment

    async with store._session(tenant_id) as session:
        row = (
            await session.execute(
                select(Enrolment)
                .where(Enrolment.tenant_id == tenant_id, Enrolment.id == enrolment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise DeskError(404, "enrolment_not_found", "no such enrolment")
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = datetime.now(UTC)
    return row


async def sharing_allowed(tenant_id: uuid.UUID, enrolment: Any, service: Any, *, now: datetime | None = None) -> str:
    """Why sharing is blocked ("" when allowed): the consent row and the Grantex grant must both be live."""
    from core.agent_identity.grantex import GrantexIdentityError

    consent = await _consent(tenant_id, enrolment.consent_id)
    if consent is None or consent.status != "granted":
        return "consent_withdrawn"
    if not enrolment.consent_grant_id:
        return "consent_grant_pending"
    try:
        grant = await service.get_grant(enrolment.consent_grant_id)
    except GrantexIdentityError:
        return "consent_service_unavailable"
    if grant.get("status") != "active":
        return "consent_grant_revoked"
    expires = _parse(grant.get("expiresAt"))
    if expires is None or expires <= (now or datetime.now(UTC)):
        return "consent_grant_expired"
    return ""


def _matching_grant(grants: list[dict[str, Any]], enrolment: Any, moment: datetime) -> dict[str, Any] | None:
    """The grant the person approved for *this* enrolment: enrolment scope and purpose, issued after it began.

    Grantex's grant view does not name the consent request it came from, so the issue time is what ties a
    grant to this enrolment; an approval from an earlier, withdrawn enrolment is never reused.
    """
    from core.agent_identity import scopes
    from core.agent_identity.grantex import ENROLMENT_PURPOSE

    created = _parse(enrolment.created_at)
    candidates = []
    for grant in grants:
        issued = _parse(grant.get("issuedAt"))
        expires = _parse(grant.get("expiresAt"))
        if (
            grant.get("status") == "active"
            and grant.get("purpose") == ENROLMENT_PURPOSE
            and scopes.ENROLMENT in (grant.get("scopes") or [])
            and created is not None
            and issued is not None
            and issued >= created
            and expires is not None
            and expires > moment
            and grant.get("grantId")
        ):
            candidates.append((issued, grant))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None


async def continue_enrolment(tenant_id: uuid.UUID, application: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Hand the verified business to the identity provider once the person approved the consent grant."""
    from connectors.framework.agent_identity_provider import (
        AgentIdentityError,
        AgentIdentityRefused,
        EnrolmentRequest,
    )
    from connectors.framework.verification_provider import Deadline
    from core.agent_identity.grantex import GrantexIdentityError
    from core.agent_identity.guard import GrantUnavailableError, _configured_provider

    moment = now or datetime.now(UTC)
    _require_available()
    live = await _live(tenant_id, application.id)
    if live is None:
        raise DeskError(404, "enrolment_not_found", "there is no agent-ready enrolment for this application")
    if live.status != "consent_pending":
        # Shared, refused or failed: what the provider answered stands; nothing is sent again.
        return {"enrolment": enrolment_view(live)}
    found = await eligibility(tenant_id, application, now=moment)
    if found is None:
        raise DeskError(409, "agent_ready_not_offered", "the agent-ready offer is not open for this application")
    if found.stale:
        raise _reverify(application, (await store.get_branding(tenant_id)).partner_name)
    service = _service()
    if not live.consent_grant_id:
        try:
            grants = await service.active_grants(
                agent_id=await _grantex_agent(tenant_id), principal_id=_principal(tenant_id, application.lead_id)
            )
        except GrantexIdentityError as exc:
            raise DeskError(503, "consent_service_unavailable", "the consent grant could not be checked") from exc
        grant = _matching_grant(grants, live, moment)
        if grant is None:
            return {"enrolment": enrolment_view(live), "consent_url": _pending_url(live)}
        live = await _update(tenant_id, live.id, consent_grant_id=str(grant.get("grantId") or "")[:128] or None)
    blocked = await sharing_allowed(tenant_id, live, service, now=moment)
    if blocked == "consent_service_unavailable":
        raise DeskError(503, blocked, "the consent grant could not be checked")
    if blocked:
        row = await _update(tenant_id, live.id, status="revoked", refusal_reason=blocked, revoked_at=moment)
        return {"enrolment": enrolment_view(row)}
    reference, fields = verified_payload(found.case)
    try:
        provider = _configured_provider()
    except GrantUnavailableError as exc:
        raise DeskError(503, exc.reason, "the identity provider is not available") from exc
    request = EnrolmentRequest(
        verification_ref=reference,
        verified_fields=fields,
        consent_grant_id=str(live.consent_grant_id),
        idempotency_key=f"enrolment:{live.id}",
    )
    try:
        receipt = await provider.enrol_business(request, deadline=Deadline.after(10))
    except AgentIdentityRefused as exc:
        row = await _update(tenant_id, live.id, status="refused", refusal_reason=exc.reason, provider=provider.name)
        return {"enrolment": enrolment_view(row)}
    except AgentIdentityError as exc:
        # Unreachable: nothing is known to have been shared; the enrolment stays pending for a retry.
        await _update(tenant_id, live.id, refusal_reason=exc.reason)
        raise DeskError(503, exc.reason, "the identity provider could not be reached; try again") from exc
    row = await _update(
        tenant_id,
        live.id,
        status="shared",
        provider=provider.name,
        provider_ref=str(receipt.reference)[:256],
        provider_status=str(receipt.status)[:64],
        shared_fields=sorted(fields),
        verification_ref=reference,
        refusal_reason=None,
        shared_at=moment,
    )
    logger.info("onboarding_agent_ready_shared", fields=len(fields))
    return {"enrolment": enrolment_view(row)}


async def withdraw(tenant_id: uuid.UUID, application: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Withdraw the agent-enrolment consent and revoke the person's Grantex enrolment grants.

    Every active enrolment grant for the person is revoked, not only the one this enrolment recorded: a
    grant approved but not yet picked up must not outlive the withdrawal. The enrolment is revoked here
    either way, so nothing more is shared; a revocation Grantex did not confirm is recorded on it.
    """
    from core.agent_identity import scopes
    from core.agent_identity.grantex import GrantexIdentityError, grantex_identity_service

    moment = now or datetime.now(UTC)
    _require_available()
    live = await _live(tenant_id, application.id)
    if live is None:
        raise DeskError(404, "enrolment_not_found", "there is no agent-ready enrolment for this application")
    await _withdraw_consent(tenant_id, live.consent_id, moment)
    revoked: list[str] = []
    failed = False
    service = grantex_identity_service()
    if service is None:
        failed = True
    else:
        try:
            held = await service.active_grants(
                agent_id=await _grantex_agent(tenant_id), principal_id=_principal(tenant_id, application.lead_id)
            )
            ids = {str(g["grantId"]) for g in held if g.get("grantId") and scopes.ENROLMENT in (g.get("scopes") or [])}
            if live.consent_grant_id:
                ids.add(str(live.consent_grant_id))
            for grant_id in sorted(ids):
                if await service.revoke_grant(grant_id):
                    revoked.append(grant_id)
        except (GrantexIdentityError, DeskError) as exc:
            failed = True
            logger.warning("onboarding_agent_ready_grant_revoke_failed", error=type(exc).__name__)
    reason = "consent_withdrawn_grant_revoke_failed" if failed else "consent_withdrawn"
    row = await _update(tenant_id, live.id, status="revoked", revoked_at=moment, refusal_reason=reason)
    return {"enrolment": enrolment_view(row), "grant_revoked": bool(revoked) and not failed, "revoke_failed": failed}


def _require_available() -> None:
    if not available():
        raise DeskError(404, "agent_ready_not_offered", "the agent-ready offer is not available")
