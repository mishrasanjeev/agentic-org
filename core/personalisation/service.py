# SPDX-License-Identifier: Apache-2.0
"""Personalisation: content for a subject, rendered only under a valid consent, with the attributes used recorded.

A consent is the current record of a subject's agreement for one purpose
(marketing, service, collections, retention, onboarding): granted with
the evidence of where it was captured and an optional expiry, or
withdrawn, which keeps the row. It is valid only while granted, not
withdrawn and not past its expiry. A profile holds a subject's
attributes, encrypted for the tenant before any row is locked. A rule
selects a variant for a purpose by conditions on attributes, tried by
priority. ``render`` refuses (403 ``consent_required``) and records the
refusal when the subject has no valid consent for the purpose; otherwise
it picks the rule (or uses the caller's template under the tenant's
allow-list in the business console), substitutes only allowed attributes
the profile holds, and records an event with the consent, the rule, the
names of the attributes used (never their values) and a hash of the
content. A preview evaluates the same under the same consent and records
nothing. Tenant scoped under row-level security; behind
``personalisation_enabled`` (default off).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import settings
from core.crypto.tenant_secrets import decrypt_for_tenant, encrypt_with_kek, resolve_tenant_kek
from core.personalisation import rules as checks
from core.personalisation.rules import PersonalisationError

logger = structlog.get_logger()

CONSENT_STATUSES = ("granted", "withdrawn")
MAX_EVENTS = 200
CALLER_TEMPLATE_SETTING = "personalisation.template_attributes"


def enabled() -> bool:
    return bool(getattr(settings, "personalisation_enabled", False))


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _actor(actor: str | None) -> str:
    """Who is acting; a write that changes consent, a profile or a rule refuses when nobody is identified."""
    who = str(actor or "").strip()[:128]
    if not who:
        raise PersonalisationError(401, "actor_required", "this change needs an identified user")
    return who


# ---------------------------------------------------------------- consents


def consent_valid(row: Any, now: datetime | None = None) -> bool:
    """Granted, not withdrawn and not past its expiry."""
    if row is None or row.status != "granted" or row.withdrawn_at is not None:
        return False
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires is None or expires > (now or _now())


def _consent_dict(row: Any, now: datetime | None = None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "subject_ref": row.subject_ref,
        "purpose": row.purpose,
        "status": row.status,
        "granted_at": _iso(row.granted_at),
        "expires_at": _iso(row.expires_at),
        "withdrawn_at": _iso(row.withdrawn_at),
        "evidence": row.evidence or "",
        "recorded_by": row.recorded_by or "",
        "updated_at": _iso(row.updated_at),
        "valid": consent_valid(row, now),
    }


async def _consent_row(session: Any, tenant_id: uuid.UUID, subject: str, purpose: str, *, lock: str = "") -> Any:
    from core.models.personalisation import PersonalisationConsent

    statement = select(PersonalisationConsent).where(
        PersonalisationConsent.tenant_id == tenant_id,
        PersonalisationConsent.subject_ref == subject,
        PersonalisationConsent.purpose == purpose,
    )
    if lock == "update":
        statement = statement.with_for_update()
    elif lock == "share":
        # A withdrawal waits for a render that read the consent to finish, so nothing renders past it.
        statement = statement.with_for_update(read=True)
    rows = (await session.execute(statement)).scalars().all()
    return rows[0] if rows else None


async def grant_consent(
    tenant_id: uuid.UUID,
    subject_ref: Any,
    purpose: Any,
    *,
    evidence: Any,
    expires_at: Any = None,
    actor: str | None,
) -> dict[str, Any]:
    """Record a grant: the current record for (subject, purpose) becomes granted, with its evidence and expiry."""
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationConsent

    who = _actor(actor)
    subject = checks.check_subject(subject_ref)
    purpose = checks.check_purpose(purpose)
    proof = str(evidence or "").strip()
    if not proof:
        raise PersonalisationError(422, "evidence_required", "evidence names where the consent was captured")
    if len(proof) > checks.MAX_EVIDENCE:
        raise PersonalisationError(422, "evidence_invalid", f"evidence is at most {checks.MAX_EVIDENCE} characters")
    now = _now()
    expires = checks.parse_time(expires_at, "expires_at")
    if expires is not None and expires <= now:
        raise PersonalisationError(422, "expiry_past", "expires_at is in the future")
    fields = {
        "status": "granted",
        "granted_at": now,
        "expires_at": expires,
        "withdrawn_at": None,
        "evidence": proof,
        "recorded_by": who,
        "updated_at": now,
    }
    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            pg_insert(PersonalisationConsent)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, subject_ref=subject, purpose=purpose, **fields)
            .on_conflict_do_nothing(index_elements=["tenant_id", "subject_ref", "purpose"])
        )
        row = await _consent_row(session, tenant_id, subject, purpose, lock="update")
        for key, value in fields.items():
            setattr(row, key, value)
        await session.flush()
        out = _consent_dict(row, now)
    logger.info("personalisation_consent_granted", purpose=purpose)
    return out


async def withdraw_consent(
    tenant_id: uuid.UUID, subject_ref: Any, purpose: Any, *, actor: str | None = None
) -> dict[str, Any]:
    """Withdraw: the row stays, marked withdrawn, and no render passes it from now on."""
    from core.database import get_tenant_session

    subject = checks.check_subject(subject_ref)
    purpose = checks.check_purpose(purpose)
    now = _now()
    async with get_tenant_session(tenant_id) as session:
        row = await _consent_row(session, tenant_id, subject, purpose, lock="update")
        if row is None:
            raise PersonalisationError(404, "consent_unknown", "no consent is recorded for this subject and purpose")
        if row.status != "withdrawn":
            row.status = "withdrawn"
            row.withdrawn_at = now
            row.updated_at = now
            who = str(actor or "").strip()[:128]
            if who:
                row.recorded_by = who
        await session.flush()
        out = _consent_dict(row, now)
    logger.info("personalisation_consent_withdrawn", purpose=purpose)
    return out


async def list_consents(tenant_id: uuid.UUID, subject_ref: Any) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationConsent

    subject = checks.check_subject(subject_ref)
    now = _now()
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    select(PersonalisationConsent).where(
                        PersonalisationConsent.tenant_id == tenant_id, PersonalisationConsent.subject_ref == subject
                    )
                )
            )
            .scalars()
            .all()
        )
    return sorted((_consent_dict(row, now) for row in rows), key=lambda c: c["purpose"])


# ---------------------------------------------------------------- profiles


async def _profile_row(session: Any, tenant_id: uuid.UUID, subject: str, *, lock: bool = False) -> Any:
    from core.models.personalisation import PersonalisationProfile

    statement = select(PersonalisationProfile).where(
        PersonalisationProfile.tenant_id == tenant_id, PersonalisationProfile.subject_ref == subject
    )
    if lock:
        statement = statement.with_for_update()
    rows = (await session.execute(statement)).scalars().all()
    return rows[0] if rows else None


def _decrypt(row: Any) -> dict[str, Any]:
    """The attributes a profile row holds; an unreadable row refuses, it is never read as empty."""
    stored = row.attributes if isinstance(row.attributes, dict) else {}
    ciphertext = stored.get("_encrypted")
    if not isinstance(ciphertext, str) or not ciphertext:
        raise PersonalisationError(500, "profile_unreadable", "the stored profile is not encrypted")
    try:
        attributes = json.loads(decrypt_for_tenant(ciphertext))
    # enterprise-gate: broad-except-ok reason=undecryptable-profile-fails-closed-and-refuses-the-read
    except Exception as exc:
        logger.error("personalisation_profile_undecryptable", error=type(exc).__name__)
        raise PersonalisationError(500, "profile_unreadable", "the stored profile could not be decrypted") from None
    if not isinstance(attributes, dict):
        raise PersonalisationError(500, "profile_unreadable", "the stored profile is not an object")
    return attributes


async def put_profile(tenant_id: uuid.UUID, subject_ref: Any, attributes: Any, *, actor: str | None) -> dict[str, Any]:
    """Replace a subject's attributes; they are encrypted for the tenant before any row is locked."""
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationProfile

    who = _actor(actor)
    subject = checks.check_subject(subject_ref)
    checked = checks.check_attributes(attributes)
    kek = await resolve_tenant_kek(tenant_id)
    stored = {"_encrypted": await asyncio.to_thread(encrypt_with_kek, json.dumps(checked, sort_keys=True), kek)}
    now = _now()
    async with get_tenant_session(tenant_id) as session:
        await session.execute(
            pg_insert(PersonalisationProfile)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                subject_ref=subject,
                attributes=stored,
                updated_by=who,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "subject_ref"])
        )
        row = await _profile_row(session, tenant_id, subject, lock=True)
        row.attributes = stored
        row.updated_by = who
        row.updated_at = now
        await session.flush()
    logger.info("personalisation_profile_kept", attributes=len(checked))
    return {"subject_ref": subject, "attributes": sorted(checked), "updated_by": who, "updated_at": _iso(now)}


