# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: leads, consents, opt-outs, the contact policy and signed lead intake.

Off by default (``AGENTICORG_ONBOARDING_DESK_ENABLED``): ``GET /status`` says
so and every other route is not found. Leads arrive from an operator, a CSV of
opted-in leads with an attestation of how consent was captured, or a signed
webhook from a partner form. ``GET /leads/{id}/contact-decision`` says whether
the desk may contact a lead on a channel now and, if not, why and when.
Contact details are stored encrypted; lists show masked forms only.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from api.deps import get_current_tenant, get_current_user, require_tenant_admin
from api.route_metadata import route_meta
from api.v1.agents import _user_uuid_from_claims
from core.onboarding_desk import leads as intake
from core.onboarding_desk import rules, store
from core.onboarding_desk.leads import LeadError
from core.onboarding_desk.store import DeskError

logger = structlog.get_logger()
router = APIRouter(prefix="/onboarding-desk", tags=["Onboarding Desk"])

MAX_WEBHOOK_BYTES = 256 * 1024
SIGNATURE_TOLERANCE_SECONDS = 300
SIGNATURE_HEADER = "x-agenticorg-signature"
PUBLIC_REASON = "partner-form-signed-webhook-hmac-verified-by-the-route-with-the-source-secret"


class PolicyIn(BaseModel):
    model_config = {"extra": "forbid"}

    channels: list[str] = Field(default_factory=lambda: list(rules.OUTREACH_CHANNELS))
    start: str | None = None
    end: str | None = None
    attempts_per_day: int | None = None
    max_reminders: int = rules.MAX_REMINDERS
    min_hours_between_reminders: int = 24
    weekends: bool = True


class SourceIn(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field(..., max_length=16)


class WithdrawIn(BaseModel):
    model_config = {"extra": "forbid"}

    channel: str = Field("all", max_length=16)
    via: str = Field("request", max_length=32)


class OptOutIn(BaseModel):
    model_config = {"extra": "forbid"}

    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=32)
    channel: str = Field("all", max_length=16)
    reason: str = Field("request", max_length=32)


def _off() -> HTTPException:
    return HTTPException(
        404,
        detail={
            "error": "onboarding_desk_disabled",
            "message": "The merchant onboarding desk is off (AGENTICORG_ONBOARDING_DESK_ENABLED).",
        },
    )


def _require_on() -> None:
    if not store.enabled():
        raise _off()


def _refused(exc: DeskError) -> HTTPException:
    return HTTPException(exc.status, detail={"error": exc.code, "message": exc.message})


def _invalid(exc: LeadError) -> HTTPException:
    return HTTPException(422, detail={"error": "lead_invalid", "field": exc.field, "message": exc.message})


def _actor(user: Any) -> str:
    found = _user_uuid_from_claims(user)
    return str(found) if found else ""


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, detail={"error": f"{what}_not_found"}) from None


