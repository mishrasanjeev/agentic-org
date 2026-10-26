# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Tenant-scoped onboarding desk records.

Contact details are encrypted under the tenant key before any row is written,
and only masked forms leave this module in lists. Intake is idempotent per
source and external reference, so a webhook retried by the sender never makes
a second lead. An opt-out suppresses the contact's keyed hash, withdraws the
matching consents and marks the lead opted out; a later lead with the same
contact arrives already opted out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.config import settings
from core.onboarding_desk import contacts, rules
from core.onboarding_desk.leads import Attestation, ConsentInput, LeadInput, leads_from_csv

logger = structlog.get_logger()

MAX_LIST = 200
SOURCE_KINDS: tuple[str, ...] = ("webhook", "form", "csv", "crm", "manual")
OPT_OUT_REASONS: tuple[str, ...] = ("stop_keyword", "unsubscribe", "request", "complaint", "do_not_call")


class DeskError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def enabled() -> bool:
    return bool(settings.onboarding_desk_enabled)


def _hash(tenant_id: uuid.UUID, kind: str, value: str) -> str:
    return contacts.contact_hash(settings.secret_key, str(tenant_id), kind, value)


async def _encrypt(tenant_id: uuid.UUID, plaintext: str) -> str:
    from core.crypto.tenant_secrets import encrypt_with_kek, resolve_tenant_kek

    kek = await resolve_tenant_kek(tenant_id)
    return await asyncio.to_thread(encrypt_with_kek, plaintext, kek)


async def _decrypt(ciphertext: str) -> str:
    from core.crypto.tenant_secrets import decrypt_for_tenant

    return await asyncio.to_thread(decrypt_for_tenant, ciphertext)


def _session(tenant_id: uuid.UUID) -> Any:
    from core.database import get_tenant_session

    return get_tenant_session(tenant_id)


# ── Policy ───────────────────────────────────────────────────────────────────


async def get_policy(tenant_id: uuid.UUID) -> rules.Policy:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    async with _session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
    return rules.policy_from(row.settings if row else None)