async def get_profile(tenant_id: uuid.UUID, subject_ref: Any) -> dict[str, Any]:
    """A subject's attributes, decrypted, for an authorised reader."""
    from core.database import get_tenant_session

    subject = checks.check_subject(subject_ref)
    async with get_tenant_session(tenant_id) as session:
        row = await _profile_row(session, tenant_id, subject)
    if row is None:
        raise PersonalisationError(404, "profile_unknown", "no profile is kept for this subject")
    return {
        "subject_ref": subject,
        "attributes": await asyncio.to_thread(_decrypt, row),
        "updated_by": row.updated_by or "",
        "updated_at": _iso(row.updated_at),
    }


# ---------------------------------------------------------------- rules


def _rule_fields(row: Any) -> dict[str, Any]:
    return {
        "name": row.name,
        "purpose": row.purpose,
        "priority": row.priority,
        "enabled": bool(row.enabled),
        "conditions": list(row.conditions or []),
        "variant": dict(row.variant or {}),
        "allowed_attributes": list(row.allowed_attributes or []),
    }


def _rule_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        **_rule_fields(row),
        "updated_by": row.updated_by or "",
        "updated_at": _iso(row.updated_at),
    }


async def list_rules(tenant_id: uuid.UUID, *, purpose: Any = None) -> list[dict[str, Any]]:
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationRule

    statement = select(PersonalisationRule).where(PersonalisationRule.tenant_id == tenant_id)
    if purpose:
        statement = statement.where(PersonalisationRule.purpose == checks.check_purpose(purpose))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    return sorted((_rule_dict(row) for row in rows), key=lambda r: (r["purpose"], r["priority"], r["name"]))