@router.get("/status")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.status",
)
async def status(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether the desk is on, its channels, countries, consent methods and import limits."""
    return {
        "enabled": store.enabled(),
        "channels": list(rules.CHANNELS),
        "outreach_channels": list(rules.OUTREACH_CHANNELS),
        "countries": list(rules.COUNTRIES),
        "consent_methods": list(intake.METHODS),
        "opt_out_reasons": list(store.OPT_OUT_REASONS),
        "limits": {
            "csv_rows": intake.MAX_CSV_ROWS,
            "csv_bytes": intake.MAX_CSV_BYTES,
            "webhook_leads": intake.MAX_LEADS_PER_WEBHOOK,
        },
    }


@router.get("/policy")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.policy.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.policy.read",
)
async def get_policy(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    return (await store.get_policy(uuid.UUID(tenant_id))).to_dict()


@router.put("/policy", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.policy.write",
    rate_limit="standard",
    idempotency="idempotent-replaces-the-tenant-policy",
    audit_event="onboarding_desk.policy.set",
)
async def put_policy(
    body: PolicyIn, tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Narrow the contact policy; the legal floor for hours and attempts cannot be widened."""
    _require_on()
    try:
        policy = await store.put_policy(uuid.UUID(tenant_id), body.model_dump(), actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    return policy.to_dict()


@router.post("/lead-sources", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.sources.write",
    rate_limit="standard",
    idempotency="not_idempotent-creates-a-lead-source",
    audit_event="onboarding_desk.source.create",
)
async def create_source(
    body: SourceIn, tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """A lead source; a webhook source's signing secret is returned once and never again."""
    _require_on()
    try:
        row, secret = await store.create_source(uuid.UUID(tenant_id), body.name, body.kind, actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    view = store.source_view(row)
    if secret:
        view["signing_secret"] = secret
        view["webhook_path"] = f"/api/v1/onboarding-desk/webhooks/leads/{tenant_id}/{row.id}"
    return view


@router.get("/lead-sources")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.sources.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.source.list",
)
async def list_sources(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    return {"sources": [store.source_view(r) for r in await store.list_sources(uuid.UUID(tenant_id))]}


@router.post("/leads")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.write",
    rate_limit="standard",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.lead.create",
)
async def create_lead(
    body: dict[str, Any], tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Record one lead and the consents the person gave."""
    _require_on()
    try:
        lead = intake.lead_from(body)
    except LeadError as exc:
        raise _invalid(exc) from exc
    outcome = await store.create_lead(uuid.UUID(tenant_id), lead, source_kind="manual", actor=_actor(user))
    return {"lead": store.lead_view(outcome.lead), "created": outcome.created}


@router.post("/leads/import")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.write",
    rate_limit="standard",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.lead.import",
)
async def import_leads(
    file: UploadFile,
    consent_wording: str = Form(""),
    consent_method: str = Form("csv_attestation"),
    consent_written: bool = Form(False),
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Import opted-in leads from CSV; consents are recorded only with an attestation of the wording."""
    _require_on()
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > intake.MAX_CSV_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    data = await file.read(intake.MAX_CSV_BYTES + 1)
    attestation = None
    try:
        if consent_wording.strip():
            attestation = intake.attestation_from(
                {"wording": consent_wording, "method": consent_method, "written": consent_written}
            )
        return await store.import_csv(uuid.UUID(tenant_id), data, attestation, actor=_actor(user), source_id=None)
    except LeadError as exc:
        raise _invalid(exc) from exc


@router.get("/leads")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.lead.list",
)
async def list_leads(
    tenant_id: str = Depends(get_current_tenant),
    status: str | None = Query(None, max_length=16),
    limit: int = Query(50, ge=1, le=store.MAX_LIST),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _require_on()
    rows = await store.list_leads(uuid.UUID(tenant_id), status=status, limit=limit, offset=offset)
    return {"leads": [store.lead_view(r) for r in rows]}


@router.get("/leads/{lead_id}")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.lead.read",
)
async def get_lead(lead_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    try:
        lead, consents = await store.get_lead(uuid.UUID(tenant_id), _uuid(lead_id, "lead"))
    except DeskError as exc:
        raise _refused(exc) from exc
    return {**store.lead_view(lead), "consents": [store.consent_view(c) for c in consents]}


@router.post("/leads/{lead_id}/consents")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="not_idempotent-records-a-consent",
    audit_event="onboarding_desk.consent.record",
)
async def record_consent(
    lead_id: str,
    body: dict[str, Any],
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Record a consent the person gave on a channel, with its wording and how it was captured."""
    _require_on()
    try:
        consent = intake.consent_from(body, now=datetime.now(UTC))
        row = await store.record_consent(uuid.UUID(tenant_id), _uuid(lead_id, "lead"), consent, actor=_actor(user))
    except LeadError as exc:
        raise _invalid(exc) from exc
    except DeskError as exc:
        raise _refused(exc) from exc
    return store.consent_view(row)


@router.post("/leads/{lead_id}/consents/withdraw")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="idempotent-withdrawn-consents-stay-withdrawn",
    audit_event="onboarding_desk.consent.withdraw",
)
async def withdraw_consents(
    lead_id: str,
    body: WithdrawIn,
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    _require_on()
    try:
        count = await store.withdraw_consents(
            uuid.UUID(tenant_id), _uuid(lead_id, "lead"), body.channel, via=body.via, actor=_actor(user)
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return {"withdrawn": count}


@router.post("/opt-outs")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.consents.write",
    rate_limit="standard",
    idempotency="idempotent-a-suppression-is-kept-once",
    audit_event="onboarding_desk.opt_out",
)
async def opt_out(body: OptOutIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Suppress an email or phone on a channel or everywhere, and withdraw the matching consents."""
    _require_on()
    try:
        return await store.opt_out(
            uuid.UUID(tenant_id),
            email=body.email,
            phone=body.phone,
            channel=body.channel,
            reason=body.reason,
            source="operator",
        )
    except DeskError as exc:
        raise _refused(exc) from exc


@router.get("/leads/{lead_id}/contact-decision")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.contact_decision",
)
async def contact_decision(
    lead_id: str,
    channel: str = Query(..., max_length=16),
    reminder: bool = Query(False),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Whether the desk may contact the lead on the channel now; if not, the reason and the next permitted time."""
    _require_on()
    try:
        decision = await store.contact_decision(
            uuid.UUID(tenant_id), _uuid(lead_id, "lead"), channel.lower(), is_reminder=reminder
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return decision.to_dict()


def verify_signature(secret: str, header: str, body: bytes, *, now: float | None = None) -> bool:
    """``t=<unix seconds>,v1=<hex HMAC-SHA256 of "t." + body>`` within the tolerance."""
    parts: dict[str, list[str]] = {}
    for piece in (header or "").split(","):
        key, _, value = piece.strip().partition("=")
        if key and value:
            parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", []))


@router.post("/webhooks/leads/{tenant_id}/{source_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.webhook",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-per-source-and-external-ref",
    audit_event="onboarding_desk.webhook.leads",
    public_reason=PUBLIC_REASON,
)
async def webhook_leads(tenant_id: str, source_id: str, request: Request) -> dict[str, Any]:
    """Leads from a partner form, signed with the source's secret; refused unless the signature is current and valid."""
    if not store.enabled():
        raise _off()
    tid, sid = _uuid(tenant_id, "source"), _uuid(source_id, "source")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, detail={"error": "too_large"})
    found = await store.webhook_secret(tid, sid)
    if found is None or not verify_signature(found[1], request.headers.get(SIGNATURE_HEADER, ""), body):
        raise HTTPException(401, detail={"error": "signature_invalid"})
    import json

    try:
        parsed = json.loads(body or b"{}")
        leads = intake.leads_from_webhook(parsed)
    except ValueError as exc:
        if isinstance(exc, LeadError):
            raise _invalid(exc) from exc
        raise HTTPException(400, detail={"error": "body_invalid"}) from exc
    created = existing = 0
    for lead in leads:
        outcome = await store.create_lead(tid, lead, source_id=sid, source_kind="webhook", actor=f"source:{sid}")
        created += int(outcome.created)
        existing += int(not outcome.created)
    return {"created": created, "existing": existing}


# ── Outreach ─────────────────────────────────────────────────────────────────


class ContactIn(BaseModel):
    model_config = {"extra": "forbid"}

    channel: str | None = Field(None, max_length=16)
    kind: str = Field("first_contact", max_length=16)


class RunIn(BaseModel):
    model_config = {"extra": "forbid"}

    limit: int = Field(50, ge=1, le=200)


@router.get("/branding")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.branding.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.branding.read",
)
async def get_branding(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    return (await store.get_branding(uuid.UUID(tenant_id))).to_dict()


@router.put("/branding", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.branding.write",
    rate_limit="standard",
    idempotency="idempotent-replaces-the-tenant-branding",
    audit_event="onboarding_desk.branding.set",
)
async def put_branding(
    body: dict[str, Any], tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """The partner name, onboarding page, postal address, senders per channel and automatic contact."""
    _require_on()
    try:
        branding = await store.put_branding(uuid.UUID(tenant_id), body, actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    return branding.to_dict()


@router.post("/leads/{lead_id}/contact")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.outreach.write",
    rate_limit="standard",
    idempotency="not_idempotent-may-send-one-message-under-a-lead-lock",
    audit_event="onboarding_desk.outreach.contact",
)
async def contact_lead(lead_id: str, body: ContactIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Send a first contact or a reminder now, only if the contact rules allow it."""
    _require_on()
    from core.onboarding_desk import outreach

    channel = body.channel.lower() if body.channel else None
    try:
        outcome = await outreach.contact_lead(
            uuid.UUID(tenant_id), _uuid(lead_id, "lead"), channel=channel, kind=body.kind
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return outcome.to_dict()


@router.get("/leads/{lead_id}/attempts")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.leads.read",
    rate_limit="standard",
    idempotency="read-only",
    audit_event="onboarding_desk.outreach.attempts",
)
async def list_attempts(lead_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    _require_on()
    rows = await store.list_attempts(uuid.UUID(tenant_id), _uuid(lead_id, "lead"))
    return {"attempts": [store.attempt_view(r) for r in rows]}


@router.post("/outreach/run", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.outreach.write",
    rate_limit="standard",
    idempotency="not_idempotent-contacts-due-leads-under-lead-locks",
    audit_event="onboarding_desk.outreach.run",
)
async def run_outreach(body: RunIn, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Contact this tenant's due leads now, as the minute sweep would."""
    _require_on()
    from core.onboarding_desk import outreach

    return await outreach.run_due(uuid.UUID(tenant_id), limit=body.limit)


_UNSUBSCRIBE_TEXT = {
    "en": (
        "Unsubscribe",
        "Stop emails from {partner} about your application?",
        "Unsubscribe",
        "You are unsubscribed. {partner} will not email you about this application again.",
        "This link is not valid or has expired.",
    ),
    "fr": (
        "Se désabonner",
        "Ne plus recevoir de courriels de {partner} au sujet de votre demande ?",
        "Se désabonner",
        "Vous êtes désabonné. {partner} ne vous écrira plus au sujet de cette demande.",
        "Ce lien n'est pas valide ou a expiré.",
    ),
}


def _page(title: str, body: str, *, lang: str) -> Response:
    import html as _html

    content = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_html.escape(title)}</title></head>"
        '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">'
        f"{body}</body></html>"
    )
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


async def _unsubscribe_context(token: str) -> tuple[Any, Any, str, str]:
    from core.onboarding_desk import tokens

    claims = tokens.verify(token, "unsubscribe")
    lead, _ = await store.get_lead(claims.tenant_id, claims.lead_id)
    branding = await store.get_branding(claims.tenant_id)
    lang = "fr" if lead.locale == "fr" else "en"
    return claims, lead, lang, branding.partner_name or ""


@router.get("/unsubscribe")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.unsubscribe",
    rate_limit="onboarding-desk-public",
    idempotency="read-only",
    audit_event="onboarding_desk.unsubscribe.view",
    public_reason="signed-expiring-unsubscribe-token-names-the-lead",
)
async def unsubscribe_page(t: str = Query(..., max_length=512)) -> Response:
    """A confirmation page; nothing changes until the person presses the button, so link scanners cannot unsubscribe."""
    import html as _html

    from core.onboarding_desk.tokens import TokenError

    if not store.enabled():
        raise _off()
    try:
        _, _, lang, partner = await _unsubscribe_context(t)
    except (TokenError, DeskError):
        return _page(_UNSUBSCRIBE_TEXT["en"][0], f"<p>{_UNSUBSCRIBE_TEXT['en'][4]}</p>", lang="en")
    title, question, button, _, _ = _UNSUBSCRIBE_TEXT[lang]
    form = (
        f"<h1>{_html.escape(title)}</h1><p>{_html.escape(question.format(partner=partner))}</p>"
        '<form method="post" action="/api/v1/onboarding-desk/unsubscribe">'
        f'<input type="hidden" name="t" value="{_html.escape(t)}">'
        f'<button type="submit">{_html.escape(button)}</button></form>'
    )
    return _page(title, form, lang=lang)


@router.post("/unsubscribe")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.unsubscribe",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-a-suppression-is-kept-once",
    audit_event="onboarding_desk.unsubscribe",
    public_reason="signed-expiring-unsubscribe-token-names-the-lead",
)
async def unsubscribe(t: str = Form(..., max_length=512)) -> Response:
    """Suppress the lead's email for this partner; also answers one-click unsubscribe posts."""
    import html as _html
    import json as _json

    from core.onboarding_desk.tokens import TokenError

    if not store.enabled():
        raise _off()
    try:
        claims, lead, lang, partner = await _unsubscribe_context(t)
    except (TokenError, DeskError):
        return _page(_UNSUBSCRIBE_TEXT["en"][0], f"<p>{_UNSUBSCRIBE_TEXT['en'][4]}</p>", lang="en")
    contact = _json.loads(await store.decrypt_contact(lead))
    if contact.get("email"):
        await store.opt_out(
            claims.tenant_id,
            email=contact["email"],
            phone=None,
            channel="email",
            reason="unsubscribe",
            source="email_link",
        )
    title, _, _, done, _ = _UNSUBSCRIBE_TEXT[lang]
    return _page(title, f"<h1>{_html.escape(title)}</h1><p>{_html.escape(done.format(partner=partner))}</p>", lang=lang)


@router.post("/webhooks/twilio/{tenant_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.twilio",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-opt-outs-are-kept-once",
    audit_event="onboarding_desk.webhook.twilio",
    public_reason="twilio-signature-verified-with-the-tenant-sms-connector-auth-token",
)
async def twilio_inbound(tenant_id: str, request: Request) -> Response:
    """An SMS reply: STOP-style keywords opt the sender out of every channel; anything else marks the lead
    engaged and, where the partner turns the conversation on, continues the lead's application."""
    import html as _html

    from api.v1.agents import _resolve_connector_configs
    from api.v1.voice_runtime import _public_api_base
    from core.onboarding_desk import contacts, conversation
    from core.voice.runtime import verify_twilio_signature

    if not store.enabled():
        raise _off()
    tid = _uuid(tenant_id, "tenant")
    form = await request.form()
    params = {str(k): str(v) for k, v in form.items()}
    branding = await store.get_branding(tid)
    sender = branding.senders.get("sms")
    if sender is None:
        raise HTTPException(403, detail={"error": "sms_sender_not_configured"})
    config, names = await _resolve_connector_configs(str(tid), [sender.connector], None, None)
    token = str(config.get("auth_token") or "") if names else ""
    url = f"{_public_api_base()}/api/v1/onboarding-desk/webhooks/twilio/{tid}"
    if not token or not verify_twilio_signature(
        url=url, params=params, signature=request.headers.get("X-Twilio-Signature", ""), auth_token=token
    ):
        raise HTTPException(403, detail={"error": "signature_invalid"})
    sender_phone = params.get("From", "")
    if contacts.is_opt_out(params.get("Body")):
        try:
            await store.opt_out(tid, email=None, phone=sender_phone, channel="all", reason="stop_keyword", source="sms")
        except DeskError:
            pass
        return Response(content='<?xml version="1.0" encoding="UTF-8"?><Response/>', media_type="text/xml")
    await store.mark_engaged(tid, sender_phone)
    try:
        reply = await conversation.handle_sms(tid, sender_phone, params.get("Body", ""))
    except DeskError:
        reply = None
    body = f"<Message>{_html.escape(reply, quote=False)}</Message>" if reply else ""
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
        if body
        else '<?xml version="1.0" encoding="UTF-8"?><Response/>',
        media_type="text/xml",
    )