async def put_policy(tenant_id: uuid.UUID, raw: dict[str, Any], *, actor: str) -> rules.Policy:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingPolicy

    try:
        policy = rules.policy_from(raw)
    except ValueError as exc:
        raise DeskError(422, "policy_invalid", str(exc)) from exc
    async with _session(tenant_id) as session:
        row = (
            await session.execute(select(OnboardingPolicy).where(OnboardingPolicy.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if row is None:
            session.add(OnboardingPolicy(tenant_id=tenant_id, settings=policy.to_dict(), updated_by=actor[:128]))
        else:
            row.settings = policy.to_dict()
            row.updated_by = actor[:128]
            row.updated_at = datetime.now(UTC)
    return policy


# ── Sources ──────────────────────────────────────────────────────────────────


def source_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "kind": row.kind,
        "enabled": bool(row.enabled),
        "signed": bool(row.secret_ciphertext),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def create_source(tenant_id: uuid.UUID, name: str, kind: str, *, actor: str) -> tuple[Any, str | None]:
    """A lead source; a webhook source gets a signing secret, returned once and stored encrypted."""
    from core.models.onboarding_desk import OnboardingLeadSource

    if kind not in SOURCE_KINDS:
        raise DeskError(422, "source_kind_invalid", f"kind must be one of {list(SOURCE_KINDS)}")
    secret = secrets.token_urlsafe(32) if kind == "webhook" else None
    row = OnboardingLeadSource(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name.strip()[:120],
        kind=kind,
        secret_ciphertext=await _encrypt(tenant_id, secret) if secret else None,
        created_by=actor[:128],
    )
    async with _session(tenant_id) as session:
        session.add(row)
    return row, secret


async def list_sources(tenant_id: uuid.UUID) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingLeadSource

    async with _session(tenant_id) as session:
        return list(
            (
                await session.execute(
                    select(OnboardingLeadSource)
                    .where(OnboardingLeadSource.tenant_id == tenant_id)
                    .order_by(OnboardingLeadSource.created_at.desc())
                )
            ).scalars()
        )


async def webhook_secret(tenant_id: uuid.UUID, source_id: uuid.UUID) -> tuple[Any, str] | None:
    """The enabled webhook source and its secret, or None."""
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingLeadSource

    async with _session(tenant_id) as session:
        row = (
            await session.execute(
                select(OnboardingLeadSource).where(
                    OnboardingLeadSource.tenant_id == tenant_id, OnboardingLeadSource.id == source_id
                )
            )
        ).scalar_one_or_none()
    if row is None or not row.enabled or row.kind != "webhook" or not row.secret_ciphertext:
        return None
    return row, await _decrypt(row.secret_ciphertext)


# ── Leads ────────────────────────────────────────────────────────────────────


def _consent_row(tenant_id: uuid.UUID, lead_id: uuid.UUID, consent: ConsentInput, actor: str) -> Any:
    from core.models.onboarding_desk import OnboardingConsent

    return OnboardingConsent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        lead_id=lead_id,
        channel=consent.channel,
        purpose=rules.PURPOSE,
        wording=consent.wording,
        wording_sha256=hashlib.sha256(consent.wording.encode("utf-8")).hexdigest(),
        method=consent.method,
        source_ref=consent.source_ref,
        written=consent.written,
        captured_at=consent.captured_at,
        expires_at=consent.expires_at,
        recorded_by=actor[:128],
    )


async def _suppressed_hashes(session: Any, tenant_id: uuid.UUID, hashes: list[str]) -> dict[str, set[str]]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingSuppression

    if not hashes:
        return {}
    rows = (
        await session.execute(
            select(OnboardingSuppression).where(
                OnboardingSuppression.tenant_id == tenant_id, OnboardingSuppression.contact_hash.in_(hashes)
            )
        )
    ).scalars()
    found: dict[str, set[str]] = {}
    for row in rows:
        found.setdefault(row.contact_hash, set()).add(row.channel)
    return found


@dataclass(frozen=True)
class Created:
    lead: Any
    created: bool


async def create_lead(
    tenant_id: uuid.UUID,
    lead: LeadInput,
    *,
    source_id: uuid.UUID | None = None,
    source_kind: str = "manual",
    actor: str = "",
) -> Created:
    """Store a lead and its consents; a lead already stored for the same source and external reference is returned."""
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingLead

    email_hash = _hash(tenant_id, "email", lead.email) if lead.email else None
    phone_hash = _hash(tenant_id, "phone", lead.phone) if lead.phone else None
    ciphertext = await _encrypt(
        tenant_id, json.dumps({"contact_name": lead.contact_name, "email": lead.email, "phone": lead.phone})
    )
    async with _session(tenant_id) as session:
        if lead.external_ref:
            existing = (
                await session.execute(
                    select(OnboardingLead).where(
                        OnboardingLead.tenant_id == tenant_id,
                        OnboardingLead.source_id == source_id,
                        OnboardingLead.external_ref == lead.external_ref,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return Created(existing, False)
        suppressed = await _suppressed_hashes(session, tenant_id, [h for h in (email_hash, phone_hash) if h])
        opted_out = any("all" in channels for channels in suppressed.values())
        row = OnboardingLead(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_id=source_id,
            source_kind=source_kind,
            external_ref=lead.external_ref,
            business_name=lead.business_name,
            country=lead.country,
            region=lead.region,
            locale=lead.locale,
            merchant_type=lead.merchant_type,
            contact_ciphertext=ciphertext,
            email_hash=email_hash,
            phone_hash=phone_hash,
            email_masked=contacts.mask_email(lead.email),
            phone_masked=contacts.mask_phone(lead.phone),
            status="opted_out" if opted_out else "new",
            created_by=actor[:128],
        )
        session.add(row)
        for consent in lead.consents:
            session.add(_consent_row(tenant_id, row.id, consent, actor))
    logger.info("onboarding_lead_created", source_kind=source_kind, consents=len(lead.consents), opted_out=opted_out)
    return Created(row, True)


async def import_csv(
    tenant_id: uuid.UUID, data: bytes, attestation: Attestation | None, *, actor: str, source_id: uuid.UUID | None
) -> dict[str, Any]:
    result = leads_from_csv(data, attestation)
    created = existing = 0
    for lead in result.leads:
        outcome = await create_lead(tenant_id, lead, source_id=source_id, source_kind="csv", actor=actor)
        if outcome.created:
            created += 1
        else:
            existing += 1
    return {"created": created, "existing": existing, "rejected": len(result.errors), "errors": result.errors[:100]}


def lead_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "business_name": row.business_name,
        "country": row.country,
        "region": row.region,
        "locale": row.locale,
        "merchant_type": row.merchant_type,
        "email": row.email_masked,
        "phone": row.phone_masked,
        "status": row.status,
        "source_kind": row.source_kind,
        "external_ref": row.external_ref,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def consent_view(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "channel": row.channel,
        "wording": row.wording,
        "wording_sha256": row.wording_sha256,
        "method": row.method,
        "source_ref": row.source_ref,
        "written": bool(row.written),
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
        "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        "withdrawn_via": row.withdrawn_via,
    }


async def list_leads(tenant_id: uuid.UUID, *, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Any]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingLead

    query = select(OnboardingLead).where(OnboardingLead.tenant_id == tenant_id)
    if status:
        query = query.where(OnboardingLead.status == status)
    query = query.order_by(OnboardingLead.created_at.desc()).limit(max(1, min(limit, MAX_LIST))).offset(max(0, offset))
    async with _session(tenant_id) as session:
        return list((await session.execute(query)).scalars())


async def get_lead(tenant_id: uuid.UUID, lead_id: uuid.UUID) -> tuple[Any, list[Any]]:
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingConsent, OnboardingLead

    async with _session(tenant_id) as session:
        lead = (
            await session.execute(
                select(OnboardingLead).where(OnboardingLead.tenant_id == tenant_id, OnboardingLead.id == lead_id)
            )
        ).scalar_one_or_none()
        if lead is None:
            raise DeskError(404, "lead_not_found", "no such lead")
        consents = list(
            (
                await session.execute(
                    select(OnboardingConsent)
                    .where(OnboardingConsent.tenant_id == tenant_id, OnboardingConsent.lead_id == lead_id)
                    .order_by(OnboardingConsent.created_at.desc())
                )
            ).scalars()
        )
    return lead, consents


async def record_consent(tenant_id: uuid.UUID, lead_id: uuid.UUID, consent: ConsentInput, *, actor: str) -> Any:
    lead, _ = await get_lead(tenant_id, lead_id)
    if lead.status == "opted_out":
        raise DeskError(409, "lead_opted_out", "this lead opted out; a new consent must come from the person directly")
    row = _consent_row(tenant_id, lead_id, consent, actor)
    async with _session(tenant_id) as session:
        session.add(row)
    return row


async def withdraw_consents(tenant_id: uuid.UUID, lead_id: uuid.UUID, channel: str, *, via: str, actor: str) -> int:
    """Withdraw a lead's granted consents for one channel or ``all``; the rows stay."""
    from sqlalchemy import select

    from core.models.onboarding_desk import OnboardingConsent

    if channel != "all" and channel not in rules.OUTREACH_CHANNELS:
        raise DeskError(422, "channel_invalid", f"channel must be all or one of {list(rules.OUTREACH_CHANNELS)}")
    now = datetime.now(UTC)
    count = 0
    async with _session(tenant_id) as session:
        query = select(OnboardingConsent).where(
            OnboardingConsent.tenant_id == tenant_id,
            OnboardingConsent.lead_id == lead_id,
            OnboardingConsent.status == "granted",
        )
        if channel != "all":
            query = query.where(OnboardingConsent.channel == channel)
        for row in (await session.execute(query)).scalars():
            row.status = "withdrawn"
            row.withdrawn_at = now
            row.withdrawn_via = via[:32]
            count += 1
    logger.info("onboarding_consents_withdrawn", channel=channel, via=via, count=count, actor=bool(actor))
    return count


async def opt_out(
    tenant_id: uuid.UUID,
    *,
    email: str | None,
    phone: str | None,
    channel: str,
    reason: str,
    source: str,
) -> dict[str, int]:
    """Suppress a contact, withdraw every matching lead's consents for the channel and mark them opted out."""
    from sqlalchemy import or_, select
    from sqlalchemy.dialects.postgresql import insert

    from core.models.onboarding_desk import OnboardingLead, OnboardingSuppression

    if channel != "all" and channel not in rules.OUTREACH_CHANNELS:
        raise DeskError(422, "channel_invalid", f"channel must be all or one of {list(rules.OUTREACH_CHANNELS)}")
    if reason not in OPT_OUT_REASONS:
        raise DeskError(422, "reason_invalid", f"reason must be one of {list(OPT_OUT_REASONS)}")
    try:
        normalised_email = contacts.normalise_email(email)
        normalised_phone = contacts.normalise_phone(phone)
    except contacts.ContactError as exc:
        raise DeskError(422, "contact_invalid", str(exc)) from exc
    hashes = [
        h
        for h in (
            _hash(tenant_id, "email", normalised_email) if normalised_email else None,
            _hash(tenant_id, "phone", normalised_phone) if normalised_phone else None,
        )
        if h
    ]
    if not hashes:
        raise DeskError(422, "contact_required", "an email or a phone number is required")
    async with _session(tenant_id) as session:
        for digest in hashes:
            await session.execute(
                insert(OnboardingSuppression)
                .values(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    contact_hash=digest,
                    channel=channel,
                    reason=reason,
                    source=source[:64],
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "contact_hash", "channel"])
            )
        leads = list(
            (
                await session.execute(
                    select(OnboardingLead).where(
                        OnboardingLead.tenant_id == tenant_id,
                        or_(OnboardingLead.email_hash.in_(hashes), OnboardingLead.phone_hash.in_(hashes)),
                    )
                )
            ).scalars()
        )
        if channel == "all":
            for lead in leads:
                lead.status = "opted_out"
                lead.updated_at = datetime.now(UTC)
    withdrawn = 0
    for lead in leads:
        withdrawn += await withdraw_consents(tenant_id, lead.id, channel, via=reason, actor=source)
    logger.info("onboarding_opt_out", channel=channel, reason=reason, leads=len(leads), withdrawn=withdrawn)
    return {"suppressed": len(hashes), "leads": len(leads), "consents_withdrawn": withdrawn}


# ── Contact decision ─────────────────────────────────────────────────────────


async def _attempt_counts(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, channel: str, now: datetime
) -> tuple[int, int, datetime | None]:
    from sqlalchemy import func, select

    from core.models.onboarding_desk import OnboardingContactAttempt as Attempt

    since = now - timedelta(hours=24)
    async with _session(tenant_id) as session:
        last_day = (
            await session.execute(
                select(func.count()).where(
                    Attempt.tenant_id == tenant_id,
                    Attempt.lead_id == lead_id,
                    Attempt.channel.in_(sorted(rules.TIMED_CHANNELS)),
                    Attempt.status == "sent",
                    Attempt.created_at >= since,
                )
            )
        ).scalar() or 0
        reminders = (
            await session.execute(
                select(func.count()).where(
                    Attempt.tenant_id == tenant_id,
                    Attempt.lead_id == lead_id,
                    Attempt.kind == "reminder",
                    Attempt.status == "sent",
                )
            )
        ).scalar() or 0
        last = (
            await session.execute(
                select(func.max(Attempt.created_at)).where(
                    Attempt.tenant_id == tenant_id, Attempt.lead_id == lead_id, Attempt.status == "sent"
                )
            )
        ).scalar()
    return int(last_day), int(reminders), last


async def contact_decision(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    channel: str,
    *,
    now: datetime | None = None,
    is_reminder: bool = False,
) -> rules.Decision:
    moment = now or datetime.now(UTC)
    lead, consents = await get_lead(tenant_id, lead_id)
    contact = json.loads(await _decrypt(lead.contact_ciphertext))
    async with _session(tenant_id) as session:
        suppressed = await _suppressed_hashes(session, tenant_id, [h for h in (lead.email_hash, lead.phone_hash) if h])
    channels: set[str] = set()
    if channel == "email" and lead.email_hash:
        channels |= suppressed.get(lead.email_hash, set())
    elif lead.phone_hash:
        channels |= suppressed.get(lead.phone_hash, set())
    for digest_channels in suppressed.values():
        if "all" in digest_channels:
            channels.add("all")
    view = rules.LeadView(
        country=lead.country,
        region=lead.region,
        opted_out=lead.status == "opted_out",
        suppressed_channels=frozenset(channels),
        has_email=bool(contact.get("email")),
        has_phone=bool(contact.get("phone")),
    )
    consent_views = [
        rules.ConsentView(c.channel, c.status == "granted", bool(c.written), c.expires_at) for c in consents
    ]
    last_day, reminders, last = await _attempt_counts(tenant_id, lead_id, channel, moment)
    return rules.decide(
        view,
        channel,
        consent_views,
        policy=await get_policy(tenant_id),
        now=moment,
        attempts_last_24h=last_day,
        reminders_sent=reminders,
        is_reminder=is_reminder,
        last_attempt_at=last,
    )