async def _rule_row(session: Any, tenant_id: uuid.UUID, rule_id: uuid.UUID) -> Any:
    from core.models.personalisation import PersonalisationRule

    rows = (
        (
            await session.execute(
                select(PersonalisationRule).where(
                    PersonalisationRule.tenant_id == tenant_id, PersonalisationRule.id == rule_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise PersonalisationError(404, "rule_unknown", "no such rule")
    return rows[0]


async def create_rule(tenant_id: uuid.UUID, raw: Any, *, actor: str | None) -> dict[str, Any]:
    """Keep a new rule; its name is unique for the tenant."""
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationRule

    who = _actor(actor)
    fields = checks.check_rule(raw)
    async with get_tenant_session(tenant_id) as session:
        rows = (
            (await session.execute(select(PersonalisationRule).where(PersonalisationRule.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        if any(row.name == fields["name"] for row in rows):
            raise PersonalisationError(409, "rule_exists", "a rule of this name exists")
        if len(rows) >= checks.MAX_RULES:
            raise PersonalisationError(409, "rules_full", f"a tenant keeps at most {checks.MAX_RULES} rules")
        row = (
            await session.execute(
                pg_insert(PersonalisationRule)
                .values(id=uuid.uuid4(), tenant_id=tenant_id, updated_by=who, updated_at=_now(), **fields)
                .on_conflict_do_nothing(index_elements=["tenant_id", "name"])
                .returning(PersonalisationRule)
            )
        ).scalar_one_or_none()
        if row is None:
            raise PersonalisationError(409, "rule_exists", "a rule of this name exists")
        out = _rule_dict(row)
    logger.info("personalisation_rule_created", purpose=out["purpose"])
    return out


RULE_FIELDS = ("purpose", "priority", "enabled", "conditions", "variant", "allowed_attributes")


async def update_rule(tenant_id: uuid.UUID, rule_id: uuid.UUID, raw: Any, *, actor: str | None) -> dict[str, Any]:
    """Change a rule; the result is checked whole, so it still declares every attribute it uses. The name stays."""
    from core.database import get_tenant_session

    who = _actor(actor)
    if not isinstance(raw, dict):
        raise PersonalisationError(422, "rule_invalid", "a change is an object")
    async with get_tenant_session(tenant_id) as session:
        row = await _rule_row(session, tenant_id, rule_id)
        merged = _rule_fields(row)
        merged.update({key: value for key, value in raw.items() if key in RULE_FIELDS})
        fields = checks.check_rule(merged)
        for key in RULE_FIELDS:
            setattr(row, key, fields[key])
        row.updated_by = who
        row.updated_at = _now()
        await session.flush()
        out = _rule_dict(row)
    return out


async def delete_rule(tenant_id: uuid.UUID, rule_id: uuid.UUID, *, actor: str | None) -> None:
    """Remove a rule; the events that name it keep their record with the rule cleared."""
    from core.database import get_tenant_session

    _actor(actor)
    async with get_tenant_session(tenant_id) as session:
        row = await _rule_row(session, tenant_id, rule_id)
        await session.delete(row)
        await session.flush()


# ---------------------------------------------------------------- rendering


async def caller_allow_list(tenant_id: uuid.UUID) -> list[str]:
    """The attributes a caller's own template may name: the tenant's list in the business console, else none."""
    from core.workbench import console

    value = await console.value(tenant_id, CALLER_TEMPLATE_SETTING)
    return [name for name in (value or []) if isinstance(name, str) and checks.NAME_RE.fullmatch(name)]


def _pick_rule(rows: list[Any], purpose: str, named: str | None, attributes: dict[str, Any]) -> Any:
    if named:
        found = [row for row in rows if row.name == named]
        if not found:
            raise PersonalisationError(404, "rule_unknown", "no rule of this name")
        row = found[0]
        if row.purpose != purpose:
            raise PersonalisationError(422, "rule_purpose_mismatch", f"the rule is for {row.purpose}, not {purpose}")
        if not row.enabled:
            raise PersonalisationError(422, "rule_disabled", "the rule is disabled")
        if not checks.rule_matches(_rule_fields(row), attributes):
            raise PersonalisationError(422, "rule_not_matched", "the subject does not meet the rule's conditions")
        return row
    candidates = sorted(
        (row for row in rows if row.enabled and row.purpose == purpose), key=lambda row: (row.priority, row.name)
    )
    for row in candidates:
        if checks.rule_matches(_rule_fields(row), attributes):
            return row
    raise PersonalisationError(404, "no_rule_matched", "no enabled rule for this purpose matches the subject")


async def render(
    tenant_id: uuid.UUID,
    subject_ref: Any,
    purpose: Any,
    *,
    template: str | None = None,
    rule: str | None = None,
    channel: Any,
    actor: str | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    """Content for a subject and purpose under a valid consent; every outcome but a preview is recorded."""
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationEvent, PersonalisationRule

    subject = checks.check_subject(subject_ref)
    purpose = checks.check_purpose(purpose)
    channel = checks.check_channel(channel)
    if template is not None and rule:
        raise PersonalisationError(422, "template_or_rule", "send a template or name a rule, not both")
    named = str(rule or "").strip()[: checks.MAX_RULE_NAME] or None
    caller_template = checks.check_template(template) if template is not None else None
    allow_list = await caller_allow_list(tenant_id) if caller_template is not None else []
    now = _now()
    refusal: PersonalisationError | None = None
    result: dict[str, Any] = {}
    event_id: str | None = None
    async with get_tenant_session(tenant_id) as session:
        consent = await _consent_row(session, tenant_id, subject, purpose, lock="share")
        chosen: Any = None
        try:
            if not consent_valid(consent, now):
                raise PersonalisationError(403, "consent_required", "the subject has no valid consent for this purpose")
            profile = await _profile_row(session, tenant_id, subject)
            attributes = await asyncio.to_thread(_decrypt, profile) if profile is not None else {}
            if caller_template is not None:
                content, used = checks.render_template(caller_template, attributes, allow_list)
            else:
                statement = select(PersonalisationRule).where(PersonalisationRule.tenant_id == tenant_id)
                if named:
                    statement = statement.where(PersonalisationRule.name == named)
                else:
                    statement = statement.where(PersonalisationRule.purpose == purpose)
                rows = list((await session.execute(statement)).scalars().all())
                chosen = _pick_rule(rows, purpose, named, attributes)
                fields = _rule_fields(chosen)
                content, used = checks.render_template(
                    fields["variant"].get("template") or "", attributes, fields["allowed_attributes"]
                )
                # The conditions that selected the variant used their attributes too.
                used = used + [name for name in checks.condition_attributes(fields) if name not in used]
            result = {
                "content": content,
                "content_hash": checks.content_hash(content),
                "rule": (
                    {"id": str(chosen.id), "name": chosen.name, "label": (chosen.variant or {}).get("label") or ""}
                    if chosen is not None
                    else None
                ),
                "attributes_used": sorted(used),
                "consent": {"id": str(consent.id), "purpose": consent.purpose, "expires_at": _iso(consent.expires_at)},
                "channel": channel,
                "preview": preview,
            }
        except PersonalisationError as exc:
            refusal = exc
        if not preview:
            event = PersonalisationEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                subject_ref=subject,
                purpose=purpose,
                consent_id=consent.id if consent is not None else None,
                rule_id=chosen.id if chosen is not None else None,
                attributes_used=[] if refusal else result["attributes_used"],
                content_hash="" if refusal else result["content_hash"],
                channel=channel,
                outcome="refused" if refusal else "rendered",
                refusal=refusal.code[:64] if refusal else "",
                actor=str(actor or "").strip()[:128],
                created_at=now,
            )
            session.add(event)
            await session.flush()
            event_id = str(event.id)
    logger.info(
        "personalisation_render",
        outcome="refused" if refusal else "rendered",
        refusal=refusal.code if refusal else "",
        purpose=purpose,
        channel=channel,
        preview=preview,
    )
    if refusal is not None:
        raise refusal
    result["event_id"] = event_id
    return result


def _event_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "subject_ref": row.subject_ref,
        "purpose": row.purpose,
        "consent_id": str(row.consent_id) if row.consent_id else None,
        "rule_id": str(row.rule_id) if row.rule_id else None,
        "attributes_used": list(row.attributes_used or []),
        "content_hash": row.content_hash or "",
        "channel": row.channel or "",
        "outcome": row.outcome,
        "refusal": row.refusal or None,
        "actor": row.actor or "",
        "created_at": _iso(row.created_at),
    }


async def list_events(tenant_id: uuid.UUID, *, subject_ref: Any = None, limit: int = 50) -> list[dict[str, Any]]:
    """What was rendered or refused, newest first: the attribute names and the content hash, never the content."""
    from core.database import get_tenant_session
    from core.models.personalisation import PersonalisationEvent

    statement = select(PersonalisationEvent).where(PersonalisationEvent.tenant_id == tenant_id)
    if subject_ref:
        statement = statement.where(PersonalisationEvent.subject_ref == checks.check_subject(subject_ref))
    statement = statement.order_by(PersonalisationEvent.created_at.desc()).limit(max(1, min(int(limit), MAX_EVENTS)))
    async with get_tenant_session(tenant_id) as session:
        rows = (await session.execute(statement)).scalars().all()
    found = [_event_dict(row) for row in rows]
    found.sort(key=lambda e: e["created_at"] or "", reverse=True)
    return found