# ── Applications: the applicant's side ───────────────────────────────────────

APPLICANT_HEADER = "x-applicant-session"
APPLICANT_REASON = "applicant-session-token-hmac-signed-bound-to-one-lead-and-verified-by-the-route"


class SessionIn(BaseModel):
    model_config = {"extra": "forbid"}

    t: str = Field(..., min_length=1, max_length=512)


class AnswersIn(BaseModel):
    model_config = {"extra": "forbid"}

    answers: dict[str, Any]


class ConfirmIn(BaseModel):
    model_config = {"extra": "forbid"}

    items: list[str] = Field(..., min_length=1, max_length=10)


class PersonIn(BaseModel):
    model_config = {"extra": "forbid"}

    message: str = Field("", max_length=1000)


class ResolveIn(BaseModel):
    model_config = {"extra": "forbid"}

    note: str = Field("", max_length=500)


async def _applicant(request: Request) -> tuple[uuid.UUID, Any]:
    """The applicant's tenant and application, from the session header; the lead must still be open."""
    from core.onboarding_desk import applications, tokens

    _require_on()
    try:
        claims = tokens.verify(request.headers.get(APPLICANT_HEADER, ""), "session")
    except tokens.TokenError:
        raise HTTPException(
            401, detail={"error": "session_invalid", "message": "Open your onboarding link again."}
        ) from None
    try:
        return claims.tenant_id, await applications.open_for_lead(claims.tenant_id, claims.lead_id)
    except DeskError as exc:
        raise _refused(exc) from exc


