# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Consent-gated outreach: first contact and reminders on the channels a lead agreed to.

``contact_lead`` asks the contact rules, claims an attempt under a lock on the
lead so two workers never message the same lead twice, renders the message in
the lead's language, sends it through the partner's configured connector, and
records the result. ``run_due`` does this for every lead that is due in one
tenant; the beat sweep runs it for tenants whose branding turns on automatic
contact. Nothing is sent without a consent on record, inside the permitted
hours, under the attempt and reminder limits.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from core.onboarding_desk import messages, senders, store, tokens
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()

DUE_STATUSES: tuple[str, ...] = ("new", "contacted", "engaged", "applying")
CLOSED_STATUSES: tuple[str, ...] = ("opted_out", "submitted", "closed")
MAX_RUN = 200


@dataclass
class Outcome:
    sent: bool
    reason: str
    channel: str | None = None
    attempt_id: str | None = None
    next_allowed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "reason": self.reason,
            "channel": self.channel,
            "attempt_id": self.attempt_id,
            "next_allowed_at": self.next_allowed_at.isoformat() if self.next_allowed_at else None,
            **({"details": self.details} if self.details else {}),
        }


def onboarding_link(base_url: str, tenant_id: uuid.UUID, lead_id: uuid.UUID, now: datetime) -> str:
    return f"{base_url}?t={tokens.sign('apply', tenant_id, lead_id, now=now)}"


def unsubscribe_link(api_base: str, tenant_id: uuid.UUID, lead_id: uuid.UUID, now: datetime) -> str:
    token = tokens.sign("unsubscribe", tenant_id, lead_id, now=now)
    return f"{api_base}/api/v1/onboarding-desk/unsubscribe?t={token}"


def _api_base() -> str:
    from core.config import settings

    return str(settings.public_api_base_url or "http://localhost:8000").rstrip("/")


async def contact_lead(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    *,
    channel: str | None = None,
    kind: str = "first_contact",
    now: datetime | None = None,
) -> Outcome:
    """Contact one lead on ``channel``, or on the first channel the policy, branding and consents allow."""
    moment = now or datetime.now(UTC)
    if kind not in messages.KINDS:
        raise DeskError(422, "kind_invalid", f"kind must be one of {list(messages.KINDS)}")
    lead, _ = await store.get_lead(tenant_id, lead_id)
    if lead.status in CLOSED_STATUSES:
        return Outcome(False, f"lead_{lead.status}")
    policy = await store.get_policy(tenant_id)
    branding = await store.get_branding(tenant_id)
    candidates = [channel] if channel else [c for c in policy.channels if c in branding.senders]
    if not candidates:
        return Outcome(False, "sender_not_configured")
    first_refusal: Outcome | None = None
    chosen: str | None = None
    for candidate in candidates:
        missing = branding.missing_for(candidate)
        if missing:
            first_refusal = first_refusal or Outcome(False, missing, candidate)
            continue
        decision = await store.contact_decision(
            tenant_id, lead_id, candidate, now=moment, is_reminder=kind == "reminder"
        )
        if decision.allowed:
            chosen = candidate
            break
        refusal = Outcome(
            False, decision.reason, candidate, next_allowed_at=decision.next_allowed_at, details=dict(decision.details)
        )
        if first_refusal is None or (
            refusal.next_allowed_at
            and (not first_refusal.next_allowed_at or refusal.next_allowed_at < first_refusal.next_allowed_at)
        ):
            first_refusal = refusal
    if chosen is None:
        assert first_refusal is not None
        # A refusal with a time waits for it; a permanent one rechecks a new lead daily and parks the rest.
        retry = first_refusal.next_allowed_at or (moment + timedelta(hours=24) if lead.status == "new" else None)
        await store.set_next_contact(tenant_id, lead_id, retry)
        if channel:
            await store.record_refusal(tenant_id, lead_id, channel, kind, first_refusal.reason)
        return first_refusal
    attempt = await store.claim_attempt(tenant_id, lead_id, chosen, kind, now=moment)
    contact = json.loads(await store.decrypt_contact(lead))
    to = contact.get("email") if chosen == "email" else contact.get("phone")
    try:
        message = messages.render(
            kind,
            chosen,
            lead.locale,
            branding,
            contact_name=contact.get("contact_name") or "",
            link=onboarding_link(branding.onboarding_url, tenant_id, lead_id, moment),
            unsubscribe_link=unsubscribe_link(_api_base(), tenant_id, lead_id, moment) if chosen == "email" else "",
        )
    except ValueError as exc:
        await store.finish_attempt(tenant_id, attempt.id, ok=False, provider_ref=None, error="message_invalid")
        return Outcome(False, "message_invalid", chosen, str(attempt.id), details={"message": str(exc)})
    result = await senders.send(tenant_id, chosen, branding, str(to or ""), message)
    next_at = moment + timedelta(hours=policy.min_hours_between_reminders) if result.ok else None
    await store.finish_attempt(
        tenant_id,
        attempt.id,
        ok=result.ok,
        provider_ref=result.provider_ref,
        error=result.error,
        next_contact_at=next_at,
    )
    logger.info("onboarding_outreach", channel=chosen, kind=kind, sent=result.ok, error=result.error)
    if not result.ok:
        return Outcome(False, result.error or "send_failed", chosen, str(attempt.id))
    return Outcome(True, "sent", chosen, str(attempt.id))


async def run_due(tenant_id: uuid.UUID, *, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    """Contact every due lead in one tenant: first contact for new leads, reminders for the rest."""
    moment = now or datetime.now(UTC)
    branding = await store.get_branding(tenant_id)
    due = await store.due_leads(
        tenant_id,
        now=moment,
        first_contact_delay=timedelta(minutes=branding.first_contact_delay_minutes),
        limit=max(1, min(limit, MAX_RUN)),
    )
    counts = {"due": len(due), "sent": 0, "deferred": 0, "refused": 0}
    for lead in due:
        kind = "first_contact" if lead.status == "new" else "reminder"
        try:
            outcome = await contact_lead(tenant_id, lead.id, kind=kind, now=moment)
        except DeskError as exc:
            logger.info("onboarding_outreach_skipped", code=exc.code)
            counts["refused"] += 1
            continue
        if outcome.sent:
            counts["sent"] += 1
        elif outcome.next_allowed_at:
            counts["deferred"] += 1
        else:
            counts["refused"] += 1
    return counts


async def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """The beat entry: run due outreach for each tenant whose branding turns on automatic contact."""
    totals = {"tenants": 0, "sent": 0, "deferred": 0, "refused": 0}
    for tenant_id in await store.tenant_ids_with_desk():
        branding = await store.get_branding(tenant_id)
        if not branding.auto_contact:
            continue
        counts = await run_due(tenant_id, now=now)
        totals["tenants"] += 1
        for key in ("sent", "deferred", "refused"):
            totals[key] += counts[key]
    return totals