async def _applicant_state(tenant_id: uuid.UUID, application_id: uuid.UUID) -> dict[str, Any]:
    from core.onboarding_desk import applications

    return await applications.state(tenant_id, await applications.get_application(tenant_id, application_id))


@router.post("/apply/session")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-the-lead-has-one-application",
    audit_event="onboarding_desk.apply.session",
    public_reason=APPLICANT_REASON,
)
async def applicant_session(body: SessionIn) -> dict[str, Any]:
    """Exchange an onboarding link for a two-hour session and the application, opened on first use."""
    from core.onboarding_desk import applications, tokens

    _require_on()
    try:
        claims = tokens.verify(body.t, "apply")
    except tokens.TokenError:
        raise HTTPException(
            401, detail={"error": "link_invalid", "message": "This link has expired. Ask for a new one."}
        ) from None
    try:
        row = await applications.open_for_lead(claims.tenant_id, claims.lead_id)
        branding = await store.get_branding(claims.tenant_id)
        state = await applications.state(claims.tenant_id, row)
    except DeskError as exc:
        raise _refused(exc) from exc
    now = datetime.now(UTC)
    return {
        "session": tokens.sign("session", claims.tenant_id, claims.lead_id, now=now),
        "expires_at": (now + tokens.TTL["session"]).isoformat(),
        "partner_name": branding.partner_name,
        "brand_color": branding.brand_color,
        "privacy_url": branding.privacy_url,
        "application": state,
    }


@router.get("/apply/application")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    public_reason=APPLICANT_REASON,
)
async def applicant_application(request: Request) -> dict[str, Any]:
    """The application as the applicant left it, on any channel."""
    from core.onboarding_desk import applications

    tenant_id, row = await _applicant(request)
    return await applications.state(tenant_id, row)


@router.put("/apply/answers")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-the-same-answers-save-the-same-values",
    audit_event="onboarding_desk.apply.answers",
    public_reason=APPLICANT_REASON,
)
async def applicant_answers(body: AnswersIn, request: Request) -> dict[str, Any]:
    """Save answers; each refused answer comes back with a code and a message in the applicant's language."""
    from core.onboarding_desk import applications, questions

    tenant_id, row = await _applicant(request)
    try:
        errors = await applications.save_answers(tenant_id, row.id, body.answers, source="web")
        state = await _applicant_state(tenant_id, row.id)
    except DeskError as exc:
        raise _refused(exc) from exc
    lang = state["locale"]
    return {
        "application": state,
        "errors": {item: {"code": code, "message": questions.error_text(code, lang)} for item, code in errors.items()},
    }


@router.post("/apply/documents")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="not_idempotent-each-upload-is-kept",
    audit_event="onboarding_desk.apply.document",
    public_reason=APPLICANT_REASON,
)
async def applicant_document(
    request: Request, file: UploadFile, item: str = Form(..., max_length=32)
) -> dict[str, Any]:
    """Upload a document for a document item; the values it suggests come back for the applicant to confirm."""
    from core.onboarding_desk import applications, documents

    tenant_id, row = await _applicant(request)
    content = await file.read(documents.MAX_BYTES + 1)
    try:
        return await applications.add_document(
            tenant_id, row.id, item=item, filename=file.filename, content=content
        )
    except DeskError as exc:
        raise _refused(exc) from exc


@router.post("/apply/documents/{document_id}/confirm")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-confirming-again-saves-the-same-values",
    audit_event="onboarding_desk.apply.confirm",
    public_reason=APPLICANT_REASON,
)
async def applicant_confirm(document_id: str, body: ConfirmIn, request: Request) -> dict[str, Any]:
    """Save the values a document suggested that the applicant confirmed."""
    from core.onboarding_desk import applications

    tenant_id, row = await _applicant(request)
    try:
        errors = await applications.confirm_suggestions(tenant_id, row.id, _uuid(document_id, "document"), body.items)
        return {"application": await _applicant_state(tenant_id, row.id), "errors": errors}
    except DeskError as exc:
        raise _refused(exc) from exc


@router.delete("/apply/documents/{document_id}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-a-removed-document-stays-removed",
    audit_event="onboarding_desk.apply.document_remove",
    public_reason=APPLICANT_REASON,
)
async def applicant_remove_document(document_id: str, request: Request) -> dict[str, Any]:
    """Remove one of the applicant's own uploads before the application is submitted."""
    from core.onboarding_desk import applications

    tenant_id, row = await _applicant(request)
    try:
        await applications.remove_document(tenant_id, row.id, _uuid(document_id, "document"))
        return await _applicant_state(tenant_id, row.id)
    except DeskError as exc:
        raise _refused(exc) from exc


@router.post("/apply/person")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="idempotent-one-open-request-per-application",
    audit_event="onboarding_desk.apply.person",
    public_reason=APPLICANT_REASON,
)
async def applicant_person(body: PersonIn, request: Request) -> dict[str, Any]:
    """Ask for a person; the partner's queue gets the request with the conversation so far."""
    from core.onboarding_desk import applications

    tenant_id, row = await _applicant(request)
    try:
        await applications.request_person(tenant_id, row.id, channel="web", message=body.message)
        return await _applicant_state(tenant_id, row.id)
    except DeskError as exc:
        raise _refused(exc) from exc


@router.post("/apply/submit")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.apply",
    rate_limit="onboarding-desk-public",
    idempotency="not_idempotent-a-submitted-application-is-closed-to-changes",
    audit_event="onboarding_desk.apply.submit",
    public_reason=APPLICANT_REASON,
)
async def applicant_submit(request: Request) -> dict[str, Any]:
    """Submit a complete application; refused with the missing items until every required item is in."""
    from core.onboarding_desk import applications, cases

    tenant_id, row = await _applicant(request)
    try:
        await applications.submit(tenant_id, row.id)
        state = await _applicant_state(tenant_id, row.id)
    except DeskError as exc:
        raise _refused(exc) from exc
    try:
        await cases.after_submit(tenant_id, row.id)
    except DeskError as exc:
        logger.warning("onboarding_case_hand_off_not_queued", reason=exc.code)
    return state


# ── Applications: the partner's side ─────────────────────────────────────────


@router.get("/application-policy")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.applications.read", rate_limit="standard")
async def get_application_policy(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The items each merchant type is asked for, required and optional."""
    from core.onboarding_desk import applications, questions

    _require_on()
    policy = await applications.get_application_policy(uuid.UUID(tenant_id))
    return {**policy.to_dict(), "catalogue": questions.view(questions.QuestionSet(tuple(questions.ITEMS), ()), "en")}


@router.put("/application-policy", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.applications.write",
    rate_limit="standard",
    idempotency="idempotent-replaces-the-application-policy",
    audit_event="onboarding_desk.application_policy.update",
)
async def put_application_policy(
    body: dict[str, Any], tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Set the required and optional items, by default and per merchant type."""
    from core.onboarding_desk import applications

    _require_on()
    try:
        policy = await applications.put_application_policy(uuid.UUID(tenant_id), body, actor=_actor(user))
    except DeskError as exc:
        raise _refused(exc) from exc
    return policy.to_dict()


@router.get("/applications")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.applications.read", rate_limit="standard")
async def list_applications(
    status: str | None = Query(None, max_length=16),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Applications, newest first, with which items are answered; no answers are shown here."""
    from core.onboarding_desk import applications

    _require_on()
    try:
        rows = await applications.list_applications(uuid.UUID(tenant_id), status=status, limit=limit)
    except DeskError as exc:
        raise _refused(exc) from exc
    return {"applications": [applications.summary_view(r) for r in rows]}


@router.get("/applications/{application_id}")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.applications.read", rate_limit="standard")
async def get_application(application_id: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """One application with its answers, documents, transcript and, once complete, the case application."""
    from core.onboarding_desk import applications

    _require_on()
    tid = uuid.UUID(tenant_id)
    try:
        row = await applications.get_application(tid, _uuid(application_id, "application"))
        view = await applications.state(tid, row, operator=True)
        view["case"] = await _case_of(tid, row)
        return view
    except DeskError as exc:
        raise _refused(exc) from exc


@router.get("/applications/{application_id}/documents/{document_id}")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.applications.read", rate_limit="standard")
async def download_document(
    application_id: str, document_id: str, tenant_id: str = Depends(get_current_tenant)
) -> Response:
    """An applicant's document, as a download."""
    from core.onboarding_desk import applications

    _require_on()
    try:
        filename, mime, content = await applications.document_content(
            uuid.UUID(tenant_id), _uuid(application_id, "application"), _uuid(document_id, "document")
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/handoffs")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.applications.read", rate_limit="standard")
async def list_handoffs(
    status: str = Query("open", max_length=16),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: str = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Applicants who asked for a person, with their message; the transcript is on the application."""
    from core.onboarding_desk import applications

    _require_on()
    try:
        rows = await applications.list_handoffs(uuid.UUID(tenant_id), status=status, limit=limit)
    except DeskError as exc:
        raise _refused(exc) from exc
    return {"handoffs": [await applications.handoff_view(r) for r in rows]}


@router.post("/handoffs/{handoff_id}/resolve")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.applications.write",
    rate_limit="standard",
    idempotency="idempotent-a-resolved-handoff-stays-resolved",
    audit_event="onboarding_desk.handoff.resolve",
)
async def resolve_handoff(
    handoff_id: str,
    body: ResolveIn,
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark a request for a person handled, with an optional note."""
    from core.onboarding_desk import applications

    _require_on()
    try:
        row = await applications.resolve_handoff(
            uuid.UUID(tenant_id), _uuid(handoff_id, "handoff"), actor=_actor(user), note=body.note
        )
    except DeskError as exc:
        raise _refused(exc) from exc
    return await applications.handoff_view(row)


# ── Cases, evidence and reporting ────────────────────────────────────────────

EVIDENCE_KEY_REASON = "public-verification-key-for-signed-evidence-packs-carries-no-tenant-data"


class OpenCaseIn(BaseModel):
    model_config = {"extra": "forbid"}

    investigate: bool = False


async def _case_of(tenant_id: uuid.UUID, row: Any) -> dict[str, Any] | None:
    from core.onboarding_desk import cases

    if not row.case_ref:
        return None
    case = await cases.get_case(tenant_id, row.case_ref)
    return cases.case_view(case) if case is not None else {"case_ref": row.case_ref, "state": "not_found"}


@router.get("/case-settings")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.cases.read", rate_limit="standard")
async def get_case_settings(tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """Whether a submitted application opens its governed case on its own, and starts the checks."""
    from core.onboarding_desk import cases

    _require_on()
    return (await cases.get_case_settings(uuid.UUID(tenant_id))).to_dict()


@router.put("/case-settings", dependencies=[require_tenant_admin])
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.cases.write",
    rate_limit="standard",
    idempotency="idempotent-replaces-the-case-settings",
    audit_event="onboarding_desk.case_settings.update",
)
async def put_case_settings(
    body: dict[str, Any], tenant_id: str = Depends(get_current_tenant), user: Any = Depends(get_current_user)
) -> dict[str, Any]:
    """Turn the automatic case hand-off and automatic checks on or off."""
    from core.onboarding_desk import cases

    _require_on()
    try:
        return (await cases.put_case_settings(uuid.UUID(tenant_id), body, actor=_actor(user))).to_dict()
    except DeskError as exc:
        raise _refused(exc) from exc


async def _investigate_later(tenant_id: uuid.UUID, case_ref: str, actor: str) -> None:
    from core.onboarding_desk import cases

    await cases.investigate(tenant_id, case_ref, actor=actor)


@router.post("/applications/{application_id}/case")
@route_meta(
    auth_required=True,
    tenant_required=True,
    scope="onboarding_desk.cases.write",
    rate_limit="standard",
    idempotency="idempotent-an-application-has-one-case",
    audit_event="onboarding_desk.case.open",
)
async def open_case(
    application_id: str,
    body: OpenCaseIn,
    background: BackgroundTasks,
    tenant_id: str = Depends(get_current_tenant),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Open the governed case for a submitted application and, if asked, start the checks."""
    from core.onboarding_desk import cases

    _require_on()
    tid = uuid.UUID(tenant_id)
    actor = f"user:{_actor(user)}" if _actor(user) else cases.WORKFLOW_ACTOR
    try:
        case_ref, created = await cases.open_case(tid, _uuid(application_id, "application"), actor=actor)
    except DeskError as exc:
        raise _refused(exc) from exc
    scheduled = bool(body.investigate and created)
    if scheduled:
        background.add_task(_investigate_later, tid, case_ref, actor)
    return {"case_ref": case_ref, "created": created, "investigation_scheduled": scheduled}


@router.get("/cases/{case_ref}")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.cases.read", rate_limit="standard")
async def application_for_case(case_ref: str, tenant_id: str = Depends(get_current_tenant)) -> dict[str, Any]:
    """The application and lead behind a governed case, for a partner receiving the case push."""
    from core.onboarding_desk import applications, cases

    _require_on()
    tid = uuid.UUID(tenant_id)
    try:
        row = await cases.application_for_case(tid, case_ref[:128])
        lead, _ = await store.get_lead(tid, row.lead_id)
    except DeskError as exc:
        raise _refused(exc) from exc
    return {
        "application": applications.summary_view(row),
        "lead": {"id": str(lead.id), "external_ref": lead.external_ref, "source_kind": lead.source_kind},
        "case": await _case_of(tid, row),
    }


@router.get("/applications/{application_id}/evidence")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.cases.read", rate_limit="standard")
async def evidence_pack(application_id: str, tenant_id: str = Depends(get_current_tenant)) -> Response:
    """The application's evidence pack: consents, transcript, documents, checks and decision, signed."""
    import json as _json

    from core.onboarding_desk import evidence

    _require_on()
    try:
        pack = await evidence.build_pack(uuid.UUID(tenant_id), _uuid(application_id, "application"))
    except DeskError as exc:
        raise _refused(exc) from exc
    return Response(
        content=_json.dumps(pack, indent=2, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="onboarding-evidence-{application_id[:36]}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/evidence-key")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:onboarding_desk.evidence_key",
    rate_limit="onboarding-desk-public",
    public_reason=EVIDENCE_KEY_REASON,
)
async def evidence_key() -> dict[str, Any]:
    """The public key evidence packs are signed with, for checking a pack offline."""
    from core.onboarding_desk import evidence

    _require_on()
    try:
        key = evidence.public_key()
    except DeskError as exc:
        raise _refused(exc) from exc
    if key is None:
        raise HTTPException(404, detail={"error": "evidence_unsigned", "message": "Evidence packs are not signed."})
    return key


@router.get("/reports/summary")
@route_meta(auth_required=True, tenant_required=True, scope="onboarding_desk.reports.read", rate_limit="standard")
async def report_summary(
    since: str | None = None, until: str | None = None, tenant_id: str = Depends(get_current_tenant)
) -> dict[str, Any]:
    """The funnel, time to submission and decision, consent coverage and checks per merchant.

    ``since`` and ``until`` are ISO 8601 times with a zone; the default is the last 30 days.
    """
    from core.onboarding_desk import reports

    _require_on()
    try:
        period = reports.period_from(reports.parse_time(since), reports.parse_time(until))
        return await reports.summary(uuid.UUID(tenant_id), period)
    except DeskError as exc:
        raise _refused(exc) from exc
